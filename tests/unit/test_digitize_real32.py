"""PR07 REAL32 binary waveform framing, resource bounds and audit redaction."""
from __future__ import annotations

import hashlib
import struct

import pytest

from keysight_n6700 import N6700
from keysight_n6700.channel import ElectronicLoadChannel
from keysight_n6700.exceptions import DriverArgumentValueError, DriverMalformedResponseError
from keysight_n6700.module_capabilities import classify_module
from keysight_n6700.scpi import parse_digitize_real32


def _load(driver: N6700) -> ElectronicLoadChannel:
    return ElectronicLoadChannel(driver, 3, classify_module("N6791A"))


def _capture(load: ElectronicLoadChannel, n: int) -> None:
    load.configure_digitize("voltage", points=n, interval_s=20.48e-6)
    load.arm_digitize()
    load.wait_digitize_armed()
    load.trigger_digitize()
    load.wait_digitize_complete(2)


def test_real32_roundtrip_and_instrument_format_restored(driver) -> None:
    ch = _load(driver)
    driver.write_scpi("FORM:BORD NORM")
    _capture(ch, 128)
    array = ch.fetch_digitized_array(transfer_format="real32")
    assert len(array.values) == 128
    assert array.format == "real"
    expected_payload = struct.pack("<128f", *([0.0] * 128))
    assert array.raw_sha256 == hashlib.sha256(expected_payload).hexdigest()
    assert driver.query_scpi("FORM?") == "ASC"
    assert driver.query_scpi("FORM:BORD?") == "NORM"


def test_automatic_large_waveform_uses_binary(driver) -> None:
    ch = _load(driver)
    _capture(ch, 4096)
    array = ch.fetch_digitized_array()
    assert array.format == "real"
    assert len(array.values) == 4096
    assert array.raw_sha256 is not None


def test_small_waveform_stays_ascii_by_default(driver) -> None:
    ch = _load(driver)
    _capture(ch, 256)
    array = ch.fetch_digitized_array()
    assert array.format == "ascii"
    assert array.raw_sha256 is None


@pytest.mark.parametrize("bad", [b"", b"\x00", b"\x00" * 12, struct.pack("<ff", float("nan"), 2.0)])
def test_real32_requires_exact_finite_payload(bad) -> None:
    with pytest.raises(DriverMalformedResponseError):
        parse_digitize_real32(bad, expected_points=2)


def test_real32_endian_is_little_endian() -> None:
    assert parse_digitize_real32(struct.pack("<2f", -3.5, 6.25), expected_points=2) == (
        -3.5, 6.25
    )


def test_invalid_format_does_not_release_or_fetch(driver) -> None:
    ch = _load(driver)
    _capture(ch, 4)
    with pytest.raises(DriverArgumentValueError):
        ch.fetch_digitized_array(transfer_format="nope")
    assert driver._digitize_leases.owner("default") is not None
    ch.abort_digitize()


def test_large_ascii_response_is_summarized_in_control_audit(tmp_path) -> None:
    audit = tmp_path / "control.jsonl"
    driver = N6700(audit_log_path=audit)
    driver.connect(connection_type="simulated")
    try:
        ch = _load(driver)
        _capture(ch, 2500)
        array = ch.fetch_digitized_array(transfer_format="ascii")
        assert len(array.values) == 2500
        data = audit.read_text(encoding="utf-8")
        assert "<omitted " in data
        assert "sha256=" in data
        assert data.count("0.0,0.0,0.0,0.0") == 0
    finally:
        driver.disconnect()
