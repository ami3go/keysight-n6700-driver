from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from keysight_n6700 import N6700

ROOT = Path(__file__).resolve().parent.parent.parent
AI_DIR = ROOT / "ai"

_MANDATORY_SECTIONS = {
    "identity",
    "mental_model",
    "state_machine",
    "resources",
    "dependencies",
    "capabilities",
    "error_catalogue",
    "safety_rules",
    "verification_objectives",
    "setup_teardown_contract",
    "limitations",
    "planning_hints",
    "unknown_handling",
    "conformance_rules",
}


def test_ai_contract_has_all_fourteen_mandatory_sections() -> None:
    contract = yaml.safe_load((AI_DIR / "ai_contract.yaml").read_text())
    missing = _MANDATORY_SECTIONS - contract.keys()
    assert not missing, f"ai_contract.yaml is missing mandatory sections: {missing}"


def test_ai_contract_lock_hash_matches_the_contract_file() -> None:
    contract_text = (AI_DIR / "ai_contract.yaml").read_text()
    lock = json.loads((AI_DIR / "ai_contract.lock").read_text())
    assert lock["contract_sha256"] == hashlib.sha256(contract_text.encode("utf-8")).hexdigest(), (
        "ai/ai_contract.lock does not match ai/ai_contract.yaml; regenerate with "
        "`python scripts/generate_ai_contract.py`"
    )


def test_ai_contract_capabilities_match_the_live_capability_model() -> None:
    contract = yaml.safe_load((AI_DIR / "ai_contract.yaml").read_text())
    assert contract["capabilities"] == N6700().get_capability_model()


def test_every_error_catalogue_entry_names_a_real_exception_class() -> None:
    from keysight_n6700 import exceptions as n6700_exceptions

    contract = yaml.safe_load((AI_DIR / "ai_contract.yaml").read_text())
    for entry in contract["error_catalogue"]:
        assert hasattr(n6700_exceptions, entry["exception_class"]), entry["exception_class"]


def test_identity_matches_the_plugin_manifest() -> None:
    from keysight_n6700.plugin import KeysightN6700Plugin

    contract = yaml.safe_load((AI_DIR / "ai_contract.yaml").read_text())
    manifest = KeysightN6700Plugin.get_descriptor()
    assert contract["identity"]["plugin_id"] == manifest["plugin_id"]
    assert contract["identity"]["driver_name"] == manifest["driver_name"]
    assert contract["identity"]["driver_class"] == manifest["driver_class"]
