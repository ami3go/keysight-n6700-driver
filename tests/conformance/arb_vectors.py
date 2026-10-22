"""Replayable golden SCPI vectors for the source ARB/LIST families (ARB-R05).

``data/arb_golden_vectors.yaml`` holds, per family, one driver call and the
exact ordered list of mutating SCPI commands it must send on the simulator.
The expected text is reviewed against the N6705 Programmer's Reference command
tree (``Keysight_documents/N6775A.pdf.md``), and ``vendor_command_tree``
below gives an independent check that every emitted header exists there, so a
vector can never merely snapshot a driver bug.

The same file is the contract a hardware qualification run replays (R06).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from scpi_driver_core.simulation.scripted import ScriptedScpiTransport

from keysight_n6700 import (
    N6700,
    ArbExponential,
    ArbPulse,
    ArbRamp,
    ArbSequenceStep,
    ArbSine,
    ArbStaircase,
    ArbStep,
    ArbTrapezoid,
    ArbUserDefined,
)

from .checks import load_yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
VENDOR_REFERENCE = REPO_ROOT / "Keysight_documents" / "N6775A.pdf.md"

_SHAPES: dict[str, Any] = {
    cls.__name__: cls
    for cls in (
        ArbStep, ArbRamp, ArbStaircase, ArbSine, ArbPulse, ArbTrapezoid, ArbExponential,
        ArbUserDefined,
    )
}


def load_vectors() -> list[dict[str, Any]]:
    return list(load_yaml("arb_golden_vectors.yaml")["vectors"])


def _resolve(value: Any) -> Any:
    if isinstance(value, dict) and "shape" in value:
        return _SHAPES[value["shape"]](*value.get("args", []), **value.get("kwargs", {}))
    if isinstance(value, dict) and "sequence_step" in value:
        spec = value["sequence_step"]
        return ArbSequenceStep(_resolve(spec["waveform"]), **spec.get("kwargs", {}))
    if isinstance(value, list):
        return [_resolve(item) for item in value]
    return value


def _call(driver: N6700, spec: dict[str, Any]) -> Any:
    args = [_resolve(item) for item in spec.get("args", [])]
    kwargs = {key: _resolve(item) for key, item in spec.get("kwargs", {}).items()}
    return getattr(driver, spec["method"])(*args, **kwargs)


def is_query(command: str) -> bool:
    return command.split(None, 1)[0].endswith("?")


def run_vector(vector: dict[str, Any]) -> list[str]:
    """Replay one vector on the simulator and return the mutating commands it sent."""
    options = dict(vector.get("simulator", {}))
    driver = N6700.connect_simulated(**options)
    try:
        for step in vector.get("prelude", []):
            _call(driver, step)
            driver.disable_output(1)
        transport = driver._session(None).transport
        assert isinstance(transport, ScriptedScpiTransport)
        transport.clear_history()
        _call(driver, vector["call"])
        return [command for command in transport.history if not is_query(command)]
    finally:
        driver.disconnect()


# -- independent oracle: the vendor command tree ------------------------------------

_OPTIONAL = re.compile(r"\[[^\[\]]*\]")


def _short(token: str) -> str:
    """SCPI short form: the leading upper-case letters (and digits) of a long-form token."""
    match = re.match(r"[A-Z0-9]+", token)
    return match.group(0) if match else token.upper()


def _expansions(syntax: str) -> set[tuple[str, ...]]:
    """All header token tuples a vendor syntax line allows, optional ``[...]`` parts on/off."""
    syntax = syntax.replace("[SOURce:]", "SOUR_OPT:").replace("[SOURce]", "")
    header = syntax.split(" ", 1)[0].split("?")[0]
    results: set[tuple[str, ...]] = set()
    bracket = re.search(r"\[(:?)([^\[\]]*)\]", header)
    if bracket is None:
        tokens = tuple(
            _short(token) for token in header.replace("SOUR_OPT:", "").split(":") if token
        )
        return {tokens} if tokens else set()
    for keep in (False, True):
        replacement = (":" + bracket.group(2)) if keep else ""
        results |= _expansions(header[: bracket.start()] + replacement + header[bracket.end():])
    return results


@lru_cache(maxsize=1)
def vendor_command_tree() -> frozenset[tuple[str, ...]]:
    commands: set[tuple[str, ...]] = set()
    for line in VENDOR_REFERENCE.read_text().splitlines():
        stripped = line.strip()
        if not re.match(r"^(\[SOURce:\]|MMEMory:|INITiate|ABORt|TRIGger|STATus:|OUTPut)", stripped):
            continue
        commands |= _expansions(stripped)
    return frozenset(commands)


def header_tokens(command: str) -> tuple[str, ...]:
    header = command.split(None, 1)[0]
    return tuple(_short(token) for token in header.split(":") if token)


def in_vendor_tree(command: str) -> bool:
    return header_tokens(command) in vendor_command_tree()
