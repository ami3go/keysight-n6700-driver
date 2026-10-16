"""Bounded ASCII constant-dwell ARB transfer and readback tests."""

from __future__ import annotations

import pytest
from scpi_driver_core.simulation.scripted import ScriptedScpiTransport

from keysight_n6700 import (
    DriverArgumentValueError,
    DriverCommandRejectedError,
    DriverOperationUncertainError,
)
from keysight_n6700.exceptions import DriverMalformedResponseError


def _transport(driver) -> ScriptedScpiTransport:
    transport = driver._session(None).transport
    assert isinstance(transport, ScriptedScpiTransport)
    return transport


@pytest.mark.parametrize("dwell_s", [10.24e-6, 0.30])
def test_cdw_accepts_documented_dwell_boundaries(driver, dwell_s) -> None:
    config = driver.configure_voltage_constant_dwell_arb(
        1, [1.0], dwell_s, maximum_voltage_v=5.0, maximum_duration_s=1.0
    )
    assert config.dwell_s == pytest.approx(dwell_s)


def test_cdw_rejects_vendor_point_overflow_before_mutation(driver) -> None:
    transport = _transport(driver)
    transport.clear_history()
    with pytest.raises(DriverArgumentValueError, match="exceeds 65535 points"):
        driver.configure_voltage_constant_dwell_arb(
            1, [0.0] * 65_536, 10.24e-6, maximum_voltage_v=5.0
        )
    assert not any(command.startswith("ARB:") for command in transport.history)


def test_cdw_rejects_oversized_ascii_without_fragmenting_or_mutating(driver) -> None:
    transport = _transport(driver)
    transport.clear_history()
    with pytest.raises(DriverArgumentValueError, match="maximum is 65536 bytes"):
        driver.configure_voltage_constant_dwell_arb(
            1, [0.0] * 65_535, 10.24e-6,
            maximum_voltage_v=5.0, maximum_duration_s=1.0,
        )
    assert not any(command.startswith("ARB:") for command in transport.history)


def test_cdw_queries_and_validates_effective_point_count(driver) -> None:
    transport = _transport(driver)
    transport.on("ARB:VOLT:CDW:POIN? (@1)", "2")
    with pytest.raises(DriverOperationUncertainError, match="point-count"):
        driver.configure_voltage_constant_dwell_arb(
            1, [1.0], 0.001, maximum_voltage_v=5.0
        )
    assert driver.power_supply(1)._transient_state == "unknown"
    assert "INIT:CONT:TRAN OFF,(@1)" in transport.history
    assert "ABOR:TRAN (@1)" in transport.history


def test_cdw_accepts_only_half_quantum_dwell_rounding(driver) -> None:
    transport = _transport(driver)
    transport.on("ARB:VOLT:CDW:DWEL? (@1)", "0.00100512")
    config = driver.configure_voltage_constant_dwell_arb(
        1, [1.0], 0.001, maximum_voltage_v=5.0
    )
    assert config.dwell_s == pytest.approx(0.00100512)

    transport.on("ARB:VOLT:CDW:DWEL? (@1)", "0.00100513")
    with pytest.raises(DriverOperationUncertainError, match=r"half a 10\.24 us quantum"):
        driver.configure_voltage_constant_dwell_arb(
            1, [1.0], 0.001, maximum_voltage_v=5.0
        )


def test_cdw_malformed_readback_cleans_up_and_stays_unknown(driver) -> None:
    transport = _transport(driver)
    transport.on("ARB:VOLT:CDW? (@1)", "not-a-number")
    with pytest.raises(DriverMalformedResponseError, match="malformed constant-dwell level"):
        driver.configure_voltage_constant_dwell_arb(
            1, [1.0], 0.001, maximum_voltage_v=5.0
        )
    channel = driver.power_supply(1)
    assert channel._transient_state == "unknown"
    assert channel.get_output() is False


def test_cdw_transfer_failure_runs_transaction_cleanup(driver, monkeypatch) -> None:
    channel = driver.power_supply(1)
    original_write = channel._write

    def fail_level_transfer(command: str) -> None:
        if command.startswith("ARB:VOLT:CDW "):
            raise DriverCommandRejectedError("injected CDW transfer failure")
        original_write(command)

    monkeypatch.setattr(channel, "_write", fail_level_transfer)
    with pytest.raises(DriverCommandRejectedError, match="injected CDW"):
        channel.configure_voltage_constant_dwell_arb(
            [1.0, 2.0], 0.001, maximum_voltage_v=5.0
        )
    assert channel._transient_state == "unknown"
    assert channel._transient_config is None
