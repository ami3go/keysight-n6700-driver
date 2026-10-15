"""Module capability model and classification.

Named ``module_capabilities`` (not ``capabilities``) to avoid clashing with
:mod:`keysight_n6700.capability_model`, which implements the LPDS-013
capability-*discovery* API. This module classifies which N6700 *modules* are
installed; that one describes what the *driver* can do.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ModuleType = Literal["power_supply", "smu", "electronic_load", "unknown"]


@dataclass(frozen=True)
class ChannelCapabilities:
    model: str
    module_type: ModuleType
    mainframe_model: str = ""
    options: tuple[str, ...] = ()
    supports_voltage_source: bool = False
    supports_current_source: bool = False
    supports_load_cc: bool = False
    supports_load_cv: bool = False
    supports_load_cr: bool = False
    supports_load_cp: bool = False
    supports_power_measurement: bool = False
    supports_array_measurement: bool = False
    supports_list_mode: bool = False
    supports_voltage_arb: bool = False
    supports_voltage_slew: bool = False
    # Current LIST/STEP transients remain available independently of the
    # N6705-only ARB subsystem.
    supports_current_transient: bool = False
    # Current ARB waveform support after combining module and mainframe.
    supports_current_arb: bool = False
    # Four-quadrant voltage sourcing is available only on the N6784A.
    supports_bipolar_voltage: bool = False
    # [SOURce:]CURRent:SLEW: "This command only applies to models N678xA SMU."
    supports_current_slew: bool = False
    supports_aux_voltage_input: bool = False
    supports_smu_priority_mode: bool = False
    supports_smu_output_off_mode: bool = False
    verified_real_load_commands: bool = False
    # Buffered/triggered acquisition (SENS:SWEep + TRIG:ACQuire + FETC:ARRay), per the
    # N6705C manual's "Digitizing Measurements" chapter. Not yet classified for the SMU/
    # power-supply families, which the manual says also support it — see known_risks.md.
    supports_digitizing: bool = False
    digitize_qualification: Literal["none", "vendor_declared"] = "none"
    digitize_min_interval_s: float | None = None


POWER_PREFIXES = ("N673", "N674", "N675", "N676", "N677")
SMU_PREFIXES = ("N678",)
# N679xA (N6791A=100W, N6792A=200W) are genuine Electronic Load Modules, not
# power supplies or SMUs — confirmed via the official Keysight N6705C User's
# Guide / Programmer's Reference (see Keysight_documents/). They support four
# priority modes (voltage/current/resistance/power, vs. the N678xA SMU's two),
# selected with the same [SOURce:]FUNCtion command the SMU uses but with two
# extra arguments (RESistance, POWer). The load's input terminals are
# programmed with the ordinary OUTP command (Note 1, "Quick Reference"
# chapter): there is no separate INPut command. This matches the earlier
# real-hardware finding (2026-09-14, real N6700C mainframe) that N6791A
# answers VOLT?/CURR?/MEAS:VOLT?/MEAS:CURR?/OUTP? like a power-supply channel
# but never replies to FUNC:MODE? — that finding was correct that FUNC:MODE is
# not a valid command for this family, but the actual command is plain FUNC
# (also true for the SMU; see review/known_risks.md for the matching SMU fix).
LOAD_MODELS = frozenset({"N6791A", "N6792A"})


def classify_module(
    model: str,
    options: list[str] | tuple[str, ...] | None = None,
    *,
    mainframe_model: str | None = None,
) -> ChannelCapabilities:
    model_clean = model.strip().upper()
    mainframe_clean = (mainframe_model or "").strip().upper()
    # The ARB command subsystem (including CDW, sequences, and MMEMory
    # sequence files) belongs to the N6705 DC Power Analyzer mainframe.  LIST
    # and triggered STEP remain module features on the other N6700-family
    # mainframes, so keep those capabilities separate.
    arb_available = not mainframe_clean or mainframe_clean.startswith("N6705")
    opts = tuple(options or ())
    if model_clean == "SIM_LOAD":
        return ChannelCapabilities(
            model=model_clean,
            module_type="electronic_load",
            mainframe_model=mainframe_clean,
            options=opts,
            supports_load_cc=True,
            supports_load_cv=True,
            supports_load_cr=True,
            supports_load_cp=True,
            supports_power_measurement=True,
            verified_real_load_commands=False,
        )
    if model_clean in LOAD_MODELS:
        return ChannelCapabilities(
            model=model_clean,
            module_type="electronic_load",
            mainframe_model=mainframe_clean,
            options=opts,
            supports_load_cc=True,
            supports_load_cv=True,
            supports_load_cr=True,
            supports_load_cp=True,
            supports_power_measurement=True,
            supports_list_mode=True,
            verified_real_load_commands=True,
            # "1 or 2 parameters (all models): 10.24 microseconds" per the manual's
            # sample-rate table; the 5.12 microsecond single-parameter rate is SMU-only.
            supports_digitizing=True,
            supports_array_measurement=True,
            digitize_qualification="vendor_declared",
            digitize_min_interval_s=10.24e-6,
        )
    if model_clean.startswith(SMU_PREFIXES):
        return ChannelCapabilities(
            model=model_clean,
            module_type="smu",
            mainframe_model=mainframe_clean,
            options=opts,
            supports_voltage_source=True,
            supports_current_source=True,
            supports_power_measurement=True,
            supports_array_measurement=True,
            supports_list_mode=True,
            supports_voltage_arb=arb_available,
            supports_voltage_slew=True,
            supports_current_transient=True,
            supports_current_arb=arb_available,
            supports_bipolar_voltage=model_clean == "N6784A",
            supports_current_slew=True,
            supports_aux_voltage_input=model_clean.startswith(("N6781", "N6785")),
            supports_smu_priority_mode=True,
            supports_smu_output_off_mode=True,
        )
    if model_clean.startswith(POWER_PREFIXES):
        return ChannelCapabilities(
            model=model_clean,
            module_type="power_supply",
            mainframe_model=mainframe_clean,
            options=opts,
            supports_voltage_source=True,
            supports_current_source=False,
            supports_power_measurement=model_clean.startswith("N676"),
            supports_array_measurement=model_clean.startswith("N676") or "054" in opts,
            supports_list_mode=True,
            supports_voltage_arb=arb_available,
            supports_voltage_slew=True,
            supports_current_transient=True,
            supports_current_arb=arb_available,
        )
    return ChannelCapabilities(
        model=model_clean,
        module_type="unknown",
        mainframe_model=mainframe_clean,
        options=opts,
    )
