from __future__ import annotations

import pytest

from keysight_n6700 import N6700, DriverConfigurationError


def test_default_configuration_validates_against_schema() -> None:
    drv = N6700()
    default = drv.get_driver_default_configuration()
    result = drv.validate_driver_configuration(default)
    assert result["valid"], result["errors"]


def test_invalid_configuration_is_rejected() -> None:
    drv = N6700()
    bad = drv.get_driver_default_configuration()
    bad["settings"]["timeouts"]["communication_timeout_s"] = -1
    result = drv.validate_driver_configuration(bad)
    assert not result["valid"]
    assert result["errors"]


def test_import_invalid_configuration_raises() -> None:
    drv = N6700()
    bad = drv.get_driver_default_configuration()
    bad["settings"]["transport"]["connection_type"] = "not-a-real-transport"
    with pytest.raises(DriverConfigurationError):
        drv.import_driver_configuration(bad, apply=True)


def test_import_and_export_round_trip(tmp_path) -> None:
    drv = N6700()
    document = drv.get_driver_default_configuration()
    document["settings"]["safety"]["auto_shutdown_on_disconnect"] = False
    drv.import_driver_configuration(document, apply=True)
    exported = drv.export_driver_configuration(tmp_path / "effective.json")
    assert exported["settings"]["safety"]["auto_shutdown_on_disconnect"] is False
    assert (tmp_path / "effective.json").exists()


def test_reset_restores_packaged_default() -> None:
    drv = N6700()
    document = drv.get_driver_default_configuration()
    document["settings"]["safety"]["auto_shutdown_on_disconnect"] = False
    drv.import_driver_configuration(document, apply=True)
    restored = drv.reset_driver_configuration()
    assert restored["settings"]["safety"]["auto_shutdown_on_disconnect"] is True
