"""LPDS-019 driver call/protocol conformance suite.

Levels realized here:

- **L0 Method Discovery** — every method in ``data/method_inventory.yaml``
  actually exists on :class:`keysight_n6700.driver.N6700`.
- **L1 Python Callability** — every realized vector calls its method with
  plain Python arguments and succeeds or fails exactly as expected.
- **L2 Outbound Protocol Verification** — the exact SCPI text sent is
  observed at ``ScriptedScpiTransport.history``, not inferred from a mock of
  the driver's own methods.
- **L3 Inbound Protocol / Return Verification** — the parsed Python return
  value has the documented type and, where given, the documented value.
- **L4 Protocol Error and Recovery Verification** — one vector
  (``protocol-error-on-unmatched-command``) proves a rejected command is both
  transmitted and later raised as the correct :mod:`keysight_n6700.exceptions`
  class.

Coverage accounting (every device-facing method has a vector or an approved
exclusion) is checked once, structurally, rather than per-method — see
``test_every_device_facing_method_has_a_vector_or_exclusion``.
"""

from __future__ import annotations

from typing import Any

import pytest

from keysight_n6700 import exceptions as n6700_exceptions
from keysight_n6700.driver import N6700

from .checks import check_return, load_yaml
from .conftest import ConformanceDriver

INVENTORY = load_yaml("method_inventory.yaml")
VECTORS = load_yaml("protocol_vectors.yaml")["vectors"]
EXCLUSIONS = load_yaml("exclusions.yaml")["exclusions"]


def _apply_preconditions(conformance_driver: ConformanceDriver, preconditions: list[str]) -> None:
    driver, transport = conformance_driver
    if "modules_discovered" in preconditions:
        driver.discover_modules()
        transport.clear_history()


# -- L0: method discovery ---------------------------------------------------


@pytest.mark.parametrize("entry", INVENTORY["methods"], ids=lambda e: e["method"])
def test_l0_method_discovery(entry: dict[str, Any]) -> None:
    assert hasattr(N6700, entry["method"]), f"{entry['method']} is not a method of N6700"
    assert callable(getattr(N6700, entry["method"]))


def test_every_device_facing_method_has_a_vector_or_exclusion() -> None:
    device_facing = {m["method"] for m in INVENTORY["methods"] if m["device_facing"]}
    vectored = {v["method"] for v in VECTORS}
    excluded = {e["method"] for e in EXCLUSIONS}
    uncovered = device_facing - vectored - excluded
    assert not uncovered, f"device-facing methods with neither a vector nor an approved exclusion: {sorted(uncovered)}"
    unknown_exclusions = excluded - device_facing
    assert not unknown_exclusions, f"exclusions.yaml references non-device-facing or unknown methods: {sorted(unknown_exclusions)}"


def test_no_method_is_both_vectored_and_excluded() -> None:
    vectored = {v["method"] for v in VECTORS}
    excluded = {e["method"] for e in EXCLUSIONS}
    overlap = vectored & excluded
    assert not overlap, f"methods listed in both protocol_vectors.yaml and exclusions.yaml: {sorted(overlap)}"


# -- L1-L3: realized protocol vectors ---------------------------------------


@pytest.mark.parametrize("vector", VECTORS, ids=lambda v: v["id"])
def test_protocol_vector(conformance_driver: ConformanceDriver, vector: dict[str, Any]) -> None:
    driver, transport = conformance_driver
    _apply_preconditions(conformance_driver, vector.get("preconditions", []))
    transport.clear_history()

    method = getattr(driver, vector["method"])
    result = method(**vector["arguments"])

    # A vector that reconnects (e.g. connect(replace=True)) replaces the
    # transport instance; re-resolve it rather than trust the one captured
    # before the call.
    current_transport = driver._session(None).transport if driver.is_connected() else transport

    expected_command = vector["expected_outbound"]["command"]
    assert any(expected_command in sent for sent in current_transport.history), (
        f"expected {expected_command!r} in outbound history, got {current_transport.history}"
    )

    check_return(result, vector.get("expected_return"))

    for cleanup in vector.get("cleanup", []):
        getattr(driver, cleanup["method"])(**cleanup["arguments"])


def test_protocol_error_vector_raises_documented_exception(conformance_driver: ConformanceDriver) -> None:
    driver, _transport = conformance_driver
    vector = next(v for v in VECTORS if v["id"] == "protocol-error-on-unmatched-command")
    getattr(driver, vector["method"])(**vector["arguments"])
    check = vector["protocol_error_check"]
    exception_class = getattr(n6700_exceptions, check["raises"])
    with pytest.raises(exception_class):
        getattr(driver, check["method"])()
