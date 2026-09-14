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
    supports_aux_voltage_input: bool = False
    supports_smu_priority_mode: bool = False
    supports_smu_output_off_mode: bool = False
    verified_real_load_commands: bool = False


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
LOAD_PREFIXES = ("N679",)


def classify_module(model: str, options: list[str] | tuple[str, ...] | None = None) -> ChannelCapabilities:
    model_clean = model.strip().upper()
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
            verified_real_load_commands=False,
        )
    if model_clean.startswith(LOAD_PREFIXES):
        return ChannelCapabilities(
            model=model_clean,
            module_type="electronic_load",
            options=opts,
            supports_load_cc=True,
            supports_load_cv=True,
            supports_load_cr=True,
            supports_load_cp=True,
            supports_power_measurement=True,
            supports_list_mode=True,
            verified_real_load_commands=True,
        )
    if model_clean.startswith(SMU_PREFIXES):
        return ChannelCapabilities(
            model=model_clean,
            module_type="smu",
            options=opts,
            supports_voltage_source=True,
            supports_current_source=True,
            supports_power_measurement=True,
            supports_array_measurement=True,
            supports_list_mode=True,
            supports_aux_voltage_input=model_clean.startswith(("N6781", "N6785")),
            supports_smu_priority_mode=True,
            supports_smu_output_off_mode=True,
        )
    if model_clean.startswith(POWER_PREFIXES):
        return ChannelCapabilities(
            model=model_clean,
            module_type="power_supply",
            options=opts,
            supports_voltage_source=True,
            supports_current_source=False,
            supports_power_measurement=model_clean.startswith("N676"),
            supports_array_measurement=model_clean.startswith("N676") or "054" in opts,
            supports_list_mode=True,
        )
    return ChannelCapabilities(model=model_clean, module_type="unknown", options=opts)
