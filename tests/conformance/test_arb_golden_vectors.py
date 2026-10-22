"""ARB-R05: replay the golden SCPI vectors and check them against the vendor tree."""

from __future__ import annotations

import pytest

from .arb_vectors import in_vendor_tree, load_vectors, run_vector, vendor_command_tree

VECTORS = load_vectors()
IDS = [vector["id"] for vector in VECTORS]

# Families the R05 table requires; a vector family may not silently disappear.
REQUIRED_FAMILIES = {"arb-shape", "udef", "list", "cdw", "sequence", "sequence-file", "step"}
# Every ARB shape must have a voltage vector, and a current vector for current Arb.
SHAPE_MNEMONICS = {"STEP", "RAMP", "STA", "SIN", "PULS", "TRAP", "EXP"}
# Configuration may not energize, initiate or trigger anything.
FORBIDDEN_PREFIXES = ("OUTP", "INIT:TRAN", "INIT:IMM", "TRIG:", "*TRG", "*RST", "ABOR:ACQ")


def test_vector_ids_are_unique_and_every_required_family_is_present() -> None:
    assert len(set(IDS)) == len(IDS)
    assert {vector["family"] for vector in VECTORS} >= REQUIRED_FAMILIES


def test_every_parametric_shape_has_a_voltage_vector() -> None:
    shapes = {
        write.split()[1].split(",")[0]
        for vector in VECTORS
        if vector["id"].startswith("voltage-arb-")
        for write in vector["writes"]
        if write.startswith("ARB:FUNC:SHAP")
    }
    assert shapes >= SHAPE_MNEMONICS


@pytest.mark.parametrize("vector", VECTORS, ids=IDS)
def test_replay_matches_the_recorded_commands_exactly(vector: dict) -> None:
    assert run_vector(vector) == vector["writes"]


@pytest.mark.parametrize("vector", VECTORS, ids=IDS)
def test_every_header_exists_in_the_vendor_command_tree(vector: dict) -> None:
    unknown = [write for write in vector["writes"] if not in_vendor_tree(write)]
    assert unknown == [], f"not in the N6705 Programmer's Reference command tree: {unknown}"


@pytest.mark.parametrize("vector", VECTORS, ids=IDS)
def test_configuration_never_energizes_initiates_or_triggers(vector: dict) -> None:
    offenders = [w for w in vector["writes"] if w.upper().startswith(FORBIDDEN_PREFIXES)]
    assert offenders == []


@pytest.mark.parametrize("vector", VECTORS, ids=IDS)
def test_every_command_addresses_the_selected_channel_only(vector: dict) -> None:
    for write in vector["writes"]:
        assert write.endswith("(@1)"), write


@pytest.mark.parametrize("vector", [v for v in VECTORS if v["family"] != "sequence-file"], ids=lambda v: v["id"])
def test_transaction_starts_by_stopping_triggers_and_fixing_the_modes(vector: dict) -> None:
    assert vector["writes"][:4] == [
        "INIT:CONT:TRAN OFF,(@1)",
        "ABOR:TRAN (@1)",
        "VOLT:MODE FIX,(@1)",
        "CURR:MODE FIX,(@1)",
    ]
    assert vector["writes"][-1] in {
        "VOLT:MODE ARB,(@1)", "VOLT:MODE LIST,(@1)", "VOLT:MODE STEP,(@1)",
        "CURR:MODE ARB,(@1)", "CURR:MODE LIST,(@1)", "CURR:MODE STEP,(@1)",
    }, "the mode switch that arms the profile must be the last command"


def test_the_oracle_rejects_a_misspelled_command() -> None:
    assert vendor_command_tree()
    assert not in_vendor_tree("ARB:VOLT:SIN:FREQUENCIES 1,(@1)")
    assert not in_vendor_tree("ARB:VOLT:RAMP:RISE 1,(@1)")
