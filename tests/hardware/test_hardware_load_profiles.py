"""Guarded N679xA load LIST/ARB protocol HIL.

Turns the selected load input ON and runs CC LIST, CC UDEF ARB and CR
constant-dwell ARB profiles under BUS triggers, plus a transient-triggered digitizer capture
and an abort while armed. It is disabled unless every explicit parameter and
the confirmation phrase are present, and refuses to enable the input while the
measured input voltage exceeds ``N6700_HIL_LOAD_PROFILE_MAX_INPUT_V``: with a
small limit (for example 1 V) nothing is connected and the load sinks no
current, so a pass proves command/lifecycle behaviour, not sinking accuracy.

Side effect: ARB runs set the mainframe-wide ``TRIG:ARB:SOUR`` to BUS. The test
leaves it there; IMMediate would start any ARB at ``INIT:TRAN``.
"""

from __future__ import annotations

import math
import os

import pytest

from keysight_n6700 import N6700

LOAD_MODELS = {"N6791A", "N6792A"}


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _run(driver: N6700, channel: int, timeout_s: float) -> None:
    token = driver.arm_load_transient(channel)
    assert driver.wait_load_transient_ready(channel, token, timeout_s=5.0).waiting_for_trigger
    driver.trigger_load_transient(channel, token)
    assert driver.wait_load_transient_complete(channel, token, timeout_s=timeout_s).state == "complete"


@pytest.mark.hardware
def test_guarded_n679x_load_profiles(hardware_driver: N6700) -> None:
    if not _truthy(os.environ.get("N6700_HIL_LOAD_PROFILE_ENABLED")):
        pytest.skip("set N6700_HIL_LOAD_PROFILE_ENABLED=true to run")
    required = {
        name: os.environ.get(name)
        for name in (
            "N6700_HIL_LOAD_PROFILE_CHANNEL",
            "N6700_HIL_LOAD_PROFILE_MAX_CURRENT_A",
            "N6700_HIL_LOAD_PROFILE_MAX_INPUT_V",
        )
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        pytest.fail(f"missing required load-profile HIL variables: {missing}")
    if os.environ.get("N6700_HIL_LOAD_PROFILE_CONFIRM") != "RUN_REVIEWED_LOAD_PROFILE":
        pytest.fail("set N6700_HIL_LOAD_PROFILE_CONFIRM=RUN_REVIEWED_LOAD_PROFILE after reviewing wiring")

    channel = int(required["N6700_HIL_LOAD_PROFILE_CHANNEL"] or "")
    maximum_current_a = float(required["N6700_HIL_LOAD_PROFILE_MAX_CURRENT_A"] or "")
    maximum_input_v = float(required["N6700_HIL_LOAD_PROFILE_MAX_INPUT_V"] or "")
    if not (math.isfinite(maximum_current_a) and 0 < maximum_current_a <= 1.0):
        pytest.fail("reviewed HIL maximum current must be within 0..1 A")
    if not (math.isfinite(maximum_input_v) and maximum_input_v > 0):
        pytest.fail("reviewed HIL maximum input voltage must be greater than zero")

    caps = hardware_driver.discover_modules().get(channel)
    if caps is None or caps.model not in LOAD_MODELS:
        pytest.fail(f"guarded load-profile HIL requires an N679xA load; discovered {caps}")
    if not caps.supports_list_mode:
        pytest.fail(f"{caps.model} in {caps.mainframe_model} does not advertise load LIST/ARB")
    load = hardware_driver.load(channel)
    if load.get_input():
        pytest.fail("selected load input is already ON; refusing to alter it")
    input_v = load.measure_voltage()
    if input_v > maximum_input_v:
        pytest.fail(f"load input reads {input_v:g} V, above the reviewed {maximum_input_v:g} V")

    low = maximum_current_a / 10
    try:
        load.set_load_mode("cc")
        load.set_load_current(low)

        # 1. CC LIST with a transient-triggered digitizer capture. The
        # transient system only triggers a measurement through a trigger
        # out, so the first step emits one at its beginning (BOST).
        config = hardware_driver.configure_load_list(
            channel, "cc", [low, maximum_current_a, low], [0.1, 0.2, 0.1],
            maximum_level=maximum_current_a, maximum_duration_s=1.0,
            begin_step_triggers=[True, False, False],
        )
        assert config.levels == pytest.approx((low, maximum_current_a, low))
        hardware_driver.configure_load_transient_trigger(channel, "bus")
        load.input_on()
        load.configure_digitize("current", points=512, interval_s=1.024e-3)
        load.configure_digitize_trigger("transient")
        load.arm_digitize()
        load.wait_digitize_armed(timeout_s=5.0)
        _run(hardware_driver, channel, timeout_s=5.0)
        load.wait_digitize_complete(timeout_s=5.0)
        capture = load.fetch_digitized_array("current")
        assert len(capture.values) == 512

        # 2. CC user-defined ARB on the mainframe-wide ARB trigger.
        load.input_off()
        hardware_driver.configure_load_arb(
            channel, "cc", [low, maximum_current_a, low], 0.1,
            maximum_level=maximum_current_a, maximum_duration_s=1.0,
        )
        hardware_driver.configure_load_transient_trigger(channel, "bus")
        load.input_on()
        _run(hardware_driver, channel, timeout_s=5.0)

        # 3. CR constant-dwell ARB. The worst-case current VOLT:RANG/minimum_level
        # must fit maximum_current_a, so the minimum resistance follows from it.
        load.input_off()
        load.set_load_mode("cr")
        minimum_ohm = load.get_load_range("cv") / maximum_current_a
        hardware_driver.configure_load_constant_dwell_arb(
            channel, "cr", [2 * minimum_ohm, 4 * minimum_ohm, 8 * minimum_ohm], 0.05,
            minimum_level=minimum_ohm, maximum_level=8 * minimum_ohm,
            maximum_current_a=maximum_current_a, maximum_duration_s=1.0,
        )
        hardware_driver.configure_load_transient_trigger(channel, "bus")
        load.input_on()
        _run(hardware_driver, channel, timeout_s=5.0)

        # 4. Abort while armed returns to idle without a trigger.
        token = hardware_driver.arm_load_transient(channel)
        hardware_driver.wait_load_transient_ready(channel, token, timeout_s=5.0)
        hardware_driver.abort_load_transient(channel)
        status = hardware_driver.get_load_transient_status(channel)
        assert status.state == "aborted"
        assert not status.waiting_for_trigger and not status.transient_active

        assert load.get_protection_status().active is False
        hardware_driver.check_errors()
    finally:
        try:
            load.input_off()
            hardware_driver.clear_load_profile(channel)
            load.set_load_mode("cc")
        finally:
            hardware_driver.safe_shutdown()
    assert load.get_input() is False
