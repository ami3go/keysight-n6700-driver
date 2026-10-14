from __future__ import annotations

import math

import pytest
from scpi_driver_core.simulation.scripted import ScriptedScpiTransport

from keysight_n6700 import (
    DriverArgumentValueError,
    DriverOperationUncertainError,
    DriverPreconditionError,
    DriverTimeoutError,
    VoltageArbConfiguration,
    VoltageListConfiguration,
)


def test_voltage_slew_round_trip_and_maximum(driver) -> None:
    assert driver.set_voltage_slew_rate(1, 500.0) == pytest.approx(500.0)
    assert driver.get_voltage_slew_rate(1) == pytest.approx(500.0)
    assert driver.set_voltage_slew_rate(1, "max") == pytest.approx(9.9e37)


@pytest.mark.parametrize("value", [0.0, -1.0, math.nan, math.inf, "slow"])
def test_voltage_slew_rejects_invalid_values(driver, value) -> None:
    with pytest.raises(DriverArgumentValueError):
        driver.set_voltage_slew_rate(1, value)


def test_configure_and_execute_voltage_list(driver) -> None:
    config = driver.configure_voltage_list(
        1,
        [12.0, 6.0, 12.0],
        [0.01, 0.02, 0.01],
        minimum_voltage_v=0.0,
        maximum_voltage_v=18.0,
        repeat_count=2,
    )
    assert isinstance(config, VoltageListConfiguration)
    assert config.levels_v == pytest.approx((12.0, 6.0, 12.0))
    assert config.dwell_s == pytest.approx((0.01, 0.02, 0.01))
    assert config.duration_s == pytest.approx(0.08)

    driver.set_dc_voltage(12.0, 1)
    driver.enable_output(1)
    token = driver.arm_voltage_transient(1)
    ready = driver.wait_voltage_transient_ready(1, token)
    assert ready.waiting_for_trigger is True
    driver.trigger_voltage_transient(1, token)
    complete = driver.wait_voltage_transient_complete(1, token)
    assert complete.state == "complete"
    assert complete.waiting_for_trigger is False
    assert complete.transient_active is False
    # terminate_last defaults to false, so the simulator returns to the DC value.
    assert driver.get_dc_voltage_setpoint(1) == pytest.approx(12.0)


def test_voltage_list_programming_is_an_ordered_verified_transaction(driver) -> None:
    transport = driver._session(None).transport
    assert isinstance(transport, ScriptedScpiTransport)
    transport.clear_history()
    driver.configure_voltage_list(
        1, [12.0, 6.0, 12.0], [0.01, 0.02, 0.01], maximum_voltage_v=18.0
    )
    expected = [
        "ABOR:TRAN (@1)",
        "INIT:CONT:TRAN OFF,(@1)",
        "VOLT:MODE FIX,(@1)",
        "LIST:VOLT 12,6,12,(@1)",
        "LIST:CURR 1,(@1)",
        "LIST:DWEL 0.01,0.02,0.01,(@1)",
        "LIST:TOUT:BOST 0,(@1)",
        "LIST:TOUT:EOST 0,(@1)",
        "LIST:COUN 1,(@1)",
        "LIST:STEP AUTO,(@1)",
        "LIST:TERM:LAST OFF,(@1)",
        "VOLT:MODE LIST,(@1)",
        "LIST:VOLT? (@1)",
        "LIST:DWEL? (@1)",
        "VOLT:MODE? (@1)",
    ]
    positions = [transport.history.index(command) for command in expected]
    assert positions == sorted(positions)


def test_configure_and_execute_user_defined_voltage_arb(driver) -> None:
    config = driver.configure_voltage_arb(
        1,
        [12.0, 4.0, 12.0],
        0.005,
        maximum_voltage_v=18.0,
        terminate_last=True,
    )
    assert isinstance(config, VoltageArbConfiguration)
    assert config.dwell_s == pytest.approx((0.005, 0.005, 0.005))

    driver.set_dc_voltage(12.0, 1)
    driver.enable_output(1)
    token = driver.arm_voltage_transient(1)
    driver.wait_voltage_transient_ready(1, token)
    driver.trigger_voltage_transient(1, token)
    driver.wait_voltage_transient_complete(1, token)
    assert driver.get_dc_voltage_setpoint(1) == pytest.approx(12.0)


def test_dwell_readback_accepts_only_documented_quantization(driver) -> None:
    channel = driver.power_supply(1)
    channel._assert_dwell_readback((0.1234567,), (0.123457,), "LIST dwell")
    with pytest.raises(DriverOperationUncertainError, match="dwell quantum"):
        channel._assert_dwell_readback((0.1234567,), (0.12346,), "LIST dwell")


def test_profile_configuration_requires_output_off(driver) -> None:
    driver.enable_output(1)
    with pytest.raises(DriverPreconditionError, match="output is OFF"):
        driver.configure_voltage_list(
            1, [12.0, 6.0, 12.0], 0.01, maximum_voltage_v=18.0
        )


def test_refused_reconfiguration_leaves_armed_profile_untouched(driver) -> None:
    driver.configure_voltage_list(1, [12.0, 6.0, 12.0], 0.01, maximum_voltage_v=18.0)
    driver.set_dc_voltage(12.0, 1)
    driver.enable_output(1)
    token = driver.arm_voltage_transient(1)
    driver.wait_voltage_transient_ready(1, token)
    transport = driver._session(None).transport
    transport.clear_history()
    with pytest.raises(DriverPreconditionError, match="output is OFF"):
        driver.configure_voltage_arb(1, [12.0, 4.0], 0.01, maximum_voltage_v=18.0)
    assert not any(cmd.startswith(("ABOR", "INIT", "VOLT:MODE")) for cmd in transport.history)
    driver.trigger_voltage_transient(1, token)
    assert driver.wait_voltage_transient_complete(1, token).state == "complete"


@pytest.mark.parametrize(
    ("levels", "dwells", "kwargs"),
    [
        ([12.0, 19.0], 0.01, {}),
        ([12.0, math.nan], 0.01, {}),
        ([12.0, 6.0], [0.01, 0.02, 0.03], {}),
        ([12.0, 6.0], 1.0, {"maximum_duration_s": 1.0}),
        ([12.0, 6.0], 0.01, {"repeat_count": 257}),
    ],
)
def test_list_profile_rejects_unsafe_or_inconsistent_input(driver, levels, dwells, kwargs) -> None:
    with pytest.raises(DriverArgumentValueError):
        driver.configure_voltage_list(
            1, levels, dwells, maximum_voltage_v=18.0, **kwargs
        )


def test_stale_transient_token_is_rejected(driver) -> None:
    driver.configure_voltage_list(1, [12.0, 6.0], 0.01, maximum_voltage_v=18.0)
    driver.set_dc_voltage(12.0, 1)
    driver.enable_output(1)
    first = driver.arm_voltage_transient(1)
    driver.abort_voltage_transient(1)
    second = driver.arm_voltage_transient(1)
    with pytest.raises(DriverPreconditionError, match="stale"):
        driver.wait_voltage_transient_ready(1, first)
    driver.abort_voltage_transient(1)
    assert second.generation == first.generation + 1


def test_completion_timeout_aborts_waiting_transient(driver) -> None:
    driver.configure_voltage_list(1, [12.0, 6.0], 0.01, maximum_voltage_v=18.0)
    driver.set_dc_voltage(12.0, 1)
    driver.enable_output(1)
    token = driver.arm_voltage_transient(1)
    driver.wait_voltage_transient_ready(1, token)
    with pytest.raises(DriverTimeoutError):
        driver.wait_voltage_transient_complete(
            1, token, timeout_s=0.001, poll_interval_s=0.0001
        )
    status = driver.get_voltage_transient_status(1)
    assert status.state == "aborted"
    assert status.operation_status & (16 | 64) == 0


def test_readiness_timeout_aborts_transient(driver, monkeypatch) -> None:
    driver.configure_voltage_list(1, [12.0, 6.0], 0.01, maximum_voltage_v=18.0)
    driver.set_dc_voltage(12.0, 1)
    driver.enable_output(1)
    token = driver.arm_voltage_transient(1)
    channel = driver.power_supply(1)
    original_query = channel._query

    def never_ready(command: str) -> str:
        if command.startswith("STAT:OPER:COND?"):
            return "0"
        return original_query(command)

    monkeypatch.setattr(channel, "_query", never_ready)
    with pytest.raises(DriverTimeoutError) as error:
        driver.wait_voltage_transient_ready(
            1, token, timeout_s=0.001, poll_interval_s=0.0001
        )
    assert error.value.details["voltage_transient_cleanup"] == "aborted"
    assert driver.get_voltage_transient_status(1).state == "aborted"


def test_safe_shutdown_aborts_transient_and_disables_output(driver) -> None:
    driver.configure_voltage_list(1, [12.0, 6.0], 0.01, maximum_voltage_v=18.0)
    driver.set_dc_voltage(12.0, 1)
    driver.enable_output(1)
    driver.arm_voltage_transient(1)
    result = driver.safe_shutdown()
    assert result["success"] is True
    assert driver.get_output_state(1) is False
    assert driver.get_dc_voltage_setpoint(1) == pytest.approx(12.0)
    assert driver.get_voltage_transient_status(1).operation_status & (16 | 64) == 0


def test_arb_profile_has_documented_point_limit(driver) -> None:
    with pytest.raises(DriverArgumentValueError, match="511"):
        driver.configure_voltage_arb(
            1, [1.0] * 512, 0.001, maximum_voltage_v=18.0
        )
