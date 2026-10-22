"""ARB-R05: vendor-derived boundary matrix and generated readback/fault matrix.

Boundaries come from the N6705 Programmer's Reference parameter tables
(``Keysight_documents/N6775A.pdf.md``); the quoted range sits next to each
case.  The fault matrix is *generated* from the golden vectors: for every
readback query a configuration issues, it injects a malformed reply, a
mismatching reply and a timeout, and requires the same safe outcome each time.
Combinations the simulator cannot judge are listed separately at the bottom as
skips, never as passes.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import pytest
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
    DriverArgumentValueError,
    DriverError,
    DriverTimeoutError,
)
from tests.conformance.arb_vectors import _call, is_query, load_vectors

BIG = {"maximum_duration_s": 1.0e7}


def _mutating(driver: N6700) -> list[str]:
    transport = driver._session(None).transport
    assert isinstance(transport, ScriptedScpiTransport)
    return [c for c in transport.history if not is_query(c)]


def _reject(driver: N6700, build: Callable[[N6700], Any]) -> None:
    transport = driver._session(None).transport
    assert isinstance(transport, ScriptedScpiTransport)
    transport.clear_history()
    with pytest.raises(DriverArgumentValueError):
        build(driver)
    assert _mutating(driver) == [], "a rejected request must not reach the instrument"


def _accept(driver: N6700, build: Callable[[N6700], Any]) -> None:
    assert build(driver) is not None
    driver.disable_output(1)


def _wave(shape: Any, **kwargs: Any) -> Callable[[N6700], Any]:
    return lambda d: d.configure_voltage_arb_waveform(1, shape, maximum_voltage_v=10.0, **BIG, **kwargs)


# -- boundaries: parametric shape times (0 - 262.144 s on every time parameter) ---------

TIME_CASES = [
    ("step.start_time_s", lambda t: ArbStep(1.0, 5.0, t)),
    ("ramp.rise_time_s", lambda t: ArbRamp(1.0, 5.0, t)),
    ("ramp.start_time_s", lambda t: ArbRamp(1.0, 5.0, 0.01, start_time_s=t)),
    ("ramp.end_time_s", lambda t: ArbRamp(1.0, 5.0, 0.01, end_time_s=t)),
    ("staircase.time_s", lambda t: ArbStaircase(1.0, 5.0, 5, t)),
    ("pulse.top_time_s", lambda t: ArbPulse(1.0, 5.0, t)),
    ("trapezoid.rise_time_s", lambda t: ArbTrapezoid(1.0, 5.0, t, 0.01, 0.01)),
    ("trapezoid.top_time_s", lambda t: ArbTrapezoid(1.0, 5.0, 0.01, t, 0.01)),
    ("trapezoid.fall_time_s", lambda t: ArbTrapezoid(1.0, 5.0, 0.01, 0.01, t)),
    ("exponential.time_s", lambda t: ArbExponential(1.0, 5.0, t, 0.002)),
    ("exponential.time_constant_s", lambda t: ArbExponential(1.0, 5.0, 0.01, t)),
]


@pytest.mark.parametrize(("name", "build"), TIME_CASES, ids=[c[0] for c in TIME_CASES])
@pytest.mark.parametrize("value", [0.0, 0.001, 262.144])
def test_time_parameter_accepts_its_documented_range(driver, name, build, value) -> None:
    _accept(driver, _wave(build(value)))


@pytest.mark.parametrize(("name", "build"), TIME_CASES, ids=[c[0] for c in TIME_CASES])
@pytest.mark.parametrize("value", [-1e-9, 262.1441, 1e6, math.nan, math.inf, -math.inf])
def test_time_parameter_rejects_values_outside_its_range(driver, name, build, value) -> None:
    with pytest.raises(DriverArgumentValueError):
        build(value)
    assert _mutating(driver) == []


# -- boundaries: sine frequency 3.8147E-5 - 1.0E4 Hz, staircase NSTeps 0 - 500 -----------


@pytest.mark.parametrize("hz", [3.8147e-5, 1.0, 1.0e4])
def test_sine_frequency_accepts_the_documented_range(driver, hz) -> None:
    _accept(driver, _wave(ArbSine(2.0, 5.0, hz)))


@pytest.mark.parametrize("hz", [3.8146e-5, 0.0, -1.0, 1.00001e4, math.nan, math.inf])
def test_sine_frequency_rejects_values_outside_the_range(driver, hz) -> None:
    with pytest.raises(DriverArgumentValueError):
        _wave(ArbSine(2.0, 5.0, hz))(driver)
    assert _mutating(driver) == []


@pytest.mark.parametrize("steps", [1, 2, 500])
def test_staircase_accepts_one_to_five_hundred_steps(driver, steps) -> None:
    _accept(driver, _wave(ArbStaircase(1.0, 5.0, steps, 0.01)))


@pytest.mark.parametrize("steps", [0, -1, 501, 2.5, True, math.nan])
def test_staircase_rejects_other_step_counts(driver, steps) -> None:
    # The vendor table allows 0, which would program a staircase with no steps;
    # the driver deliberately refuses it.
    with pytest.raises(DriverArgumentValueError):
        _wave(ArbStaircase(1.0, 5.0, steps, 0.01))(driver)
    assert _mutating(driver) == []


# -- boundaries: level envelope ---------------------------------------------------------


@pytest.mark.parametrize("peak", [10.0])
def test_level_exactly_at_the_envelope_is_accepted(driver, peak) -> None:
    _accept(driver, _wave(ArbRamp(0.0, peak, 0.01)))


@pytest.mark.parametrize("peak", [10.0001, 11.0, 1e9])
def test_level_above_the_envelope_is_rejected_before_any_write(driver, peak) -> None:
    _reject(driver, _wave(ArbRamp(0.0, peak, 0.01)))


@pytest.mark.parametrize("low", [-0.0001, -1.0])
def test_negative_level_on_a_unipolar_supply_is_rejected_before_any_write(driver, low) -> None:
    _reject(driver, _wave(ArbRamp(low, 5.0, 0.01)))


# -- boundaries: repeat counts (ARB:COUNt 1-16,777,216; CDW 1-256; LIST 1-256; SEQ 1-4096)


def _udef(count: Any) -> Callable[[N6700], Any]:
    return lambda d: d.configure_voltage_arb(
        1, [1.0, 2.0], [0.01], maximum_voltage_v=10.0, repeat_count=count, **BIG
    )


def _cdw(count: Any) -> Callable[[N6700], Any]:
    return lambda d: d.configure_voltage_constant_dwell_arb(
        1, [1.0, 2.0], 0.01, maximum_voltage_v=10.0, repeat_count=count, **BIG
    )


def _list(count: Any) -> Callable[[N6700], Any]:
    return lambda d: d.configure_voltage_list(
        1, [1.0, 2.0], [0.01], maximum_voltage_v=10.0, repeat_count=count, **BIG
    )


def _sequence(count: Any) -> Callable[[N6700], Any]:
    steps = [ArbSequenceStep(ArbStep(1.0, 2.0, 0.01))]
    return lambda d: d.configure_voltage_arb_sequence(
        1, steps, maximum_voltage_v=10.0, sequence_count=count, **BIG
    )


COUNT_CASES = [
    ("single-arb", _udef, 16_777_216),
    ("constant-dwell", _cdw, 256),
    ("list", _list, 256),
    ("sequence", _sequence, 4096),
]
# A sequence is finite through the normal API; an infinite one needs a loaded file
# with allow_infinite=True.
INFINITE_OK = {"single-arb", "constant-dwell", "list"}


@pytest.mark.parametrize(("family", "make", "maximum"), COUNT_CASES, ids=[c[0] for c in COUNT_CASES])
def test_repeat_count_accepts_one_and_the_documented_maximum(driver, family, make, maximum) -> None:
    _accept(driver, make(1))
    _accept(driver, make(maximum))
    if family in INFINITE_OK:
        _accept(driver, make("infinite"))
    else:
        _reject(driver, make("infinite"))


@pytest.mark.parametrize(("family", "make", "maximum"), COUNT_CASES, ids=[c[0] for c in COUNT_CASES])
@pytest.mark.parametrize("bad", ["zero", "negative", "over", "fraction", "bool", "text"])
def test_repeat_count_rejects_everything_else(driver, family, make, maximum, bad) -> None:
    value = {
        "zero": 0, "negative": -1, "over": maximum + 1, "fraction": 1.5, "bool": True, "text": "forever",
    }[bad]
    _reject(driver, make(value))


# -- boundaries: point counts (UDEF 511, LIST 512, CDW 65,535, sequence 100 steps) ------


@pytest.mark.parametrize(("points", "ok"), [(1, True), (511, True), (512, False), (0, False)])
def test_user_defined_point_limit(driver, points, ok) -> None:
    build = lambda d: d.configure_voltage_arb(  # noqa: E731
        1, [1.0 + (i % 2) for i in range(points)], [0.01], maximum_voltage_v=10.0, **BIG
    )
    _accept(driver, build) if ok else _reject(driver, build)


@pytest.mark.parametrize(("points", "ok"), [(1, True), (512, True), (513, False), (0, False)])
def test_list_point_limit(driver, points, ok) -> None:
    build = lambda d: d.configure_voltage_list(  # noqa: E731
        1, [1.0 + (i % 2) for i in range(points)], [0.01], maximum_voltage_v=10.0, **BIG
    )
    _accept(driver, build) if ok else _reject(driver, build)


@pytest.mark.parametrize("points", [65_536, 100_000])
def test_constant_dwell_point_limit(driver, points) -> None:
    _reject(
        driver,
        lambda d: d.configure_voltage_constant_dwell_arb(
            1, [1.0] * points, 0.01, maximum_voltage_v=10.0, **BIG
        ),
    )


@pytest.mark.parametrize(("steps", "ok"), [(1, True), (100, True), (101, False), (0, False)])
def test_sequence_step_limit(driver, steps, ok) -> None:
    sequence = [ArbSequenceStep(ArbStep(1.0, 2.0, 0.001)) for _ in range(steps)]
    build = lambda d: d.configure_voltage_arb_sequence(  # noqa: E731
        1, sequence, maximum_voltage_v=10.0, **BIG
    )
    _accept(driver, build) if ok else _reject(driver, build)


# -- boundaries: dwell (UDEF/LIST 0 - 262.144 s; CDW 10.24 us - 0.30 s) ---------------------


@pytest.mark.parametrize("dwell", [0.0, 262.144])
def test_udef_and_list_dwell_accept_the_documented_range(driver, dwell) -> None:
    _accept(driver, lambda d: d.configure_voltage_arb(1, [1.0, 2.0], [dwell], maximum_voltage_v=10.0, **BIG))
    _accept(driver, lambda d: d.configure_voltage_list(1, [1.0, 2.0], [dwell], maximum_voltage_v=10.0, **BIG))


@pytest.mark.parametrize("dwell", [-1e-9, 262.1441, math.nan, math.inf])
def test_udef_and_list_dwell_reject_values_outside_the_range(driver, dwell) -> None:
    _reject(driver, lambda d: d.configure_voltage_arb(1, [1.0, 2.0], [dwell], maximum_voltage_v=10.0, **BIG))
    _reject(driver, lambda d: d.configure_voltage_list(1, [1.0, 2.0], [dwell], maximum_voltage_v=10.0, **BIG))


@pytest.mark.parametrize("dwell", [10.24e-6, 0.001, 0.30])
def test_constant_dwell_accepts_its_documented_range(driver, dwell) -> None:
    _accept(driver, lambda d: d.configure_voltage_constant_dwell_arb(1, [1.0, 2.0], dwell, maximum_voltage_v=10.0, **BIG))


@pytest.mark.parametrize("dwell", [0.0, 1e-6, 10.0e-6, 0.3001, -1.0, math.nan])
def test_constant_dwell_rejects_values_outside_its_range(driver, dwell) -> None:
    _reject(driver, lambda d: d.configure_voltage_constant_dwell_arb(1, [1.0, 2.0], dwell, maximum_voltage_v=10.0, **BIG))


# -- generated readback / fault matrix ----------------------------------------------------

VECTORS = [v for v in load_vectors() if v["family"] != "sequence-file"]
CLEANUP = ("INIT:CONT:TRAN OFF,(@1)", "ABOR:TRAN (@1)", "VOLT:MODE FIX,(@1)", "CURR:MODE FIX,(@1)")


def _prepared(vector: dict[str, Any]) -> N6700:
    driver = N6700.connect_simulated(**vector.get("simulator", {}))
    for step in vector.get("prelude", []):
        _call(driver, step)
        driver.disable_output(1)
    return driver


def _readbacks(vector: dict[str, Any]) -> list[tuple[str, str]]:
    """(query, original reply) for every parameter readback the call issues."""
    driver = _prepared(vector)
    try:
        transport = driver._session(None).transport
        assert isinstance(transport, ScriptedScpiTransport)
        transport.clear_history()
        _call(driver, vector["call"])
        written = {c.split(None, 1)[0] for c in transport.history if not is_query(c)}
        queries: list[str] = []
        for command in transport.history:
            header = command.split(None, 1)[0]
            if is_query(command) and header[:-1] in written and command not in queries:
                queries.append(command)
        return [(q, driver.query_scpi(q)) for q in queries]
    finally:
        driver.disconnect()


CASES = [
    pytest.param(vector, query, original, fault, id=f"{vector['id']}|{query}|{fault}")
    for vector in VECTORS
    for query, original in _readbacks(vector)
    for fault in ("malformed", "mismatch", "timeout")
]


def _mismatch(original: str) -> str:
    try:
        value = float(original.split(",")[0])
    except ValueError:
        return "XYZ"
    # 9.9E37 and above is how the instrument reports INFinity, so "twice as big"
    # would still mean the same thing; use a small finite count instead.
    return "5" if value >= 9.0e37 else repr(value * 2.0 + 1.0)


@pytest.mark.parametrize(("vector", "query", "original", "fault"), CASES)
def test_bad_readback_aborts_cleanly_and_never_energizes(vector, query, original, fault) -> None:
    driver = _prepared(vector)
    try:
        transport = driver._session(None).transport
        assert isinstance(transport, ScriptedScpiTransport)
        channel = driver.power_supply(1)
        attempts: list[int] = []  # history length at each attempt of the faulted query
        if fault == "timeout":
            real_query = channel._query

            def failing(command: str, *args: Any, **kwargs: Any) -> str:
                if command == query:
                    attempts.append(len(transport.history))
                    raise DriverTimeoutError("injected readback timeout")
                return real_query(command, *args, **kwargs)

            channel._query = failing  # type: ignore[method-assign]
        else:
            transport.on(query, "not-a-number" if fault == "malformed" else _mismatch(original))
        transport.clear_history()
        with pytest.raises(DriverError):
            _call(driver, vector["call"])

        history = list(transport.history)
        if fault != "timeout":
            attempts = [i for i, c in enumerate(history) if c == query]
        assert channel._transient_state == "unknown"
        assert channel._transient_config is None
        assert not any(c.upper().startswith("OUTP ON") for c in history)
        assert len(attempts) == 1, "a failed readback must not be retried"
        # Cleanup starts after the failure point.  A timed-out query never reached the
        # transport, so its position is the history length when it was attempted.
        after = history[attempts[0] + (1 if fault != "timeout" else 0):]
        for command in CLEANUP:
            assert command in after, f"{command} missing after the failed readback"
        armed = ("VOLT:MODE ARB", "VOLT:MODE LIST", "VOLT:MODE STEP",
                 "CURR:MODE ARB", "CURR:MODE LIST", "CURR:MODE STEP")
        assert not any(c.startswith(armed) for c in after), "profile must not be armed after a failure"
        assert driver.get_output_state(1) is False
    finally:
        driver.disconnect()


# -- combinations the simulator cannot judge: reported as skips, never as passes -------------

NOT_RUN = [
    ("source LIST/ARB analog output", "no Option 054 module on the bench (ARB-R07)"),
    ("N6784A bipolar voltage Arb", "no N678xA SMU installed (issue #34)"),
    ("SMU negative-current Arb quadrants", "no N678xA SMU installed (issue #34)"),
    ("external trigger edge capture accuracy", "needs an independent DSO (ARB-R07)"),
    ("continuous-initiation abort on real firmware", "needs authorized hardware run (ARB-R07)"),
    ("sequence step numbering on real firmware (0- vs 1-based)", "needs an Option 054 module (ARB-R07)"),
    ("constant-dwell 65,535-point transfer on real firmware", "needs an Option 054 module (ARB-R07)"),
    ("dwell quantization beyond the documented half-quantum rule", "instrument-specific; hardware only"),
]


@pytest.mark.parametrize(("combination", "reason"), NOT_RUN, ids=[c[0] for c in NOT_RUN])
def test_not_run_combinations_are_reported_as_skipped(combination: str, reason: str) -> None:
    pytest.skip(f"NOT_RUN: {combination}: {reason}")
