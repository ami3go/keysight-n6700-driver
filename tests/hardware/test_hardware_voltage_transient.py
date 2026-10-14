"""Guarded N677x voltage LIST/ARB protocol HIL.

This test intentionally energizes a source and generates a real voltage dip.
It is disabled unless all explicit parameters and the confirmation phrase are
present. Passing proves command/lifecycle behavior and safe teardown on the
selected instrument; it does not prove edge timing or voltage accuracy without
an independently triggered oscilloscope capture.
"""

from __future__ import annotations

import math
import os

import pytest

from keysight_n6700 import N6700


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@pytest.mark.hardware
def test_guarded_n677x_voltage_dip_protocol(hardware_driver: N6700) -> None:
    if not _truthy(os.environ.get("N6700_HIL_VOLTAGE_TRANSIENT_ENABLED")):
        pytest.skip("set N6700_HIL_VOLTAGE_TRANSIENT_ENABLED=true to run")
    required = {
        name: os.environ.get(name)
        for name in (
            "N6700_HIL_VOLTAGE_TRANSIENT_CHANNEL",
            "N6700_HIL_VOLTAGE_TRANSIENT_BASELINE_V",
            "N6700_HIL_VOLTAGE_TRANSIENT_DIP_V",
            "N6700_HIL_VOLTAGE_TRANSIENT_DWELL_S",
            "N6700_HIL_VOLTAGE_TRANSIENT_CURRENT_LIMIT_A",
            "N6700_HIL_VOLTAGE_TRANSIENT_MAXIMUM_V",
        )
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        pytest.fail(f"missing required voltage-transient HIL variables: {missing}")
    if os.environ.get("N6700_HIL_VOLTAGE_TRANSIENT_CONFIRM") != "GENERATE_REVIEWED_DIP":
        pytest.fail(
            "set N6700_HIL_VOLTAGE_TRANSIENT_CONFIRM=GENERATE_REVIEWED_DIP after reviewing wiring"
        )

    channel = int(required["N6700_HIL_VOLTAGE_TRANSIENT_CHANNEL"] or "")
    baseline_v = float(required["N6700_HIL_VOLTAGE_TRANSIENT_BASELINE_V"] or "")
    dip_v = float(required["N6700_HIL_VOLTAGE_TRANSIENT_DIP_V"] or "")
    dwell_s = float(required["N6700_HIL_VOLTAGE_TRANSIENT_DWELL_S"] or "")
    current_limit_a = float(required["N6700_HIL_VOLTAGE_TRANSIENT_CURRENT_LIMIT_A"] or "")
    maximum_v = float(required["N6700_HIL_VOLTAGE_TRANSIENT_MAXIMUM_V"] or "")

    numeric_values = (baseline_v, dip_v, dwell_s, current_limit_a, maximum_v)
    if not all(math.isfinite(value) for value in numeric_values):
        pytest.fail("all voltage-transient HIL values must be finite")

    modules = hardware_driver.discover_modules()
    caps = modules.get(channel)
    if caps is None or caps.model != "N6775A":
        pytest.fail(f"guarded qualification requires the reviewed N6775A; discovered {caps}")
    if hardware_driver.get_output_state(channel):
        pytest.fail("selected source output is already ON; refusing to alter it")
    if not 0 <= dip_v < baseline_v <= maximum_v:
        pytest.fail("require 0 <= dip_v < baseline_v <= maximum_v")
    if not 0 < dwell_s <= 1.0:
        pytest.fail("reviewed HIL dwell must be within 0..1 s")
    if current_limit_a <= 0:
        pytest.fail("reviewed HIL current limit must be greater than zero")

    try:
        hardware_driver.set_dc_current(current_limit_a, channel)
        hardware_driver.set_dc_voltage(baseline_v, channel)
        hardware_driver.set_voltage_slew_rate(channel, "max")
        hardware_driver.configure_voltage_list(
            channel,
            [baseline_v, dip_v, baseline_v],
            [0.01, dwell_s, 0.01],
            minimum_voltage_v=0.0,
            maximum_voltage_v=maximum_v,
            maximum_duration_s=1.1,
        )
        hardware_driver.configure_voltage_transient_trigger(channel, "bus")
        hardware_driver.enable_output(channel)
        hardware_driver.wait_for_voltage_in_range(
            channel, baseline_v * 0.95, baseline_v * 1.05, timeout_s=5.0
        )
        token = hardware_driver.arm_voltage_transient(channel)
        ready = hardware_driver.wait_voltage_transient_ready(channel, token, timeout_s=5.0)
        assert ready.waiting_for_trigger
        hardware_driver.trigger_voltage_transient(channel, token)
        complete = hardware_driver.wait_voltage_transient_complete(
            channel, token, timeout_s=max(5.0, dwell_s + 1.0)
        )
        assert complete.state == "complete"
        hardware_driver.wait_for_voltage_in_range(
            channel, baseline_v * 0.95, baseline_v * 1.05, timeout_s=5.0
        )
        assert hardware_driver.channel(channel).get_protection_status().active is False
        hardware_driver.check_errors()
    finally:
        try:
            hardware_driver.abort_voltage_transient(channel)
        finally:
            try:
                hardware_driver.disable_output(channel)
            finally:
                hardware_driver.safe_shutdown()
    assert hardware_driver.get_output_state(channel) is False
