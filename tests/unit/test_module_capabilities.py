"""Module classification, including hardware-verified cases.

test_n6791a_classifies_as_power_supply_not_smu documents a real finding:
against a real N6700C mainframe (2026-09-14), an N6791A module answered
VOLT?/CURR?/MEAS:VOLT?/MEAS:CURR?/OUTP? correctly but never replied to
FUNC:MODE? at all (the query timed out and faulted the transport, rather
than returning a SCPI error). Classifying N679x as power_supply, not smu,
is what keeps the driver from ever sending that query to this family; this
test is a regression guard against that classification silently changing.
"""

from __future__ import annotations

from keysight_n6700.module_capabilities import classify_module


def test_n6791a_classifies_as_power_supply_not_smu() -> None:
    caps = classify_module("N6791A")
    assert caps.module_type == "power_supply"
    assert caps.supports_voltage_source is True
    assert caps.supports_smu_priority_mode is False


def test_n679x_family_is_recognized_case_insensitively() -> None:
    assert classify_module("n6790a").module_type == "power_supply"
    assert classify_module("N6799A").module_type == "power_supply"


def test_n678x_still_classifies_as_smu() -> None:
    caps = classify_module("N6781A")
    assert caps.module_type == "smu"
    assert caps.supports_smu_priority_mode is True
    assert caps.supports_aux_voltage_input is True
