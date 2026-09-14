"""Module classification, including hardware- and manual-verified cases.

test_n6791a_classifies_as_electronic_load documents the corrected finding:
against a real N6700C mainframe (2026-09-14), an N6791A module answered
VOLT?/CURR?/MEAS:VOLT?/MEAS:CURR?/OUTP? correctly but never replied to
FUNC:MODE? at all (the query timed out and faulted the transport, rather
than returning a SCPI error). The official Keysight N6705C User's Guide /
Programmer's Reference (see Keysight_documents/) later confirmed N6791A and
N6792A are genuine Electronic Load Modules with four priority modes
(voltage/current/resistance/power), and that the real command is plain
FUNCtion (accepting all four for this family, two for the N678xA SMU) —
FUNC:MODE was never valid for any module family, which is why the earlier
probe hung rather than erroring.
"""

from __future__ import annotations

from keysight_n6700.module_capabilities import classify_module


def test_n6791a_classifies_as_electronic_load() -> None:
    caps = classify_module("N6791A")
    assert caps.module_type == "electronic_load"
    assert caps.verified_real_load_commands is True
    assert caps.supports_load_cc is True
    assert caps.supports_load_cv is True
    assert caps.supports_load_cr is True
    assert caps.supports_load_cp is True
    assert caps.supports_smu_priority_mode is False


def test_n679x_family_is_recognized_case_insensitively() -> None:
    assert classify_module("n6791a").module_type == "electronic_load"
    assert classify_module("N6792A").module_type == "electronic_load"


def test_n678x_still_classifies_as_smu() -> None:
    caps = classify_module("N6781A")
    assert caps.module_type == "smu"
    assert caps.supports_smu_priority_mode is True
    assert caps.supports_aux_voltage_input is True
