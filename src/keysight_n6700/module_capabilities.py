"""Module capability model and classification.

Named ``module_capabilities`` (not ``capabilities``) to avoid clashing with
:mod:`keysight_n6700.capability_model`, which implements the LPDS-013
capability-*discovery* API. This module classifies which N6700 *modules* are
installed; that one describes what the *driver* can do.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from typing import Literal

ModuleType = Literal["power_supply", "smu", "electronic_load", "unknown"]
ArbTriggerDialect = Literal["unsupported", "channel_scoped"]


@dataclass(frozen=True)
class ChannelCapabilities:
    model: str
    module_type: ModuleType
    mainframe_model: str = ""
    options: tuple[str, ...] = ()
    firmware_version: str = ""
    capability_fingerprint: str = ""
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
    arb_trigger_dialect: ArbTriggerDialect = "unsupported"
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


# Exact source models in the Keysight N6700C Model Differences / specification
# tables. Prefix matching can silently advertise Arb for nonexistent/unknown modules.
_OPTION_054_MODELS = frozenset(
    {f"N673{n}B" for n in range(1, 7)}
    | {f"N674{n}B" for n in range(1, 7)}
    | {f"N675{n}A" for n in range(1, 7)}
    | {f"N677{n}A" for n in range(3, 8)}
)
_PRECISION_MODELS = frozenset(f"N676{n}A" for n in range(1, 7))
_SMU_MODELS = frozenset({"N6781A", "N6782A", "N6783A", "N6784A", "N6785A", "N6786A"})
_QUALIFIED_MAINFRAMES = frozenset({"N6700C", "N6705C"})
_OPTION_PATTERN = re.compile(r"[A-Z0-9]{1,16}$")
_FIRMWARE_PATTERN = re.compile(r"[A-Z][A-Z0-9]*(?:[.][A-Z0-9]+){1,4}$")
_INVALID_OPTION_REPLIES = frozenset({"UNKNOWN", "ERROR", "NONE", "NA", "UNAVAILABLE"})


def classify_module(
    model: str,
    options: list[str] | tuple[str, ...] | None = None,
    *,
    mainframe_model: str | None = None,
    firmware_version: str | None = None,
) -> ChannelCapabilities:
    """Classify using a documented module/mainframe/option combination.

    Unknown chassis, unrecognized models, absent/invalid option inventory and
    unreviewed firmware identity *never* enable source LIST or voltage Arb.
    The returned SHA-256 fingerprint is deterministic, not a proof of hardware
    identity and not authorization for unattended energization.
    """
    model_clean = model.strip().upper()
    mainframe = (mainframe_model or "").strip().upper()
    firmware = (firmware_version or "").strip().upper()
    raw_options = tuple(str(item).strip().upper() for item in (options or ()))
    opts = tuple(sorted({item for item in raw_options if item and item not in {"0", "+0"}}))
    options_known = options is not None and all(
        _OPTION_PATTERN.fullmatch(item) and item not in _INVALID_OPTION_REPLIES for item in opts
    )
    known_identity = bool(mainframe and _FIRMWARE_PATTERN.fullmatch(firmware))
    fingerprint_data = [mainframe, firmware, model_clean, list(opts), options_known]
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_data, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()
    common = ChannelCapabilities(
        model=model_clean,
        module_type="unknown",
        options=opts,
        mainframe_model=mainframe,
        firmware_version=firmware,
        capability_fingerprint=fingerprint,
    )
    supported_context = mainframe in _QUALIFIED_MAINFRAMES and known_identity and options_known

    if model_clean == "SIM_LOAD":
        return replace(
            common,
            module_type="electronic_load",
            supports_load_cc=True,
            supports_load_cv=True,
            supports_load_cr=True,
            supports_load_cp=True,
            supports_power_measurement=True,
            verified_real_load_commands=False,
        )
    if model_clean in LOAD_MODELS:
        return replace(
            common,
            module_type="electronic_load",
            supports_load_cc=True,
            supports_load_cv=True,
            supports_load_cr=True,
            supports_load_cp=True,
            supports_power_measurement=True,
            supports_list_mode=supported_context,
            verified_real_load_commands=True,
            supports_digitizing=True,
            supports_array_measurement=True,
            digitize_qualification="vendor_declared",
            digitize_min_interval_s=10.24e-6,
        )
    if model_clean in _SMU_MODELS:
        return replace(
            common,
            module_type="smu",
            supports_voltage_source=True,
            supports_current_source=True,
            supports_power_measurement=True,
            supports_array_measurement=True,
            supports_list_mode=supported_context,
            supports_voltage_arb=supported_context,
            arb_trigger_dialect="channel_scoped" if supported_context else "unsupported",
            supports_voltage_slew=True,
            supports_current_transient=supported_context,
            supports_current_arb=supported_context,
            supports_bipolar_voltage=model_clean == "N6784A",
            supports_current_slew=True,
            supports_aux_voltage_input=model_clean in {"N6781A", "N6785A"},
            supports_smu_priority_mode=model_clean != "N6783A",
            supports_smu_output_off_mode=True,
        )
    if model_clean in _OPTION_054_MODELS or model_clean in _PRECISION_MODELS:
        optioned = model_clean in _OPTION_054_MODELS
        arb_allowed = supported_context and (
            not optioned or mainframe == "N6705C" or "054" in opts
        )
        return replace(
            common,
            module_type="power_supply",
            supports_voltage_source=True,
            supports_current_source=False,
            supports_power_measurement=model_clean in _PRECISION_MODELS,
            supports_array_measurement=arb_allowed,
            supports_list_mode=arb_allowed,
            supports_voltage_arb=arb_allowed,
            arb_trigger_dialect="channel_scoped" if arb_allowed else "unsupported",
            supports_voltage_slew=True,
            supports_current_transient=arb_allowed,
            supports_current_arb=arb_allowed,
        )
    return common
