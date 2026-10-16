"""Shared helper: run the configured ARB once and always switch the output off."""

from __future__ import annotations

from keysight_n6700 import N6700


def play(supply: N6700, channel: int) -> None:
    """Enable the output, trigger the configured waveform, wait, then turn off."""
    supply.configure_voltage_transient_trigger(channel, "bus")
    try:
        supply.enable_output(channel)
        token = supply.arm_voltage_transient(channel)
        supply.wait_voltage_transient_ready(channel, token, timeout_s=5.0)
        supply.trigger_voltage_transient(channel, token)
        status = supply.wait_voltage_transient_complete(channel, token, timeout_s=30.0)
        print("Waveform finished:", status.state)
    finally:
        try:
            supply.abort_voltage_transient(channel)
        finally:
            supply.disable_output(channel)
