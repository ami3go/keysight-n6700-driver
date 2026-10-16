"""DRV-ARB-A: fail-closed source transient capability and stale-handle gates.

The simulator has no authority to establish analog Arb timing or protection.
These tests inspect discovery, negative preflight and SCPI mutation boundaries.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from keysight_n6700 import (
    N6700,
    DriverPreconditionError,
    DriverUnsupportedOperationError,
)
from keysight_n6700.module_capabilities import classify_module


@pytest.mark.parametrize(
    ("mainframe", "model", "options", "expected"),
    [
        ("N6700C", "N6775A", [], False),
        ("N6700C", "N6775A", ["054"], True),
        ("N6700C", "N6731B", [], False),
        ("N6700C", "N6731B", ["760", "054"], True),
        ("N6700C", "N6756A", [], False),
        ("N6700C", "N6756A", ["054"], True),
        ("N6700C", "N6762A", [], True),
        ("N6700C", "N6781A", [], True),
        ("N6705C", "N6775A", [], True),
        ("N6705C", "N6751A", [], True),
        ("N6705C", "N6762A", [], True),
        ("N6700B", "N6775A", ["054"], False),
        ("UNKNOWN", "N6775A", ["054"], False),
    ],
)
def test_arb_matrix(
    mainframe: str, model: str, options: list[str], expected: bool
) -> None:
    cap = classify_module(
        model, options, mainframe_model=mainframe, firmware_version="A.01.02"
    )
    assert cap.supports_list_mode is expected
    assert cap.supports_voltage_arb is expected
    assert cap.arb_trigger_dialect == ("channel_scoped" if expected else "unsupported")
    assert cap.mainframe_model == mainframe
    assert cap.firmware_version == "A.01.02"
    assert len(cap.capability_fingerprint) == 64


@pytest.mark.parametrize(
    ("model", "options", "mainframe", "firmware"),
    [
        ("N6775A", None, "N6700C", "C.01.02"),
        ("N6775A", ["?server-error"], "N6700C", "C.01.02"),
        ("N6762A", ["UNKNOWN"], "N6705C", "C.01.02"),
        ("N6762A", ["054"], "N6705C", "UNKNOWN"),
        ("N6775A", ["54"], "N6700C", "C.01.02"),
        ("N6775A", ["1054"], "N6700C", "C.01.02"),
        ("N6775A", ["054"], None, "C.01.02"),
        ("N6775A", ["054"], "N6700C", None),
        ("N6775Z", ["054"], "N6700C", "C.01.02"),
        ("N6739B", ["054"], "N6700C", "C.01.02"),
        ("N6789A", [], "N6705C", "C.01.02"),
    ],
)
def test_unknown_context_never_grants_arb(
    model: str, options: list[str] | None, mainframe: str | None, firmware: str | None
) -> None:
    cap = classify_module(
        model, options, mainframe_model=mainframe, firmware_version=firmware
    )
    assert cap.supports_list_mode is False
    assert cap.supports_voltage_arb is False


def test_option_order_whitespace_and_case_do_not_change_fingerprint() -> None:
    first = classify_module(
        " n6775a ", ["054", "760"], mainframe_model="n6700c", firmware_version="a.01"
    )
    reordered = classify_module(
        "N6775A", [" 760", " 054 ", "054"], mainframe_model="N6700C",
        firmware_version="A.01",
    )
    assert first == reordered
    changed = classify_module(
        "N6775A", [], mainframe_model="N6700C", firmware_version="A.01"
    )
    assert changed.capability_fingerprint != first.capability_fingerprint
    assert changed.supports_voltage_arb is False


def test_n6783a_negative_current_is_not_claimed_as_a_voltage_arb_rule() -> None:
    cap = classify_module(
        "N6783A", ["BAT"], mainframe_model="N6700C", firmware_version="A.01"
    )
    assert cap.module_type == "smu"
    assert cap.supports_voltage_arb is True
    assert cap.supports_smu_priority_mode is False


def test_n679_load_is_not_exposed_as_source_voltage_arb() -> None:
    cap = classify_module(
        "N6791A", [], mainframe_model="N6700C", firmware_version="A.01"
    )
    assert cap.module_type == "electronic_load"
    assert cap.supports_voltage_arb is False


def test_simulator_has_integrated_n6705c_source_arb_capability() -> None:
    driver = N6700.connect_simulated()
    try:
        assert driver.idn().model == "N6705C"
        cap = driver.channel(1).capabilities
        assert cap.model == "N6751A"
        assert cap.supports_list_mode
        assert cap.supports_voltage_arb
    finally:
        driver.disconnect()


def test_discovery_disables_unsupported_modes_before_any_scpi_write(monkeypatch: pytest.MonkeyPatch) -> None:
    driver = N6700.connect_simulated()
    writes: list[str] = []
    try:
        monkeypatch.setattr(
            driver, "idn", lambda **_kwargs: SimpleNamespace(model="N6700C", firmware="C.01")
        )
        monkeypatch.setattr(driver, "channel_options", lambda *_args, **_kwargs: [])
        driver.discover_modules()
        assert not driver.channel(1).capabilities.supports_voltage_arb
        monkeypatch.setattr(driver, "write_scpi", lambda command, **_kwargs: writes.append(command))
        with pytest.raises(DriverUnsupportedOperationError):
            driver.configure_voltage_arb(
                1, [1.0, 2.0], [0.01, 0.01], maximum_voltage_v=10.0
            )
        with pytest.raises(DriverUnsupportedOperationError):
            driver.configure_voltage_list(
                1, [1.0, 2.0], [0.01, 0.01], maximum_voltage_v=10.0
            )
        assert writes == []
    finally:
        driver.disconnect()


def test_rediscovery_revokes_old_channel_tokens_and_write_handles() -> None:
    driver = N6700.connect_simulated()
    try:
        previous = driver.channel(1)
        previous._transient_generation = 3
        driver.discover_modules()
        assert previous._transient_generation == 4
        assert previous._transient_state == "unknown"
        assert previous._transient_config is None
        with pytest.raises(DriverPreconditionError, match="stale"):
            previous.output_on()
        with pytest.raises(DriverPreconditionError, match="stale"):
            previous.get_output()
        assert driver.channel(1) is not previous
        assert driver.channel(1).capabilities.supports_voltage_arb
    finally:
        driver.disconnect()
