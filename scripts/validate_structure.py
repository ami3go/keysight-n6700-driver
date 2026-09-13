#!/usr/bin/env python3
"""Validate the mandatory LPDS-005 repository structure and cross-references.

Run from the repository root: ``python scripts/validate_structure.py``
Exits non-zero and prints every problem found, rather than stopping at the
first one, so a single run tells you everything to fix.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

REQUIRED_PATHS = [
    "src/keysight_n6700/__init__.py",
    "src/keysight_n6700/driver.py",
    "src/keysight_n6700/exceptions.py",
    "src/keysight_n6700/py.typed",
    "adapters/robotframework/keysight_n6700_robotframework_adapter/__init__.py",
    "ai/ai_contract.yaml",
    "ai/ai_contract.lock",
    "capability/capability_model.yaml",
    "capability/capability_model.schema.json",
    "config/schema.json",
    "config/default.json",
    "config/example.json",
    "tests/conformance/data/method_inventory.yaml",
    "tests/conformance/data/protocol_vectors.yaml",
    "tests/conformance/data/exclusions.yaml",
    "history/README.md",
    "review/README.md",
    "review/known_risks.md",
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "pyproject.toml",
]


def check_required_paths() -> list[str]:
    return [path for path in REQUIRED_PATHS if not (ROOT / path).exists()]


def check_capability_model_matches_schema() -> list[str]:
    problems = []
    try:
        import jsonschema

        schema = json.loads((ROOT / "capability" / "capability_model.schema.json").read_text())
        document = yaml.safe_load((ROOT / "capability" / "capability_model.yaml").read_text())
        jsonschema.Draft202012Validator(schema).validate(document)
    except Exception as exc:
        problems.append(f"capability model does not validate against its schema: {exc}")
    return problems


def check_ai_contract_lock() -> list[str]:
    import hashlib

    contract_text = (ROOT / "ai" / "ai_contract.yaml").read_text()
    lock = json.loads((ROOT / "ai" / "ai_contract.lock").read_text())
    digest = hashlib.sha256(contract_text.encode("utf-8")).hexdigest()
    if lock.get("contract_sha256") != digest:
        return ["ai/ai_contract.lock does not match ai/ai_contract.yaml; regenerate with scripts/generate_ai_contract.py"]
    return []


def main() -> int:
    problems = [f"missing required path: {path}" for path in check_required_paths()]
    if not problems:
        problems += check_capability_model_matches_schema()
        problems += check_ai_contract_lock()

    if problems:
        print("Structure validation FAILED:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("Structure validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
