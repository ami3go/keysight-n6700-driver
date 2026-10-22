"""Current ARB role, quadrant, compliance, and protection safety matrix."""

from __future__ import annotations

import pytest
from scpi_driver_core.simulation.scripted import ScriptedScpiTransport

from keysight_n6700 import (
    N6700,
    DriverArgumentValueError,
    DriverPreconditionError,
    DriverUnsupportedOperationError,
)
from keysight_n6700.module_capabilities import classify_module


@pytest.mark.parametrize(
    ("model", "role", "negative"),
    [
        ("N6751A", "current_limit", False),
        ("N6781A", "current_source", True),
        ("N6784A", "current_source", True),
        ("N6783A", "current_limit", False),
        ("UNKNOWN", "unsupported", False),
    ],
)
def test_current_programming_capability_matrix(model, role, negative) -> None:
    caps = classify_module(
        model, [], mainframe_model="N6705C", firmware_version="B.00.00"
    )
    assert caps.current_programming_role == role
    assert caps.supports_negative_current_arb is negative


def test_supply_current_arb_is_explicitly_current_limit_behavior(driver) -> None:
    transport = driver._session(None).transport
    assert isinstance(transport, ScriptedScpiTransport)
    transport.clear_history()
    config = driver.configure_current_arb(
        1, [0.1, 0.2], 0.001, maximum_current_a=0.5
    )
    assert config.levels_a == pytest.approx((0.1, 0.2))
    assert not any(command.startswith(("FUNC?", "VOLT:LIM?")) for command in transport.history)
    assert "VOLT? (@1)" in transport.history


def test_supply_rejects_negative_current_before_arb_mutation(driver) -> None:
    transport = driver._session(None).transport
    assert isinstance(transport, ScriptedScpiTransport)
    transport.clear_history()
    with pytest.raises(DriverArgumentValueError, match="negative-current ARB"):
        driver.configure_current_arb(
            1, [-0.1, 0.1], 0.001,
            minimum_current_a=-0.2, maximum_current_a=0.2,
        )
    assert not any(command.startswith("ARB:") for command in transport.history)


def test_smu_negative_current_arb_requires_priority_and_valid_compliance() -> None:
    driver = N6700.connect_simulated(models={1: "N6781A"}, mainframe_model="N6705C")
    try:
        transport = driver._session(None).transport
        assert isinstance(transport, ScriptedScpiTransport)
        transport.clear_history()
        with pytest.raises(DriverPreconditionError, match="current priority"):
            driver.configure_current_arb(
                1, [-0.2, 0.2], 0.001,
                minimum_current_a=-0.5, maximum_current_a=0.5,
            )
        assert not any(command.startswith("ARB:") for command in transport.history)

        smu = driver.smu(1)
        smu.configure_current_priority(0.0, 5.0)
        transport.clear_history()
        config = driver.configure_current_arb(
            1, [-0.2, 0.2], 0.001,
            minimum_current_a=-0.5, maximum_current_a=0.5,
        )
        assert config.levels_a == pytest.approx((-0.2, 0.2))
        assert "VOLT:LIM? (@1)" in transport.history
        assert "STAT:QUES:COND? (@1)" in transport.history

        # N6781A tops out at 20 V; a 0 V compliance is a valid setpoint but an
        # invalid envelope for a current-priority ARB.
        smu.set_voltage_limit(0.0)
        with pytest.raises(DriverPreconditionError, match="voltage compliance"):
            driver.configure_current_arb(
                1, [0.1], 0.001, maximum_current_a=0.5
            )
    finally:
        driver.disconnect()


def test_current_arb_rejects_active_protection_before_mutation(driver) -> None:
    transport = driver._session(None).transport
    assert isinstance(transport, ScriptedScpiTransport)
    transport.instrument.channels[1].questionable_status = 2
    transport.clear_history()
    with pytest.raises(DriverPreconditionError, match="cleared protection"):
        driver.configure_current_arb(1, [0.1], 0.001, maximum_current_a=0.5)
    assert not any(command.startswith("ARB:") for command in transport.history)


def test_application_specific_n6783_rejects_negative_current_arb() -> None:
    driver = N6700.connect_simulated(models={1: "N6783A"}, mainframe_model="N6705C")
    try:
        with pytest.raises(DriverArgumentValueError, match="negative-current ARB"):
            driver.configure_current_arb(
                1, [-0.1, 0.1], 0.001,
                minimum_current_a=-0.2, maximum_current_a=0.2,
            )
        with pytest.raises(DriverUnsupportedOperationError, match="selectable"):
            driver.smu(1).set_smu_mode("current")
    finally:
        driver.disconnect()
