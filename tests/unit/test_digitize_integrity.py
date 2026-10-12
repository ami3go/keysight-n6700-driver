"""PR05: strict waveform integrity, SCPI rejection and shared buffer limits."""
from __future__ import annotations

import pytest

from keysight_n6700.channel import ElectronicLoadChannel
from keysight_n6700.exceptions import (
    DriverArgumentValueError,
    DriverCommandRejectedError,
    DriverMalformedResponseError,
    DriverOperationUncertainError,
)
from keysight_n6700.module_capabilities import classify_module
from keysight_n6700.scpi import parse_digitize_ascii


def load(driver) -> ElectronicLoadChannel:
    return ElectronicLoadChannel(driver, 3, classify_module("N6791A"))


@pytest.mark.parametrize("reply", ["", "1,,3", "1,NaN,3", "1,inf,3", "1,notnum,3", "1,2"])
def test_ascii_rejects_missing_or_invalid_samples(reply) -> None:
    with pytest.raises(DriverMalformedResponseError):
        parse_digitize_ascii(reply, expected_points=3)


def test_ascii_accepts_exact_number_of_finite_points() -> None:
    assert parse_digitize_ascii("1, -2.5, 3e-6", expected_points=3) == (1.0, -2.5, 3e-6)


def test_preserve_and_restore_previous_format(driver) -> None:
    driver.write_scpi("FORM REAL")
    ch = load(driver)
    ch.configure_digitize("voltage", points=10, interval_s=20.48e-6)
    assert driver.query_scpi("FORM?") == "ASC"
    ch.arm_digitize()
    ch.wait_digitize_armed()
    ch.trigger_digitize()
    ch.wait_digitize_complete(1)
    assert len(ch.fetch_digitized_array().values) == 10
    assert driver.query_scpi("FORM?") == "REAL"


def test_abort_restores_format(driver) -> None:
    driver.write_scpi("FORM REAL")
    ch = load(driver)
    ch.configure_digitize("voltage", points=10, interval_s=20.48e-6)
    ch.abort_digitize()
    assert driver.query_scpi("FORM?") == "REAL"


def test_changed_array_format_cannot_silently_decode_wrong_payload(driver) -> None:
    ch = load(driver)
    ch.configure_digitize("voltage", points=10, interval_s=20.48e-6)
    ch.arm_digitize()
    ch.wait_digitize_armed()
    ch.trigger_digitize()
    ch.wait_digitize_complete(1)
    driver.write_scpi("FORM REAL")
    with pytest.raises(DriverOperationUncertainError, match="format changed"):
        ch.fetch_digitized_array()
    ch.abort_digitize()


def test_shared_512k_budget_protects_other_channels(driver) -> None:
    # Only digitizing-capable channels (real N679xA/N678xA modules) have any
    # claim on the shared sweep memory -- a plain power supply (channel 1's
    # default simulated model) never replies to SENS:SWE:POIN? on real
    # hardware. Register channel 1 as a second genuinely digitizing-capable
    # module so this test exercises cross-channel budget sharing honestly.
    driver._discovery["default"].capabilities[1] = classify_module("N6792A")
    driver.write_scpi("SENS:FUNC:VOLT ON,(@1)")
    driver.write_scpi("SENS:SWE:POIN 500000,(@1)")
    ch = load(driver)
    with pytest.raises(DriverArgumentValueError, match="shared points"):
        ch.configure_digitize("voltage", points=50000, interval_s=20.48e-6)
    ch.abort_digitize()


def test_other_measurement_function_disabled_by_default(driver) -> None:
    driver.write_scpi("SENS:FUNC:CURR ON,(@3)")
    ch = load(driver)
    ch.configure_digitize("voltage", points=100, interval_s=20.48e-6)
    assert driver.query_scpi("SENS:FUNC:VOLT? (@3)") == "1"
    assert driver.query_scpi("SENS:FUNC:CURR? (@3)") == "0"
    ch.abort_digitize()


def test_pending_scpi_errors_fail_configuration(driver) -> None:
    driver.query_scpi("INVALID:SCPI:QUERY?")
    ch = load(driver)
    with pytest.raises(DriverCommandRejectedError):
        ch.configure_digitize("voltage", points=100, interval_s=20.48e-6)
    ch.abort_digitize()


def test_wrong_waveform_length_is_not_returned_as_valid(driver, monkeypatch) -> None:
    ch = load(driver)
    ch.configure_digitize("voltage", points=10, interval_s=20.48e-6)
    ch.arm_digitize()
    ch.wait_digitize_armed()
    ch.trigger_digitize()
    ch.wait_digitize_complete(1)
    query = ch._query

    def truncate(command: str) -> str:
        if command.startswith("FETC:ARR:"):
            return "1,2,3"
        return query(command)

    monkeypatch.setattr(ch, "_query", truncate)
    with pytest.raises(DriverMalformedResponseError):
        ch.fetch_digitized_array()
    ch.abort_digitize()
