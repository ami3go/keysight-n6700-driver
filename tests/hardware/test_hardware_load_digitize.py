"""Real-hardware smoke test for digitized/buffered measurement (v0.6.0).

Disabled unless ``N6700_HIL_ENABLED=true`` and ``N6700_RESOURCE`` are both
set (see ``conftest.py``), **and** all of:

- ``N6700_HIL_DIGITIZE_ENABLED=true``
- ``N6700_HIL_DIGITIZE_CHANNEL`` — the electronic-load channel number, no default
- ``N6700_HIL_DIGITIZE_CONFIRM=yes`` — attests the channel is a genuine,
  already-reviewed N679xA electronic-load module safe to arm a measurement on

This is a software round-trip smoke test, not a transient-capture
demonstration: it uses a software (``BUS``) trigger rather than a level
trigger on a real DUT event, so it needs no wiring beyond the load module
itself, and only checks that the instrument accepts the configuration, arms,
triggers, and returns a buffer of the requested length — not that the
buffer's *content* is meaningful. Capturing a real transient (e.g. the
startup/shutdown/load-transient cases this capability was added for, see
``review/known_risks.md``) needs a DUT wired into the load's input and is
deliberately out of scope here.

Passed against N6700C ``MY56320259`` at ``192.168.0.6`` with an N6791A on
channel 2 on 2026-10-09. See ``review/known_risks.md`` and the retained
evidence under ``results/load_transient/20261009_n6791a_internal_buffer``.
"""

from __future__ import annotations

import os

import pytest

from keysight_n6700 import N6700

POINTS = 256
INTERVAL_S = 20.48e-6


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def test_digitize_bus_triggered_round_trip(hardware_driver: N6700) -> None:
    if not _truthy(os.environ.get("N6700_HIL_DIGITIZE_ENABLED")):
        pytest.skip("set N6700_HIL_DIGITIZE_ENABLED=true to run the digitize smoke test")

    channel_raw = os.environ.get("N6700_HIL_DIGITIZE_CHANNEL")
    if not channel_raw:
        pytest.fail("N6700_HIL_DIGITIZE_ENABLED=true requires N6700_HIL_DIGITIZE_CHANNEL to be set")
    if not _truthy(os.environ.get("N6700_HIL_DIGITIZE_CONFIRM")):
        pytest.fail(
            "set N6700_HIL_DIGITIZE_CONFIRM=yes to confirm this channel is a genuine, "
            "already-reviewed N679xA electronic-load module"
        )
    channel = int(channel_raw)

    modules = hardware_driver.discover_modules()
    caps = modules.get(channel)
    if caps is None or caps.module_type != "electronic_load" or not caps.supports_digitizing:
        pytest.fail(f"channel {channel} is not a discovered, digitizing-capable electronic-load channel: {caps}")

    load = hardware_driver.load(channel)
    load.configure_digitize("voltage", points=POINTS, interval_s=INTERVAL_S)
    load.configure_digitize_trigger("bus")
    load.arm_digitize()
    load.wait_digitize_armed(timeout_s=5.0)
    load.trigger_digitize()
    load.wait_digitize_complete(timeout_s=5.0)

    result = load.fetch_digitized_array()

    assert result.channel == channel
    assert result.unit == "V"
    assert len(result.values) == POINTS
    assert all(isinstance(v, float) for v in result.values)
    assert result.interval_s == pytest.approx(INTERVAL_S)
    assert result.offset_points == 0
    hardware_driver.check_errors()
