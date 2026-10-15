# Task: Production-oriented N6700 Arb waveform generator integration

**Task ID:** N6700-ARB-001 · **Prepared:** 2026-10-15 · **Status:** READY TO START REVIEWED PRs; **unimplemented features remain unimplemented**
**Repository:** https://github.com/ami3go/keysight-n6700-driver
**Inspected baseline:** `main@0594f5161ca49169fe75171b466842320d29a26f`, package `0.7.6`
**Core dependency pinned at baseline:** `scpi-driver-core@ec19ab88906d8e6ac270b30d7615ce7e75dc6a08` in both normal and `[visa]` requirements.
**Companion core task:** [N6700_ARB_CORE_HARDENING_TASK.md](https://github.com/ami3go/scpi-driver-core/blob/main/task/N6700_ARB_CORE_HARDENING_TASK.md) (published on core `main` at commit [`ef913b1`](https://github.com/ami3go/scpi-driver-core/commit/ef913b1078600630af511c7aebb652092f4651b6)).
**Status constraint:** this document is a **task**, not a code delivery, software qualification, or permission to energize hardware. All code blocks are implementation sketches unless explicitly labeled current public API.

## 1. Objective and scope

Implement safe, validated **finite voltage and current Arb profiles** for N6700-family modules by extending the existing, working **bounded voltage LIST/UDEF** transient state machine rather than making a second transport or a second trigger state machine. Planned stages: Option 054 / mainframe-aware capability classification, truthful trigger source selection, standard voltage shapes, bounded current shapes, constant dwell and indexed sequences, robust error-state handling, and independent analog qualification.

The generic [`scpi-driver-core`](https://github.com/ami3go/scpi-driver-core) already provides byte transport, text SCPI, IEEE blocks, locking, error queue, trace and polling. Do **not** duplicate those layers here; consult the companion task for narrowly scoped core fixes.

### Primary reviewed evidence

| Reference | Relevant section / verified fact |
| --- | --- |
| [Keysight N6700C Operating and Service Guide](https://www.keysight.com/nz/en/assets/9018-04957/service-manuals/9018-04957.pdf) | Model Differences/Option 054; generating Arb waveforms; `ARB:FUNC:TYPE`, `ARB:FUNC:SHAP`, pulse/sequence/point command families |
| [Keysight N6705C User's Guide](https://www.keysight.com/us/en/assets/9018-01649/user-manuals/9018-01649.pdf) | N6705C Arb behavior, module limits and trigger sequencing; different mainframe context |
| [`docs/ARB_WAVEFORM_REFERENCE.md`](ARB_WAVEFORM_REFERENCE.md) | Vendor command inventory, point limits, supported types, safe execution contract |
| [`docs/ARB_SOURCE_CODE_ANALYSIS.md`](ARB_SOURCE_CODE_ANALYSIS.md) | Ten inspected third-party projects, five static driver findings, source-to-design comparisons |
| [`docs/ARB_WORKED_CODE_EXAMPLES.md`](ARB_WORKED_CODE_EXAMPLES.md) | Current simulator-only UDEF example; pure offline PULSE/CDW/sequence planners and negative tests |
| [`src/keysight_n6700/channel.py`](../src/keysight_n6700/channel.py) | Current `configure_voltage_list/arb`, channel lock, `_configuration_failed`, trigger/WTG/abort lifecycle |
| [`src/keysight_n6700/module_capabilities.py`](../src/keysight_n6700/module_capabilities.py) | Source `supports_list_mode=True` and `supports_voltage_arb=True` currently independent of options/mainframe |
| [`src/keysight_n6700/driver.py`](../src/keysight_n6700/driver.py) | `discover_modules()` knows the channel module and options, but current classifier does not receive mainframe identity |
| [`tests/unit/test_voltage_transient.py`](../tests/unit/test_voltage_transient.py) | Existing simulator tests for ordered SCPI, finite durations, readback quantization and token safety |

## 2. Architecture and critical invariants

**Do not break existing LIST/UDEF callers.** The source API remains Python-first with optional Robot binding; avoid a new `src/` migration, separate instrument drivers, vendor-specific changes in generic core, or hidden hardware actions in constructors.

Required invariants for **every** new waveform API:
1. **One state/owner per channel** with a serialized mainframe SCPI session; different channels share a transport and **may share global trigger/protection state**. Discover model, options, firmware and mainframe first.
2. Configuration is allowed **only with source output confirmed OFF**. Do not issue `*RST`, output enable, `*TRG`, `OUTP:PROT:CLE`, or infinite `ARB:COUN` in connection/configuration.
3. Validate **entire request** before the first SCPI mutation: finite numeric values, waveform extrema (sine offset ± amplitude), dwell and frequency, point memory, module voltage/current/power capability, OVP/current protection, repeat counts, total run duration and approved DUT limits.
4. Configure/reconfigure under a **common `ScpiSession.operation_lock()`** for all SCPI write/readback steps, not only `PowerSupplyChannel._transient_lock`. Do not hold the session lock through the full waveform duration or blocking poll loop.
5. Verify **effective** SCPI readbacks and error queue. A successful write or a SCPI echo is **not** evidence that the analog waveform meets specifications.
6. Preserve generation-token protection, per-channel WTG/active status polling, finite waits, abort plus `INIT:CONT:TRAN OFF`, explicit output OFF and **UNKNOWN** state if physical state cannot be confirmed.
7. **No non-idempotent auto-retry**, especially after write timeout: a trigger/output change may have reached hardware. Never infer that reconnect means source output is safely OFF.
8. No power/resistance Arb on ordinary power-supply modules. Only consider N679xA **electronic load** Arb in a separate model-specific plan after HIL.
9. Keep acquisition/scope as a distinct state machine; any simultaneous Arb+digitizer work respects acquisition leases, snapshot ordering and trigger ownership.

**Software status remains simulation/protocol qualified only until guarded HIL + independent DSO acceptance.**

## 3. Deliverables, ownership and PR dependency graph

| Work package | Priority | Branch / PR suggestion | Depends on | Merge gate |
| --- | --- | --- | --- | --- |
| **DRV-ARB-A** | P0 | `fix/arb-module-option-capabilities` | None | Exact mainframe/model/option matrix, negative tests |
| **DRV-ARB-B** | P0 | `fix/arb-failure-preservation-and-locking` | None; coordinate A | Primary errors preserved; no SCPI interleaving |
| **DRV-ARB-C** | P0 | `feat/arb-trigger-command-contract` | A + B | Correct `ARB` vs `TRAN` source command mapping, no global cross-channel trigger |
| **DRV-ARB-D** | P1 | `feat/finite-voltage-standard-arbs` | A/B/C | Typed finite STEP/RAMP/STA/SIN/PULS/TRAP/EXP with validators and readbacks |
| **DRV-ARB-E** | P1 | `feat/finite-current-arbs` | D, model limits | Correct current-mode/SMU compliance behavior and independent envelope |
| **DRV-ARB-F** | P1 | `feat/arb-constant-dwell` | D or E; large binary simulation from core only **if** binary chosen | Finite bounded vector transfer and readback; protocol framing |
| **DRV-ARB-G** | P2 | `feat/arb-sequence` | D + F where reused | Finite zero-based indexed sequence, memory and duration limits |
| **DRV-ARB-H** | P1 integration | `chore/pin-reviewed-scpi-core` | Core task CORE-ARB-A/B merged and tested | Both dependency refs pinned to one verified core commit |
| **DRV-ARB-I** | Release gate | `test/arb-hil-qualification` | Selected completed feature PRs | Reviewed site plan, DSO and independent physical evidence |

**Parallel work:** A and B can start simultaneously. Keep D/E/F/G as separate bounded feature PRs with final rebases; never merge while a prerequisite is unmerged. H may occur before D if the new core build passes all existing regressions, but is not a prerequisite for standard-shape text SCPI. I is a **nonenergizing preparation task** until site authorization is documented.

## 4. DRV-ARB-A — correct module capability detection (blocking)

### Verified current-code problem

`module_capabilities.classify_module(model, options)` sets both `supports_voltage_arb` and `supports_list_mode` to `True` for **every** `POWER_PREFIXES` match, even if options are empty. `discover_modules()` retrieves each module's options but does not supply the mainframe model to classification. Existing `tests/unit/test_module_capabilities.py::test_n677x_supports_voltage_list_arb_and_slew` assumes `classify_module("N6775A")` supports both without `054`.

Keysight documents Option 054 for ordinary N673x/N674x/N675x/N677x source LIST/Arb in the **N6700C** guide; **N676x precision sources and N678x SMUs differ**, and N6705C has its own built-in capability context. **Do not apply one Option 054 rule to all mainframes or model families.** Verify exact A/B suffix/model, unsupported negative-current SMU modes, firmware and options with the official guide.

### Proposed interface and algorithm (design sketch)

~~~python
from dataclasses import dataclass

@dataclass(frozen=True)
class InstrumentContext:
    mainframe_model: str
    firmware_version: str
    module_model: str
    options: frozenset[str]

def supports_voltage_arb(ctx: InstrumentContext) -> bool:
    main = ctx.mainframe_model.strip().upper()
    model = ctx.module_model.strip().upper()
    options = {x.strip().upper() for x in ctx.options}
    # Narrow, officially reviewed example; build out exact model matrix.
    if main == "N6700C" and model == "N6775A":
        return "054" in options
    if main == "N6705C" and model == "N6775A":
        return True  # recheck precise model/firmware applicability
    return False  # unknown and unreviewed model combinations fail closed
~~~

Implementation: obtain mainframe via existing session identity/`*IDN?`, normalize `SYST:CHAN:MOD?` and `SYST:CHAN:OPT?`, compute a stable per-channel capability fingerprint (mainframe/module/options/firmware), and reject an unknown option query with **unsupported**, not `True`. Extend `ChannelCapabilities` rather than inventing a parallel capability taxonomy. Reclassify on rediscovery/reconnect and invalidate stale transient tokens/configs if installed module identity changes.

**Tests:** parametrize mainframe N6700C/N6705C/unknown × N6775A with no/054/unknown options; test N676x, N678x, N679x separately and unknown suffix; assert an unsupported `configure_voltage_arb()` generates **no** Arb writes. Include a test correcting the current falsely positive N6775A expectation. **Do not claim a supported module from just an error-free query.**

## 5. DRV-ARB-B — preserve errors and serialize configuration

### Verified current-code problem

`configure_voltage_list/arb()` catches `BaseException`, calls `_configuration_failed()`, then re-raises; but `_configuration_failed()` itself writes abort/continuous-off/fixed-mode and can throw. A cleanup error may **mask the primary write/readback error**. Current `_cleanup_failed_voltage_transient(primary)` already demonstrates a safer approach: preserve the primary and attach cleanup result.

Additionally, `_transient_lock` is per channel but all channels share a core `ScpiClient` and underlying mainframe. Individual writes are serialized, yet another thread/channel could interleave *between* waveform-configuration writes. Use the already existing core **`ScpiSession.operation_lock()`**, exposed cleanly through the facade/driver as necessary.

~~~python
# SKETCH: integrate with existing channel/facade; not directly pasteable.
with self._transient_lock:
    self._require_output_off_for_configuration()
    with self._driver.session_operation_lock():  # new facade delegation
        try:
            self._prepare_transient_configuration()
            self._write_plan(verified_commands)
            self._verify_readbacks()
        except BaseException as primary:
            outcome = self._best_effort_abort_and_restore_fixed()
            self._transient_state = "unknown"
            self._transient_config = None
            # Primary exception remains the one raised.
            if hasattr(primary, "add_note"):
                primary.add_note(f"Arb cleanup: {outcome}")
            raise
~~~

**Design cautions:** establish a single documented lock order (channel ownership → client/session, or coordinate with existing acquisition lock), verify no callback acquires these in reverse order and do not sleep/poll while holding shared session lock. Cleanup should attempt `ABOR:TRAN`, `INIT:CONT:TRAN OFF`, `VOLT:MODE FIX` and safe output-OFF as required by operation context; a failed physical-off/readback leaves **UNKNOWN**, not success.

**Tests:** failure at each SCPI position including query and error-queue check, secondary cleanup write failure, two channels configuring in separate threads, disconnect mid-readback, cancellation/KeyboardInterrupt behavior, and bounded no-deadlock watchdog tests. Assert the original exception identity/typed class is preserved.

## 6. DRV-ARB-C — resolve Arb vs transient trigger semantics

The official SCPI trees document both `TRIG:TRAN:SOUR` and `TRIG:ARB:SOUR`. The existing driver uses `TRIG:TRAN:SOUR` for LIST/UDEF Arb and `TRIG:TRAN (@n)` for channel-scoped software trigger. Third-party examples use `TRIG:ARB:SOUR` with different channel forms. **Not a confirmed bug until readback and actual triggering are compared on supported hardware.**

**Implementation contract:**
- Keep a typed *command dialect / capability* for each approved mainframe/firmware/mode, not an operator-supplied SCPI string.
- Determine exact syntax and whether `TRIG:ARB:SOUR` is mainframe-global or channel-qualified. Reject unresolved selectors.
- Preserve per-channel targeted `TRIG:TRAN (@n)` for confirmed BUS behavior; forbid mainframe-wide `*TRG` unless a separate synchronized multi-channel plan is authorized.
- Record source-readback, status WTG mask, transient active mask, channel and version in evidence.

~~~python
# Offline mapping sketch — only after documented & HIL verification.
def trigger_source_command(*, dialect: str, ch: int) -> str:
    if not 1 <= ch <= 4:
        raise ValueError("channel must be 1..4")
    if dialect == "transient_channel_scoped_verified":
        return f"TRIG:TRAN:SOUR BUS,(@{ch})"
    if dialect == "arb_mainframe_scoped_verified":
        return "TRIG:ARB:SOUR BUS"
    raise ValueError("SCPI trigger source dialect not qualified")
~~~

**Tests:** mainframe-specific golden vectors, software BUS versus external trigger, early/missed trigger, stale token, cross-channel WTG behavior, unknown dialect refusal, and no global `*TRG` in ordinary runs. A simulator passing both dialects is **not** HIL proof.

## 7. DRV-ARB-D — finite standard voltage waveform shapes

### API proposal (illustrative, deliberately not yet implemented)

~~~python
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class VoltagePulse:
    start_v: float
    top_v: float
    start_s: float
    top_s: float
    end_s: float

@dataclass(frozen=True)
class VoltageSine:
    offset_v: float
    amplitude_v: float
    frequency_hz: float

VoltageArbShape = VoltagePulse | VoltageSine  # later include STEP/RAMP/...

def configure_voltage_shape(
    channel: int,
    shape: VoltageArbShape,
    *,
    minimum_voltage_v: float,
    maximum_voltage_v: float,
    repeat_count: int = 1,
    maximum_duration_s: float = 60.0,
) -> None:
    """PROPOSED API; no implementation in v0.7.6."""
    raise NotImplementedError
~~~

**First production PR:** complete all seven standard shapes: `STEP`, `RAMP`, `STAircase`, `SINusoid`, `PULSe`, `TRAPezoid`, `EXPonential`, with immutable shape-specific types, exact vendor setters/queries and capability gating. Reuse current LIST/UDEF state/token management, immutable configuration objects and readback tolerances. No continuous/infinite repeat, silent auto-output or `TERM:LAST ON` by default.

**Pure preflight:** reject NaN/Inf, negative/zero illegal durations, amplitude extrema outside min/max, bad frequency, excessive rise/fall/dwell and total worst-case duration, invalid count, unsupported shape/model and signal direction. Validate OVP/current/power/SMU limit envelope as applicable, including slew-induced transients. **Quantization:** read back effective frequency/timings; `ARB:VOLT:PULS:FREQ` interacts with pulse segment times, so choose one canonical request mode (frequency/duty **or** explicit segment times).

~~~python
# Example of full-envelope validation (pure, no I/O).
import math

def validate_sine(offset_v: float, amplitude_v: float,
                  min_v: float, max_v: float) -> None:
    values = (offset_v, amplitude_v, min_v, max_v)
    if not all(math.isfinite(v) for v in values) or amplitude_v < 0:
        raise ValueError("invalid sine values")
    if offset_v - amplitude_v < min_v or offset_v + amplitude_v > max_v:
        raise ValueError("sine exceeds approved output envelope")
~~~

**SCPI example only, never auto-executed:**

~~~text
ARB:FUNC:TYPE VOLT,(@1)
ARB:FUNC:SHAP PULS,(@1)
ARB:VOLT:PULS:STAR 12,(@1)
ARB:VOLT:PULS:TOP 6,(@1)
ARB:VOLT:PULS:STAR:TIM 0.005,(@1)
ARB:VOLT:PULS:TOP:TIM 0.020,(@1)
ARB:VOLT:PULS:END:TIM 0.005,(@1)
ARB:COUN 1,(@1)
ARB:TERM:LAST OFF,(@1)
VOLT:MODE ARB,(@1)
~~~

**Order:** validate → output-OFF readback → prepare `ABOR:TRAN`/`INIT:CONT:TRAN OFF` → program under session lock → verify every effective readback/errors → mark configured. The caller separately approves output enable, arms, waits WTG, triggers and confirms completion. See [worked examples](ARB_WORKED_CODE_EXAMPLES.md).

## 8. DRV-ARB-E — current Arb and SMU priority/compliance

Propose `configure_current_shape(channel, shape, *, minimum_current_a, maximum_current_a, repeat_count, maximum_duration_s)`. Reuse shape dataclasses where safe, but keep separate voltage/current validators and SCPI templates. A current shape is not a voltage shape with the prefix substituted blindly.

**Model facts to gate:** N678x SMU priority/quadrant, current limit vs commanded source, negative current validity, protection and N679x electronic-load-specific behavior. Do not silently extend power-supply APIs to load power/resistance modes. Read back actual `FUNC` mode/priority, approved limits, `CURR:MODE ARB`, all shape settings and protection state.

**Tests:** type × model × priority × quadrant negative matrix, overload/protection, fail-closed unsupported module, stale cached capability, and transfer of wrong current/voltage commands. No real DUT unless independent energy/protection review passes.

## 9. DRV-ARB-F — constant-dwell vector transfer

Vendor `CDW` uses **one common dwell and many levels**, unlike UDEF per-point dwell, with a different hardware point/sequence budget. Plan a finite API:

~~~python
# PROPOSED ONLY
@dataclass(frozen=True)
class CurrentConstantDwell:
    levels_a: tuple[float, ...]
    dwell_s: float
    repeat_count: int = 1

def validate_current_cdw(profile: CurrentConstantDwell,
                         *, minimum_a: float, maximum_a: float,
                         point_limit: int, duration_limit_s: float) -> None:
    import math
    if not 1 <= len(profile.levels_a) <= point_limit:
        raise ValueError("CDW point count")
    if (not all(math.isfinite(v) and minimum_a <= v <= maximum_a
                for v in profile.levels_a)
        or not math.isfinite(profile.dwell_s)
        or profile.dwell_s <= 0
        or type(profile.repeat_count) is not int
        or profile.repeat_count < 1):
        raise ValueError("invalid current CDW")
    if len(profile.levels_a) * profile.dwell_s * profile.repeat_count > duration_limit_s:
        raise ValueError("CDW total duration")
~~~

Choose **ASCII CDW commands first** if manual permits and message size is instrument-supported. `ScpiTextCodec.maximum_command_size` is configurable, but raising it globally needs an explicit maximum and memory/latency review. Use generic `ScpiClient.write_binary_block` **only** for a manufacturer-confirmed binary input grammar and correct endian/data type; this is where core task CORE-ARB-B helps. Do not fragment one vector into multiple SCPI writes as an undocumented optimization.

**Tests:** 1, boundary and max points, zero/negative/NaN/Inf, oversized ASCII, `readback COUNT`, quantized effective dwell, large delays, malformed answer, vector containing terminator-equivalent bytes in binary block (if supported), and output-OFF on failure. Check error queue and ensure exact effective number of accepted points.

## 10. DRV-ARB-G — indexed finite sequences

Use source [sequence command catalog](ARB_WAVEFORM_REFERENCE.md#6-sequence-arb-command-inventory) and [offline plan](ARB_WORKED_CODE_EXAMPLES.md#7-example-6--two-step-arb-sequence-planning-and-command-ordering).

- Zero-based consecutive index creation 0–99; no gaps or silent step reordering.
- Calculate the **vendor-generated point budget**, not simply count Python objects; sequence waveform-point limit and CDW memory have distinct meanings.
- Validate step type/shape, step repeats, pacing (`DWELL` vs `TRIGgered`), global repeat, per-step time and total finite worst-case execution **before** the destructive `ARB:SEQ:RES`.
- Only use sequence-specific `ARB:SEQ:STEP:* ` commands; no imported generic source-mode assumptions. Query `ARB:SEQ:LENG?` and `ARB:SEQ:QUAL?` as appropriate and record expected vs actual.
- Reject unbounded triggered pacing unless the test harness defines a finite trigger supply schedule and timeout/abort contract.

**Illustrative validator:**

~~~python
# PROPOSED ONLY
def validate_sequence_length(step_indices: tuple[int, ...]) -> None:
    if not step_indices or len(step_indices) > 100:
        raise ValueError("sequence length outside 1..100")
    if tuple(range(len(step_indices))) != step_indices:
        raise ValueError("sequence steps must be zero-based, contiguous, ordered")
~~~

**Tests:** 0, 1 and 100 steps; index 100; wrong ordering; large sine/shape generated point count; low/high quality `ARB:SEQ:QUAL?`; count overflow; pacing and trigger starvation; interrupted upload; abort during sequence step and mainframe channel isolation.

## 11. DRV-ARB-H — consume reviewed scpi-driver-core commit

**Do not upgrade blindly.** Baseline core dependency is pinned to `ec19ab8` in both `pyproject.toml` locations; the companion core task was published as docs-only core `0.1.0.dev7` at `ef913b1` and **does not yet implement** its own core deadlines/binary parser.

1. Once CORE-ARB-A/B are merged, record exact new immutable core commit and commit-by-commit API changes.
2. Create a feature branch for compatibility, pin **both** base and `[visa]` extra to the same **reviewed SHA**, bump N6700 package version, and run isolation install, wheel/sdist, pytest simulator and existing voltage-transient/scope/acquisition suites.
3. Verify multichannel lock order, byte-preservation, `write_binary_block/query_binary_block`, error translation, tracing, session-state and timeout behavior; do not assume generic core mock implies N6700 protocol qualification.
4. On incompatibility, fix in appropriate owning repository, rerun tests and keep the existing pin until passing.
5. Merge with changelog and release-evidence links. No broad `@main` or unpinned dependency for 24/7 test-environment use.

## 12. DRV-ARB-I — HIL and independent DSO acceptance

**Preparation may be merged software-only; physical execution requires separately documented site authorization.** HIL equipment needs an approved N6700C or N6705C mainframe, exact installed source models/options, a passive or protected test load, independent scope with calibrated probes and sample rate suitable to the waveform, documented E-stop/inhibit, wiring review and a site-approved operating envelope.

Minimum matrix:
- Supported/unsupported module–mainframe–Option 054 combinations.
- STEP/RAMP/PULSE/SIN amplitudes, dwell, frequency, slew, over/undershoot, load dependence, and duration/repeat count.
- BUS and external trigger timing/latency/jitter; source vs acquisition synchronization; two-channel isolation.
- Bad trigger, unplugged cable, VISA/TCP timeouts, instrument SCPI command error, OVP/OCP trip and cross-channel protection coupling.
- Abort/timeout/failed cleanup: `INIT:CONT:TRAN OFF`, output OFF and independent hardware-off readback; unknown state blocks further tests.
- Evidence: *IDN, module/options/firmware, requests/readbacks, source limits, raw trace, error queue, DSO raw waveform, calibrated timing/amplitude uncertainty, operator authorization, hashes and signed result.

HIL outcome classifications: **simulation pass**, **protocol conformance pass**, **analog metrology pass** and **approved unattended DUT bench** are four separate approval levels.

## 13. Automated tests and CI acceptance

| Package | Required hardware-free assertion | Owning suite |
| --- | --- | --- |
| A | Model/option/context exact matrix; no SCPI side effects when unsupported | `tests/unit/test_module_capabilities.py`, new table tests |
| B | Multi-thread interleave prevention, primary error vs cleanup, no deadlocks | `tests/unit/test_voltage_transient.py` + fault-injection |
| C | Trigger family command truth, WTG/active bit transition, foreign token/channel protection | `tests/unit/test_voltage_transient.py` + simulator command-golden |
| D | Shape × parameter × channel vectors; quantization, envelope, finite repeats, readback, safe OFF | new `tests/unit/test_arb_standard_shapes.py` |
| E | Current priority and compliance modes, supported negative quadrant, wrong-family refusal | new `tests/unit/test_current_arb.py` |
| F | CDW length/dwell, ASCII limit, binary write framing (only if supported), readback | new `tests/unit/test_arb_cdw.py` |
| G | Zero-based step order, 511 generated-point budget, pacing, repeat/time limit, sequence cleanup | new `tests/unit/test_arb_sequence.py` |
| H | Pinned core upgrade; old and new core matrix for all existing LIST/UDEF, digitizer/scope APIs | `tests/unit/test_scpi_driver_core_integration.py` plus full suite |
| I | Site-authorized HIL + DSO evidence; abnormal-failure safety, no output left energized | `tests/hardware/test_hardware_voltage_transient.py` and scoped Arb additions |

**Quality gates per PR:** correct version bump, Ruff/format, strict mypy, Python 3.10–3.13 CI, pytest offline, conformance protocol vectors, negative fault injection, wheel/sdist, documentation and independent review. Check suite paths in current `pyproject.toml` before adding tests. Preserve existing public Python + Robot APIs; update LPDS plugin manifest and AI contract **when public capabilities actually change**. Do not merely mark Arb supported in manifests before tests and model gates pass.

### Suggested test patterns

~~~python
# Example negative unit test for DRV-ARB-A after classifier change:
def test_n6700c_n6775a_without_option_054_is_rejected():
    caps = classify_module(
        "N6775A", options=(),
        mainframe_model="N6700C",  # PROPOSED signature
    )
    assert not caps.supports_voltage_arb
    assert not caps.supports_list_mode
~~~

~~~python
# Example source-independent no-side-effects assertion:
transport.clear_history()
with pytest.raises(DriverUnsupportedOperationError):
    driver.configure_voltage_shape(
        1, VoltagePulse(12, 6, .005, .020, .005),
        minimum_voltage_v=0, maximum_voltage_v=18,
    )
assert not any("ARB:" in command for command in transport.history)
# IMPORTANT: configure_voltage_shape does NOT exist at task publication.
~~~

## 14. Review-before-merge checklist

1. Start from latest `main`; compare against this baseline and the [companion core task](https://github.com/ami3go/scpi-driver-core/blob/main/task/N6700_ARB_CORE_HARDENING_TASK.md).
2. Review exact Keysight command spelling **and** model applicability before coding; do not derive SCPI grammar from third-party code alone.
3. Create one bounded PR, update documented API/config/schema and safety rationale, add negative unit and simulator tests **first**, then implementation.
4. Run independent technical review against thread safety, energy/overvoltage limits, fallback state, option gating, cleanup, readback, timeout and trace semantics. Fix all blockers in same PR.
5. Rebase PR onto latest merged prerequisites. Re-run all CI and conformance tests; do not merge a green prior head after a rebase without rerun.
6. Update `CHANGELOG.md`, `history/`, package version and `ai/ai_contract.yaml`. Robot adapter is **independently versioned**; bump it only if its own public contract changes.
7. Preserve the current module layout and installation behavior; do not use a broad dependency update without a pinned, tested new core SHA.
8. Merge only when offline gates are green; physical qualification remains explicitly blocked until HIL acceptance.

### Completion criteria

- [ ] A: Mainframe-aware option capability gates pass negative matrix
- [ ] B: Primary error preservation and shared-session command serialization pass
- [ ] C: Arb and transient trigger selection verified without global side effects
- [ ] D: Seven finite voltage standard shapes implemented with readback and simulation
- [ ] E: Approved finite current shapes with priority/SMU compliance
- [ ] F: CDW vector and limit tests; vendor-confirmed transfer framing
- [ ] G: Indexed finite sequence builder with generated-point/time budgets
- [ ] H: Exact reviewed core commit pinned and regression compatibility green
- [ ] I: Site-authorized independent DSO qualification evidence, no unknown output states
- [ ] Every PR has a version increment, independent review, green CI and merge evidence
- [ ] No production/HIL-ready claims from simulator-only tests

**Task publication version:** `v0.7.7` updates documentation/metadata only. No runtime implementation, core-pin change or hardware operation is authorized or completed by this task file.
