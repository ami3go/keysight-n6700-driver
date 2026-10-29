"""Real-hardware check that a capture no longer slows later DC MEASure.

Same gates as ``test_hardware_load_digitize.py``: ``N6700_HIL_ENABLED=true``,
``N6700_RESOURCE``, ``N6700_HIL_DIGITIZE_ENABLED=true``,
``N6700_HIL_DIGITIZE_CHANNEL`` and ``N6700_HIL_DIGITIZE_CONFIRM=yes``.

Non-energizing: it fails before any command if an output or load input is ON,
and uses a BUS trigger, so no DUT wiring is needed. ``MEASure`` integrates over
``SENS:SWE:POIN`` x ``TINT`` (default 1024 x 20.48 us, about 21 ms); before the
fix a 48830 x 10.24 us capture left every later load reading at about 0.5 s.
Measured on N6700C ``MY56320259`` / N6791A channel 2 on 2026-10-29: 515 ms
before, 27 ms after (``results/digitize_sense_leak_20261029``).
"""

from __future__ import annotations

import os
import statistics
import time

import pytest

from keysight_n6700 import N6700

CAPTURE_POINTS = 48830
CAPTURE_INTERVAL_S = 10.24e-6
# Defaults integrate about 21 ms; anything under 100 ms rules out the 0.5 s leak.
MAX_RESTORED_MEAS_MS = 100.0


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _median_meas_ms(load) -> float:
    times = []
    for _ in range(5):
        began = time.monotonic()
        load.measure_voltage()
        times.append((time.monotonic() - began) * 1000.0)
    return statistics.median(times)


@pytest.fixture
def load_channel(hardware_driver: N6700):
    if not _truthy(os.environ.get("N6700_HIL_DIGITIZE_ENABLED")):
        pytest.skip("set N6700_HIL_DIGITIZE_ENABLED=true to run the digitize HIL tests")
    channel_raw = os.environ.get("N6700_HIL_DIGITIZE_CHANNEL")
    if not channel_raw:
        pytest.fail("N6700_HIL_DIGITIZE_ENABLED=true requires N6700_HIL_DIGITIZE_CHANNEL")
    if not _truthy(os.environ.get("N6700_HIL_DIGITIZE_CONFIRM")):
        pytest.fail("set N6700_HIL_DIGITIZE_CONFIRM=yes to confirm a reviewed N679xA channel")
    channel = int(channel_raw)
    modules = hardware_driver.discover_modules()
    caps = modules.get(channel)
    if caps is None or caps.module_type != "electronic_load" or not caps.supports_digitizing:
        pytest.fail(f"channel {channel} is not a digitizing electronic load: {caps}")
    for number, other in modules.items():
        if other.module_type == "electronic_load":
            assert hardware_driver.load(number).get_input() is False, f"load ch{number} input is ON"
        elif other.module_type in {"power_supply", "smu"}:
            assert hardware_driver.power_supply(number).get_output() is False, f"ch{number} output is ON"
    load = hardware_driver.load(channel)
    load.restore_measurement_defaults()
    yield load
    load.restore_measurement_defaults()


def test_abort_restores_sweep_and_dc_measure_speed(hardware_driver: N6700, load_channel) -> None:
    load = load_channel
    before = load._read_sense_state()
    baseline_ms = _median_meas_ms(load)

    load.configure_digitize("voltage", points=CAPTURE_POINTS, interval_s=CAPTURE_INTERVAL_S)
    load.configure_digitize_trigger("bus")
    load.arm_digitize()
    load.wait_digitize_armed(timeout_s=5.0)
    load.trigger_digitize()
    load.wait_digitize_complete(timeout_s=10.0)
    assert len(load.fetch_digitized_array().values) == CAPTURE_POINTS
    leaked_ms = _median_meas_ms(load)  # capture still held until the release step

    load.abort_digitize()
    restored_ms = _median_meas_ms(load)

    assert load.measurement_restore_status == "restored"
    assert load._read_sense_state() == before
    assert leaked_ms > 300.0, f"capture sweep not in effect before abort ({leaked_ms:.1f} ms)"
    assert restored_ms < MAX_RESTORED_MEAS_MS, (
        f"DC MEASure still slow after abort: {restored_ms:.1f} ms (baseline {baseline_ms:.1f} ms)"
    )
    hardware_driver.check_errors()


def test_restore_measurement_defaults_recovers_a_polluted_sweep(
    hardware_driver: N6700, load_channel
) -> None:
    load = load_channel
    channel = load.channel
    hardware_driver.write_scpi(f"SENS:SWE:POIN {CAPTURE_POINTS},(@{channel})")
    hardware_driver.write_scpi(f"SENS:SWE:TINT 1.024e-05,(@{channel})")
    assert _median_meas_ms(load) > 300.0

    result = load.restore_measurement_defaults()

    assert result["points"] == 1024 and result["offset_points"] == 0
    assert _median_meas_ms(load) < MAX_RESTORED_MEAS_MS
    hardware_driver.check_errors()
