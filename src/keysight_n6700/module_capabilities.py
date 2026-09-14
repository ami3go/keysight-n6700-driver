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


POWER_PREFIXES = ("N673", "N674", "N675", "N676", "N677", "N679")
SMU_PREFIXES = ("N678",)
# N679x (found on real hardware: N6791A) responds correctly to VOLT?/CURR?/
# MEAS:VOLT?/MEAS:CURR?/OUTP? like any power-supply channel, but FUNC:MODE?
# — the SMU voltage/current-priority-mode query — does not just error, it
# never replies at all and faults the transport. Classifying it as
# power_supply (not smu) is what keeps the driver from ever sending that
# query to this family. Confirmed 2026-09-14 against a real N6700C
# mainframe; see review/known_risks.md.

# Deliberately empty for real hardware until official load docs are added.
VERIFIED_LOAD_MODELS: dict[str, dict[str, object]] = {}


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
    if model_clean in VERIFIED_LOAD_MODELS:
        return ChannelCapabilities(
            model=model_clean,
            module_type="electronic_load",
            options=opts,
            supports_load_cc=True,
            supports_load_cv=True,
            supports_load_cr=True,
            supports_load_cp=True,
            supports_power_measurement=True,
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
