"""Hardware-in-the-loop acceptance tests against a real N6700.

Disabled unless ``N6700_HIL_ENABLED=true`` and ``N6700_RESOURCE`` are both
set (see ``conftest.py``). Everything here is read-only except
``test_guarded_output_enable_measure_disable``, which additionally requires
``N6700_HIL_OUTPUT_TEST_ENABLED=true``, ``N6700_HIL_OUTPUT_CHANNEL``,
``N6700_HIL_OUTPUT_VOLTAGE``, ``N6700_HIL_OUTPUT_CURRENT_LIMIT``, and
``N6700_HIL_OUTPUT_CONFIRM=yes`` — five separate explicit signals, so no
combination of defaults can accidentally energize a channel. See
``docs/hardware_acceptance_tests.md`` for the full variable reference.
"""

from __future__ import annotations

import os

import pytest

from keysight_n6700 import N6700, DriverError
from keysight_n6700.module_capabilities import ChannelCapabilities


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


# -- identity, connection, introspection -------------------------------------


def test_identity_reports_a_supported_manufacturer(hardware_driver: N6700) -> None:
    identity = hardware_driver.get_identity()
    assert identity.split(",")[0].strip()


def test_connection_state_reports_connected(hardware_driver: N6700) -> None:
    state = hardware_driver.get_connection_state(refresh=True)
    assert state["connected"] is True
    assert state["communication_ok"] is True


def test_self_test_completes(hardware_driver: N6700) -> None:
    result = hardware_driver.self_test()
    assert result.code is not None


def test_capability_and_configuration_introspection_are_consistent(hardware_driver: N6700) -> None:
    validation = hardware_driver.validate_driver_capabilities()
    assert validation["valid"], validation["problems"]
    schema = hardware_driver.get_driver_configuration_schema()
    default = hardware_driver.get_driver_default_configuration()
    assert hardware_driver.validate_driver_configuration(default)["valid"]
    assert schema["title"]


def test_error_queue_can_be_drained_without_raising(hardware_driver: N6700) -> None:
    errors = hardware_driver.get_all_device_errors()
    assert isinstance(errors, list)


# -- module discovery and per-channel checks ---------------------------------


def test_discover_modules_finds_at_least_one_channel(hardware_driver: N6700) -> None:
    modules = hardware_driver.discover_modules()
    assert modules
    assert set(modules) == set(hardware_driver.list_channels())


@pytest.fixture(scope="module")
def discovered_modules(hardware_driver: N6700) -> dict[int, ChannelCapabilities]:
    return hardware_driver.discover_modules()


def test_every_channel_reports_model_serial_and_options(
    hardware_driver: N6700, discovered_modules: dict[int, ChannelCapabilities]
) -> None:
    for channel in discovered_modules:
        assert hardware_driver.channel_model(channel)
        hardware_driver.channel_serial(channel)  # may legitimately be empty; must not raise
        hardware_driver.channel_options(channel)


def test_every_power_or_smu_channel_reports_setpoints_and_measurements(
    hardware_driver: N6700, discovered_modules: dict[int, ChannelCapabilities]
) -> None:
    for channel, caps in discovered_modules.items():
        if caps.module_type not in {"power_supply", "smu"}:
            continue
        assert isinstance(hardware_driver.get_dc_voltage_setpoint(channel), float)
        assert isinstance(hardware_driver.get_dc_current_setpoint(channel), float)
        assert isinstance(hardware_driver.measure_dc_voltage(channel), float)
        assert isinstance(hardware_driver.measure_dc_current(channel), float)
        assert isinstance(hardware_driver.get_output_state(channel), bool)


def test_every_channel_reports_protection_and_status(
    hardware_driver: N6700, discovered_modules: dict[int, ChannelCapabilities]
) -> None:
    for channel in discovered_modules:
        hardware_driver.channel(channel).get_protection_status()
        hardware_driver.get_operation_status(channel)
        hardware_driver.get_questionable_status(channel)


def test_measure_all_covers_every_discovered_channel(
    hardware_driver: N6700, discovered_modules: dict[int, ChannelCapabilities]
) -> None:
    measurements = hardware_driver.measure_all()
    assert set(measurements) == set(discovered_modules)


# -- guarded output test (five explicit signals required) -------------------


def test_guarded_output_enable_measure_disable(
    hardware_driver: N6700, discovered_modules: dict[int, ChannelCapabilities]
) -> None:
    if not _truthy(os.environ.get("N6700_HIL_OUTPUT_TEST_ENABLED")):
        pytest.skip("set N6700_HIL_OUTPUT_TEST_ENABLED=true to run the guarded output test")
    channel_raw = os.environ.get("N6700_HIL_OUTPUT_CHANNEL")
    voltage_raw = os.environ.get("N6700_HIL_OUTPUT_VOLTAGE")
    current_raw = os.environ.get("N6700_HIL_OUTPUT_CURRENT_LIMIT")
    if not (channel_raw and voltage_raw and current_raw):
        pytest.fail(
            "N6700_HIL_OUTPUT_TEST_ENABLED=true requires N6700_HIL_OUTPUT_CHANNEL, "
            "N6700_HIL_OUTPUT_VOLTAGE, and N6700_HIL_OUTPUT_CURRENT_LIMIT to all be set"
        )
    if not _truthy(os.environ.get("N6700_HIL_OUTPUT_CONFIRM")):
        pytest.fail("set N6700_HIL_OUTPUT_CONFIRM=yes to confirm you reviewed these exact parameters")

    channel = int(channel_raw)
    voltage = float(voltage_raw)
    current_limit = float(current_raw)
    caps = discovered_modules.get(channel)
    if caps is None or caps.module_type not in {"power_supply", "smu"}:
        pytest.fail(f"channel {channel} is not a discovered power-supply/SMU channel")
    if hardware_driver.get_output_state(channel):
        pytest.fail(f"channel {channel} output is already ON; refusing to touch it")

    try:
        hardware_driver.set_dc_voltage(voltage, channel)
        hardware_driver.set_dc_current(current_limit, channel)
        assert hardware_driver.get_dc_voltage_setpoint(channel) == pytest.approx(voltage)
        assert hardware_driver.get_dc_current_setpoint(channel) == pytest.approx(current_limit)

        hardware_driver.enable_output(channel)
        try:
            measured = hardware_driver.wait_for_voltage_in_range(
                channel, voltage * 0.95, voltage * 1.05, timeout_s=5.0
            )
        except DriverError:
            measured = hardware_driver.measure_dc_voltage(channel)
        assert measured == pytest.approx(voltage, rel=0.1)
    finally:
        hardware_driver.disable_output(channel)
        hardware_driver.safe_shutdown()
