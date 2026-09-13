"""Coverage for the scpi-driver-core capabilities wired into the driver
beyond the base transport/session layer: protocol tracing, bounded
wait-for-condition polling, retry for idempotent queries, and the raw-SCPI
confirmation guard.
"""

from __future__ import annotations

import pytest
from scpi_driver_core.tracing.events import TraceDirection
from scpi_driver_core.tracing.observer import RecordingTraceObserver
from scpi_driver_core.tracing.redaction import PatternRedactor

from keysight_n6700 import (
    N6700,
    DriverConfigurationError,
    DriverTimeoutError,
    DriverUnsafeOperationError,
)


def test_protocol_trace_observer_records_every_write_and_read() -> None:
    observer = RecordingTraceObserver()
    drv = N6700()
    drv.connect(connection_type="simulated", protocol_trace=observer)
    try:
        drv.set_dc_voltage(5.0, channel=1)
    finally:
        drv.disconnect()

    tx_events = [e for e in observer.events if e.direction is TraceDirection.TX]
    assert any("VOLT 5,(@1)" in (e.text or "") for e in tx_events)
    assert any(e.direction is TraceDirection.OPEN for e in observer.events)
    assert any(e.direction is TraceDirection.CLOSE for e in observer.events)


def test_protocol_trace_to_file_writes_jsonl(tmp_path) -> None:
    path = tmp_path / "trace.jsonl"
    drv = N6700()
    drv.connect(connection_type="simulated", protocol_trace=path)
    drv.write_scpi("*CLS")
    drv.disconnect()  # closes the owned JsonlTraceSink

    lines = path.read_text().splitlines()
    assert lines
    import json

    records = [json.loads(line) for line in lines]
    assert any("*CLS" in record.get("text", "") for record in records)


def test_protocol_trace_redactor_hides_matched_text() -> None:
    observer = RecordingTraceObserver()
    redactor = PatternRedactor([r"CAL:SEC:CODE (\S+)"])
    drv = N6700()
    drv.connect(connection_type="simulated", protocol_trace=observer, protocol_trace_redactor=redactor)
    try:
        drv.write_scpi("CAL:SEC:CODE secretvalue")
    finally:
        drv.disconnect()

    redacted = [e for e in observer.events if e.redacted]
    assert redacted
    assert "secretvalue" not in (redacted[0].text or "")
    assert "***" in (redacted[0].text or "")


def test_wait_for_voltage_in_range_returns_once_condition_met(driver) -> None:
    driver.set_dc_voltage(5.0, channel=1)
    driver.enable_output(1)
    value = driver.wait_for_voltage_in_range(1, 4.9, 5.1, timeout_s=2.0, poll_interval_s=0.01)
    assert value == pytest.approx(5.0)


def test_wait_for_voltage_in_range_times_out(driver) -> None:
    driver.set_dc_voltage(5.0, channel=1)
    driver.enable_output(1)
    with pytest.raises(DriverTimeoutError):
        driver.wait_for_voltage_in_range(1, 100.0, 101.0, timeout_s=0.2, poll_interval_s=0.05)


def test_wait_for_current_in_range_returns_once_condition_met(driver) -> None:
    driver.set_dc_voltage(5.0, channel=1)
    driver.set_dc_current(0.5, channel=1)
    driver.enable_output(1)
    value = driver.wait_for_current_in_range(1, 0.0, 1.0, timeout_s=2.0, poll_interval_s=0.01)
    assert 0.0 <= value <= 1.0


def test_query_scpi_retry_defaults_to_no_retry(driver) -> None:
    # No retry_attempts given: behaves exactly as before.
    assert driver.query_scpi("*IDN?").startswith("KEYSIGHT TECHNOLOGIES")


def test_query_scpi_accepts_a_retry_policy(driver) -> None:
    assert driver.query_scpi("*IDN?", retry_attempts=3, retry_delay_s=0.0).startswith(
        "KEYSIGHT TECHNOLOGIES"
    )


def test_raw_scpi_is_unguarded_by_default(driver) -> None:
    driver.write_scpi("*CLS")  # must not raise; no guard configured


def test_raw_scpi_guard_blocks_until_enabled(driver) -> None:
    driver.set_raw_scpi_guard("confirm-raw-scpi")
    with pytest.raises(DriverUnsafeOperationError):
        driver.write_scpi("*CLS")
    with pytest.raises(DriverUnsafeOperationError):
        driver.query_scpi("*IDN?")

    driver.enable_raw_scpi("confirm-raw-scpi")
    driver.write_scpi("*CLS")
    assert driver.query_scpi("*IDN?").startswith("KEYSIGHT TECHNOLOGIES")

    driver.disable_raw_scpi()
    with pytest.raises(DriverUnsafeOperationError):
        driver.write_scpi("*CLS")


def test_enable_raw_scpi_without_a_guard_set_raises(driver) -> None:
    with pytest.raises(DriverConfigurationError):
        driver.enable_raw_scpi("anything")


def test_wrong_confirmation_phrase_is_rejected(driver) -> None:
    driver.set_raw_scpi_guard("correct-phrase")
    with pytest.raises(DriverUnsafeOperationError):
        driver.enable_raw_scpi("wrong-phrase")
