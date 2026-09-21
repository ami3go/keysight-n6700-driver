from __future__ import annotations

import struct

import pytest
from scpi_driver_core.tracing.observer import RecordingTraceObserver

from keysight_n6700 import (
    DriverArgumentTypeError,
    DriverUnsupportedValueError,
    N6700,
    classify_module,
)
from keysight_n6700.scpi import parse_multi_binary_real_arrays


def _sim(driver: N6700, alias: str | None = None):
    transport = driver._session(alias).transport
    return getattr(transport, "instrument", None) or transport._inner.instrument


def _tx(observer: RecordingTraceObserver) -> list[str]:
    return [
        event.data.decode().strip()
        for event in observer.events
        if event.direction.value == "tx"
    ]


def test_n67_001_disconnect_actually_switches_outputs_off() -> None:
    driver = N6700.connect_simulated()
    simulator = _sim(driver)
    driver.enable_output(1)
    assert simulator.channels[1].enabled is True
    driver.disconnect()
    assert not any(channel.enabled for channel in simulator.channels.values())
    assert driver.get_last_shutdown_result() is not None
    assert driver.get_last_shutdown_result().success is True


def test_n67_003_raw_guard_does_not_block_typed_api_or_shutdown() -> None:
    driver = N6700.connect_simulated()
    try:
        driver.set_raw_scpi_guard("unlock")
        driver.set_dc_voltage(1.0, channel=1)
        driver.enable_output(1)
        result = driver.safe_shutdown()
        assert result["success"] is True
        with pytest.raises(Exception):
            driver.write_scpi("*RST")
    finally:
        driver.set_auto_shutdown_on_disconnect(False)
        driver.disconnect()


def test_n67_002_smu_uses_documented_limit_and_off_mode_commands() -> None:
    observer = RecordingTraceObserver()
    driver = N6700()
    driver.connect(connection_type="simulated", protocol_trace=observer)
    try:
        driver.configure_smu_voltage_priority(2, 5.0, 0.01)
        driver.configure_smu_current_priority(2, 0.01, 3.0)
        driver.smu(2).set_ovp(6.0)
        driver.smu(2).set_smu_output_off_mode("low_z")
        traffic = _tx(observer)
        assert "CURR:LIM 0.01,(@2)" in traffic
        assert "VOLT:LIM 3,(@2)" in traffic
        assert "VOLT:PROT:REM 6,(@2)" in traffic
        assert "OUTP:TMOD LOWZ,(@2)" in traffic
        assert not any(command.startswith("SIM:SMU") for command in traffic)
    finally:
        driver.disconnect()


def test_n67_005_range_parameter_cannot_inject_scpi() -> None:
    observer = RecordingTraceObserver()
    driver = N6700()
    driver.connect(connection_type="simulated", protocol_trace=observer)
    try:
        before = len(_tx(observer))
        with pytest.raises(DriverUnsupportedValueError):
            driver.power_supply(1).set_voltage_setpoint(
                1.0, voltage_range="MAX,(@1);*RST;OUTP ON"
            )
        assert len(_tx(observer)) == before
        with pytest.raises(DriverArgumentTypeError):
            driver.channel(True)  # type: ignore[arg-type]
    finally:
        driver.disconnect()


def test_n67_006_voltage_range_compound_resets_header_path() -> None:
    observer = RecordingTraceObserver()
    driver = N6700()
    driver.connect(connection_type="simulated", protocol_trace=observer)
    try:
        driver.power_supply(1).set_voltage_setpoint(3.0, voltage_range=5.0)
        assert "VOLT:RANG 5,(@1);:VOLT 3,(@1)" in _tx(observer)
        assert driver.get_dc_voltage_setpoint(1) == pytest.approx(3.0)
    finally:
        driver.disconnect()


def test_n67_007_shutdown_works_without_discovery_cache() -> None:
    driver = N6700()
    driver.connect(connection_type="simulated", discover=False)
    simulator = _sim(driver)
    driver.write_scpi("OUTP ON,(@1)")
    assert simulator.channels[1].enabled is True
    result = driver.shutdown_all()
    assert result.success is True
    assert simulator.channels[1].enabled is False
    driver.set_auto_shutdown_on_disconnect(False)
    driver.disconnect()


def test_n67_008_context_manager_closes_every_alias() -> None:
    driver = N6700()
    with driver:
        driver.connect(connection_type="simulated", alias="a")
        driver.connect(connection_type="simulated", alias="b")
        sim_a = _sim(driver, "a")
        sim_b = _sim(driver, "b")
        driver.enable_output(1, alias="a")
        driver.enable_output(1, alias="b")
    assert driver.list_connected_aliases() == []
    assert sim_a.channels[1].enabled is False
    assert sim_b.channels[1].enabled is False


def test_n67_009_aliases_are_normalized_consistently() -> None:
    driver = N6700()
    driver.connect(connection_type="simulated", alias=" Bench1 ")
    try:
        assert driver.get_current_alias() == "bench1"
        assert driver.get_identity().startswith("KEYSIGHT TECHNOLOGIES")
    finally:
        driver.disconnect_all()


def test_n67_016_remote_local_uses_real_scpi_command() -> None:
    observer = RecordingTraceObserver()
    driver = N6700()
    driver.connect(connection_type="simulated", protocol_trace=observer)
    try:
        driver.set_remote_state("remote_lockout")
        assert driver.get_remote_state() == "remote_lockout"
        assert "SYST:COMM:RLST RWL" in _tx(observer)
        assert "SYST:COMM:RLST?" in _tx(observer)
    finally:
        driver.disconnect()


def test_n67_017_io_watchdog_api_round_trip() -> None:
    driver = N6700.connect_simulated()
    try:
        driver.enable_io_watchdog(5.0)
        assert driver.get_io_watchdog() == {"enabled": True, "delay_s": 5.0}
        driver.disable_io_watchdog()
        assert driver.get_io_watchdog()["enabled"] is False
    finally:
        driver.disconnect()


def _block(payload: bytes) -> bytes:
    length = str(len(payload)).encode()
    return b"#" + str(len(length)).encode() + length + payload


def test_n67_018_binary_arrays_walk_headers_not_payload_commas() -> None:
    payload = struct.pack(">2f", 0.671875, 1.0)  # first float contains byte 0x2c
    data = _block(payload) + b"," + _block(payload)
    arrays = parse_multi_binary_real_arrays(data, 2)
    assert arrays[0] == pytest.approx([0.671875, 1.0])
    assert arrays[1] == pytest.approx([0.671875, 1.0])


def test_n67_019_only_exact_verified_load_models_are_real_enabled() -> None:
    assert classify_module("N6791A").verified_real_load_commands is True
    assert classify_module("N6792A").verified_real_load_commands is True
    assert classify_module("N6799Z").verified_real_load_commands is False
    assert classify_module("N6783A").module_type != "smu"
