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


POWER_PREFIXES = ("N673", "N674", "N675", "N676", "N677")
LOAD_PREFIXES = ("N679",)
_VERIFIED_LOADS = frozenset(VERIFIED_REAL_ELECTRONIC_LOAD_MODELS)
# N6783A is deliberately excluded: Keysight documents CURR:LIM for it but
# distinguishes it from "N678xA SMU" for priority-mode commands such as FUNC.
_VERIFIED_SMU_MODELS = frozenset({"N6781A", "N6782A", "N6784A", "N6785A", "N6786A"})


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
            supports_simultaneous_vi=True,
            supports_list_mode=True,
            verified_real_load_commands=model_clean in _VERIFIED_LOADS,
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
