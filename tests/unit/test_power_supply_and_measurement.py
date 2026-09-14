from __future__ import annotations

import pytest

from keysight_n6700 import DriverArgumentValueError, DriverUnsupportedOperationError


def test_set_and_get_voltage_setpoint(driver) -> None:
    driver.set_dc_voltage(5.0, channel=1)
    assert driver.get_dc_voltage_setpoint(channel=1) == 5.0


def test_set_and_get_current_limit(driver) -> None:
    driver.set_dc_current(0.5, channel=1)
    assert driver.get_dc_current_setpoint(channel=1) == 0.5


def test_output_enable_disable_roundtrip(driver) -> None:
    driver.enable_output(1)
    assert driver.get_output_state(1) is True
    driver.output_should_be_enabled(1)
    driver.disable_output(1)
    assert driver.get_output_state(1) is False
    driver.output_should_be_disabled(1)


def test_measure_voltage_reflects_setpoint_when_enabled(driver) -> None:
    driver.set_dc_voltage(5.0, channel=1)
    driver.enable_output(1)
    assert driver.measure_dc_voltage(1) == pytest.approx(5.0)


def test_measure_all_returns_every_installed_channel(driver) -> None:
    measurements = driver.measure_all()
    assert set(measurements) == {1, 2, 3, 4}


def test_invalid_channel_raises_argument_error(driver) -> None:
    with pytest.raises(DriverArgumentValueError):
        driver.set_dc_voltage(5.0, channel=99)


def test_electronic_load_channel_rejects_power_supply_api(driver) -> None:
    # channel 3 is SIM_LOAD in the default simulator model map
    with pytest.raises(DriverUnsupportedOperationError):
        driver.power_supply(3)


def test_smu_channel_voltage_priority(driver) -> None:
    smu = driver.smu(2)
    smu.configure_voltage_priority(3.3, 0.2, output=False)
    assert smu.get_smu_mode() == "voltage"
    assert smu.get_voltage_setpoint() == pytest.approx(3.3)


def test_smu_channel_current_priority(driver) -> None:
    # Regression: set_smu_mode/get_smu_mode use plain FUNC, not FUNC:MODE,
    # which is not a valid N6700 command for any module family.
    smu = driver.smu(2)
    smu.configure_current_priority(0.1, 5.0, output=False)
    assert smu.get_smu_mode() == "current"
    assert smu.get_current_setpoint() == pytest.approx(0.1)


def test_load_channel_cc_mode(driver) -> None:
    load = driver.load(3)
    load.configure_cc(0.5, input_on=True)
    assert load.get_load_mode() == "cc"
    assert load.get_input() is True
