"""PR04: acquisition lease protects buffers across SCPI transactions."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from keysight_n6700 import N6700
from keysight_n6700.channel import ElectronicLoadChannel
from keysight_n6700.exceptions import DriverResourceBusyError, DriverResourceConflictError
from keysight_n6700.module_capabilities import classify_module


def load(driver: N6700, channel: int = 3) -> ElectronicLoadChannel:
    return ElectronicLoadChannel(driver, channel, classify_module("N6791A"))


def test_measure_and_reset_are_blocked_until_fetch(driver: N6700) -> None:
    ch = load(driver)
    ch.configure_digitize("voltage", points=10, interval_s=20.48e-6)
    with pytest.raises(DriverResourceConflictError):
        driver.query_scpi("MEAS:VOLT? (@3)")
    with pytest.raises(DriverResourceConflictError):
        driver.write_scpi("*RST")
    with pytest.raises(DriverResourceBusyError):
        driver.discover_modules()
    ch.arm_digitize()
    ch.wait_digitize_armed()
    ch.trigger_digitize()
    ch.wait_digitize_complete(1.0)
    assert len(ch.fetch_digitized_array().values) == 10
    assert driver.query_scpi("MEAS:VOLT? (@3)") != ""


def test_cross_thread_measurement_denied_emergency_off_allowed(driver: N6700) -> None:
    ch = load(driver)
    ch.configure_digitize("voltage", points=10, interval_s=20.48e-6)

    def contend() -> tuple[bool, bool]:
        blocked = False
        with pytest.raises(DriverResourceBusyError):
            driver.query_scpi("MEAS:CURR? (@1)")
            blocked = True
        driver.write_scpi("OUTP OFF,(@3)")
        return blocked, driver.query_scpi("STAT:OPER:COND? (@3)") == "0"

    with ThreadPoolExecutor(max_workers=1) as pool:
        blocked, idle = pool.submit(contend).result(timeout=4)
    assert blocked and idle
    ch.abort_digitize()


def test_second_channel_cannot_steal_ownership(driver: N6700) -> None:
    first = load(driver, 3)
    other = load(driver, 2)
    first.configure_digitize("voltage", points=10, interval_s=20.48e-6)
    with pytest.raises(DriverResourceBusyError):
        other.configure_digitize("voltage", points=10, interval_s=20.48e-6)
    first.abort_digitize()
    other.configure_digitize("voltage", points=10, interval_s=20.48e-6)
    other.abort_digitize()


def test_different_mainframe_alias_is_not_blocked(driver: N6700) -> None:
    driver.connect(alias="other", connection_type="simulated")
    driver.select("default")
    ch = load(driver)
    ch.configure_digitize("voltage", points=10, interval_s=20.48e-6)
    assert driver.query_scpi("MEAS:VOLT? (@3)", alias="other") != ""
    ch.abort_digitize()


def test_discovery_and_disconnect_cannot_invalidate_acquisition(driver: N6700) -> None:
    ch = load(driver)
    ch.configure_digitize("voltage", points=10, interval_s=20.48e-6)
    with pytest.raises(DriverResourceBusyError):
        driver.disconnect()
    with pytest.raises(DriverResourceBusyError):
        driver.discover_modules()
    ch.abort_digitize()
    driver.discover_modules()


def test_lease_is_released_after_verified_abort(driver: N6700) -> None:
    ch = load(driver)
    ch.configure_digitize("voltage", points=10, interval_s=20.48e-6)
    ch.abort_digitize()
    assert driver._digitize_leases.owner("default") is None
    assert driver.query_scpi("MEAS:VOLT? (@3)") != ""
