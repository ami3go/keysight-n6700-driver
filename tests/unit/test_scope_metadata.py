"""PR06 capability truthfulness and exact supported-model declarations."""
from __future__ import annotations

from keysight_n6700 import N6700, __version__
from keysight_n6700.module_capabilities import classify_module


def test_only_documented_n679_load_models_are_supported() -> None:
    for model in ("N6791A", "N6792A"):
        cap = classify_module(model)
        assert cap.module_type == "electronic_load"
        assert cap.supports_digitizing
        assert cap.supports_array_measurement
        assert cap.digitize_qualification == "vendor_declared"
    for model in ("N6799A", "N679", "N6790B", "SIM_LOAD"):
        cap = classify_module(model)
        assert not cap.supports_digitizing
        assert cap.digitize_qualification == "none"


def test_all_eight_digitize_methods_are_declared_mutating() -> None:
    driver = N6700()
    methods = {
        "configure_digitize",
        "configure_digitize_trigger",
        "arm_digitize",
        "wait_digitize_armed",
        "trigger_digitize",
        "wait_digitize_complete",
        "abort_digitize",
        "fetch_digitized_array",
    }
    model = driver.get_capability_model()
    digitize = [r for r in model if r["binding"]["python_method"] in methods]
    assert len(digitize) == 8
    assert {r["binding"]["python_method"] for r in digitize} == methods
    assert len({r["capability_id"] for r in digitize}) == 8
    assert all(r["mutating"] is True for r in digitize)
    assert all(r["risk_level"] == "low" for r in digitize)
    assert all(r["preconditions"] for r in digitize)


def test_runtime_metadata_version_is_package_version() -> None:
    assert N6700().metadata.version == __version__
