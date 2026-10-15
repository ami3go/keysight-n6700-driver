# N6700 / N6705 Arb waveform software: comparative source-code analysis

**Assessment date:** 2026-10-15  
**Document revision:** 1.1; code examples added in v0.7.6; first published in `keysight-n6700-driver` **v0.7.5**.  
**Baseline inspected:** `keysight-n6700-driver` **v0.7.4**, as checked against GitHub `main`.  
**Scope:** source-code inspection and vendor-reference cross-check only; **no third-party code executed, no physical equipment connected, and no instrument compliance or metrology claims**.

See the [Arb waveform function inventory](ARB_WAVEFORM_REFERENCE.md), [existing finite voltage LIST/UDEF architecture](VOLTAGE_TRANSIENT_IMPLEMENTATION.md) and [hardware acceptance plan](hardware_acceptance_tests.md).

## 1. Method, evidence grades, and review boundaries

This review inspects actual public Python/C++ source code, identifies functional behavior and error-handling patterns, and compares hardware-facing commands against the [official N6700C Operating and Service Guide](https://www.keysight.com/er/en/assets/9018-04957/service-manuals/9018-04957.pdf), especially the feature/option matrix (printed pp. 43–45), waveform chapter (pp. 123–135), and Arb/trigger SCPI reference (pp. 184–232 and 301–303). The [N6705C User's Guide](https://www.keysight.com/us/en/assets/9018-01649/user-manuals/9018-01649.pdf) is a second official source for Arb startup and data-logging examples.

**Evidence labels:**

- **SOURCE-CONFIRMED** — explicitly visible in the linked implementation, including the exact SCPI spelling or code path.
- **VENDOR-CONFIRMED** — manufacturer guide explicitly defines a command, restriction, parameter relationship, or option.
- **INTEGRATION RISK** — a potential failure when adapting the pattern for an unattended, shared-mainframe DUT bench. It is **not** an assertion the third-party program failed in the author's environment.
- **HIL REQUIRED** — actual instrument behavior, trigger timing, analog amplitude or failure recovery not established by reading code/manuals.

This is an original technical comparison. No code is copied from third parties. Source links are pinned to inspected commits where available. Do not use their code on a live source/load without independent review.

## 2. Source-by-source code examination

| ID | Reviewed code (line-targeted source) | What the implementation actually does | Positive pattern | Limitations or risks for our driver |
| --- | --- | --- | --- | --- |
| EXT-01 | [SweepMe! N6705 Signal driver](https://github.com/SweepMe/instrument-drivers/blob/f69a9a17cca86ef6d83788634aaedcab3cc0af89/src/Signal-Keysight_N6705/main.py#L49-L223) | Describes `SIN`, `STEP`, `RAMP`, `STAIRCASE`, `PULSE`, `TRAPEZOID`, `EXP` using a dictionary of SCPI suffixes and units; `poweron()` selects type/shape, sets `ARB:COUNT INF`, `TRIG:ARB:SOURCE IMM`, `OUTP ON`, `INIT:TRAN`. | Good declarative shape/parameter registry for a user interface. | `initialize()` issues `*RST`; output enable and infinite repeats are built into the startup sequence. No evidence in these functions of bounds, readback or safe failure state. |
| EXT-02 | [SweepMe! N6705 Signal live updates](https://github.com/SweepMe/instrument-drivers/blob/f69a9a17cca86ef6d83788634aaedcab3cc0af89/src/Signal-Keysight_N6705/main.py#L214-L287) | `apply()` aborts, uses `*WAI`, rewrites selected parameters and re-initiates; `convert_gui_value_to_float()` may silently substitute a default if a string is invalid. | Reusable compile/reconfigure idea and separation of shape-specific parameter calculations. | `*WAI` is not a verified output-OFF transition. Silent fallback can hide operator input errors; nonfinite values and divide-by-zero remain a validation concern. |
| EXT-03 | [SweepMe! N6705 SMU](https://github.com/SweepMe/instrument-drivers/blob/f69a9a17cca86ef6d83788634aaedcab3cc0af89/src/SMU-Keysight_N6705/main.py#L135-L225) | Branches on N678x for current compliance, provides pulse on/off-time/level fields, configures `ARB:FUNC:TYPE` and `ARB:FUNC:SHAPE`, and executes voltage/current pulse Arbs. | Model-aware limits instead of one generic power-supply command set. | Also uses `*RST` and `ARB:COUNT INF`; reconfiguration while running is not appropriate for the current safety contract. |
| EXT-04 | [pyVirtualLab N6705C Signals](https://github.com/Ben3094/pyVirtualLab/blob/221ba738ed9968ede3c44524f0504a8942ddd479/pyVirtualLab/Instruments/Keysight/KeysightN6705C/Signals.py#L1-L225) | `SignalType` voltage/current enum; classes for pulse, sequence, UDEF, CDW and other shapes; channel-scoped `Write/Query` helpers; properties query after a write. | Typed waveform objects and consistent channel formatting. | UDEF `Triggers`, `Durations`, `Levels` SCPI paths are hardcoded to `CURR` even if `SignalType` is voltage. Many numeric properties use exact float/list readback equality despite documented quantization. |
| EXT-05 | [KK_Lab N6705C SCPI adapter](https://github.com/KversKv/KK_Lab/blob/ec7e800cedbc939a49bb4dc71602c3bc110f2929/instruments/power/keysight/n6705c.py#L180-L267) | Has individual step/staircase/pulse configuration methods, `INIT:TRAN`, `STAT:OPER:COND?` and `ARB:VOLT:PULS:FREQ` after explicit pulse times. | Dedicated typed operation signatures; useful pulse-frequency example. | `arb_off()` sends `ABOR:TRAN? (@n)` via `write()`, but manufacturer documents **`ABOR:TRAN` as a command without `?`**. Global `*TRG` is also exposed separately. |
| EXT-06 | [VoltFraud N6700 fault-injection helper](https://github.com/PDSHan/VoltFraud/blob/20a9030a8dea9d59e481cf00a220c5f51c496068/VoltFraud-sgx/lib/DCpower_ctr.py#L33-L85) | Creates voltage UDEF level/dwell arrays, selects external triggering and optionally continuous initiation. | Concrete fault/glitch profiles with explicitly selectable channel and trigger. | Uses `clear()`, output enable, continuous operation and status polling; copy only the idea of a typed finite glitch profile, not the unbounded lifecycle. |
| EXT-07 | [EIS_keysight current CDW generator](https://github.com/katzgo/EIS_keysight/blob/4a8912dc58d689e166d0d67dc519b252821569b0/Pruebas_EIS/EIS_GenerateARB_v7_clean.py#L40-L125) | Computes a current waveform array and sends `ARB:CURR:CDW` using PyVISA `write_ascii_values`, with a separate common dwell value. | Useful vector-transfer method and distinct CDW versus UDEF semantics. | Uses `ARB:COUN INF`; ASCII formatting, channel suffix, point budgets and write/readback must be validated before hardware use. |
| EXT-08 | [IVM6311ATE Keysight N670x helper](https://github.com/HarishKumarSedu/IVM6311ATE/blob/4b41017d16d87b0050eeccc5588c850b9acd57bd/Instruments/KeySight_N670x.py#L233-L266) | Programs voltage/current PULSE start/top levels, individual times and sequence shape control. | Clear grouping of pulse parameters and per-channel methods. | Source methods provide SCPI writes, but not verified finished transactions with approved limits and error recovery. |
| EXT-09 | [MrBuslic C++ pulse helper](https://github.com/MrBuslic/SERVER/blob/a797986e16d07bece03aeacbedecc65711b410b5/src/Devices/PUOP/PowerSource/PSModule.cpp#L90-L116) | Converts requested frequency to fractions of a pulse period; sets Arb voltage/current parameters; `ARB:COUN INF` and uses `*TRG`. | Useful pure planning logic for pulse period decomposition. | `*TRG` can affect other armed channels; frequency arithmetic needs `f>0` and practical timing/quantization validation; infinite operation is explicitly prohibited by current driver policy. |
| EXT-10 | [N67xx notes and sample concepts](https://github.com/tmbinc/random/blob/81459d9bd0678243f8971a12b784327b55b0d5bb/agilent/n67xx/README.md#L45-L56) | Contrasts host-driven voltage writes with device-stored list/waveform execution and notes model-dependent bandwidth/slew. | Useful warning about software vs hardware-timed waveform generation. | Host-side SCPI loops cannot establish accurate sub-second pulse timing; no measurable output bandwidth guarantee can be transferred between module types. |

**Assessment:** EXT-01/03/04/05 are the most useful interface-design references; EXT-06/07/08 are the most useful low-level Arb command examples. **None of these reviewed source fragments justifies importing its power-on/reset/infinite-loop policy into this repository.**

## 3. Manufacturer-verified SCPI semantics that resolve conflicting examples

### 3.1 Option 054 is a capability gate on N6700C

The N6700C guide's **Model Differences** table (printed p. 43) distinguishes ordinary power modules from precision modules:

| Installed family on N6700C | Hardware Arb/LIST claim | Required driver behavior |
| --- | --- | --- |
| N673xB / N674xB / N675xA / N677xA | **Option 054 required** | Validate `SYST:CHAN:OPT? (@n)` contains the option; otherwise mark both LIST and Arb unsupported for that mainframe/module combination. |
| N676xA precision source | Included by manufacturer | Gate on exact supported model, then apply source/SMU mode restrictions. |
| N678xA SMU | Included by manufacturer, **with limitations** | Check polarity/quadrants and the N6783A negative-current exclusion before exposing a specific waveform mode. |
| N6791A / N6792A load | Arb supported, with different modes | Treat power/resistance Arb as electronic-load functionality, not voltage/current sourcing. |
| N6705C context | Separately documented capability set | Do not import an N6700C Option 054 rule blindly into a DC Power Analyzer chassis. |

The SCPI guide's command index makes the same option restriction clear. Even if a simulator accepts `ARB:FUNC:SHAP`, capability must be based on **mainframe + module + installed options + firmware**, not just model-prefix matching.

[Official evidence: feature matrix, p. 43](https://www.keysight.com/er/en/assets/9018-04957/service-manuals/9018-04957.pdf) (PDF text pp. 42–45); the **N6783A negative-current limitation** is on printed p. 44.

### 3.2 Arb trigger source and generic transient trigger source are separate paths

The N6700C SCPI reference (printed pp. 301–303) explicitly lists:

~~~text
TRIG:ARB:SOUR <source>          ; Arb trigger-source selector (documented without channel list)
TRIG:TRAN:SOUR <source>,(@n)    ; generic transient-system trigger-source selector
TRIG:TRAN (@n)                 ; immediate transient trigger, requires prior INIT:TRAN
~~~

These are **documented separate command families**, not interchangeable aliases by inference. The source guide's Arb setup examples use `TRIG:ARB:SOUR BUS,(@1)` in some operational tables while the formal SCPI reference lists the `TRIG:ARB:SOUR` selector without `(@n)`; version/model applicability and exact accepted argument form must be confirmed in model-specific protocol tests.

**Do not change the existing driver to `TRIG:ARB:SOUR` on documentation evidence alone.** For a new standard-shape Arb implementation, test the selected trigger source, read it back, arm, observe WTG, issue a **channel-targeted** trigger and verify the waveform starts exactly once. Confirm the selected command truly governs the active Arb on the connected firmware. Avoid `*TRG` outside explicitly synchronized multi-channel tests.

### 3.3 Pulse frequency is legal but coupled to time parameters

The guide (printed p. 199) explicitly documents `ARB:VOLT:PULS:FREQ` and its query, as well as current/power/resistance counterparts. **Frequency and pulse segment times are coupled.** Therefore EXT-05's frequency-last order and EXT-09's time-derived-from-frequency order are alternative planning approaches, not evidence either achieves the requested shape without readback.

Recommended planning invariant for a periodic pulse:

~~~text
period_s = 1 / frequency_hz
start_s >= 0
top_s > 0
end_s >= 0
start_s + top_s + end_s ~= period_s
~~~

The approximation requires **instrument-specific quantization tolerance**, not an arbitrary fixed 1 ns threshold. Program either times or frequency via one documented precedence policy; then query **both** effective frequency and effective times and check the resulting period. Analog pulse shape still requires DSO measurement.

### 3.4 Abort is a command and cleanup is more than abort

The N6700C guide defines `ABORt:TRANsient (@chanlist)` without a query form. **`ABOR:TRAN? (@n)` must not be copied** from EXT-05. The safe cleanup policy also sends `INIT:CONT:TRAN OFF`, checks idle status and commands output OFF when shutting down a test.

**Important distinction:** aborting waveform execution and forcing the output OFF are separate safety actions. A successful SCPI write alone is insufficient verification after connection loss or protection faults.

## 4. Concrete findings in our own v0.7.4 driver

Unlike the generic risks in earlier catalogs, the following are tied to directly inspected repository code. Source observations are **verified statically**; none is claimed as a physically observed hardware failure.

| ID | Severity | Repository source | Finding and evidence | Recommended action |
| --- | --- | --- | --- | --- |
| **DRV-ARB-01** | **HIGH / capability correctness** | [`module_capabilities.py`](../src/keysight_n6700/module_capabilities.py), [`driver.py`](../src/keysight_n6700/driver.py) | `classify_module()` sets `supports_list_mode=True` and `supports_voltage_arb=True` for all `POWER_PREFIXES` **without using `opts`**. `discover_modules()` passes model/options but not the mainframe's model. This conflicts with the N6700C vendor feature/Option 054 table for N673xB/N674xB/N675xA/N677xA. | Add mainframe-aware, exact-family/option gating; unknown mainframe/option state should be conservatively unsupported. Verify N6705C's separately documented behavior rather than applying N6700C's rule universally. |
| **DRV-ARB-02** | **HIGH / regression coverage** | [`test_module_capabilities.py`](../tests/unit/test_module_capabilities.py) | `test_n677x_supports_voltage_list_arb_and_slew` passes `classify_module("N6775A")` with **no** option list, but expects LIST and Arb support. This would preserve DRV-ARB-01 during refactors. | Add parametrized **mainframe × source-model × options** tests: N6700C/N6775A with empty options → reject; with 054 → allow; N676xA allow; N678xA allow within subtype limits; N6705C cases separately validated. |
| **DRV-ARB-03** | **MEDIUM / command routing** | [`channel.py`](../src/keysight_n6700/channel.py) (trigger setup and UDEF execution) | `configure_voltage_transient_trigger()` uses `TRIG:TRAN:SOUR` for both LIST and UDEF Arb. The vendor separately documents an Arb-specific trigger source command, while the original bounded LIST/UDEF simulation expects the transient path. This is a **verification gap, not a confirmed runtime bug**. | Add a simulator/protocol model for both subsystems; inspect actual `TRIG:ARB:SOUR?` and `TRIG:TRAN:SOUR?` behavior on each supported mainframe, with readback/status and a passive guarded hardware test. |
| **DRV-ARB-04** | **MEDIUM / error preservation** | [`channel.py`](../src/keysight_n6700/channel.py) (`_configuration_failed`) | In `except BaseException: self._configuration_failed(); raise`, a failed cleanup write in `_configuration_failed` can **replace the original configuration exception**. Cleanup does mark state unknown in `finally`, but may conceal the initiating SCPI fault. | Preserve the primary exception, attach cleanup outcome as metadata or an exception note (similar to `_cleanup_failed_voltage_transient`), and retain fail-closed `UNKNOWN` status. Inject failure in both programming and cleanup. |
| **DRV-ARB-05** | **MEDIUM / evidence integrity** | [`channel.py`](../src/keysight_n6700/channel.py) (WTG/active status polling) | The lifecycle is generation-token-based and waits for trigger readiness; completion polls for both waiting and active bits to clear. A **very short** Arb may complete between polls and cannot be analog-qualified from SCPI bits alone. | Keep bounded state machine; augment physical tests with independent trigger/DSO capture and inspect actual output waveform. Avoid strengthening confidence based only on simulator's timing. |

The present driver already has useful safeguards absent from several third-party examples: output-OFF configuration; finite, envelope-bounded LIST/UDEF; current-limit and OVP checks; readback with dwell quantization; transient token fencing; channel-targeted trigger; and cleanup on wait failures. **Preserve these** while extending waveforms. See [`configure_voltage_arb`](../src/keysight_n6700/channel.py) and [`tests/unit/test_voltage_transient.py`](../tests/unit/test_voltage_transient.py).

## 5. Reusable patterns transformed into safe proposed APIs

### 5.1 Typed shape registry, without raw SCPI injection

Adopt EXT-01's table-driven waveform taxonomy and EXT-04's shape-specific objects, but expose **immutable, validated parameter models** and a **separately compiled command plan**. A shape declaration describes legal parameters and readback paths. It **must not** execute SCPI in its constructor.

**Illustrative pseudo-API, NOT currently implemented or hardware-tested:**

~~~python
from dataclasses import dataclass

@dataclass(frozen=True)
class PulseArb:
    start_level_v: float
    top_level_v: float
    start_time_s: float
    top_time_s: float
    end_time_s: float
    repeat_count: int = 1
    terminate_last: bool = False

@dataclass(frozen=True)
class ValidatedArbPlan:
    channel: int
    capability_fingerprint: str
    commands: tuple[str, ...]
    expected_readbacks: tuple[str, ...]
    maximum_duration_s: float

def plan_voltage_pulse(
    request: PulseArb,
    *,
    channel: int,
    discovered_capability,
    approved_voltage_envelope,
    approved_maximum_duration_s: float,
) -> ValidatedArbPlan:
    """Pure preflight: validate, compute effective limits, then construct a plan.

    This is an API sketch: implement and test all checks, not a callable body.
    """
    raise NotImplementedError
~~~

A future implementation must prevalidate numeric finiteness; approved voltage minima/maxima; **all** waveform extrema (sine: offset ± amplitude); point memory; current/power/OVP and applicable quadrants; positive/quantized timing; peak and aggregate duration; count; module option and firmware; and stored prior state.

### 5.2 Separate configuration, execution, and emergency OFF

**Recommended state transition contract:**

~~~text
CONNECTED / OUTPUT_OFF
      |
      v
DISCOVERED(model + mainframe + options + firmware)
      |
      v
VALIDATED (all constraints and total energy/duration budget)
      |
      v
PROGRAMMED -> READBACK_VERIFIED (still OUTPUT_OFF)
      |
      +--> explicit operator-approved ENABLE_OUTPUT
      |        |
      |        v
      |      ARM -> WTG_VERIFIED -> TRIGGER -> COMPLETE_VERIFIED
      |        |                                    |
      |        +---------- ABORT / FAULT ---------+
      |                          |
      v                          v
SAFE_OFF_VERIFIED      ABORT; CONTINUOUS_OFF; OUTPUT_OFF; VERIFY
                                 |
                                 +--> UNKNOWN if readback not possible
~~~

**Constraints inherited from the current driver:** one controller owns a channel/mainframe; no implicit `*RST` or `OUTP ON` from `connect()` / `configure()`; channel-scoped trigger unless an explicitly approved multi-channel synchronization plan; bounded timeouts; instrument status/readback; durable capture of failures.

### 5.3 Explicit timing mode and conversion, not dual setter guesses

~~~python
# Design sketch, not a live driver method
@dataclass(frozen=True)
class PulseByFrequency:
    frequency_hz: float
    duty_cycle: float         # e.g., 0.25 means 25% at top level
    start_fraction: float     # fraction of non-top period before the pulse

# Convert to (start_s, top_s, end_s) once, validate at instrument resolution,
# program according to vendor command precedence, and query resulting times.
~~~

Do not independently program `FREQ` after an unreviewed set of times merely because a third-party example does so.

## 6. Proposed regression matrix and acceptance gates

| Test ID | Offline scenario | Expected assertion | Requirement provenance |
| --- | --- | --- | --- |
| T01 | N6700C/N6775A, no 054 option | LIST and voltage Arb both fail closed **before any Arb write** | Vendor feature table / DRV-ARB-01 |
| T02 | N6700C/N6775A with 054; N676xA; supported N678xA | Correct capability flags and model/option-fingerprint; unsupported negative-current modes refused | Vendor matrix / DRV-ARB-01 |
| T03 | N6705C/N6775A without 054, where documented | Supported only under separately validated N6705C rule, not N6700C shortcut | Mainframe-context correctness |
| T04 | Standard PULSE/STEP/RAMP/SIN shapes: all parameter combinations | SCPI golden vectors, readbacks, finite levels and approved envelope | EXT-01/04, vendor command tree |
| T05 | Pulse frequency and segment times both supplied | Reject conflict or normalize by documented precedence; read back actual `PULS:FREQ?` and time fields | Vendor coupled setter / EXT-05 |
| T06 | UDEF voltage/current, 1/511 points, wrong type, malformed array | `ARB:VOLT` versus `ARB:CURR` correctly distinguished; enforced point/dwell limits | EXT-04, vendor UDEF |
| T07 | CDW array transfer and dwell; one/maximum point count | Correct ASCII/binary transport framing, exact point count, byte order, block length and readback | EXT-07 / vendor CDW |
| T08 | Trigger source `ARB` vs `TRAN` | Correct documented syntax and effective trigger routing per mainframe; no early trigger; scoped `TRIG:TRAN` | Vendor trigger reference / DRV-ARB-03 |
| T09 | Another channel armed during waveform under test | No global `*TRG` or unexpected cross-channel energization | EXT-05/09 |
| T10 | Invalid NaN/Inf, zero frequency, negative time, over-envelope/OVP | Reject **without SCPI mutations**; never fall back silently to a default | EXT-02/09 |
| T11 | Primary configuration write fails; cleanup also times out | Preserve original exception and cleanup result; state UNKNOWN; output OFF attempted | DRV-ARB-04 |
| T12 | INFinity, unexpected continuous enable, missing trigger, cancelled wait | Typed API rejects unbounded configuration; abort, continuous OFF and safe OFF verified or UNKNOWN | EXT-01/03/06/07 |
| T13 | Simultaneous two-channel acquisition + source Arb | Independent acquisition and Arb state machines; raw waveform retained before unrelated `MEAS` overwrites buffer | Scope contract and forum findings |
| T14 | DSO timing and amplitude calibration | Independent measurement of pulse timing, edges, extrema, overshoot and trigger jitter with traceable uncertainty | Physical HIL gate |

**Suggested PR order:** (1) capability-model correction + negative unit tests; (2) per-mainframe trigger command protocol verification; (3) new typed finite voltage shape models and golden SCPI tests; (4) finite current and CDW; (5) bounded sequence builder; (6) guarded DSO hardware qualification. **Do not declare these features implemented or qualified until their own gates pass.**

**No changes to runtime driver code are made in this analysis release.** The observations above are actionable review findings for subsequent feature/bug-fix PRs.

## 7. Source inventory and licenses

| Source category | Immutable/official reference |
| --- | --- |
| Manufacturer, N6700C command/option matrix | https://www.keysight.com/er/en/assets/9018-04957/service-manuals/9018-04957.pdf |
| Manufacturer, N6705C operating/Arb examples | https://www.keysight.com/us/en/assets/9018-01649/user-manuals/9018-01649.pdf |
| EXT-01/02/03 SweepMe! | https://github.com/SweepMe/instrument-drivers/tree/f69a9a17cca86ef6d83788634aaedcab3cc0af89/src |
| EXT-04 pyVirtualLab | https://github.com/Ben3094/pyVirtualLab/blob/221ba738ed9968ede3c44524f0504a8942ddd479/pyVirtualLab/Instruments/Keysight/KeysightN6705C/Signals.py |
| EXT-05 KK_Lab | https://github.com/KversKv/KK_Lab/blob/ec7e800cedbc939a49bb4dc71602c3bc110f2929/instruments/power/keysight/n6705c.py |
| EXT-06 VoltFraud | https://github.com/PDSHan/VoltFraud/blob/20a9030a8dea9d59e481cf00a220c5f51c496068/VoltFraud-sgx/lib/DCpower_ctr.py |
| EXT-07 EIS_keysight | https://github.com/katzgo/EIS_keysight/blob/4a8912dc58d689e166d0d67dc519b252821569b0/Pruebas_EIS/EIS_GenerateARB_v7_clean.py |
| EXT-08 IVM6311ATE | https://github.com/HarishKumarSedu/IVM6311ATE/blob/4b41017d16d87b0050eeccc5588c850b9acd57bd/Instruments/KeySight_N670x.py |
| EXT-09 C++ pulse source | https://github.com/MrBuslic/SERVER/blob/a797986e16d07bece03aeacbedecc65711b410b5/src/Devices/PUOP/PowerSource/PSModule.cpp |
| EXT-10 N67xx engineering notes | https://github.com/tmbinc/random/blob/81459d9bd0678243f8971a12b784327b55b0d5bb/agilent/n67xx/README.md |

Most third-party repositories have their own licenses (SweepMe!'s reviewed file explicitly contains MIT terms). Confirm the **actual source-path license** and attribution before any copying. This document links and paraphrases but **does not import third-party code**.

## 8. Worked code examples and offline tests (v0.7.6)

See [**ARB_WORKED_CODE_EXAMPLES.md**](ARB_WORKED_CODE_EXAMPLES.md), which converts the reviewed sources into an existing simulator-only UDEF execution example and six original, offline-only proposals: typed PULSE compiler, frequency/duty conversion, mainframe/Option 054 capability gating, CDW vector formatting, finite sequence compilation, and trigger-family selection. Includes explicit source links and seven negative/contract tests. Standard PULSE/CDW/sequence APIs are **not implemented** in the driver; no live instrument command sequences are provided.
