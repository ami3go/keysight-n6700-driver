"""Built-in ARB shapes, sequences, constant-dwell, step, current and continuous transients."""

from __future__ import annotations

import math
from dataclasses import replace

import pytest
from scpi_driver_core.simulation.scripted import ScriptedScpiTransport

from keysight_n6700 import (
    N6700,
    ArbExponential,
    ArbPulse,
    ArbRamp,
    ArbSequenceStep,
    ArbSine,
    ArbStaircase,
    ArbStep,
    ArbTrapezoid,
    ArbUserDefined,
    DriverArgumentValueError,
    DriverPreconditionError,
    DriverTimeoutError,
    DriverUnsupportedOperationError,
    shape_from_name,
)
from keysight_n6700.waveforms import PARAMETRIC_MAXIMUM_TIME_S, normalize_repeat_count


def _sim(driver):
    return driver._session(None).transport.instrument


def _run_bus(driver, channel: int = 1, baseline_v: float = 2.0):
    """Energize at ``baseline_v``, run one BUS-triggered pass and return the final status."""
    driver.set_dc_voltage(baseline_v, channel)
    driver.enable_output(channel)
    token = driver.arm_voltage_transient(channel)
    driver.wait_voltage_transient_ready(channel, token)
    driver.trigger_voltage_transient(channel, token)
    return driver.wait_voltage_transient_complete(channel, token)


SHAPES = [
    ArbStep(1.0, 5.0, 0.01),
    ArbRamp(1.0, 5.0, 0.01, start_time_s=0.002, end_time_s=0.003),
    ArbStaircase(1.0, 5.0, 5, 0.01),
    ArbSine(2.0, 5.0, 100.0),
    ArbPulse(1.0, 5.0, 0.01, start_time_s=0.001, end_time_s=0.001),
    ArbTrapezoid(1.0, 5.0, 0.01, 0.01, 0.01),
    ArbExponential(1.0, 5.0, 0.01, 0.002),
    ArbUserDefined((1.0, 5.0, 3.0), (0.01,), (True, False, False)),
]


# -- shape objects ------------------------------------------------------------


@pytest.mark.parametrize("waveform", SHAPES, ids=lambda shape: type(shape).__name__)
def test_voltage_arb_shape_round_trips_and_runs(driver, waveform) -> None:
    config = driver.configure_voltage_arb_waveform(1, waveform, maximum_voltage_v=10.0)
    assert config.waveform == waveform
    assert config.duration_s == pytest.approx(waveform.duration_s)
    assert _run_bus(driver).state == "complete"


def test_shape_level_bounds_and_durations() -> None:
    assert ArbSine(2.0, 5.0, 100.0).level_bounds == (3.0, 7.0)
    assert ArbSine(2.0, 5.0, 100.0).duration_s == pytest.approx(0.01)
    assert ArbPulse(1.0, 5.0, 0.01, 0.002, 0.003).duration_s == pytest.approx(0.015)
    # The time constant shapes the curve; it adds no run time.
    assert ArbExponential(1.0, 5.0, 0.01, 0.5, start_time_s=0.002).duration_s == pytest.approx(0.012)
    assert ArbStaircase(1.0, 5.0, 4.0, 0.01).steps == 4


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ArbSine(-1.0, 5.0, 100.0),
        lambda: ArbSine(1.0, 5.0, 2.0e4),
        lambda: ArbSine(1.0, 5.0, 1.0e-6),
        lambda: ArbRamp(1.0, 5.0, -0.01),
        lambda: ArbRamp(1.0, math.nan, 0.01),
        lambda: ArbStep(True, 5.0),
        lambda: ArbStaircase(1.0, 5.0, 0, 0.01),
        lambda: ArbStaircase(1.0, 5.0, 501, 0.01),
        lambda: ArbStaircase(1.0, 5.0, 2.5, 0.01),
        lambda: ArbUserDefined((), (0.01,)),
        lambda: ArbUserDefined((1.0, 2.0), (0.01, 0.01, 0.01)),
        lambda: ArbUserDefined((1.0, 2.0), (-0.01,)),
        lambda: ArbUserDefined((1.0,) * 512, (0.01,)),
        lambda: ArbUserDefined((1.0, 2.0), (0.01,), (True, False, True)),
    ],
)
def test_invalid_shape_parameters_are_rejected(factory) -> None:
    with pytest.raises(DriverArgumentValueError):
        factory()


@pytest.mark.parametrize(
    ("waveform", "attribute"),
    [
        (ArbStep(1.0, 2.0), "start_time_s"),
        (ArbRamp(1.0, 2.0, 1.0), "start_time_s"),
        (ArbRamp(1.0, 2.0, 1.0), "rise_time_s"),
        (ArbRamp(1.0, 2.0, 1.0), "end_time_s"),
        (ArbStaircase(1.0, 2.0, 2, 1.0), "start_time_s"),
        (ArbStaircase(1.0, 2.0, 2, 1.0), "staircase_time_s"),
        (ArbStaircase(1.0, 2.0, 2, 1.0), "end_time_s"),
        (ArbPulse(1.0, 2.0, 1.0), "start_time_s"),
        (ArbPulse(1.0, 2.0, 1.0), "top_time_s"),
        (ArbPulse(1.0, 2.0, 1.0), "end_time_s"),
        (ArbTrapezoid(1.0, 2.0, 1.0, 1.0, 1.0), "start_time_s"),
        (ArbTrapezoid(1.0, 2.0, 1.0, 1.0, 1.0), "rise_time_s"),
        (ArbTrapezoid(1.0, 2.0, 1.0, 1.0, 1.0), "top_time_s"),
        (ArbTrapezoid(1.0, 2.0, 1.0, 1.0, 1.0), "fall_time_s"),
        (ArbTrapezoid(1.0, 2.0, 1.0, 1.0, 1.0), "end_time_s"),
        (ArbExponential(1.0, 2.0, 1.0, 1.0), "start_time_s"),
        (ArbExponential(1.0, 2.0, 1.0, 1.0), "time_s"),
        (ArbExponential(1.0, 2.0, 1.0, 1.0), "time_constant_s"),
    ],
    ids=lambda value: value if isinstance(value, str) else type(value).__name__,
)
def test_every_parametric_shape_time_obeys_documented_ceiling(waveform, attribute) -> None:
    assert replace(waveform, **{attribute: PARAMETRIC_MAXIMUM_TIME_S}).times_s
    with pytest.raises(DriverArgumentValueError, match=r"must be <= 262\.144 s"):
        replace(waveform, **{attribute: PARAMETRIC_MAXIMUM_TIME_S + 0.001})


def test_n678_udef_uses_its_distinct_dwell_timebase() -> None:
    driver = N6700.connect_simulated(models={1: "N6784A"}, mainframe_model="N6705C")
    try:
        config = driver.configure_voltage_arb_waveform(
            1,
            ArbUserDefined((0.0,), (268.435,)),
            minimum_voltage_v=-1.0,
            maximum_voltage_v=1.0,
            maximum_duration_s=269.0,
        )
        assert config.waveform.duration_s == pytest.approx(268.435)
    finally:
        driver.disconnect()


def test_user_defined_shape_broadcasts_dwell_and_trigger_flags() -> None:
    shape = ArbUserDefined((1.0, 2.0, 3.0), (0.01,), (True,))
    assert shape.dwell_s == (0.01, 0.01, 0.01)
    assert shape.begin_step_triggers == (True, True, True)
    assert ArbUserDefined((1.0, 2.0), (0.01,), (False, False)).begin_step_triggers == ()


def test_shape_from_name_builds_and_validates() -> None:
    assert shape_from_name("Sine", {"amplitude": 1, "offset": 2, "frequency_hz": 10}) == ArbSine(
        1.0, 2.0, 10.0
    )
    assert isinstance(
        shape_from_name("udef", {"levels": [1, 2], "dwell_s": [0.1]}), ArbUserDefined
    )
    with pytest.raises(DriverArgumentValueError, match="unknown ARB shape"):
        shape_from_name("square", {})
    with pytest.raises(DriverArgumentValueError, match="does not accept"):
        shape_from_name("ramp", {"start_level": 1, "end_level": 2, "rise_time_s": 1, "slope": 3})
    with pytest.raises(DriverArgumentValueError, match="invalid ArbRamp"):
        shape_from_name("ramp", {"start_level": 1})


@pytest.mark.parametrize(
    ("value", "expected"), [(3, 3), (4.0, 4), ("infinite", "infinite"), (" INF ", "infinite")]
)
def test_repeat_count_normalization(value, expected) -> None:
    assert normalize_repeat_count(value, 256) == expected


@pytest.mark.parametrize("value", [0, 257, 1.5, True, math.inf, "forever"])
def test_repeat_count_rejects_invalid_values(value) -> None:
    with pytest.raises(DriverArgumentValueError):
        normalize_repeat_count(value, 256)


# -- single-shape ARB envelope ------------------------------------------------


def test_sine_beyond_envelope_is_rejected_before_any_write(driver) -> None:
    transport = driver._session(None).transport
    assert isinstance(transport, ScriptedScpiTransport)
    transport.clear_history()
    with pytest.raises(DriverArgumentValueError, match="envelope"):
        # offset 5 V + 6 V peak reaches 11 V, above the approved 10 V.
        driver.configure_voltage_arb_waveform(1, ArbSine(6.0, 5.0, 100.0), maximum_voltage_v=10.0)
    assert not any(command.startswith("ARB:") for command in transport.history)


def test_n6784a_accepts_bipolar_voltage_waveform() -> None:
    driver = N6700.connect_simulated(models={1: "N6784A"}, mainframe_model="N6705C")
    try:
        config = driver.configure_voltage_arb_waveform(
            1,
            ArbSine(5.0, 0.0, 100.0),
            minimum_voltage_v=-5.0,
            maximum_voltage_v=5.0,
        )
        assert config.minimum_level == pytest.approx(-5.0)
        assert config.maximum_level == pytest.approx(5.0)
        assert config.waveform.level_bounds == pytest.approx((-5.0, 5.0))
    finally:
        driver.disconnect()


def test_non_bipolar_module_rejects_negative_voltage_envelope(driver) -> None:
    with pytest.raises(DriverArgumentValueError, match="safety bounds"):
        driver.configure_voltage_arb_waveform(
            1,
            ArbSine(1.0, 0.0, 100.0),
            minimum_voltage_v=-1.0,
            maximum_voltage_v=1.0,
        )


def test_source_transients_are_rejected_early_without_option_054() -> None:
    driver = N6700.connect_simulated(mainframe_model="N6700C")
    try:
        transport = driver._session(None).transport
        transport.clear_history()
        with pytest.raises(DriverUnsupportedOperationError, match="LIST/ARB support"):
            driver.configure_voltage_arb_waveform(
                1, ArbSine(1.0, 2.0, 100.0), maximum_voltage_v=5.0
            )
        assert not any(command.startswith("ARB:") for command in transport.history)

        with pytest.raises(DriverUnsupportedOperationError, match="LIST/ARB support"):
            driver.configure_voltage_list(
                1, [1.0, 2.0], 0.001, maximum_voltage_v=5.0
            )
        assert not any(command.startswith(("ARB:", "LIST:")) for command in transport.history)
    finally:
        driver.disconnect()


def test_shape_duration_is_bounded(driver) -> None:
    with pytest.raises(DriverArgumentValueError, match="maximum_duration_s"):
        driver.configure_voltage_arb_waveform(
            1, ArbSine(1.0, 5.0, 1.0), maximum_voltage_v=10.0, repeat_count=100,
            maximum_duration_s=10.0,
        )


def test_trigger_source_uses_mode_specific_typed_dialect(driver) -> None:
    transport = driver._session(None).transport
    assert isinstance(transport, ScriptedScpiTransport)

    driver.configure_voltage_list(1, [1.0, 2.0], 0.001, maximum_voltage_v=5.0)
    transport.clear_history()
    driver.configure_voltage_transient_trigger(1, "bus")
    assert "TRIG:TRAN:SOUR BUS,(@1)" in transport.history
    assert "TRIG:TRAN:SOUR? (@1)" in transport.history
    assert not any(command.startswith("TRIG:ARB:SOUR") for command in transport.history)

    driver.configure_voltage_arb_waveform(
        1, ArbSine(1.0, 2.0, 100.0), maximum_voltage_v=5.0
    )
    transport.clear_history()
    driver.configure_voltage_transient_trigger(1, "external")
    assert "TRIG:ARB:SOUR EXT,(@1)" in transport.history
    assert "TRIG:ARB:SOUR? (@1)" in transport.history
    assert not any(command.startswith(("TRIG:TRAN:SOUR", "*TRG")) for command in transport.history)


def test_unknown_arb_trigger_dialect_fails_closed(driver) -> None:
    driver.configure_voltage_arb_waveform(
        1, ArbSine(1.0, 2.0, 100.0), maximum_voltage_v=5.0
    )
    channel = driver.power_supply(1)
    channel.capabilities = replace(channel.capabilities, arb_trigger_dialect="unsupported")
    with pytest.raises(DriverUnsupportedOperationError, match="no approved ARB trigger dialect"):
        channel.configure_voltage_transient_trigger("bus")


def test_infinite_repeat_cannot_wait_for_completion(driver) -> None:
    driver.configure_voltage_arb_waveform(
        1, ArbSine(1.0, 5.0, 100.0), maximum_voltage_v=10.0, repeat_count="infinite"
    )
    driver.set_dc_voltage(5.0, 1)
    driver.enable_output(1)
    token = driver.arm_voltage_transient(1)
    driver.wait_voltage_transient_ready(1, token)
    driver.trigger_voltage_transient(1, token)
    with pytest.raises(DriverPreconditionError, match="never completes"):
        driver.wait_voltage_transient_complete(1, token)
    driver.abort_voltage_transient(1)
    assert driver.get_voltage_transient_status(1).transient_active is False


# -- constant-dwell ARB -------------------------------------------------------


def test_constant_dwell_arb_round_trips_and_runs(driver) -> None:
    levels = [1.0 + index / 100 for index in range(200)]
    config = driver.configure_voltage_constant_dwell_arb(
        1, levels, 0.001, maximum_voltage_v=10.0, repeat_count=2
    )
    assert config.levels == pytest.approx(tuple(levels))
    assert config.dwell_s == pytest.approx(0.001)
    assert config.duration_s == pytest.approx(0.4)
    assert _run_bus(driver).state == "complete"


@pytest.mark.parametrize(
    ("dwell_s", "kwargs"),
    [(5.0e-6, {}), (0.5, {}), (0.001, {"repeat_count": 257})],
)
def test_constant_dwell_arb_rejects_out_of_range_settings(driver, dwell_s, kwargs) -> None:
    with pytest.raises(DriverArgumentValueError):
        driver.configure_voltage_constant_dwell_arb(
            1, [1.0, 2.0], dwell_s, maximum_voltage_v=10.0, **kwargs
        )


# -- ARB sequences ------------------------------------------------------------


def _sequence():
    return [
        ArbSequenceStep(ArbPulse(1.0, 5.0, 0.01), repeat_count=2),
        ArbSequenceStep(ArbRamp(1.0, 4.0, 0.01)),
        ArbSequenceStep(ArbSine(1.0, 3.0, 100.0)),
    ]


def test_arb_sequence_programs_steps_in_order_and_runs(driver) -> None:
    transport = driver._session(None).transport
    transport.clear_history()
    config = driver.configure_voltage_arb_sequence(
        1, _sequence(), maximum_voltage_v=10.0, sequence_count=2
    )
    assert config.steps == tuple(_sequence())
    assert config.sequence_count == 2
    assert config.trigger_paced is False
    assert config.duration_s == pytest.approx(2 * (2 * 0.01 + 0.01 + 0.01))
    shapes = [c for c in transport.history if c.startswith("ARB:SEQ:STEP:FUNC:SHAP ")]
    assert shapes == [
        "ARB:SEQ:STEP:FUNC:SHAP PULS,0,(@1)",
        "ARB:SEQ:STEP:FUNC:SHAP RAMP,1,(@1)",
        "ARB:SEQ:STEP:FUNC:SHAP SIN,2,(@1)",
    ]
    assert transport.history.index("ARB:SEQ:RES (@1)") < transport.history.index(shapes[0])
    assert _run_bus(driver).state == "complete"


@pytest.mark.parametrize("step_base", [0, 1])
def test_arb_sequence_detects_firmware_step_numbering(step_base) -> None:
    driver = N6700.connect_simulated(
        mainframe_model="N6705C", sequence_step_base=step_base
    )
    try:
        transport = driver._session(None).transport
        transport.clear_history()
        config = driver.configure_voltage_arb_sequence(
            1, _sequence(), maximum_voltage_v=10.0
        )
        assert config.steps == tuple(_sequence())
        shapes = [
            command
            for command in transport.history
            if command.startswith("ARB:SEQ:STEP:FUNC:SHAP ")
        ]
        assert [int(command.rsplit(",", 2)[1]) for command in shapes] == [
            step_base,
            step_base + 1,
            step_base + 2,
        ]
        assert driver.power_supply(1)._sequence_step_base == step_base
    finally:
        driver.disconnect()


def test_arb_sequence_rejects_invalid_steps(driver) -> None:
    with pytest.raises(DriverArgumentValueError, match=r"1\.\.100"):
        driver.configure_voltage_arb_sequence(1, [], maximum_voltage_v=10.0)
    with pytest.raises(DriverArgumentValueError, match=r"1\.\.100"):
        driver.configure_voltage_arb_sequence(
            1, [ArbSequenceStep(ArbStep(1.0, 2.0))] * 101, maximum_voltage_v=10.0
        )
    with pytest.raises(DriverArgumentValueError, match="ArbSequenceStep"):
        driver.configure_voltage_arb_sequence(1, [ArbStep(1.0, 2.0)], maximum_voltage_v=10.0)
    with pytest.raises(DriverArgumentValueError, match="envelope"):
        driver.configure_voltage_arb_sequence(
            1, [ArbSequenceStep(ArbPulse(1.0, 12.0, 0.01))], maximum_voltage_v=10.0
        )
    with pytest.raises(DriverArgumentValueError, match="trigger-paced"):
        ArbSequenceStep(ArbStep(1.0, 2.0), repeat_count="infinite")


def test_trigger_paced_sequence_requires_external_source(driver) -> None:
    driver.configure_voltage_arb_sequence(
        1,
        [ArbSequenceStep(ArbPulse(1.0, 5.0, 0.01), pacing="trigger")],
        maximum_voltage_v=10.0,
    )
    driver.set_dc_voltage(1.0, 1)
    driver.enable_output(1)
    with pytest.raises(DriverPreconditionError, match="trigger-paced"):
        driver.arm_voltage_transient(1)
    driver.configure_voltage_transient_trigger(1, "external")
    token = driver.arm_voltage_transient(1)
    assert driver.wait_voltage_transient_ready(1, token).state == "ready"
    _sim(driver).execute("TRIG:TRAN (@1)")  # the external edge
    assert driver.wait_voltage_transient_complete(1, token, timeout_s=1.0).state == "complete"


def test_sequence_file_store_and_load_round_trip(driver) -> None:
    driver.configure_voltage_arb_sequence(1, _sequence(), maximum_voltage_v=10.0)
    driver.store_arb_sequence(1, r"internal:\seq\dip.seq")
    driver.configure_voltage_arb_waveform(1, ArbStep(1.0, 2.0), maximum_voltage_v=10.0)
    config = driver.load_voltage_arb_sequence(1, r"internal:\seq\dip.seq", maximum_voltage_v=10.0)
    assert config.steps == tuple(_sequence())
    assert _run_bus(driver).state == "complete"


def test_loaded_sequence_outside_envelope_is_refused(driver) -> None:
    driver.configure_voltage_arb_sequence(1, _sequence(), maximum_voltage_v=10.0)
    driver.store_arb_sequence(1, r"internal:\dip.seq")
    with pytest.raises(DriverPreconditionError, match="rejected"):
        driver.load_voltage_arb_sequence(1, r"internal:\dip.seq", maximum_voltage_v=4.5)
    status = driver.get_voltage_transient_status(1)
    assert status.state == "unknown"
    assert driver.power_supply(1)._query("VOLT:MODE? (@1)").strip() == "FIX"


def test_loaded_infinite_sequence_requires_opt_in(driver) -> None:
    driver.configure_voltage_arb_sequence(
        1, [ArbSequenceStep(ArbPulse(1.0, 5.0, 0.01))], maximum_voltage_v=10.0,
        sequence_count="infinite",
    )
    driver.store_arb_sequence(1, r"internal:\inf.seq")
    with pytest.raises(DriverPreconditionError, match="allow_infinite"):
        driver.load_voltage_arb_sequence(1, r"internal:\inf.seq", maximum_voltage_v=10.0)
    config = driver.load_voltage_arb_sequence(
        1, r"internal:\inf.seq", maximum_voltage_v=10.0, allow_infinite=True
    )
    assert config.sequence_count == "infinite"


@pytest.mark.parametrize(
    "filename", ["", "dip.seq", r"internal:\dip.csv", 'internal:\\a";*RST;".seq', r"internal:\a;b.seq"]
)
def test_sequence_filename_is_validated(driver, filename) -> None:
    with pytest.raises(DriverArgumentValueError):
        driver.store_arb_sequence(1, filename)


def test_store_requires_a_configured_sequence(driver) -> None:
    driver.configure_voltage_arb_waveform(1, ArbStep(1.0, 2.0), maximum_voltage_v=10.0)
    with pytest.raises(DriverPreconditionError, match="not a sequence"):
        driver.store_arb_sequence(1, r"internal:\dip.seq")


# -- triggered step -----------------------------------------------------------


def test_voltage_step_moves_output_to_triggered_level(driver) -> None:
    config = driver.configure_voltage_step(1, 4.0, maximum_voltage_v=10.0, trigger_output=True)
    assert config.triggered_level == pytest.approx(4.0)
    assert config.trigger_output is True
    assert _run_bus(driver, baseline_v=2.0).state == "complete"
    assert driver.get_dc_voltage_setpoint(1) == pytest.approx(4.0)


def test_voltage_step_outside_envelope_is_rejected(driver) -> None:
    with pytest.raises(DriverArgumentValueError, match="envelope"):
        driver.configure_voltage_step(1, 12.0, maximum_voltage_v=10.0)


# -- current transients -------------------------------------------------------


def test_current_list_keeps_voltage_fixed_and_runs(driver) -> None:
    channel = driver.power_supply(1)
    config = channel.configure_current_list([0.1, 0.2], 0.01, maximum_current_a=0.5)
    assert config.levels_a == pytest.approx((0.1, 0.2))
    assert channel._query("VOLT:MODE? (@1)").strip() == "FIX"
    assert channel._query("CURR:MODE? (@1)").strip() == "LIST"
    assert _run_bus(driver).state == "complete"


def test_current_arb_runs_and_reports_current_quantity(driver) -> None:
    driver.power_supply(1).configure_current_arb([0.1, 0.3, 0.1], 0.01, maximum_current_a=0.5)
    status = _run_bus(driver)
    assert status.state == "complete"
    assert status.quantity == "current"


def test_current_profile_above_current_range_is_rejected(driver) -> None:
    with pytest.raises(DriverArgumentValueError, match="range"):
        driver.power_supply(1).configure_current_list([0.1], 0.01, maximum_current_a=5.0)


def test_smu_current_profile_requires_current_priority(driver) -> None:
    smu = driver.channel(2)
    with pytest.raises(DriverPreconditionError, match="current priority"):
        smu.configure_current_list([0.1, 0.2], 0.01, maximum_current_a=0.5)
    smu.set_smu_mode("current")
    assert smu.configure_current_list([0.1, 0.2], 0.01, maximum_current_a=0.5).levels_a == (
        pytest.approx((0.1, 0.2))
    )


def test_current_slew_is_smu_only(driver) -> None:
    with pytest.raises(DriverUnsupportedOperationError):
        driver.power_supply(1).set_current_slew_rate(10.0)
    assert driver.channel(2).set_current_slew_rate(10.0) == pytest.approx(10.0)


# -- continuous initiation ----------------------------------------------------


def test_continuous_profile_retriggers_with_one_token(driver) -> None:
    driver.configure_voltage_list(1, [3.0, 4.0], 0.001, maximum_voltage_v=10.0)
    driver.set_dc_voltage(2.0, 1)
    driver.enable_output(1)
    token = driver.arm_voltage_transient(1, continuous=True)
    driver.wait_voltage_transient_ready(1, token)
    for _ in range(2):
        driver.trigger_voltage_transient(1, token)
        status = driver.wait_voltage_transient_complete(1, token)
        assert status.state == "ready"
        assert status.continuous is True
        assert status.waiting_for_trigger is True
    driver.abort_voltage_transient(1)
    status = driver.get_voltage_transient_status(1)
    assert status.continuous is False
    assert status.operation_status & (16 | 64) == 0
    assert _sim(driver).channels[1].transient_continuous is False


def test_safe_shutdown_stops_continuous_initiation(driver) -> None:
    driver.configure_voltage_list(1, [3.0, 4.0], 0.001, maximum_voltage_v=10.0)
    driver.set_dc_voltage(2.0, 1)
    driver.enable_output(1)
    driver.arm_voltage_transient(1, continuous=True)

    assert driver.safe_shutdown()["success"] is True

    sim_channel = _sim(driver).channels[1]
    assert sim_channel.transient_continuous is False
    assert sim_channel.transient_state == "aborted"
    status = driver.get_voltage_transient_status(1)
    assert status.state == "aborted"
    assert status.continuous is False
    assert status.operation_status & (16 | 64) == 0
    # A later re-energize cannot replay the profile on a stray trigger, and a
    # single-shot re-arm starts from an idle trigger system.
    driver.enable_output(1)
    token = driver.arm_voltage_transient(1)
    driver.wait_voltage_transient_ready(1, token)
    driver.trigger_voltage_transient(1, token)
    assert driver.wait_voltage_transient_complete(1, token).state == "complete"


# -- external trigger readiness ----------------------------------------------


def _armed_external(driver):
    driver.configure_voltage_list(1, [3.0, 4.0], 0.01, maximum_voltage_v=10.0)
    driver.configure_voltage_transient_trigger(1, "external")
    driver.set_dc_voltage(2.0, 1)
    driver.enable_output(1)
    return driver.arm_voltage_transient(1)


def test_external_arm_latches_transient_active_edges(driver) -> None:
    sim_channel = _sim(driver).channels[1]
    sim_channel.operation_ptr = 0
    sim_channel.operation_event = 0xFFFF  # stale events from before the arm
    _armed_external(driver)
    # Only TRAN_active is added to the filter; other PTR bits stay untouched.
    assert sim_channel.operation_ptr == 64
    # Stale bits were cleared before INIT:TRAN; only the new initiation latched.
    assert sim_channel.operation_event == 64


def test_external_idle_without_latched_initiation_is_not_reported_triggered(driver) -> None:
    token = _armed_external(driver)
    sim_channel = _sim(driver).channels[1]
    # The instrument has not initiated yet: idle condition, nothing latched.
    sim_channel.operation_condition = 0
    sim_channel.operation_event = 0
    with pytest.raises(DriverTimeoutError):
        driver.wait_voltage_transient_ready(1, token, timeout_s=0.02, poll_interval_s=0.001)
    assert driver.get_voltage_transient_status(1).state == "aborted"


def test_external_initiation_after_first_poll_is_ready(driver, monkeypatch) -> None:
    token = _armed_external(driver)
    sim_channel = _sim(driver).channels[1]
    sim_channel.operation_condition = 0
    sim_channel.operation_event = 0
    channel = driver.power_supply(1)
    original_query = channel._query
    polls = 0

    def late_initiation(command: str) -> str:
        nonlocal polls
        if command.startswith("STAT:OPER:COND?"):
            polls += 1
            if polls == 3:
                sim_channel.operation_status = 16 | 64
        return original_query(command)

    monkeypatch.setattr(channel, "_query", late_initiation)
    status = driver.wait_voltage_transient_ready(1, token, poll_interval_s=0.001)
    assert status.state == "ready"
    assert polls >= 3
