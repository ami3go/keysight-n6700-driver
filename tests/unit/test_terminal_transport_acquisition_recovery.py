"""DRV-14: terminal transport faults must not strand an in-process digitizer lease.

All tests use the scripted simulator; no physical instrument is contacted.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from keysight_n6700 import N6700
from keysight_n6700.channel import ElectronicLoadChannel
from keysight_n6700.exceptions import (
    DriverConnectionError,
    DriverPreconditionError,
    DriverResourceBusyError,
    DriverResourceConflictError,
)
from keysight_n6700.module_capabilities import classify_module


def _load(driver: N6700) -> ElectronicLoadChannel:
    return ElectronicLoadChannel(driver, 3, classify_module("N6791A"))


def test_faulted_transport_releases_bookkeeping_without_claiming_safe_off(driver: N6700) -> None:
    channel = _load(driver)
    channel.configure_digitize("voltage", points=10, interval_s=20.48e-6)
    assert driver._digitize_leases.owner("default") is not None

    driver._session(None).transport.invalidate()
    with pytest.raises(DriverConnectionError):
        driver.query_scpi("OUTP? (@1)")
    assert driver._digitize_leases.owner("default") is None
    state = driver.get_connection_state()
    assert state["state"] == "faulted"
    assert state["communication_ok"] is None
    # Releasing host-side bookkeeping never verifies the physical output state.
    with pytest.raises(DriverPreconditionError):
        driver.query_scpi("OUTP? (@1)")
    with pytest.raises(DriverPreconditionError):
        channel.arm_digitize()

    driver.disconnect()
    driver.connect(connection_type="simulated")
    assert driver.query_scpi("OUTP? (@1)") in {"0", "1"}


def test_faulted_disconnection_without_intermediate_query(driver: N6700) -> None:
    channel = _load(driver)
    channel.configure_digitize("voltage", points=10, interval_s=20.48e-6)
    driver._session(None).transport.invalidate()
    driver.disconnect()
    assert driver._digitize_leases.owner("default") is None


def test_healthy_transport_does_not_release_foreign_capture(driver: N6700) -> None:
    channel = _load(driver)
    channel.configure_digitize("voltage", points=10, interval_s=20.48e-6)
    with pytest.raises(DriverResourceConflictError):
        driver.query_scpi("MEAS:VOLT? (@3)")
    assert driver._digitize_leases.owner("default") is not None

    with ThreadPoolExecutor(max_workers=1) as pool:
        def contend() -> None:
            with pytest.raises(DriverResourceBusyError):
                driver.query_scpi("MEAS:CURR? (@1)")
            with pytest.raises(DriverResourceBusyError):
                driver._begin_digitize(2)

        pool.submit(contend).result(timeout=4)
    assert driver._digitize_leases.owner("default") is not None
    channel.abort_digitize()


def test_transport_fault_invalidates_only_affected_alias(driver: N6700) -> None:
    driver.connect(alias="second", connection_type="simulated")
    driver.select("default")
    channel = _load(driver)
    channel.configure_digitize("voltage", points=10, interval_s=20.48e-6)

    driver._session("default").transport.invalidate()
    with pytest.raises(DriverConnectionError):
        driver.query_scpi("OUTP? (@1)", alias="default")
    assert driver._digitize_leases.owner("default") is None
    assert driver.query_scpi("OUTP? (@1)", alias="second") in {"0", "1"}
    assert driver.get_connection_state("second")["state"] == "connected"
    driver.disconnect(alias="default")
    driver.disconnect(alias="second")


def test_faulted_alias_replacement_permits_fresh_acquisition(driver: N6700) -> None:
    channel = _load(driver)
    channel.configure_digitize("voltage", points=10, interval_s=20.48e-6)
    driver._session(None).transport.invalidate()

    driver.connect(connection_type="simulated", replace=True)
    assert driver._digitize_leases.owner("default") is None
    assert driver.query_scpi("*IDN?").startswith("KEYSIGHT TECHNOLOGIES")
    fresh = _load(driver)
    fresh.configure_digitize("voltage", points=5, interval_s=20.48e-6)
    fresh.abort_digitize()


def test_repeated_transport_fault_cleanup_is_idempotent(driver: N6700) -> None:
    channel = _load(driver)
    channel.configure_digitize("voltage", points=10, interval_s=20.48e-6)
    driver._session(None).transport.invalidate()
    with pytest.raises(DriverConnectionError):
        driver.query_scpi("OUTP? (@1)")
    driver._reconcile_terminal_transport_lease("default")
    driver._reconcile_terminal_transport_lease("default")
    assert driver._digitize_leases.owner("default") is None
    driver.disconnect_all()
