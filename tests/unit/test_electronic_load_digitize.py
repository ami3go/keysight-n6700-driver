"""Digitized/buffered measurement (SENS:SWEep + TRIG:ACQuire + FETC:ARRay).

Per the official Keysight N6705C User's Guide / Programmer's Reference (see
``Keysight_documents/`` in this repository): this is the same remote-only
command surface the N679xA "Load Model Differences" table marks available
("SCPI command array readback", "SCPI command programmable sample rate").
Like ``test_electronic_load_channel.py``, these tests build an
``ElectronicLoadChannel`` with real (non-``SIM_LOAD``) capabilities pointed
at the simulator's channel 3, which answers the shared command surface
regardless of the placeholder model name stored in that channel's simulated
state. Unlike the plain load setpoints, there is no simulated-waveform
twin: the simulator replays a flat buffer rather than modeling a real
transient, so these tests exercise configuration/polling/parsing, not
waveform fidelity.
"""

from __future__ import annotations

import pytest

from keysight_n6700 import (
    DriverArgumentValueError,
    DriverConnectionError,
    DriverOperationUncertainError,
    DriverPreconditionError,
    DriverTimeoutError,
    DriverUnsupportedOperationError,
)
from keysight_n6700.acquisition import AcquisitionState
from keysight_n6700.channel import ElectronicLoadChannel
from keysight_n6700.module_capabilities import classify_module


def _real_load_channel(driver, channel: int = 3) -> ElectronicLoadChannel:
    return ElectronicLoadChannel(driver, channel, classify_module("N6791A"))


def test_digitize_round_trip(driver) -> None:
    load = _real_load_channel(driver)
    load.configure_digitize(
        "voltage", points=50, interval_s=10.24e-6, offset_points=-25
    )
    load.configure_digitize_trigger("bus")
    load.arm_digitize()
    load.wait_digitize_armed(timeout_s=1.0)
    load.trigger_digitize()
    load.wait_digitize_complete(timeout_s=1.0)

    result = load.fetch_digitized_array()

    assert result.channel == 3
    assert len(result.values) == 50
    assert result.unit == "V"
    assert result.format == "ascii"
    assert result.interval_s == pytest.approx(10.24e-6)
    assert result.offset_points == -25


def test_digitize_rejects_unconfigured_fetch_function(driver) -> None:
    load = _real_load_channel(driver)
    load.configure_digitize("voltage", points=10, interval_s=10.24e-6)
    load.configure_digitize_trigger("bus")
    load.arm_digitize()
    load.wait_digitize_armed(timeout_s=1.0)
    load.trigger_digitize()
    load.wait_digitize_complete(timeout_s=1.0)

    with pytest.raises(DriverArgumentValueError, match="only 'voltage'"):
        load.fetch_digitized_array("current")
    assert len(load.fetch_digitized_array("voltage").values) == 10


def test_digitize_rejects_interval_below_hardware_minimum(driver) -> None:
    load = _real_load_channel(driver)
    with pytest.raises(DriverArgumentValueError):
        load.configure_digitize("voltage", points=10, interval_s=1e-6)


def test_digitize_rejects_fewer_than_one_point(driver) -> None:
    load = _real_load_channel(driver)
    with pytest.raises(DriverArgumentValueError):
        load.configure_digitize("voltage", points=0, interval_s=10.24e-6)


@pytest.mark.parametrize("points", [1.5, True, 524289])
def test_digitize_rejects_invalid_point_count(driver, points) -> None:
    load = _real_load_channel(driver)
    with pytest.raises(DriverArgumentValueError):
        load.configure_digitize("voltage", points=points, interval_s=10.24e-6)


@pytest.mark.parametrize("offset", [-10, 2_000_000_001, 1.5, True])
def test_digitize_rejects_invalid_offset(driver, offset) -> None:
    load = _real_load_channel(driver)
    with pytest.raises(DriverArgumentValueError):
        load.configure_digitize(
            "voltage", points=10, interval_s=10.24e-6, offset_points=offset
        )


def test_level_trigger_requires_fresh_level_and_slope(driver) -> None:
    load = _real_load_channel(driver)
    with pytest.raises(DriverArgumentValueError, match="requires both"):
        load.configure_digitize_trigger("current", level=1.5)


def test_digitize_requires_configure_before_fetch(driver) -> None:
    load = _real_load_channel(driver)
    with pytest.raises(DriverArgumentValueError):
        load.fetch_digitized_array()


def test_digitize_is_not_modeled_by_the_simulator_placeholder(driver) -> None:
    sim_load = driver.load(3)  # channel 3 is SIM_LOAD in the default simulator map
    with pytest.raises(DriverUnsupportedOperationError):
        sim_load.configure_digitize("voltage", points=10, interval_s=10.24e-6)


def test_wait_digitize_complete_honors_abort_request(driver) -> None:
    load = _real_load_channel(driver)
    load.configure_digitize("voltage", points=10, interval_s=10.24e-6)
    load.arm_digitize()
    load.wait_digitize_armed(timeout_s=1.0)

    with pytest.raises(DriverOperationUncertainError):
        load.wait_digitize_complete(timeout_s=1.0, abort_requested=lambda: True)
    assert load.digitize_status.state is AcquisitionState.ABORTED


def test_status_does_not_report_completed_capture_before_trigger(driver) -> None:
    """F04: MEAS_active alone must not be treated as a completion oracle."""
    load = _real_load_channel(driver)
    load.configure_digitize("voltage", points=10, interval_s=10.24e-6)
    load.configure_digitize_trigger("bus")
    load.arm_digitize()
    assert load.digitize_status.state is AcquisitionState.ARMED
    with pytest.raises(DriverPreconditionError):
        load.fetch_digitized_array()
    load.wait_digitize_armed(timeout_s=1.0)
    assert load.digitize_status.state is AcquisitionState.WAITING
    with pytest.raises(DriverTimeoutError) as excinfo:
        load.wait_digitize_complete(timeout_s=0.1)
    assert excinfo.value.details["acquisition_cleanup"] == "aborted_and_idle_verified"
    assert load.digitize_status.state is AcquisitionState.ABORTED
    with pytest.raises(DriverPreconditionError):
        load.fetch_digitized_array()
    load.abort_digitize()


def test_generation_and_abort_invalidate_previous_buffer(driver) -> None:
    load = _real_load_channel(driver)
    load.configure_digitize("voltage", points=5, interval_s=10.24e-6)
    load.arm_digitize()
    load.wait_digitize_armed(timeout_s=1.0)
    load.trigger_digitize()
    load.wait_digitize_complete(timeout_s=1.0)
    first = load.fetch_digitized_array()
    assert len(first.values) == 5
    token1 = load.digitize_status.generation
    assert load.digitize_status.state is AcquisitionState.FETCHED

    load.arm_digitize()
    assert load.digitize_status.generation == token1 + 1
    with pytest.raises(DriverPreconditionError):
        load.fetch_digitized_array()
    load.abort_digitize()
    assert load.digitize_status.state is AcquisitionState.ABORTED
    with pytest.raises(DriverPreconditionError):
        load.fetch_digitized_array()

    load.configure_digitize("voltage", points=4, interval_s=10.24e-6)
    load.arm_digitize()
    load.wait_digitize_armed(timeout_s=1.0)
    load.trigger_digitize()
    load.wait_digitize_complete(timeout_s=1.0)
    assert len(load.fetch_digitized_array().values) == 4


def test_reconfiguration_invalidates_completed_capture(driver) -> None:
    load = _real_load_channel(driver)
    load.configure_digitize("voltage", points=10, interval_s=10.24e-6)
    load.arm_digitize()
    load.wait_digitize_armed(timeout_s=1.0)
    load.trigger_digitize()
    load.wait_digitize_complete(timeout_s=1.0)

    load.configure_digitize_trigger("current", level=0.5, slope="pos")
    assert load.digitize_status.state is AcquisitionState.CONFIGURED
    with pytest.raises(DriverPreconditionError):
        load.fetch_digitized_array()


def test_trigger_requires_verified_armed_status(driver) -> None:
    load = _real_load_channel(driver)
    load.configure_digitize("voltage", points=10, interval_s=10.24e-6)
    load.arm_digitize()
    with pytest.raises(DriverPreconditionError):
        load.trigger_digitize()
    load.wait_digitize_armed(timeout_s=1.0)
    load.trigger_digitize()
    load.wait_digitize_complete(timeout_s=1.0)
    assert load.digitize_status.explicit_trigger_requested


def test_second_configuration_while_armed_is_rejected(driver) -> None:
    load = _real_load_channel(driver)
    load.configure_digitize("voltage", points=5, interval_s=10.24e-6)
    load.arm_digitize()
    with pytest.raises(DriverPreconditionError):
        load.configure_digitize("current", points=3, interval_s=10.24e-6)
    with pytest.raises(DriverPreconditionError):
        load.configure_digitize_trigger("bus")
    load.abort_digitize()


def test_simulator_exposes_in_progress_and_invalidates_aborted_array(driver) -> None:
    load = _real_load_channel(driver)
    load.configure_digitize("voltage", points=3, interval_s=10.24e-6)
    load.arm_digitize()
    assert int(driver.query_scpi("STAT:OPER:COND? (@3)")) & 8
    assert int(driver.query_scpi("STAT:OPER:COND? (@3)")) & 32
    load.abort_digitize()
    assert int(driver.query_scpi("STAT:OPER:COND? (@3)")) & (8 | 32) == 0
    assert driver.query_scpi("FETC:ARR:VOLT? (@3)") == ""


def test_wait_without_arming_is_rejected(driver) -> None:
    load = _real_load_channel(driver)
    load.configure_digitize("voltage", points=5, interval_s=10.24e-6)
    with pytest.raises(DriverPreconditionError):
        load.wait_digitize_complete(timeout_s=0.1)
    with pytest.raises(DriverPreconditionError):
        load.wait_digitize_armed(timeout_s=0.1)


def test_wait_armed_timeout_aborts_and_verifies_idle(driver, monkeypatch) -> None:
    load = _real_load_channel(driver)
    load.configure_digitize("voltage", points=10, interval_s=10.24e-6)
    load.arm_digitize()
    monkeypatch.setattr(load, "_query", lambda _: "0")

    with pytest.raises(DriverTimeoutError) as excinfo:
        load.wait_digitize_armed(timeout_s=0.06)
    assert excinfo.value.details["acquisition_cleanup"] == "aborted_and_idle_verified"
    assert load.digitize_status.state is AcquisitionState.ABORTED


def test_wait_complete_transport_failure_preserves_primary_error(driver, monkeypatch) -> None:
    load = _real_load_channel(driver)
    load.configure_digitize("voltage", points=10, interval_s=10.24e-6)
    load.arm_digitize()
    load.wait_digitize_armed(timeout_s=1.0)

    def unavailable(_: str) -> str:
        raise DriverConnectionError("transport disconnected")

    monkeypatch.setattr(load, "_query", unavailable)
    with pytest.raises(DriverConnectionError, match="transport disconnected") as excinfo:
        load.wait_digitize_complete(timeout_s=0.1)
    assert excinfo.value.details["acquisition_cleanup"].startswith("abort_failed")
    assert load.digitize_status.state is AcquisitionState.UNKNOWN
    with pytest.raises(DriverPreconditionError):
        load.fetch_digitized_array()


def test_abort_not_acknowledged_does_not_claim_success(driver, monkeypatch) -> None:
    load = _real_load_channel(driver)
    load.configure_digitize("voltage", points=10, interval_s=10.24e-6)
    load.arm_digitize()
    load.wait_digitize_armed(timeout_s=1.0)
    monkeypatch.setattr(load, "_query", lambda _: "40")

    with pytest.raises(DriverTimeoutError):
        load.abort_digitize(timeout_s=0.06)
    assert load.digitize_status.state is AcquisitionState.UNKNOWN


@pytest.mark.parametrize("invalid", [0.0, -1.0, float("nan"), float("inf"), True])
def test_invalid_timeouts_fail_before_abort(driver, invalid) -> None:
    load = _real_load_channel(driver)
    load.configure_digitize("voltage", points=10, interval_s=10.24e-6)
    load.arm_digitize()
    with pytest.raises(DriverArgumentValueError):
        load.wait_digitize_armed(timeout_s=invalid)
    assert load.digitize_status.state is AcquisitionState.ARMED
    load.abort_digitize()


def test_operator_cancel_reports_verified_abort_in_original_exception(driver) -> None:
    load = _real_load_channel(driver)
    load.configure_digitize("voltage", points=10, interval_s=10.24e-6)
    load.arm_digitize()
    load.wait_digitize_armed(timeout_s=1.0)

    with pytest.raises(DriverOperationUncertainError) as excinfo:
        load.wait_digitize_complete(timeout_s=1.0, abort_requested=lambda: True)
    assert excinfo.value.details["acquisition_cleanup"] == "already_aborted"
    assert load.digitize_status.state is AcquisitionState.ABORTED
