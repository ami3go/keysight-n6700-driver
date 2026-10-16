# Worked Arb waveform code examples — N6700/N6705

**Revision:** 1.1 · **2026-10-16** · implementation release **v0.7.10**
**Purpose:** preserve the source-linked design examples that informed the typed
ARB implementation and show their current public-API equivalents.
**Safety:** examples remain offline/simulator material, not approved
live-instrument scripts. All real hardware operation requires a separately
approved HIL plan, per-model capability checks and independent protection.

This document complements the [source-code analysis](ARB_SOURCE_CODE_ANALYSIS.md), [manufacturer command inventory](ARB_WAVEFORM_REFERENCE.md) and the [implemented bounded voltage-transient API](VOLTAGE_TRANSIENT_IMPLEMENTATION.md). New code below is an **original adaptation of concepts**, not copied third-party source. Each section identifies exact sources, implemented status, dangers and tests.

## 1. Source-to-example traceability

| Example | Origin and original technique | What we retain | What we deliberately change |
| --- | --- | --- | --- |
| 1. Existing finite voltage UDEF Arb | [Current driver implementation](../src/keysight_n6700/channel.py), [transient tests](../tests/unit/test_voltage_transient.py) | `configure_voltage_arb`, channel-specific trigger, ready/completion polling | Simulator only; verified final OFF; no real device address or hardware assumption |
| 2. Shape-to-SCPI compiler | [SweepMe! N6705 Signal](https://github.com/SweepMe/instrument-drivers/blob/f69a9a17cca86ef6d83788634aaedcab3cc0af89/src/Signal-Keysight_N6705/main.py) and [pyVirtualLab N6705C Signals](https://github.com/Ben3094/pyVirtualLab/blob/221ba738ed9968ede3c44524f0504a8942ddd479/pyVirtualLab/Instruments/Keysight/KeysightN6705C/Signals.py) | Shape metadata, typed pulse parameters, channel-scoped addressing | Frozen data, validation-first pure compiler; no `*RST`, no `OUTP ON`, no infinite count |
| 3. Pulse frequency and duty cycle | [KK_Lab N6705C](https://github.com/KversKv/KK_Lab/blob/ec7e800cedbc939a49bb4dc71602c3bc110f2929/instruments/power/keysight/n6705c.py), [MrBuslic C++](https://github.com/MrBuslic/SERVER/blob/a797986e16d07bece03aeacbedecc65711b410b5/src/Devices/PUOP/PowerSource/PSModule.cpp) | Frequency-to-segment timing calculation | Single timing authority, finite duration, explicit quantization/readback gate |
| 4. Module capability preflight | [Driver classifier](../src/keysight_n6700/module_capabilities.py) + [Keysight N6700C guide](https://www.keysight.com/nz/en/assets/9018-04957/service-manuals/9018-04957.pdf) | Module discovery, Option 054 applicability | Mainframe + exact module + option check; fail closed for unknown models |
| 5. Constant-dwell current vector | [EIS_keysight CDW](https://github.com/katzgo/EIS_keysight/blob/4a8912dc58d689e166d0d67dc519b252821569b0/Pruebas_EIS/EIS_GenerateARB_v7_clean.py) | Vector serialization and common dwell | No hardware write, bounded length/amplitude/time; no repeat INF |
| 6. Two-step Arb sequence | [Keysight sequence example](https://www.keysight.com/nz/en/assets/9018-04957/service-manuals/9018-04957.pdf), [IVM6311ATE](https://github.com/HarishKumarSedu/IVM6311ATE/blob/4b41017d16d87b0050eeccc5588c850b9acd57bd/Instruments/KeySight_N670x.py) | `ARB:SEQ:RESET`, shape per index, source type | Prevalidate every step and keep output OFF; no automatic `*TRG` or `OUTP ON` |
| 7. Trigger and fault-injection plan | [VoltFraud](https://github.com/PDSHan/VoltFraud/blob/20a9030a8dea9d59e481cf00a220c5f51c496068/VoltFraud-sgx/lib/DCpower_ctr.py), [SweepMe! SMU](https://github.com/SweepMe/instrument-drivers/blob/f69a9a17cca86ef6d83788634aaedcab3cc0af89/src/SMU-Keysight_N6705/main.py) | Explicit source selection, finite status polling, fault-injection scenarios | Distinguish `TRIG:ARB:SOUR` from `TRIG:TRAN:SOUR`; preserve generation tokens, timeout cleanup |

**Current supported driver behavior:** bounded voltage/current LIST and UDEF,
typed standard shapes, ASCII CDW, finite dwell-paced sequences, triggered STEP,
typed ARB/TRAN trigger selection, and guarded lifecycle control. Examples 2–7
remain historical pure compilers/plans; use the public APIs documented in
[VOLTAGE_TRANSIENT_IMPLEMENTATION.md](VOLTAGE_TRANSIENT_IMPLEMENTATION.md).

## 2. Example 1 — current public API, runnable in the simulator

**Source:** [`tests/unit/test_voltage_transient.py`](../tests/unit/test_voltage_transient.py), [`configure_voltage_arb()`](../src/keysight_n6700/channel.py) and [`docs/VOLTAGE_TRANSIENT_IMPLEMENTATION.md`](VOLTAGE_TRANSIENT_IMPLEMENTATION.md).

The example configures a 12 V → 6 V → 12 V UDEF Arb. The driver validates a caller-approved envelope, reads back effective voltage points/dwells, uses a finite count and requires output OFF at configuration. Only the bundled simulator is used.

~~~python
from keysight_n6700 import N6700

CHANNEL = 1

with N6700.connect_simulated() as supply:  # NEVER a physical instrument
    try:
        supply.set_dc_current(1.0, channel=CHANNEL)
        supply.set_dc_voltage(12.0, channel=CHANNEL)

        # Existing public API; configuration DOES NOT enable the output.
        profile = supply.configure_voltage_arb(
            CHANNEL,
            levels_v=[12.0, 6.0, 12.0],
            dwell_s=[0.005, 0.020, 0.005],
            minimum_voltage_v=0.0,
            maximum_voltage_v=18.0,
            maximum_duration_s=0.1,
            repeat_count=1,
            terminate_last=False,
        )
        supply.configure_voltage_transient_trigger(CHANNEL, "bus")
        assert len(profile.levels_v) == 3

        # Explicit energization ONLY of the simulated instrument.
        supply.enable_output(CHANNEL)
        token = supply.arm_voltage_transient(CHANNEL)
        supply.wait_voltage_transient_ready(CHANNEL, token, timeout_s=5.0)
        supply.trigger_voltage_transient(CHANNEL, token)
        finished = supply.wait_voltage_transient_complete(
            CHANNEL, token, timeout_s=10.0
        )
        assert finished.state == "complete"
    finally:
        # Cleanup attempts are independent. Never let abort failure skip OFF.
        try:
            supply.abort_voltage_transient(CHANNEL)
        finally:
            try:
                supply.disable_output(CHANNEL)
            finally:
                supply.safe_shutdown()
~~~

**Expected:** the simulator reports a completed transient; configuration never implicitly enables output. **Not established:** real module supports Arb, true analog 6 V level, 20 ms dwell, trigger latency, slew rate, or protection behavior. **Physical use:** do not simply change `connect_simulated()` to Ethernet; real HIL requires source-module/Option 054 verification and a signed-off electrical envelope.

**Alternative with the same existing API:** use `configure_voltage_list()` for a finite 512-point-capacity LIST. Never assume the LIST limit (512) is the same as UDEF (511) or CDW (vendor higher).

## 3. Example 2 — immutable pulse request and offline SCPI compiler

**Sources:** [SweepMe! declarative waveform dictionary](https://github.com/SweepMe/instrument-drivers/blob/f69a9a17cca86ef6d83788634aaedcab3cc0af89/src/Signal-Keysight_N6705/main.py), [pyVirtualLab typed PulseSignal](https://github.com/Ben3094/pyVirtualLab/blob/221ba738ed9968ede3c44524f0504a8942ddd479/pyVirtualLab/Instruments/Keysight/KeysightN6705C/Signals.py) and [Keysight N6700C pulse example](https://www.keysight.com/nz/en/assets/9018-04957/service-manuals/9018-04957.pdf).

**Design goal:** translate a typed request into a fully validated, inspectable **sequence of strings**. It does **not** connect or execute. This is especially important because several third-party `poweron()` routines combine `ARB:COUN INF`, `OUTP ON` and `INIT:TRAN`.

Save the following into a temporary local `arb_planning_example.py` for pure offline experimentation; **do not add it to the production package as an instrument API without the gates below**.

~~~python
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class VoltageEnvelope:
    minimum_v: float
    maximum_v: float
    maximum_duration_s: float


@dataclass(frozen=True)
class VoltagePulse:
    start_v: float
    top_v: float
    start_s: float
    top_s: float
    end_s: float
    repeat_count: int = 1


def _finite(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def compile_voltage_pulse(
    channel: int,
    pulse: VoltagePulse,
    envelope: VoltageEnvelope,
    *,
    capability_confirmed: bool,
) -> tuple[str, ...]:
    """Pure compiler. Returns SCPI for review; never sends commands."""
    if not capability_confirmed:
        raise ValueError("mainframe, module, firmware and Arb option not confirmed")
    if type(channel) is not int or not 1 <= channel <= 4:
        raise ValueError("channel must be 1..4")
    if type(pulse.repeat_count) is not int or not 1 <= pulse.repeat_count <= 256:
        raise ValueError("finite application repeat_count must be 1..256")

    minimum = _finite(envelope.minimum_v, "minimum_v")
    maximum = _finite(envelope.maximum_v, "maximum_v")
    max_duration = _finite(envelope.maximum_duration_s, "maximum_duration_s")
    if not (0 <= minimum < maximum and max_duration > 0):
        raise ValueError("invalid approved envelope")

    start = _finite(pulse.start_v, "start_v")
    top = _finite(pulse.top_v, "top_v")
    lead = _finite(pulse.start_s, "start_s")
    width = _finite(pulse.top_s, "top_s")
    trail = _finite(pulse.end_s, "end_s")

    if not (minimum <= min(start, top) and max(start, top) <= maximum):
        raise ValueError("pulse exceeds approved voltage envelope")
    if lead < 0 or width <= 0 or trail < 0:
        raise ValueError("invalid pulse segment time")
    if (lead + width + trail) * pulse.repeat_count > max_duration:
        raise ValueError("pulse exceeds approved total duration")

    c = f"(@{channel})"
    fmt = lambda x: format(x, ".17g")
    return (
        f"ARB:FUNC:TYPE VOLT,{c}",
        f"ARB:FUNC:SHAP PULS,{c}",
        f"ARB:VOLT:PULS:STAR {fmt(start)},{c}",
        f"ARB:VOLT:PULS:TOP {fmt(top)},{c}",
        f"ARB:VOLT:PULS:STAR:TIM {fmt(lead)},{c}",
        f"ARB:VOLT:PULS:TOP:TIM {fmt(width)},{c}",
        f"ARB:VOLT:PULS:END:TIM {fmt(trail)},{c}",
        f"ARB:COUN {pulse.repeat_count},{c}",
        f"ARB:TERM:LAST OFF,{c}",
        f"VOLT:MODE ARB,{c}",
    )
~~~

**Offline example (does not talk to an instrument):**

~~~python
plan = compile_voltage_pulse(
    channel=1,
    pulse=VoltagePulse(
        start_v=12.0, top_v=6.0,
        start_s=0.005, top_s=0.020, end_s=0.005,
    ),
    envelope=VoltageEnvelope(0.0, 18.0, maximum_duration_s=0.1),
    capability_confirmed=True,  # test fixture only; NOT hardware discovery
)
assert plan[0] == "ARB:FUNC:TYPE VOLT,(@1)"
assert "ARB:VOLT:PULS:TOP 6,(@1)" in plan
assert not any("OUTP ON" in item or "COUNT INF" in item for item in plan)
~~~

**What this compiler does not prove or implement:** it has no real SCPI client, no instrument readback, no OVP/current/power/energy protection, no module/Option 054 discovery, no device quantum check, no output-state guard, and no transaction rollback. The **real driver layer** must perform all those checks before *any* write, plus channel ownership and cleanup; do not run the resulting commands against a real DUT as-is.

## 4. Example 3 — frequency and duty-cycle conversion without conflicting setters

**Sources:** [KK_Lab's `PULS:FREQ` usage](https://github.com/KversKv/KK_Lab/blob/ec7e800cedbc939a49bb4dc71602c3bc110f2929/instruments/power/keysight/n6705c.py), [C++ period split](https://github.com/MrBuslic/SERVER/blob/a797986e16d07bece03aeacbedecc65711b410b5/src/Devices/PUOP/PowerSource/PSModule.cpp) and [Keysight pulse waveform timing table](https://www.keysight.com/nz/en/assets/9018-04957/service-manuals/9018-04957.pdf).

The manual documents both pulse times and `ARB:VOLT:PULS:FREQ`. **Do not treat a frequency setter and the individual timing setters as independent values.** For a new driver API, use a single authoritative request model and query resulting values after hardware programming.

~~~python
def pulse_from_frequency(
    *, base_v: float, top_v: float, frequency_hz: float,
    duty_cycle: float, leading_fraction: float = 0.5,
    repeat_count: int = 1,
) -> VoltagePulse:
    """Convert frequency to segment times; pure arithmetic, no SCPI."""
    f = _finite(frequency_hz, "frequency_hz")
    duty = _finite(duty_cycle, "duty_cycle")
    leading = _finite(leading_fraction, "leading_fraction")
    if f <= 0 or not 0 < duty < 1 or not 0 <= leading <= 1:
        raise ValueError("invalid pulse frequency, duty cycle or phase split")
    period = 1.0 / f
    on_time = period * duty
    off_time = period - on_time
    return VoltagePulse(
        start_v=base_v, top_v=top_v,
        start_s=off_time * leading,
        top_s=on_time,
        end_s=off_time * (1 - leading),
        repeat_count=repeat_count,
    )


pulse = pulse_from_frequency(
    base_v=12.0, top_v=6.0, frequency_hz=100.0,
    duty_cycle=0.20, leading_fraction=0.5,
)
assert abs(pulse.top_s - 0.002) < 1e-12
assert abs(pulse.start_s + pulse.top_s + pulse.end_s - 0.01) < 1e-12
~~~

**Protocol qualification to add:** for each approved model, compare `ARB:VOLT:PULS:FREQ?` to the effective `STAR:TIM?`, `TOP:TIM?`, `END:TIM?`. Re-derive the real period from readbacks and reject discrepancy exceeding **documented module timing quantization**. A source with insufficient slew/current capability may not reproduce fast pulse edges even if SCPI accepts the commands.

## 5. Example 4 — fail-closed mainframe/Option 054 gating

**Sources:** [current `classify_module()`](../src/keysight_n6700/module_capabilities.py), [`discover_modules()`](../src/keysight_n6700/driver.py) and [Keysight N6700C family feature table](https://www.keysight.com/nz/en/assets/9018-04957/service-manuals/9018-04957.pdf).

A real correctness gap documented in [DRV-ARB-01](ARB_SOURCE_CODE_ANALYSIS.md#4-concrete-findings-in-our-own-v074-driver): current source-module flags do not depend on Option 054. The following **narrow** example illustrates only the validated `N6775A` cases and rejects every unknown combination. **It does not replace a comprehensive manufacturer-driven capability matrix.**

~~~python
def n6775a_voltage_arb_allowed(
    mainframe: str, module: str, installed_options: tuple[str, ...]
) -> bool:
    main = mainframe.strip().upper()
    model = module.strip().upper()
    opts = {item.strip().upper() for item in installed_options}
    if model != "N6775A":
        return False              # not reviewed in this tiny example
    if main == "N6700C":
        return "054" in opts      # this chassis requires Option 054
    if main == "N6705C":
        return True               # separately documented analyzer capability
    return False                  # N6700B/other -> unresolved, fail closed


assert not n6775a_voltage_arb_allowed("N6700C", "N6775A", ())
assert n6775a_voltage_arb_allowed("N6700C", "N6775A", ("054",))
assert n6775a_voltage_arb_allowed("N6705C", "N6775A", ())
assert not n6775a_voltage_arb_allowed("unknown", "N6775A", ("054",))
~~~

Production solution: add an explicit mainframe identity and firmware context to the capability model, normalize the instrument's `SYST:CHAN:OPT?` reply, and parameterize unit tests across each exact model. **Do not** authorize Arb merely because the existing simulator or a source file implements an Arb command.

## 6. Example 5 — current CDW array serialization, offline only

**Sources:** [EIS_keysight `write_ascii_values('ARB:CURR:CDW ...')`](https://github.com/katzgo/EIS_keysight/blob/4a8912dc58d689e166d0d67dc519b252821569b0/Pruebas_EIS/EIS_GenerateARB_v7_clean.py), [Keysight N6700C CDW command reference](https://www.keysight.com/nz/en/assets/9018-04957/service-manuals/9018-04957.pdf).

Constant dwell supplies many **level** points with **one** shared dwell. This differs from UDEF, where a vector of per-point dwell values is sent. The vendor describes a larger CDW point budget; the **64-point** bound below is a deliberately smaller demonstration policy, not the instrument limit.

~~~python
def compile_current_cdw_data(
    channel: int,
    levels_a: tuple[float, ...],
    *,
    approved_abs_current_a: float,
    maximum_points: int = 64,
) -> str:
    """Format an ASCII CDW vector, without instrument I/O."""
    limit = _finite(approved_abs_current_a, "approved_abs_current_a")
    if limit <= 0 or type(channel) is not int or not 1 <= channel <= 4:
        raise ValueError("invalid channel or current limit")
    if not 1 <= len(levels_a) <= maximum_points <= 65535:
        raise ValueError("invalid CDW vector length")
    levels = tuple(_finite(x, "current level") for x in levels_a)
    if any(abs(x) > limit for x in levels):
        raise ValueError("CDW level exceeds approved absolute current")
    values = ",".join(format(value, ".12g") for value in levels)
    return f"ARB:CURR:CDW {values},(@{channel})"


assert compile_current_cdw_data(
    1, (0.1, 0.3, 0.1), approved_abs_current_a=0.5
) == "ARB:CURR:CDW 0.1,0.3,0.1,(@1)"
~~~

**Implemented equivalent (v0.7.10):**
`configure_current_constant_dwell_arb()` performs this transaction while the
output is verified OFF, checks the current programming role/range/compliance,
preflights the complete ASCII command at 64 KiB, verifies `CDW:POIN?`, and
reads back dwell/count/mode. It never enables or triggers the output. Binary
CDW input and vectors exceeding the safe ASCII command budget remain rejected.

## 7. Example 6 — two-step Arb sequence planning and command ordering

**Sources:** [Keysight N6700C sequence example](https://www.keysight.com/nz/en/assets/9018-04957/service-manuals/9018-04957.pdf) (waveform chapter) and [IVM6311ATE N670x Arb sequences](https://github.com/HarishKumarSedu/IVM6311ATE/blob/4b41017d16d87b0050eeccc5588c850b9acd57bd/Instruments/KeySight_N670x.py).

A **finite** PULSE followed by RAMP must preserve zero-based indices and a consistent voltage/current Arb type. This example returns a manufacturer-style **illustrative command plan**. It omits output-on, `INIT:TRAN`, and `*TRG` intentionally. The actual driver must validate per-shape point-memory estimation (511 generated-point budget), ≤100 indexed steps, pacing, count and a finite worst-case run duration **before** sending `ARB:SEQ:RES`.

~~~python
def compile_example_voltage_sequence(channel: int = 1) -> tuple[str, ...]:
    """Offline-only two-step sequence; fixed example values."""
    if type(channel) is not int or not 1 <= channel <= 4:
        raise ValueError("invalid channel")
    c = f"(@{channel})"
    return (
        f"ARB:FUNC:TYPE VOLT,{c}",
        f"ARB:FUNC:SHAP SEQ,{c}",
        f"ARB:SEQ:RES {c}",  # destructive: clear existing sequence
        f"ARB:SEQ:STEP:FUNC:SHAP PULS,0,{c}",
        f"ARB:SEQ:STEP:VOLT:PULS:STAR 12,0,{c}",
        f"ARB:SEQ:STEP:VOLT:PULS:TOP 6,0,{c}",
        f"ARB:SEQ:STEP:VOLT:PULS:STAR:TIM 0.005,0,{c}",
        f"ARB:SEQ:STEP:VOLT:PULS:TOP:TIM 0.02,0,{c}",
        f"ARB:SEQ:STEP:VOLT:PULS:END:TIM 0.005,0,{c}",
        f"ARB:SEQ:STEP:FUNC:SHAP RAMP,1,{c}",
        f"ARB:SEQ:STEP:VOLT:RAMP:STAR 6,1,{c}",
        f"ARB:SEQ:STEP:VOLT:RAMP:END 12,1,{c}",
        f"ARB:SEQ:STEP:VOLT:RAMP:STAR:TIM 0.005,1,{c}",
        f"ARB:SEQ:STEP:VOLT:RAMP:RTIM 0.02,1,{c}",
        f"ARB:SEQ:STEP:VOLT:RAMP:END:TIM 0.005,1,{c}",
        f"ARB:SEQ:COUN 1,{c}",
        f"ARB:SEQ:TERM:LAST OFF,{c}",
        f"VOLT:MODE ARB,{c}",
    )


commands = compile_example_voltage_sequence(1)
assert commands[3] == "ARB:SEQ:STEP:FUNC:SHAP PULS,0,(@1)"
assert not any("OUTP ON" in line or "*TRG" in line for line in commands)
~~~

**Critical caveat:** the module may quantize pulse/ramp timing; sequence-generated point allocation and `ARB:SEQ:QUAL?` must be read back. A sequence is **not** ready merely because the strings can be generated. Command grammar and persistence of `ARB:SEQ:TERM:LAST` must be tested on the exact approved firmware before feature release.

## 8. Example 7 — trigger-family selection and explicit safety state

**Sources:** [VoltFraud external triggering](https://github.com/PDSHan/VoltFraud/blob/20a9030a8dea9d59e481cf00a220c5f51c496068/VoltFraud-sgx/lib/DCpower_ctr.py), [SweepMe! Arb control](https://github.com/SweepMe/instrument-drivers/blob/f69a9a17cca86ef6d83788634aaedcab3cc0af89/src/SMU-Keysight_N6705/main.py), [Keysight N6705 Programmer's Reference](https://www.keysight.com/us/en/assets/9018-03616/programming-guides/9018-03616.pdf).

**Two documented selectors must not be conflated:**

| Context | Representative command | Driver consequence |
| --- | --- | --- |
| LIST/STEP transient system | `TRIG:TRAN:SOUR BUS,(@1)` | Selected by typed mode state and read back |
| ARB transient system | `TRIG:ARB:SOUR BUS,(@1)` | Selected only for an approved channel-scoped capability dialect and read back; physical firmware verification remains pending |
| Trigger specific armed transient | `TRIG:TRAN (@1)` | Existing driver uses this instead of mainframe-wide `*TRG` |

A safe **offline** selection helper cannot auto-detect which selector is correct merely from the waveform name:

~~~python
def choose_trigger_source_command(
    *, family: str, channel: int, firmware_verified: bool
) -> str:
    if not firmware_verified or type(channel) is not int or not 1 <= channel <= 4:
        raise ValueError("trigger family or channel not verified")
    if family == "transient":
        return f"TRIG:TRAN:SOUR BUS,(@{channel})"
    if family == "arb":
        # Source selector scope differs by mainframe/firmware.
        return "TRIG:ARB:SOUR BUS"
    raise ValueError("unrecognized trigger family")


assert choose_trigger_source_command(
    family="transient", channel=1, firmware_verified=True
) == "TRIG:TRAN:SOUR BUS,(@1)"
~~~

**Avoid:** issuing `*TRG` while another channel is armed, using `sleep()` instead of waiting for WTG, re-triggering on socket timeout, or assuming `ABOR:TRAN` disables `INIT:CONT:TRAN ON`. Refer to [the current generation-token lifecycle](VOLTAGE_TRANSIENT_IMPLEMENTATION.md) and [source-confirmed driver gaps](ARB_SOURCE_CODE_ANALYSIS.md#4-concrete-findings-in-our-own-v074-driver).

## 9. Copy/paste negative tests for the pure offline examples

The following `pytest` examples assume the **functions in §§3–8 are copied** into a local `arb_planning_example.py`; unlike live HIL tests they do not open any resource or require PyVISA. They deliberately test the conditions third-party samples often leave to operators.

~~~python
# test_arb_planning_example.py
import pytest
from arb_planning_example import (
    VoltageEnvelope, VoltagePulse, compile_voltage_pulse,
    compile_current_cdw_data, n6775a_voltage_arb_allowed,
    pulse_from_frequency, compile_example_voltage_sequence,
    choose_trigger_source_command,
)


def test_pulse_is_finite_and_never_energizes():
    commands = compile_voltage_pulse(
        1, VoltagePulse(12, 6, 0.005, 0.020, 0.005),
        VoltageEnvelope(0, 18, 0.1), capability_confirmed=True,
    )
    assert commands[-1] == "VOLT:MODE ARB,(@1)"
    assert not any("OUTP" in text or "INIT:TRAN" in text for text in commands)


@pytest.mark.parametrize("bad_top", [float("nan"), float("inf"), 19.0])
def test_bad_voltage_rejected(bad_top):
    with pytest.raises(ValueError):
        compile_voltage_pulse(
            1, VoltagePulse(12, bad_top, 0.005, 0.020, 0.005),
            VoltageEnvelope(0, 18, 0.1), capability_confirmed=True,
        )


def test_missing_option_or_capability_fails_closed():
    assert not n6775a_voltage_arb_allowed("N6700C", "N6775A", ())
    with pytest.raises(ValueError, match="not confirmed"):
        compile_voltage_pulse(
            1, VoltagePulse(12, 6, 0.005, 0.020, 0.005),
            VoltageEnvelope(0, 18, 0.1), capability_confirmed=False,
        )


def test_frequency_validation_and_segment_sum():
    with pytest.raises(ValueError):
        pulse_from_frequency(base_v=0, top_v=5, frequency_hz=0, duty_cycle=.5)
    p = pulse_from_frequency(
        base_v=0, top_v=5, frequency_hz=100, duty_cycle=.25,
    )
    assert p.start_s + p.top_s + p.end_s == pytest.approx(.01)


def test_cdw_limits_and_no_output():
    with pytest.raises(ValueError):
        compile_current_cdw_data(1, (0.1, 2.0), approved_abs_current_a=.5)
    rendered = compile_current_cdw_data(
        1, (.1, .2, .1), approved_abs_current_a=.5,
    )
    assert rendered.startswith("ARB:CURR:CDW ")
    assert "OUTP" not in rendered


def test_sequence_is_offline_plan_not_trigger():
    commands = compile_example_voltage_sequence(1)
    assert "ARB:SEQ:RES (@1)" in commands
    assert not any("*TRG" in text or "OUTP" in text for text in commands)


def test_trigger_selector_cannot_auto_guess():
    with pytest.raises(ValueError):
        choose_trigger_source_command(
            family="arb", channel=1, firmware_verified=False,
        )
~~~

**Run only the copied pure-example tests:**

~~~bash
python -m pytest -q test_arb_planning_example.py
~~~

For the actual driver, build separate fake-SCPI/scripted-transport tests that assert ordered outbound commands, exact queried responses, failure after command `k`, protection fault, teardown on timeout, concurrency isolation and clean residual instrument state. Existing [`test_voltage_transient.py`](../tests/unit/test_voltage_transient.py) is the reference for simulator-based command ordering. **The snippets above do not substitute for a real hardware/DSO acceptance matrix.**

## 10. Failure modes, evidence and implementation sequence

| Source-derived failure mode | Offline acceptance criterion | Required hardware acceptance |
| --- | --- | --- |
| Source module lacks Option 054 on N6700C | Capability false **before any profile write**; unsupported family also rejected | Read model, option and firmware identification from real hardware |
| Invalid shape numeric input, zero frequency, infinite count | Raise, preserve prior state, no SCPI side effects | Source and DUT remain safe; error queue inspected |
| `ABOR:TRAN?` or wrong `ARB:CURR` for voltage UDEF | Golden grammar and type × shape matrix reject wrong command | Firmware accepts command/query contracts without hanging |
| Partial SCPI write and cleanup failure | Preserve primary exception; state UNKNOWN; attempted abort/continuous OFF | Emergency OFF plus independent protection confirmed |
| Unverified `TRIG:ARB:SOUR` versus `TRIG:TRAN:SOUR` | Refuse family selection until model-specific rule is known | Observed trigger source and WTG/active transition on selected channel |
| Unverified analog waveform shape | Never equate successful readback with analog pass | Calibrated DSO/probe amplitude, edges, timing, overshoot, jitter |
| Large CDW/UDEF vector | Point budget, finite duration, serialization/termination and timeout coverage | End-to-end upload and exact effective point count |

**Recommended PR order:** (1) correct mainframe/Option 054 capability truth with tests; (2) vendor-specific Arb trigger selection contract with simulator tests; (3) finite standard voltage pulse/ramp/sine typed APIs; (4) current/CDW APIs with proper compliance; (5) finite sequence builder; (6) guarded physical HIL + independent DSO qualification. Review and merge each independently.

**No hardware-connected examples are supplied in this document.** Do not move pure planners into the driver until the existing state machine, module-specific protection, readbacks and fault recovery have been integrated and verified.
