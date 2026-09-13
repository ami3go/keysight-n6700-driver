"""HIL gating, per LPDS-009 §18: disabled by default, no silent fallback.

Every test under ``tests/hardware`` is skipped unless ``N6700_HIL_ENABLED=true``
*and* ``N6700_RESOURCE`` are both set — presence of a resource alone is not
enough, matching the two-signal gate ``scripts/run_hardware_self_check.py``
uses. Nothing here ever substitutes the simulator for a missing connection.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from keysight_n6700 import N6700


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


HIL_ENABLED = _truthy(os.environ.get("N6700_HIL_ENABLED"))
RESOURCE = os.environ.get("N6700_RESOURCE")
CONNECTION_TYPE = os.environ.get("N6700_CONNECTION_TYPE", "visa")
PORT = int(os.environ.get("N6700_PORT", "5025"))


@pytest.fixture(autouse=True, scope="package")
def _require_hil() -> None:
    if not (HIL_ENABLED and RESOURCE):
        pytest.skip(
            "hardware tests require N6700_HIL_ENABLED=true and N6700_RESOURCE to both be "
            "set (see docs/hardware_acceptance_tests.md)"
        )


@pytest.fixture(scope="module")
def hardware_driver() -> Iterator[N6700]:
    drv = N6700()
    drv.connect(RESOURCE or "", connection_type=CONNECTION_TYPE, port=PORT, discover=True)
    try:
        yield drv
    finally:
        drv.disconnect()
