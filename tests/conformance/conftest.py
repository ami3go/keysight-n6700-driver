from __future__ import annotations

from collections.abc import Iterator
from typing import NamedTuple

import pytest
from scpi_driver_core.simulation.scripted import ScriptedScpiTransport

from keysight_n6700 import N6700


class ConformanceDriver(NamedTuple):
    driver: N6700
    transport: ScriptedScpiTransport


@pytest.fixture
def conformance_driver() -> Iterator[ConformanceDriver]:
    """A connected driver plus the scripted transport underneath it.

    ``transport.history`` is the actual decoded SCPI text sent, observed at
    the transport boundary rather than by mocking the driver's own methods —
    this is what makes the assertions in the conformance tests real protocol
    verification (LPDS-019 §10) instead of a tautology.
    """
    driver = N6700()
    driver.connect(connection_type="simulated", discover=False)
    transport = driver._session(None).transport
    assert isinstance(transport, ScriptedScpiTransport)
    transport.clear_history()
    try:
        yield ConformanceDriver(driver, transport)
    finally:
        driver.disconnect()
