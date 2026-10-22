"""N679xA electronic-load LIST/ARB profiles against an N6700C-model simulator.

The simulator mirrors behaviour confirmed on an N6700C (E.02.09) with an
N6791A: every load LIST/ARB type reads back exactly, a LIST starts on its
channel's TRIG:TRAN:SOUR, and an ARB start is gated by the mainframe-wide
TRIG:ARB:SOUR whose IMMediate default runs the ARB at INIT:TRAN.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import suppress

import pytest
from scpi_driver_core.simulation.scripted import ScriptedScpiTransport

from keysight_n6700 import (
    N6700,
    DriverArgumentValueError,
    DriverOperationUncertainError,
    DriverPreconditionError,
    DriverUnsupportedOperationError,
    LoadProfileConfiguration,
)
from keysight_n6700.module_capabilities import classify_module

LOAD = 2


@pytest.fixture
def bench() -> Iterator[N6700]:
    """N6700C with an N6775A source on channel 1 and an N6791A load on channel 2."""
    instrument = N6700.connect_simulated(models={1: "N6775A", 2: "N6791A"}, mainframe_model="N6700C")
    load = _sim(instrument).channels[LOAD]
    # Ranges read from a real N6791A (CURR:RANG?, VOLT:RANG?).
    load.current_range = 20.4
    load.voltage_range = 61.2
    try:
        yield instrument
    finally:
        with suppress(Exception):
            instrument.disconnect()


def _transport(driver: N6700) -> ScriptedScpiTransport:
    transport = driver._session(None).transport
    assert isinstance(transport, ScriptedScpiTransport)
    return transport


def _sim(driver: N6700):
    return _transport(driver).instrument


def _cc_list(driver: N6700, **kwargs) -> LoadProfileConfiguration:
    driver.load(LOAD).set_load_mode("cc")
    return driver.configure_load_list(
        LOAD, "cc", [0.5, 2.0, 0.5], [0.01, 0.02, 0.01], maximum_level=3.0, **kwargs
    )


# -- capability gating ----------------------------------------------------------


def test_n6791a_in_n6700c_advertises_list_and_global_arb_dialect() -> None:
    cap = classify_module("N6791A", (), mainframe_model="N6700C", firmware_version="E.02.09.3271")
    assert cap.supports_list_mode
    assert cap.arb_trigger_dialect == "mainframe_global"


def test_unknown_context_disables_load_profiles() -> None:
    cap = classify_module("N6791A")
    assert not cap.supports_list_mode
    assert cap.arb_trigger_dialect == "unsupported"


def test_simulator_only_load_has_no_profiles(driver) -> None:
    load = driver.load(3)
    assert load.capabilities.model == "SIM_LOAD"
    with pytest.raises(DriverUnsupportedOperationError):
        load.configure_load_list("cc", [0.1], 0.01, maximum_level=1.0)


# -- configuration --------------------------------------------------------------


def test_cc_list_round_trip_and_command_order(bench) -> None:
    transport = _transport(bench)
    bench.load(LOAD).set_load_mode("cc")
    transport.clear_history()

    config = bench.configure_load_list(
        LOAD, "cc", [0.5, 2.0, 0.5], [0.01, 0.02, 0.01], maximum_level=3.0, repeat_count=2
    )

    assert config.kind == "list"
    assert config.mode == "cc"
    assert config.levels == pytest.approx((0.5, 2.0, 0.5))
    assert config.dwell_s == pytest.approx((0.01, 0.02, 0.01))
    assert config.duration_s == pytest.approx(0.08)
    assert config.worst_case_current_a is None
    expected = [
        "ABOR:TRAN (@2)",
        "CURR:MODE FIX,(@2)",
        "LIST:CURR 0.5,2,0.5,(@2)",
        "LIST:COUN 2,(@2)",
        "CURR:MODE LIST,(@2)",
    ]
    history = [command.replace(".0,", ",") for command in transport.history]
    positions = [history.index(command) for command in expected]
    assert positions == sorted(positions)
    assert not any(command.startswith("OUTP ") for command in transport.history)
    sim = _sim(bench).channels[LOAD]
    assert (sim.current_mode, sim.voltage_mode, sim.resistance_mode, sim.power_mode) == (
        "LIST", "FIX", "FIX", "FIX"
    )


def test_cv_list_bound_is_the_live_current_limit(bench) -> None:
    load = bench.load(LOAD)
    load.set_load_mode("cv")
    load.set_load_current_limit(0.5)

    config = bench.configure_load_list(
        LOAD, "cv", [5.0, 3.0], 0.01, maximum_level=10.0, maximum_current_a=1.0
    )

    assert config.levels == pytest.approx((5.0, 3.0))
    assert config.worst_case_current_a == pytest.approx(0.5)


def test_cp_bound_is_maximum_power_over_voltage_on_level(bench) -> None:
    bench.load(LOAD).set_load_mode("cp")
    _sim(bench).channels[LOAD].voltage_on_level = 5.0

    config = bench.configure_load_list(
        LOAD, "cp", [1.0, 5.0], 0.01, maximum_level=10.0, maximum_current_a=2.0
    )

    assert config.worst_case_current_a == pytest.approx(2.0)


def test_cp_refuses_when_voltage_on_level_allows_excess_current(bench) -> None:
    """At the 10 mV default, a 10 W profile could try to sink 1000 A."""
    bench.load(LOAD).set_load_mode("cp")
    with pytest.raises(DriverPreconditionError, match="VOLT:INH:VON"):
        bench.configure_load_list(LOAD, "cp", [1.0], 0.01, maximum_level=10.0, maximum_current_a=2.0)


def test_cr_bound_is_voltage_range_over_minimum_resistance(bench) -> None:
    bench.load(LOAD).set_load_mode("cr")

    config = bench.configure_load_list(
        LOAD, "cr", [200.0, 400.0], 0.01, minimum_level=122.4, maximum_level=500.0,
        maximum_current_a=0.5,
    )

    assert config.worst_case_current_a == pytest.approx(0.5)
    with pytest.raises(DriverPreconditionError, match="minimum_level"):
        bench.configure_load_list(
            LOAD, "cr", [20.0], 0.01, minimum_level=10.0, maximum_level=50.0, maximum_current_a=0.5
        )


def test_load_range_reads_each_quantity(bench) -> None:
    load = bench.load(LOAD)
    assert load.get_load_range("cc") == pytest.approx(20.4)
    assert load.get_load_range("cv") == pytest.approx(61.2)
    assert load.get_load_range("cr") == pytest.approx(8000.0)
    assert load.get_load_range("cp") == pytest.approx(102.0)


def test_current_limit_is_rejected_outside_voltage_priority(bench) -> None:
    load = bench.load(LOAD)
    load.set_load_mode("cp")
    load.set_load_current_limit(0.5)
    with pytest.raises(Exception, match="voltage priority"):
        bench.check_errors()


def test_non_cc_profile_requires_maximum_current(bench) -> None:
    bench.load(LOAD).set_load_mode("cv")
    with pytest.raises(DriverArgumentValueError, match="maximum_current_a"):
        bench.configure_load_list(LOAD, "cv", [5.0], 0.01, maximum_level=10.0)


def test_cc_profile_rejects_maximum_current(bench) -> None:
    bench.load(LOAD).set_load_mode("cc")
    with pytest.raises(DriverArgumentValueError, match="maximum_current_a"):
        bench.configure_load_list(LOAD, "cc", [0.5], 0.01, maximum_level=1.0, maximum_current_a=1.0)


def test_current_limit_above_approved_maximum_is_refused(bench) -> None:
    load = bench.load(LOAD)
    load.set_load_mode("cv")
    load.set_load_current_limit(5.0)
    transport = _transport(bench)
    transport.clear_history()
    with pytest.raises(DriverPreconditionError, match="set_load_current_limit"):
        bench.configure_load_list(LOAD, "cv", [5.0], 0.01, maximum_level=10.0, maximum_current_a=1.0)
    assert not any(command.startswith(("LIST:", "ABOR")) for command in transport.history)


def test_priority_mode_mismatch_is_refused_before_any_write(bench) -> None:
    bench.load(LOAD).set_load_mode("cv")
    transport = _transport(bench)
    transport.clear_history()
    with pytest.raises(DriverPreconditionError, match="priority mode"):
        bench.configure_load_list(LOAD, "cc", [0.5], 0.01, maximum_level=1.0)
    assert not any(command.startswith(("LIST:", "ABOR", "CURR:MODE")) for command in transport.history)


def test_input_on_is_refused_before_any_write(bench) -> None:
    load = bench.load(LOAD)
    load.set_load_mode("cc")
    load.input_on()
    transport = _transport(bench)
    transport.clear_history()
    with pytest.raises(DriverPreconditionError, match="input is OFF"):
        bench.configure_load_list(LOAD, "cc", [0.5], 0.01, maximum_level=1.0)
    assert not any(command.startswith(("LIST:", "ABOR", "CURR:MODE")) for command in transport.history)


@pytest.mark.parametrize(
    ("mode", "levels", "kwargs", "match"),
    [
        ("cc", [0.5, 4.0], {"maximum_level": 3.0}, "envelope"),
        ("cc", [0.5], {"maximum_level": 3.0, "minimum_level": -1.0}, "0 <= minimum_level"),
        ("cc", [0.5], {"maximum_level": 0.5, "minimum_level": 0.5}, "minimum_level < maximum_level"),
        ("cr", [10.0], {"maximum_level": 50.0, "maximum_current_a": 1.0}, "minimum_level > 0"),
        ("cc", [], {"maximum_level": 3.0}, "must not be empty"),
        ("xx", [0.5], {"maximum_level": 3.0}, "mode must be"),
    ],
)
def test_offline_envelope_violations(bench, mode, levels, kwargs, match) -> None:
    with pytest.raises(DriverArgumentValueError, match=match):
        bench.configure_load_list(LOAD, mode, levels, 0.01, **kwargs)


def test_maximum_above_live_range_is_refused(bench) -> None:
    bench.load(LOAD).set_load_mode("cc")
    _sim(bench).channels[LOAD].current_range = 2.0
    with pytest.raises(DriverArgumentValueError, match="instrument range"):
        bench.configure_load_list(LOAD, "cc", [0.5], 0.01, maximum_level=3.0)


def test_power_udef_arb_round_trip(bench) -> None:
    bench.load(LOAD).set_load_mode("cp")
    _sim(bench).channels[LOAD].voltage_on_level = 10.0

    config = bench.configure_load_arb(
        LOAD, "cp", [1.0, 5.0, 1.0], [0.01, 0.05, 0.01], maximum_level=10.0, maximum_current_a=1.0
    )

    assert config.kind == "arb"
    assert config.levels == pytest.approx((1.0, 5.0, 1.0))
    sim = _sim(bench).channels[LOAD]
    assert (sim.arb_type, sim.arb_shape, sim.power_mode) == ("POW", "UDEF", "ARB")


def test_resistance_constant_dwell_arb_round_trip(bench) -> None:
    bench.load(LOAD).set_load_mode("cr")

    config = bench.configure_load_constant_dwell_arb(
        LOAD,
        "cr",
        [100.0, 200.0, 300.0],
        0.001,
        maximum_level=500.0,
        minimum_level=61.2,
        maximum_current_a=1.0,
    )

    assert config.kind == "constant_dwell_arb"
    assert config.dwell_s == pytest.approx((0.001,))
    assert config.duration_s == pytest.approx(0.003)
    sim = _sim(bench).channels[LOAD]
    assert (sim.arb_type, sim.arb_shape, sim.resistance_mode) == ("RES", "CDW", "ARB")


def test_resistance_arb_uses_long_form_node(bench) -> None:
    """ARB:RES:... is an undefined header on hardware; only :RESISTANCE works."""
    bench.load(LOAD).set_load_mode("cr")
    transport = _transport(bench)
    transport.clear_history()

    bench.configure_load_arb(
        LOAD, "cr", [200.0, 400.0], 0.01, minimum_level=122.4, maximum_level=500.0,
        maximum_current_a=0.5,
    )

    assert "ARB:FUNC:TYPE RES,(@2)" in transport.history
    assert any(command.startswith("ARB:RESISTANCE:UDEF:LEV ") for command in transport.history)
    assert not any(command.startswith("ARB:RES:") for command in transport.history)


def test_configuration_failure_returns_every_mode_to_fix(bench, monkeypatch) -> None:
    load = bench.load(LOAD)
    load.set_load_mode("cc")

    def reject(feature: str) -> None:
        raise DriverOperationUncertainError(f"injected {feature} failure")

    monkeypatch.setattr(load, "_check_profile_errors", reject)
    with pytest.raises(DriverOperationUncertainError, match="injected") as caught:
        bench.configure_load_list(LOAD, "cc", [0.5], 0.01, maximum_level=1.0)

    assert caught.value.details["load_profile_configuration_cleanup"] == "cleanup completed"
    sim = _sim(bench).channels[LOAD]
    assert (sim.current_mode, sim.voltage_mode, sim.resistance_mode, sim.power_mode) == ("FIX",) * 4
    assert load.get_load_transient_status().state == "unknown"


# -- trigger lifecycle ----------------------------------------------------------


def test_list_bus_lifecycle_uses_channel_scoped_trigger(bench) -> None:
    _cc_list(bench)
    bench.configure_load_transient_trigger(LOAD, "bus")
    bench.load(LOAD).input_on()
    transport = _transport(bench)
    transport.clear_history()

    token = bench.arm_load_transient(LOAD)
    assert bench.wait_load_transient_ready(LOAD, token, timeout_s=1.0).waiting_for_trigger
    bench.trigger_load_transient(LOAD, token)
    status = bench.wait_load_transient_complete(LOAD, token, timeout_s=1.0)

    assert status.state == "complete"
    assert status.kind == "list"
    assert "TRIG:TRAN (@2)" in transport.history
    assert "*TRG" not in transport.history
    assert not any(command.startswith("TRIG:ARB") for command in transport.history)


def test_arm_requires_input_on(bench) -> None:
    _cc_list(bench)
    bench.configure_load_transient_trigger(LOAD, "bus")
    with pytest.raises(DriverPreconditionError, match="input must be ON"):
        bench.arm_load_transient(LOAD)


def test_arb_refuses_to_arm_while_mainframe_trigger_is_immediate(bench) -> None:
    """IMMediate would start the ARB at INIT:TRAN without any trigger."""
    load = bench.load(LOAD)
    load.set_load_mode("cc")
    bench.configure_load_arb(LOAD, "cc", [0.5, 1.0], 0.01, maximum_level=2.0)
    load.input_on()
    assert _sim(bench).global_arb_trigger_source == "IMM"
    transport = _transport(bench)
    transport.clear_history()

    with pytest.raises(DriverPreconditionError, match="trigger sources"):
        bench.arm_load_transient(LOAD)
    assert not any(command.startswith("INIT:TRAN") for command in transport.history)


def test_arb_trigger_configures_mainframe_wide_source_and_runs(bench) -> None:
    load = bench.load(LOAD)
    load.set_load_mode("cc")
    bench.configure_load_arb(LOAD, "cc", [0.5, 1.0], 0.01, maximum_level=2.0)
    transport = _transport(bench)
    transport.clear_history()

    bench.configure_load_transient_trigger(LOAD, "bus")

    assert "TRIG:ARB:SOUR BUS" in transport.history
    assert "TRIG:TRAN:SOUR BUS,(@2)" in transport.history
    assert _sim(bench).global_arb_trigger_source == "BUS"
    load.input_on()
    token = bench.arm_load_transient(LOAD)
    bench.wait_load_transient_ready(LOAD, token, timeout_s=1.0)
    bench.trigger_load_transient(LOAD, token)
    assert bench.wait_load_transient_complete(LOAD, token, timeout_s=1.0).state == "complete"


def test_step_once_list_requires_external_trigger(bench) -> None:
    _cc_list(bench, step_mode="once")
    bench.configure_load_transient_trigger(LOAD, "bus")
    bench.load(LOAD).input_on()
    with pytest.raises(DriverPreconditionError, match="external"):
        bench.arm_load_transient(LOAD)


def test_completion_with_input_off_is_not_clean(bench) -> None:
    _cc_list(bench)
    bench.configure_load_transient_trigger(LOAD, "bus")
    bench.load(LOAD).input_on()
    token = bench.arm_load_transient(LOAD)
    bench.wait_load_transient_ready(LOAD, token, timeout_s=1.0)
    bench.trigger_load_transient(LOAD, token)
    _sim(bench).channels[LOAD].enabled = False

    with pytest.raises(DriverOperationUncertainError, match="input is OFF"):
        bench.wait_load_transient_complete(LOAD, token, timeout_s=1.0)
    assert bench.get_load_transient_status(LOAD).state == "unknown"


def test_abort_invalidates_token_and_allows_rearm(bench) -> None:
    _cc_list(bench)
    bench.configure_load_transient_trigger(LOAD, "bus")
    bench.load(LOAD).input_on()
    stale = bench.arm_load_transient(LOAD)

    bench.abort_load_transient(LOAD)

    assert bench.get_load_transient_status(LOAD).state == "aborted"
    with pytest.raises(DriverPreconditionError, match="stale"):
        bench.wait_load_transient_ready(LOAD, stale, timeout_s=0.1)
    fresh = bench.arm_load_transient(LOAD)
    assert bench.wait_load_transient_ready(LOAD, fresh, timeout_s=1.0).waiting_for_trigger


def test_abort_waits_for_idle_before_returning_modes_to_fix(bench) -> None:
    """Real ABOR:TRAN settles in ~0.2 s and MODE writes meanwhile return +308."""
    _cc_list(bench)
    bench.configure_load_transient_trigger(LOAD, "bus")
    bench.load(LOAD).input_on()
    token = bench.arm_load_transient(LOAD)
    bench.wait_load_transient_ready(LOAD, token, timeout_s=1.0)
    _sim(bench).abort_settle_polls = 3

    bench.clear_load_profile(LOAD)

    sim = _sim(bench).channels[LOAD]
    assert sim.current_mode == "FIX"
    assert not sim.operation_status & 64
    assert bench.get_load_transient_status(LOAD).state == "idle"


def test_abort_that_never_settles_is_uncertain(bench, monkeypatch) -> None:
    _cc_list(bench)
    bench.configure_load_transient_trigger(LOAD, "bus")
    bench.load(LOAD).input_on()
    bench.arm_load_transient(LOAD)
    _sim(bench).abort_settle_polls = 10_000
    load = bench.load(LOAD)
    original = load._stop_profile_system
    monkeypatch.setattr(load, "_stop_profile_system", lambda: original(timeout_s=0.05))

    with pytest.raises(DriverOperationUncertainError, match="idle"):
        bench.abort_load_transient(LOAD)
    assert bench.get_load_transient_status(LOAD).state == "unknown"


def test_trigger_source_cannot_change_while_armed(bench) -> None:
    _cc_list(bench)
    bench.configure_load_transient_trigger(LOAD, "bus")
    bench.load(LOAD).input_on()
    bench.arm_load_transient(LOAD)
    with pytest.raises(DriverPreconditionError, match="armed"):
        bench.configure_load_transient_trigger(LOAD, "external")


def test_clear_load_profile_returns_to_fix(bench) -> None:
    _cc_list(bench)
    bench.clear_load_profile(LOAD)
    sim = _sim(bench).channels[LOAD]
    assert sim.current_mode == "FIX"
    assert bench.get_load_transient_status(LOAD).kind is None


def test_shutdown_aborts_load_transient_before_input_off(bench) -> None:
    _cc_list(bench)
    bench.configure_load_transient_trigger(LOAD, "bus")
    bench.load(LOAD).input_on()
    bench.arm_load_transient(LOAD)
    transport = _transport(bench)
    transport.clear_history()

    result = bench.shutdown_all()

    assert result.success
    load_commands = [command for command in transport.history if command.endswith("(@2)")]
    assert load_commands[:3] == ["INIT:CONT:TRAN OFF,(@2)", "ABOR:TRAN (@2)", "OUTP OFF,(@2)"]
    assert _sim(bench).channels[LOAD].enabled is False


def test_profile_runs_while_digitizer_owns_the_mainframe(bench) -> None:
    """A transient-triggered capture: the lease must let the profile lifecycle through."""
    load = bench.load(LOAD)
    _cc_list(bench)
    bench.configure_load_transient_trigger(LOAD, "bus")
    load.input_on()
    load.configure_digitize("current", points=64, interval_s=20.48e-6)
    load.configure_digitize_trigger("transient")
    load.arm_digitize()
    load.wait_digitize_armed(timeout_s=1.0)

    token = bench.arm_load_transient(LOAD)
    bench.wait_load_transient_ready(LOAD, token, timeout_s=1.0)
    bench.trigger_load_transient(LOAD, token)
    assert bench.wait_load_transient_complete(LOAD, token, timeout_s=1.0).state == "complete"
    load.abort_digitize()
