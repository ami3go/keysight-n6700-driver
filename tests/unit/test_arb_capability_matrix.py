"""ARB-R02: exact mainframe x module x option x firmware capability matrix (issue #21).

Vendor evidence (Keysight N6700C Operating and Service Guide 9018-04957,
retrieved 2026-10-22; printed page numbers):

* p.43 Model Differences: "Arbitrary waveform generation  Option 054" for the
  N673xB/N674xB/N675xA/N677xA columns, standard for N676xA.
* p.46 Option 054 "High-Speed Test Extensions. Includes digitized output
  measurements and output list capability. Available for Models N673xB, -4xB,
  -5xA, -7xA. Included with Models N676xA, N678xA SMU, and N6783A."
* p.34 / p.37 SCPI tree: "ARB commands only on N676xA, N678xA SMU, Option 054"
  and "LIST commands only on N676xA, N678xA SMU and Option 054".

Everything outside the reviewed mainframes (N6700C, N6705C) fails closed; the
N6705C is accepted on the strength of the N6705 Programmer's Reference.  These
tests only inspect classification and the outbound SCPI log.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from scpi_driver_core.simulation.scripted import ScriptedScpiTransport

from keysight_n6700 import (
    N6700,
    ArbSequenceStep,
    ArbSine,
    ArbStep,
    DriverUnsupportedOperationError,
)
from keysight_n6700.module_capabilities import (
    _OPTION_054_MODELS,
    _PRECISION_MODELS,
    _SMU_MODELS,
    ChannelCapabilities,
    classify_module,
)

FIRMWARE = "E.02.09.3271"  # firmware reported by the bench N6700C
OPTIONED = sorted(_OPTION_054_MODELS)
PRECISION = sorted(_PRECISION_MODELS)
SMUS = sorted(_SMU_MODELS)
REVIEWED_MAINFRAMES = ("N6700C", "N6705C")
UNREVIEWED_MAINFRAMES = (
    "N6700A", "N6700B", "N6701A", "N6701C", "N6702A", "N6702C", "N6705A", "N6705B", "",
)


def _cap(
    model: str, options: list[str] | None, mainframe: str | None, firmware: str | None = FIRMWARE
) -> ChannelCapabilities:
    return classify_module(model, options, mainframe_model=mainframe, firmware_version=firmware)


def _source_flags(cap: Any) -> tuple[bool, ...]:
    return (
        cap.supports_list_mode,
        cap.supports_voltage_arb,
        cap.supports_current_arb,
        cap.supports_current_transient,
        cap.supports_array_measurement,
    )


# -- classification matrix ------------------------------------------------------


@pytest.mark.parametrize("model", OPTIONED)
def test_optioned_models_need_054_on_n6700c(model: str) -> None:
    assert not any(_source_flags(_cap(model, [], "N6700C")))
    assert not any(_source_flags(_cap(model, ["760"], "N6700C")))
    granted = _cap(model, ["054"], "N6700C")
    assert all(_source_flags(granted))
    assert granted.arb_trigger_dialect == "channel_scoped"


@pytest.mark.parametrize("model", OPTIONED)
def test_optioned_models_on_n6705c_do_not_need_054(model: str) -> None:
    assert all(_source_flags(_cap(model, [], "N6705C")))


@pytest.mark.parametrize("model", PRECISION + SMUS)
@pytest.mark.parametrize("mainframe", REVIEWED_MAINFRAMES)
def test_precision_and_smu_models_include_arb_without_option(model: str, mainframe: str) -> None:
    cap = _cap(model, [], mainframe)
    assert cap.supports_list_mode and cap.supports_voltage_arb
    assert cap.arb_trigger_dialect == "channel_scoped"


@pytest.mark.parametrize("model", OPTIONED + PRECISION + SMUS + ["N6791A", "N6792A"])
@pytest.mark.parametrize("mainframe", UNREVIEWED_MAINFRAMES)
def test_unreviewed_mainframes_never_grant_list_or_arb(model: str, mainframe: str) -> None:
    for options in ([], ["054"], ["054", "760"]):
        cap = _cap(model, options, mainframe)
        assert not cap.supports_list_mode
        assert not cap.supports_voltage_arb
        assert cap.arb_trigger_dialect == "unsupported"


@pytest.mark.parametrize("model", ["N6775A", "N6762A", "N6781A", "N6791A"])
@pytest.mark.parametrize(
    "firmware", [None, "", "UNKNOWN", "1", "E.02.09.3271.1.2.3", "e..02", "E 02 09"]
)
def test_unreviewed_firmware_identity_fails_closed(model: str, firmware: str | None) -> None:
    cap = _cap(model, ["054"], "N6700C", firmware)
    assert not cap.supports_list_mode
    assert not cap.supports_voltage_arb


@pytest.mark.parametrize("options", [None, ["?"], ["UNKNOWN"], ["error"], ["bad option"], ["x" * 17]])
def test_invalid_option_inventory_fails_closed(options: list[str] | None) -> None:
    cap = _cap("N6775A", options, "N6700C")
    assert not cap.supports_list_mode and not cap.supports_voltage_arb


@pytest.mark.parametrize("model", ["", "N67", "N6775", "N6775AA", "N6700C", "N6705C", "N6790A", "SIM"])
def test_unknown_models_are_not_classified_as_sources(model: str) -> None:
    cap = _cap(model, ["054"], "N6700C")
    assert cap.module_type == "unknown"
    assert not any(_source_flags(cap))


def test_electronic_loads_get_list_only_in_a_reviewed_context() -> None:
    for model in ("N6791A", "N6792A"):
        granted = _cap(model, [], "N6700C")
        assert granted.supports_list_mode
        assert granted.arb_trigger_dialect == "mainframe_global"
        assert not granted.supports_voltage_arb and not granted.supports_current_arb
        denied = _cap(model, [], "N6700B")
        assert not denied.supports_list_mode and denied.arb_trigger_dialect == "unsupported"


def test_bench_context_matches_what_the_real_n6700c_reports() -> None:
    """Captured 2026-10-22 from MY56320259: both channels answer SYST:CHAN:OPT? with no option."""
    n6775a = _cap("N6775A", [], "N6700C")
    assert not any(_source_flags(n6775a))
    assert n6775a.arb_trigger_dialect == "unsupported"
    n6791a = _cap("N6791A", [], "N6700C")
    assert n6791a.supports_list_mode and n6791a.arb_trigger_dialect == "mainframe_global"


def test_every_matrix_cell_is_deterministic_and_fingerprints_the_full_context() -> None:
    first = _cap("N6775A", ["054"], "N6700C")
    assert first == _cap("N6775A", ["054"], "N6700C")
    for changed in (
        _cap("N6775A", [], "N6700C"),
        _cap("N6775A", ["054"], "N6705C"),
        _cap("N6775A", ["054"], "N6700C", "E.02.09.3272"),
        _cap("N6774A", ["054"], "N6700C"),
    ):
        assert changed.capability_fingerprint != first.capability_fingerprint


# -- end-to-end: denied requests perform no SCPI mutation -----------------------

STEPS = [ArbSequenceStep(ArbStep(1.0, 2.0, 0.01))]
CURRENT_STEPS = [ArbSequenceStep(ArbStep(0.1, 0.2, 0.01))]
ENTRY_POINTS: dict[str, Callable[[N6700], Any]] = {
    "voltage_list": lambda d: d.configure_voltage_list(1, [1.0, 2.0], [0.01, 0.01], maximum_voltage_v=5.0),
    "voltage_arb": lambda d: d.configure_voltage_arb(1, [1.0, 2.0], [0.01, 0.01], maximum_voltage_v=5.0),
    "voltage_waveform": lambda d: d.configure_voltage_arb_waveform(1, ArbSine(1.0, 2.0, 100.0), maximum_voltage_v=5.0),
    "voltage_cdw": lambda d: d.configure_voltage_constant_dwell_arb(1, [1.0, 2.0], 0.01, maximum_voltage_v=5.0),
    "voltage_sequence": lambda d: d.configure_voltage_arb_sequence(1, STEPS, maximum_voltage_v=5.0),
    "voltage_step": lambda d: d.configure_voltage_step(1, 2.0, maximum_voltage_v=5.0),
    "current_list": lambda d: d.configure_current_list(1, [0.1, 0.2], [0.01, 0.01], maximum_current_a=1.0),
    "current_arb": lambda d: d.configure_current_arb(1, [0.1, 0.2], [0.01, 0.01], maximum_current_a=1.0),
    "current_cdw": lambda d: d.configure_current_constant_dwell_arb(1, [0.1, 0.2], 0.01, maximum_current_a=1.0),
    "current_sequence": lambda d: d.configure_current_arb_sequence(1, CURRENT_STEPS, maximum_current_a=1.0),
    "current_step": lambda d: d.configure_current_step(1, 0.2, maximum_current_a=1.0),
    "store_sequence": lambda d: d.store_arb_sequence(1, "internal:\\x.seq"),
    "load_voltage_sequence": lambda d: d.load_voltage_arb_sequence(1, "internal:\\x.seq", maximum_voltage_v=5.0),
    "load_current_sequence": lambda d: d.load_current_arb_sequence(1, "internal:\\x.seq", maximum_current_a=1.0),
}

# (mainframe, option reported by SYST:CHAN:OPT?) combinations that must be denied for an N6775A.
DENIED_CONTEXTS = [("N6700C", ""), ("N6700C", "760"), ("N6700B", "054"), ("N6700A", "054"), ("N6705B", "054")]


def _scripted(driver: N6700) -> Any:
    transport = driver._session(None).transport
    assert isinstance(transport, ScriptedScpiTransport)
    return transport


def _simulated(mainframe: str, option: str) -> N6700:
    driver = N6700.connect_simulated(models={1: "N6775A"}, mainframe_model=mainframe)
    _scripted(driver).instrument.channels[1].option = option
    driver.discover_modules()
    return driver


@pytest.mark.parametrize("entry", sorted(ENTRY_POINTS))
@pytest.mark.parametrize(("mainframe", "option"), DENIED_CONTEXTS)
def test_denied_context_rejects_every_arb_entry_point_without_any_write(
    entry: str, mainframe: str, option: str
) -> None:
    driver = _simulated(mainframe, option)
    try:
        transport = _scripted(driver)
        transport.clear_history()
        with pytest.raises(DriverUnsupportedOperationError):
            ENTRY_POINTS[entry](driver)
        mutating = [c for c in transport.history if not c.split(None, 1)[0].endswith("?")]
        assert mutating == [], f"{entry} wrote {mutating} in {mainframe}/{option!r}"
    finally:
        driver.disconnect()


@pytest.mark.parametrize(("mainframe", "option"), DENIED_CONTEXTS)
def test_denied_context_still_allows_ordinary_dc_commands(mainframe: str, option: str) -> None:
    driver = _simulated(mainframe, option)
    try:
        driver.set_dc_voltage(1.5, 1)
        assert driver.get_dc_voltage_setpoint(1) == pytest.approx(1.5)
    finally:
        driver.disconnect()


@pytest.mark.parametrize(
    "entry",
    ["voltage_list", "voltage_arb", "voltage_waveform", "voltage_cdw", "voltage_sequence",
     "voltage_step", "current_list", "current_arb", "current_cdw", "current_sequence", "current_step"],
)
def test_granted_context_accepts_the_same_entry_points(entry: str) -> None:
    driver = _simulated("N6700C", "054")
    try:
        assert driver.channel(1).capabilities.supports_voltage_arb
        assert ENTRY_POINTS[entry](driver) is not None
    finally:
        driver.disconnect()


def test_changed_option_inventory_is_picked_up_by_rediscovery() -> None:
    driver = _simulated("N6700C", "054")
    try:
        assert driver.channel(1).capabilities.supports_voltage_arb
        _scripted(driver).instrument.channels[1].option = ""
        driver.discover_modules()
        assert not driver.channel(1).capabilities.supports_voltage_arb
        with pytest.raises(DriverUnsupportedOperationError):
            ENTRY_POINTS["voltage_list"](driver)
    finally:
        driver.disconnect()
