"""Realizes the LPDS-019 protocol vectors against real hardware.

LPDS-001 §9 defines ``stable`` status as "LPDS-019 conformance passes
against real hardware." The simulator-based suite
(``tests/conformance/test_driver_call_protocol_conformance.py``) asserts
the *exact outbound SCPI text* by reading ``ScriptedScpiTransport.history``
— a simulator-only recording feature that a real VISA/TCP transport does
not expose. This module closes that gap by using ``connect(protocol_trace=
RecordingTraceObserver())`` (the same LPDS-008 tracing mechanism
``docs/architecture.md`` describes) to capture the real outbound bytes at
the transport boundary instead, and checking each vector's
``expected_outbound.command`` against *that*.

``tests/conformance/data/protocol_vectors.yaml`` is the single source of
truth for what a vector expects — this file does not fork it. Three kinds
of adjustment are made before a vector runs against real hardware:

1. **Simulator-only vectors are skipped** (``SIMULATOR_ONLY_VECTOR_IDS``):
   ``connect-simulated`` exercises the simulated-connection path
   specifically, and ``set-load-mode`` uses the simulator's placeholder
   ``SIM:LOAD:*`` syntax, not any real instrument's command set. Real
   N679xA load conformance is covered separately by
   ``test_hardware_load_sweep.py``, which uses the real ``FUNC``/``VOLT``/
   ``CURR``/``RES``/``POW``/``OUTP`` commands.
2. **Channel numbers are remapped** onto whatever channel this real
   mainframe actually has that module type on, since the vectors hardcode
   the bundled simulator's default map (channel 1 = power supply, channel
   2 = SMU). A vector needing a module type this mainframe doesn't have
   (most commonly a real SMU) is skipped, not faked.
3. **Invasive or output-energizing vectors are opt-in**, gated behind their
   own explicit signals below — no combination of the base HIL gate alone
   can reset the instrument or energize an output.

Disabled unless ``N6700_HIL_ENABLED=true`` and ``N6700_RESOURCE`` are both
set (see ``conftest.py``), **and** ``N6700_HIL_PROTOCOL_TEST_ENABLED=true``.
Additionally:

- ``N6700_HIL_PROTOCOL_ALLOW_RESET=yes`` — required to run the
  ``reset-device`` vector (``*RST``), which resets every channel on the
  mainframe, not just the one vector's target.
- ``N6700_HIL_PROTOCOL_OUTPUT_CONFIRM=yes`` — required to run the
  ``enable-output`` vector, which energizes a real output. Refuses to run
  it if that channel's output is already on.

Without either signal, the corresponding vector is skipped (reported, not
silently dropped) rather than the whole suite failing.
"""

from __future__ import annotations

import copy
import os
from collections.abc import Iterator
from contextlib import suppress
from typing import Any

import pytest
from scpi_driver_core.tracing import RecordingTraceObserver, TraceDirection

from keysight_n6700 import N6700
from keysight_n6700 import exceptions as n6700_exceptions
from tests.conformance.checks import check_return, check_type, load_yaml

from .conftest import CONNECTION_TYPE, PORT, RESOURCE, TIMEOUT_S, _truthy

VECTORS = load_yaml("protocol_vectors.yaml")["vectors"]

# connect-simulated exercises the simulated-connection path specifically;
# set-load-mode sends SIM:LOAD:MODE, syntax that only the bundled simulator
# understands. Neither has a real-hardware equivalent to check.
SIMULATOR_ONLY_VECTOR_IDS = {"connect-simulated", "set-load-mode"}

# These vectors' arguments/expected_outbound hardcode the simulator's
# default channel map (see protocol_vectors.yaml's header comment) and must
# be remapped onto this real mainframe's actual channel numbers.
POWER_CHANNEL_VECTOR_IDS = {
    "set-dc-voltage",
    "get-dc-voltage-setpoint",
    "set-dc-current",
    "enable-output",
    "disable-output",
    "get-output-state",
    "measure-dc-voltage",
    "measure-dc-current",
    "safe-shutdown",
}
SMU_CHANNEL_VECTOR_IDS = {"set-smu-mode", "get-smu-mode"}
SIM_POWER_CHANNEL = 1
SIM_SMU_CHANNEL = 2

RESET_VECTOR_ID = "reset-device"
OUTPUT_ENABLE_VECTOR_ID = "enable-output"

# channel_count() caches its result per session (driver.py); the shared
# connection this suite uses already triggered it once via connect()'s own
# discover_modules() before any vector runs, so calling it again from the
# vector legitimately sends nothing. Checked against the whole session's
# trace instead of just "since this call" for these IDs only.
CACHED_VECTOR_IDS = {"channel-count"}

# The vector's expected_return.equals is the bundled simulator's fixture
# topology (always 4 channels), not a real property of any instrument --
# a real mainframe reports however many modules are actually installed
# (2, here). Type-checked but not compared to the simulator's fixed value.
TOPOLOGY_DEPENDENT_RETURN_VECTOR_IDS = {"channel-count"}

# get_identity()'s *IDN? contains-check is inherently simulator-specific:
# the bundled simulator's fake identity is "KEYSIGHT TECHNOLOGIES,..."
# (uppercase), but real Keysight instruments answer "Keysight
# Technologies,..." (title case) -- confirmed against a real N6700C. The
# driver's own manufacturer validation (driver.py's _validate_identity)
# already uppercases before comparing, so this check does the same rather
# than fail on a real, correctly-identified instrument.
CASE_INSENSITIVE_CONTAINS_VECTOR_IDS = {"get-identity"}


def _truthy_env(name: str) -> bool:
    return _truthy(os.environ.get(name))


def _remap_channel(vector: dict[str, Any], sim_channel: int, real_channel: int) -> dict[str, Any]:
    """Copy ``vector`` with every reference to ``sim_channel`` replaced by ``real_channel``."""
    remapped = copy.deepcopy(vector)
    if "channel" in remapped.get("arguments", {}):
        remapped["arguments"]["channel"] = real_channel
    remapped["expected_outbound"]["command"] = remapped["expected_outbound"]["command"].replace(
        f"(@{sim_channel})", f"(@{real_channel})"
    )
    for cleanup in remapped.get("cleanup", []):
        if "channel" in cleanup.get("arguments", {}):
            cleanup["arguments"]["channel"] = real_channel
    return remapped


def _outbound_texts_since(recorder: RecordingTraceObserver, start_index: int) -> list[str]:
    return [
        e.text
        for e in recorder.events[start_index:]
        if e.direction == TraceDirection.TX and e.text is not None
    ]


def _all_outbound_texts(recorder: RecordingTraceObserver) -> list[str]:
    return [e.text for e in recorder.events if e.direction == TraceDirection.TX and e.text is not None]


@pytest.fixture(scope="module")
def traced_driver() -> Iterator[tuple[N6700, RecordingTraceObserver]]:
    recorder = RecordingTraceObserver()
    drv = N6700()
    drv.connect(
        RESOURCE or "",
        connection_type=CONNECTION_TYPE,
        port=PORT,
        timeout_s=TIMEOUT_S,
        discover=True,
        protocol_trace=recorder,
    )
    try:
        yield drv, recorder
    finally:
        # Guaranteed safety net independent of which vectors ran or failed.
        with suppress(Exception):
            drv.safe_shutdown()
        drv.disconnect()


@pytest.fixture(scope="module")
def channel_roles(traced_driver: tuple[N6700, RecordingTraceObserver]) -> dict[str, int | None]:
    driver, _recorder = traced_driver
    modules = driver.discover_modules()
    power = next((ch for ch, c in sorted(modules.items()) if c.module_type in {"power_supply", "smu"}), None)
    smu = next((ch for ch, c in sorted(modules.items()) if c.module_type == "smu"), None)
    return {"power": power, "smu": smu}


@pytest.mark.parametrize("vector", VECTORS, ids=lambda v: v["id"])
def test_protocol_vector_against_real_hardware(
    traced_driver: tuple[N6700, RecordingTraceObserver],
    channel_roles: dict[str, int | None],
    vector: dict[str, Any],
) -> None:
    if not _truthy_env("N6700_HIL_PROTOCOL_TEST_ENABLED"):
        pytest.skip("set N6700_HIL_PROTOCOL_TEST_ENABLED=true to run LPDS-019 vectors against real hardware")

    driver, recorder = traced_driver
    vector_id = vector["id"]

    if vector_id in SIMULATOR_ONLY_VECTOR_IDS:
        pytest.skip("simulator-only command/connection path, not applicable to real hardware")
    if vector_id == RESET_VECTOR_ID and not _truthy_env("N6700_HIL_PROTOCOL_ALLOW_RESET"):
        pytest.skip("set N6700_HIL_PROTOCOL_ALLOW_RESET=yes to include *RST (resets every channel)")
    if vector_id == OUTPUT_ENABLE_VECTOR_ID and not _truthy_env("N6700_HIL_PROTOCOL_OUTPUT_CONFIRM"):
        pytest.skip("set N6700_HIL_PROTOCOL_OUTPUT_CONFIRM=yes to energize a real output")

    if vector_id in POWER_CHANNEL_VECTOR_IDS:
        power_channel = channel_roles["power"]
        if power_channel is None:
            pytest.skip("no power-supply/SMU channel discovered on this mainframe")
        vector = _remap_channel(vector, SIM_POWER_CHANNEL, power_channel)
        if vector_id == OUTPUT_ENABLE_VECTOR_ID and driver.get_output_state(power_channel):
            pytest.fail(f"channel {power_channel} output is already ON; refusing to run enable-output vector")
    if vector_id in SMU_CHANNEL_VECTOR_IDS:
        smu_channel = channel_roles["smu"]
        if smu_channel is None:
            pytest.skip("no real SMU module discovered on this mainframe")
        vector = _remap_channel(vector, SIM_SMU_CHANNEL, smu_channel)

    start_index = len(recorder.events)
    method = getattr(driver, vector["method"])
    result = method(**vector["arguments"])

    expected_command = vector["expected_outbound"]["command"]
    sent = _outbound_texts_since(recorder, start_index)
    if not sent and vector_id in CACHED_VECTOR_IDS:
        sent = _all_outbound_texts(recorder)
    assert any(expected_command in text for text in sent), (
        f"expected {expected_command!r} in outbound trace, got {sent}"
    )

    expected_return = vector.get("expected_return")
    if expected_return and vector_id in CASE_INSENSITIVE_CONTAINS_VECTOR_IDS and "contains" in expected_return:
        check_type(result, expected_return["type"])
        assert expected_return["contains"].lower() in result.lower(), (
            f"expected {expected_return['contains']!r} (case-insensitive) in {result!r}"
        )
    elif expected_return and vector_id in TOPOLOGY_DEPENDENT_RETURN_VECTOR_IDS:
        check_type(result, expected_return["type"])
        assert result >= 1, f"expected at least one channel, got {result!r}"
    else:
        check_return(result, expected_return)

    if "protocol_error_check" in vector:
        check = vector["protocol_error_check"]
        exception_class = getattr(n6700_exceptions, check["raises"])
        with pytest.raises(exception_class):
            getattr(driver, check["method"])()

    for cleanup in vector.get("cleanup", []):
        getattr(driver, cleanup["method"])(**cleanup["arguments"])
