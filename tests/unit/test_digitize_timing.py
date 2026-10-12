"""PR03 timing/readback tests; no hardware and no wall-clock timing assumptions."""

from __future__ import annotations

import pytest

from keysight_n6700 import (
    DriverOperationUncertainError,
    DriverTimeoutError,
)
from keysight_n6700.acquisition import AcquisitionState
from keysight_n6700.channel import ElectronicLoadChannel
from keysight_n6700.exceptions import DriverArgumentValueError, DriverMalformedResponseError
from keysight_n6700.module_capabilities import classify_module


def _load(driver) -> ElectronicLoadChannel:
    return ElectronicLoadChannel(driver, 3, classify_module("N6791A"))


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds > 0
        self.sleeps.append(seconds)
        self.now += seconds


def _clock(monkeypatch) -> FakeClock:
    clock = FakeClock()
    monkeypatch.setattr("keysight_n6700.channel.monotonic", clock.monotonic)
    monkeypatch.setattr("keysight_n6700.channel.sleep", clock.sleep)
    return clock


def test_effective_interval_comes_from_instrument_query(driver) -> None:
    load = _load(driver)
    load.configure_digitize("voltage", points=100, interval_s=35e-6, offset_points=-50)
    timing = load.digitize_timing
    assert timing is not None
    assert timing.requested_interval_s == pytest.approx(35e-6)
    assert timing.actual_interval_s == pytest.approx(40.96e-6)
    assert timing.points == 100
    assert timing.offset_points == -50
    assert timing.pretrigger_fill_s == pytest.approx(50 * 40.96e-6)

    load.arm_digitize()
    load.wait_digitize_armed(timeout_s=1)
    load.trigger_digitize()
    load.wait_digitize_complete(timeout_s=1)
    result = load.fetch_digitized_array()
    assert result.interval_s == pytest.approx(40.96e-6)
    assert result.offset_points == -50


def test_negative_offset_uses_actual_sample_interval_and_guard(driver, monkeypatch) -> None:
    clock = _clock(monkeypatch)
    load = _load(driver)
    load.configure_digitize("voltage", points=1024, interval_s=35e-6, offset_points=-512)
    load.arm_digitize()
    load.wait_digitize_armed(timeout_s=1.0, pretrigger_guard_s=0.003)
    expected = 512 * 40.96e-6 + 0.003
    assert clock.now == pytest.approx(expected, abs=1e-9)
    assert max(clock.sleeps) <= 0.01
    assert load.digitize_status.state is AcquisitionState.WAITING


@pytest.mark.parametrize("offset", [0, 4])
def test_no_negative_offset_has_no_fill_delay(driver, monkeypatch, offset) -> None:
    clock = _clock(monkeypatch)
    load = _load(driver)
    load.configure_digitize("voltage", points=10, interval_s=10.24e-6, offset_points=offset)
    load.arm_digitize()
    load.wait_digitize_armed(timeout_s=1.0)
    assert clock.now == 0.0


def test_pretrigger_timeout_includes_fill_and_guard(driver, monkeypatch) -> None:
    clock = _clock(monkeypatch)
    load = _load(driver)
    load.configure_digitize("voltage", points=1024, interval_s=20.48e-6, offset_points=-512)
    load.arm_digitize()

    with pytest.raises(DriverTimeoutError, match="pretrigger fill requires") as exc:
        load.wait_digitize_armed(timeout_s=0.005, pretrigger_guard_s=0.005)
    assert exc.value.details["acquisition_cleanup"] == "aborted_and_idle_verified"
    assert load.digitize_status.state is AcquisitionState.ABORTED
    assert clock.now == 0.0


def test_pretrigger_wait_can_be_canceled_without_sleeping_full_fill(driver, monkeypatch) -> None:
    clock = _clock(monkeypatch)
    load = _load(driver)
    load.configure_digitize("voltage", points=1024, interval_s=20.48e-6, offset_points=-512)
    load.arm_digitize()

    def cancel() -> bool:
        return clock.now >= 0.004

    with pytest.raises(DriverOperationUncertainError, match="pretrigger fill canceled") as exc:
        load.wait_digitize_armed(
            timeout_s=1.0,
            pretrigger_guard_s=0,
            abort_requested=cancel,
        )
    assert exc.value.details["acquisition_cleanup"] == "aborted_and_idle_verified"
    assert load.digitize_status.state is AcquisitionState.ABORTED
    assert clock.now < 512 * 20.48e-6


@pytest.mark.parametrize("guard", [-1, float("nan"), float("inf"), True])
def test_invalid_pretrigger_guard_does_not_change_hardware_state(driver, guard) -> None:
    load = _load(driver)
    load.configure_digitize("voltage", points=10, interval_s=10.24e-6, offset_points=-4)
    load.arm_digitize()
    with pytest.raises(DriverArgumentValueError):
        load.wait_digitize_armed(timeout_s=1.0, pretrigger_guard_s=guard)
    assert load.digitize_status.state is AcquisitionState.ARMED
    load.abort_digitize()


def test_invalid_effective_interval_marks_unknown(driver, monkeypatch) -> None:
    load = _load(driver)
    query = load._query

    def invalid(command: str) -> str:
        if "SENS:SWE:TINT?" in command:
            return "NaN"
        return query(command)

    monkeypatch.setattr(load, "_query", invalid)
    with pytest.raises(DriverMalformedResponseError, match="invalid instrument-reported"):
        load.configure_digitize("voltage", points=5, interval_s=10.24e-6)
    assert load.digitize_timing is None
    assert load.digitize_status.state is AcquisitionState.UNKNOWN


def test_sweep_points_mismatch_is_not_reported_as_committed(driver, monkeypatch) -> None:
    load = _load(driver)
    query = load._query

    def mismatch(command: str) -> str:
        if "SENS:SWE:POIN?" in command:
            return "6"
        return query(command)

    monkeypatch.setattr(load, "_query", mismatch)
    with pytest.raises(DriverOperationUncertainError, match="readback mismatch"):
        load.configure_digitize("voltage", points=5, interval_s=10.24e-6)
    assert load.digitize_status.state is AcquisitionState.UNKNOWN
    assert load.digitize_timing is None


def test_nonintegral_offset_readback_is_rejected(driver, monkeypatch) -> None:
    load = _load(driver)
    query = load._query

    def invalid_offset(command: str) -> str:
        if "SENS:SWE:OFFS:POIN?" in command:
            return "-3.5"
        return query(command)

    monkeypatch.setattr(load, "_query", invalid_offset)
    with pytest.raises(DriverMalformedResponseError, match="non-integral"):
        load.configure_digitize("voltage", points=10, interval_s=10.24e-6, offset_points=-3)
    assert load.digitize_status.state is AcquisitionState.UNKNOWN


def test_quantization_depends_on_resolution(driver) -> None:
    load = _load(driver)
    driver.write_scpi("SENS:SWE:TINT:RES RES40,(@3)")
    load.configure_digitize("voltage", points=10, interval_s=65e-6)
    timing = load.digitize_timing
    assert timing is not None
    assert timing.actual_interval_s == pytest.approx(81.92e-6)


def test_pretrigger_trigger_disappears_during_fill_is_not_reported_ready(driver, monkeypatch) -> None:
    _clock(monkeypatch)
    load = _load(driver)
    load.configure_digitize("voltage", points=1024, interval_s=20.48e-6, offset_points=-512)
    load.arm_digitize()
    query = load._query
    status_reads = 0

    def triggered_early(command: str) -> str:
        nonlocal status_reads
        if command.startswith("STAT:OPER:COND?"):
            status_reads += 1
            if status_reads == 2:
                return "0"
        return query(command)

    monkeypatch.setattr(load, "_query", triggered_early)
    with pytest.raises(DriverOperationUncertainError, match="no longer waiting") as exc:
        load.wait_digitize_armed(timeout_s=1.0)
    assert status_reads >= 2
    assert exc.value.details["acquisition_cleanup"] == "aborted_and_idle_verified"
    assert load.digitize_status.state is AcquisitionState.ABORTED


def test_non_numeric_interval_readback_is_protocol_error(driver, monkeypatch) -> None:
    load = _load(driver)
    query = load._query

    def invalid(command: str) -> str:
        if command.startswith("SENS:SWE:TINT?"):
            return "not-a-number"
        return query(command)

    monkeypatch.setattr(load, "_query", invalid)
    with pytest.raises(DriverMalformedResponseError, match="interval readback"):
        load.configure_digitize("voltage", points=10, interval_s=10.24e-6)
    assert load.digitize_status.state is AcquisitionState.UNKNOWN
