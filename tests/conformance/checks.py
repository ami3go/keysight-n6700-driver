"""Shared LPDS-019 vector assertion helpers.

Used by both the simulator-based conformance suite
(``test_driver_call_protocol_conformance.py``) and the real-hardware
realization of the same vectors (``tests/hardware/
test_hardware_protocol_conformance.py``), so "what it means for a vector to
pass" can never silently diverge between the two.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DATA_DIR = Path(__file__).parent / "data"

_TYPE_NAMES: dict[str, type] = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "dict": dict,
    "list": list,
    "NoneType": type(None),
}


def load_yaml(name: str) -> dict[str, Any]:
    with (DATA_DIR / name).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def check_type(value: Any, expected: str) -> None:
    if expected in _TYPE_NAMES:
        assert isinstance(value, _TYPE_NAMES[expected]), f"expected {expected}, got {type(value)!r}"
    else:
        assert type(value).__name__ == expected, f"expected {expected}, got {type(value).__name__!r}"


def check_return(result: Any, expected_return: dict[str, Any] | None) -> None:
    if not expected_return:
        return
    check_type(result, expected_return["type"])
    if "equals" in expected_return:
        assert result == expected_return["equals"]
    if "contains" in expected_return:
        assert expected_return["contains"] in result
    if "one_of" in expected_return:
        assert result in expected_return["one_of"]
    if "field_equals" in expected_return:
        for field, value in expected_return["field_equals"].items():
            assert result[field] == value, f"{field}: expected {value!r}, got {result[field]!r}"
