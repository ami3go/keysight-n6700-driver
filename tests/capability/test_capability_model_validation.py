from __future__ import annotations

from pathlib import Path

import jsonschema
import yaml

from keysight_n6700 import N6700

ROOT = Path(__file__).resolve().parent.parent.parent


def test_capability_model_file_matches_live_driver_output() -> None:
    on_disk = yaml.safe_load((ROOT / "capability" / "capability_model.yaml").read_text())
    live = N6700().get_capability_model()
    assert on_disk["capabilities"] == live, (
        "capability/capability_model.yaml is stale; regenerate with "
        "`python scripts/generate_capability_model.py`"
    )


def test_capability_model_file_validates_against_its_schema() -> None:
    schema = yaml.safe_load((ROOT / "capability" / "capability_model.schema.json").read_text())
    document = yaml.safe_load((ROOT / "capability" / "capability_model.yaml").read_text())
    jsonschema.Draft202012Validator(schema).validate(document)


def test_driver_self_reports_a_valid_capability_model() -> None:
    result = N6700().validate_driver_capabilities()
    assert result["valid"], result["problems"]


def test_capability_binding_targets_exist_on_the_driver_class() -> None:
    for record in N6700().get_capability_model():
        assert hasattr(N6700, record["binding"]["python_method"])
