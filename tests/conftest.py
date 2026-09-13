from __future__ import annotations

from collections.abc import Iterator
from contextlib import suppress

import pytest

from keysight_n6700 import N6700


@pytest.fixture
def driver() -> Iterator[N6700]:
    """A driver connected to the bundled simulator, with modules discovered."""
    instrument = N6700.connect_simulated()
    try:
        yield instrument
    finally:
        with suppress(Exception):
            instrument.disconnect()
