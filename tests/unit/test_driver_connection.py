from __future__ import annotations

import pytest

from keysight_n6700 import N6700, DriverConnectionError, DriverPreconditionError


def test_connect_simulated_discovers_modules() -> None:
    drv = N6700.connect_simulated()
    try:
        assert drv.is_connected()
        assert drv.channel_count() == 4
        assert set(drv.list_channels()) == {1, 2, 3, 4}
    finally:
        drv.disconnect()


def test_connect_returns_lpds002_connection_state_schema() -> None:
    drv = N6700.connect_simulated()
    try:
        info = drv.get_connection_state()
        assert info.keys() == {
            "alias",
            "resource",
            "connected",
            "communication_ok",
            "transport",
            "identity",
            "timeout_s",
            "state",
        }
        assert info["state"] == "connected"
        assert info["identity"]["manufacturer"] == "KEYSIGHT TECHNOLOGIES"
    finally:
        drv.disconnect()


def test_get_identity_returns_one_string() -> None:
    drv = N6700.connect_simulated()
    try:
        identity = drv.get_identity()
        assert identity.count(",") == 3
        assert identity.startswith("KEYSIGHT TECHNOLOGIES")
    finally:
        drv.disconnect()


def test_double_connect_same_alias_without_replace_fails() -> None:
    drv = N6700()
    drv.connect(connection_type="simulated", alias="a")
    try:
        with pytest.raises(DriverConnectionError):
            drv.connect(connection_type="simulated", alias="a")
    finally:
        drv.disconnect_all()


def test_double_connect_with_replace_succeeds() -> None:
    drv = N6700()
    drv.connect(connection_type="simulated", alias="a")
    try:
        drv.connect(connection_type="simulated", alias="a", replace=True)
        assert drv.is_connected("a")
    finally:
        drv.disconnect_all()


def test_operations_before_connect_raise_precondition_error() -> None:
    drv = N6700()
    with pytest.raises(DriverPreconditionError):
        drv.get_identity()


def test_multiple_aliases_are_independent_sessions() -> None:
    drv = N6700()
    drv.connect(connection_type="simulated", alias="a")
    drv.connect(connection_type="simulated", alias="b")
    try:
        drv.set_dc_voltage(5.0, 1, alias="a")
        drv.set_dc_voltage(9.0, 1, alias="b")
        assert drv.get_dc_voltage_setpoint(1, alias="a") == 5.0
        assert drv.get_dc_voltage_setpoint(1, alias="b") == 9.0
        assert set(drv.list_connected_aliases()) == {"a", "b"}
    finally:
        drv.disconnect_all()


def test_select_changes_the_default_alias() -> None:
    drv = N6700()
    drv.connect(connection_type="simulated", alias="a")
    drv.connect(connection_type="simulated", alias="b")
    try:
        drv.select("b")
        assert drv.get_current_alias() == "b"
        drv.set_dc_voltage(7.0, 1)  # no alias -> "b"
        assert drv.get_dc_voltage_setpoint(1, alias="b") == 7.0
    finally:
        drv.disconnect_all()


def test_disconnect_all_closes_every_session() -> None:
    drv = N6700()
    drv.connect(connection_type="simulated", alias="a")
    drv.connect(connection_type="simulated", alias="b")
    drv.disconnect_all()
    assert drv.list_connected_aliases() == []


def test_context_manager_disconnects_on_exit() -> None:
    with N6700.connect_simulated() as drv:
        assert drv.is_connected()
    assert not drv.is_connected()
