"""Module capability model and conservative module classification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .modules import VERIFIED_REAL_ELECTRONIC_LOAD_MODELS

ModuleType = Literal["power_supply", "smu", "electronic_load", "unknown"]


@dataclass(frozen=True)
class ChannelCapabilities:
    model: str
    module_type: ModuleType
    options: tuple[str, ...] = ()
    supports_voltage_source: bool = False
    supports_current_source: bool = False
    supports_load_cc: bool = False
    supports_load_cv: bool = False
    supports_load_cr: bool = False
    supports_load_cp: bool = False
    supports_power_measurement: bool = False
    supports_simultaneous_vi: bool = False
    supports_array_measurement: bool = False
    supports_list_mode: bool = False
    supports_aux_voltage_input: bool = False
    supports_smu_priority_mode: bool = False
    supports_smu_output_off_mode: bool = False
    verified_real_load_commands: bool = False
    min_voltage: float | None = None
    max_voltage: float | None = None
    min_current: float | None = None
    max_current: float | None = None
    # Buffered/triggered acquisition (SENS:SWEep + TRIG:ACQuire + FETC:ARRay), per the
    # N6705C manual's "Digitizing Measurements" chapter. Not yet classified for the SMU/
    # power-supply families, which the manual says also support it — see known_risks.md.
    supports_digitizing: bool = False
    digitize_qualification: Literal["none", "vendor_declared"] = "none"
    digitize_min_interval_s: float | None = None


POWER_PREFIXES = ("N673", "N674", "N675", "N676", "N677")
_VERIFIED_LOADS = frozenset(VERIFIED_REAL_ELECTRONIC_LOAD_MODELS)
# N6783A is deliberately excluded: Keysight documents CURR:LIM for it but
# distinguishes it from "N678xA SMU" for priority-mode commands such as FUNC.
_VERIFIED_SMU_MODELS = frozenset({"N6781A", "N6782A", "N6784A", "N6785A", "N6786A"})
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
    model: str, options: list[str] | tuple[str, ...] | None = None
) -> ChannelCapabilities:
    model_clean = model.strip().strip('"').upper()
    opts = tuple(options or ())
    if model_clean == "SIM_LOAD":
        return ChannelCapabilities(
            model=model_clean,
            module_type="electronic_load",
            options=opts,
            supports_load_cc=True,
            supports_load_cv=True,
            supports_load_cr=True,
            supports_load_cp=True,
            supports_power_measurement=True,
            supports_simultaneous_vi=True,
        )
    if model_clean in LOAD_MODELS:
        return ChannelCapabilities(
            model=model_clean,
            module_type="electronic_load",
            options=opts,
            supports_load_cc=True,
            supports_load_cv=True,
            supports_load_cr=True,
            supports_load_cp=True,
            supports_power_measurement=True,
            supports_simultaneous_vi=True,
            supports_list_mode=True,
            verified_real_load_commands=model_clean in _VERIFIED_LOADS,
            # "1 or 2 parameters (all models): 10.24 microseconds" per the manual's
            # sample-rate table; the 5.12 microsecond single-parameter rate is SMU-only.
            supports_digitizing=True,
            supports_array_measurement=True,
            digitize_qualification="vendor_declared",
            digitize_min_interval_s=10.24e-6,
        )
    if model_clean in _VERIFIED_SMU_MODELS:
        return ChannelCapabilities(
            model=model_clean,
            module_type="smu",
            options=opts,
            supports_voltage_source=True,
            supports_current_source=True,
            supports_power_measurement=True,
            supports_simultaneous_vi=True,
            supports_array_measurement=True,
            supports_list_mode=True,
            supports_aux_voltage_input=model_clean.startswith(("N6781", "N6785")),
            supports_smu_priority_mode=True,
            supports_smu_output_off_mode=True,
        )
    if model_clean.startswith("N6783"):
        return ChannelCapabilities(
            model=model_clean,
            module_type="power_supply",
            options=opts,
            supports_voltage_source=True,
            supports_power_measurement=True,
            supports_simultaneous_vi=True,
        )
    if model_clean.startswith(POWER_PREFIXES):
        is_precision = model_clean.startswith("N676")
        return ChannelCapabilities(
            model=model_clean,
            module_type="power_supply",
            options=opts,
            supports_voltage_source=True,
            supports_current_source=False,
            supports_power_measurement=is_precision,
            supports_simultaneous_vi=is_precision,
            supports_array_measurement=is_precision or "054" in opts,
            supports_list_mode=True,
        )
    return ChannelCapabilities(model=model_clean, module_type="unknown", options=opts)
