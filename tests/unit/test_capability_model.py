from __future__ import annotations

from keysight_n6700 import N6700


def test_capability_model_is_non_empty_and_valid() -> None:
    drv = N6700()
    model = drv.get_capability_model()
    assert model
    for record in model:
        assert "." in record["capability_id"]
        assert record["risk_level"] in {"none", "low", "medium", "high", "critical"}
    result = drv.validate_driver_capabilities()
    assert result["valid"], result["problems"]


def test_mandatory_capability_identity_driver_read_is_declared() -> None:
    drv = N6700()
    assert drv.get_driver_capability("identity.driver.read") is not None


def test_find_driver_capabilities_filters_by_category() -> None:
    from keysight_n6700.capability_model import CapabilityQuery

    drv = N6700()
    source_caps = drv.find_driver_capabilities(CapabilityQuery(category="source"))
    assert source_caps
    assert all(record["category"] == "source" for record in source_caps)


def test_get_driver_features_matches_capability_model_ids() -> None:
    drv = N6700()
    ids = {record["capability_id"] for record in drv.get_capability_model()}
    assert set(drv.get_driver_features()) == ids


def test_unknown_capability_id_returns_none() -> None:
    drv = N6700()
    assert drv.get_driver_capability("nonexistent.capability.id") is None
