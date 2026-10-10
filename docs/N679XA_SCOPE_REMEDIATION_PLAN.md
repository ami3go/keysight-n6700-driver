# N679xA Electronic Load Scope / Buffered Acquisition — Remediation and Qualification Plan

**Document revision:** 1.0 (2026-10-10)  
**Status:** READY FOR IMPLEMENTATION IN ORDERED PRs; NOT a certification of current runtime safety  
**Repository:** https://github.com/ami3go/keysight-n6700-driver  
**Reviewed code baseline:** main at 49aa10eadf4a050198845345af642de3fd50bcde (package v0.6.0)  
**Scope:** N6791A/N6792A load-channel digitizing via SENS:SWE, TRIG:ACQ, INIT:ACQ, FETC:ARR; transport, concurrency, hardware test evidence, reporting interface.  
**Execution authority:** This is a design/change specification, not permission to energize equipment or execute hardware tests. The maintainer must confirm each HIL wiring and test envelope.

## 1. Purpose, evidence, and outcome

Implement and independently verify all 14 findings in the v0.6.0 scope review, with particular emphasis on **correct waveform provenance and safe, bounded acquisition lifecycle**. The target is a dependable waveform-capture subsystem suitable for unattended power-supply regression testing *within a demonstrated measurement accuracy envelope*, not a feature-equivalent replacement for an arbitrary-bandwidth DSO.

The repository already records a successful BUS-triggered 256-point round trip and six current-level-triggered 1,024-point N6791A voltage captures at 20.48 microseconds/sample, each with 512 pre-trigger points. Those results establish initial functional feasibility, not fault tolerance or measurement uncertainty. Do not discard or overwrite historical evidence.

**Out of scope:** unrelated power supply/SMU behavior; repository-wide layout changes; new GUI; implicit powering or re-triggering DUTs; automatic deployment to production benches; claiming support for models not independently qualified.

### 1.1 Normative reference index

- **K-UG:** Keysight N6705C DC Power Analyzer User's Guide, chapters 1 and 6, *Digitizing Measurements* (printed pp. 211–217), *Measurement Data Formats* (p. 222), model-difference tables (pp. 41–45), and *Using the Scope Functions* (pp. 140–150). Official guide: https://www.keysight.com/us/en/assets/9018-01649/user-manuals/9018-01649.pdf . The repository includes a text extraction: [Keysight_documents/N6791A_manual.pdf.md](../Keysight_documents/N6791A_manual.pdf.md). Verify page numbering against the exact revision used for implementation.
- **K-PR:** Keysight N6705 Programmer's Reference Help Guide, command definitions for SENSe:SWEep, SENSe:FUNCtion, TRIGger:ACQuire, INITiate:ACQuire, ABORt:ACQuire, STATus:OPERation:CONDition?, FORMat[:DATA], FORMat:BORDer, FETCh:ARRay, and SYSTem:ERRor?. https://www.keysight.com/us/en/assets/9018-03616/programming-guides/9018-03616.pdf . Command response shape, accepted range and device revision MUST be verified for the installed mainframe/module.
- **K-HIL:** Existing repository [hardware test instructions](../docs/hardware_acceptance_tests.md), [v0.6.0 release evidence](../history/v0.6.0.md), [known risks](../review/known_risks.md), and [six-case N6791A evidence](../results/load_transient/20261009_n6791a_internal_buffer/README.md).
- **K-CORE:** scpi-driver-core transport/session/locking/polling contracts, version pinned by [pyproject.toml](../pyproject.toml); validate error translation and raw binary query support before choosing APIs.
- **K-LPDS:** Repository capability/contract and conformance conventions: [capability/capability_model.yaml](../capability/capability_model.yaml), [ai/ai_contract.yaml](../ai/ai_contract.yaml), [tests/conformance/data/protocol_vectors.yaml](../tests/conformance/data/protocol_vectors.yaml), [tests/conformance/data/exclusions.yaml](../tests/conformance/data/exclusions.yaml), and [scripts/generate_ai_contract.py](../scripts/generate_ai_contract.py).
- **R-CODE:** [channel.py](../src/keysight_n6700/channel.py) lines 517–719, [driver.py](../src/keysight_n6700/driver.py) lines 945–1017, [simulator.py](../src/keysight_n6700/simulator.py), [scpi.py](../src/keysight_n6700/scpi.py), [base.py](../src/keysight_n6700/base.py).
- **R-REVIEW:** v0.6.0 independent review, 2026-10-10, fourteen findings F01–F14 (the review was delivered separately; the issue descriptions below are self-contained).

### 1.2 Evidence standard and decisions that cannot be assumed

Distinguish **documented command behavior**, **source-code observation**, **offline simulator behavior**, **verified HIL behavior**, and **assumption pending experiment**. In PR descriptions and final release reports, use those labels. In particular:

1. Keysight explicitly says a trigger arriving before the requested pre-trigger record has filled is ignored (K-UG, printed p. 213).
2. Keysight describes 512 Ki-points shared among active measurements, interval quantization, and binary IEEE-754 single-precision REAL array replies (K-UG / K-PR). Do not assume a requested time interval is the effective interval.
3. The operation-status WTG_meas and MEAS_active bits are **status observations**, not an unconditional proof that the current software generation completed. Rapid acquisitions may finish between polls. An algorithm that treats a cleared bit as success without additional provenance is unsafe.
4. The published evidence establishes N6700C firmware E.02.09.3271 with N6791A. N6792A behavior is vendor-described but not yet HIL-qualified in this repository.
5. Neither trigger jitter, analog bandwidth, threshold crossing latency nor a tolerance for time-domain pass/fail has yet been demonstrated against a synchronized reference DSO.

## 2. Core architectural contract (mandatory before individual fixes)

### 2.1 Lifecycle ownership and state model

Preserve the current public eight-method API for existing callers, but route methods through an internal **per-session acquisition coordinator**. One mainframe is a shared resource: measurement buffers, FORM format and SCPI error state can interact across channels. Scope ownership cannot be guaranteed by a lock around only one query.

Proposed explicit states:

| State | Meaning | Allowed next states |
|---|---|---|
| IDLE | No owned in-progress capture | CONFIGURED |
| CONFIGURED | Setpoints/readbacks committed, no capture active | ARMED, IDLE |
| ARMED | INIT accepted; readiness not yet proven | WAITING, ABORTED, FAILED, UNKNOWN |
| WAITING | Waiting for trigger; pre-trigger barrier satisfied before operator-generated event | ACQUIRING, COMPLETE, ABORTED, FAILED, UNKNOWN |
| ACQUIRING | Trigger accepted or status indicates active capture | COMPLETE, ABORTED, FAILED, UNKNOWN |
| COMPLETE | Current-generation acquisition demonstrated complete; buffer reserved for read | FETCHED, FAILED, UNKNOWN |
| FETCHED | Waveform retrieved and validated; ownership may release | IDLE |
| ABORTED | Acquisition canceled; no waveform may be returned as valid | IDLE |
| FAILED | Recoverable instrument/protocol error, cleanup attempted | IDLE after verified reset |
| UNKNOWN | Communication failed; hardware state cannot be verified | IDLE only after explicit reconnect/reconciliation |

A quick capture need not expose the ACQUIRING state in polling; transition directly WAITING -> COMPLETE is permitted only with generation evidence and instrument-readback validation. **Prove or appropriately limit** what can be concluded from status bits; do not invent firmware guarantees. Use an immutable acquisition generation ID plus session alias, channel, actual settings, trigger mode, start/finish timestamps, and error/abort information. A reconnect or discovery reset must invalidate current generations.

**Important separation:** The acquisition coordinator controls the measuring subsystem. DUT energization/load-step safety stays in the bench harness. On an instrument timeout do not silently repeat a hazardous load step. Coordinate with the bench's existing guarded shutdown/final verification policy.

### 2.2 Proposed typed API (illustrative; not existing executable code)

~~~python
from dataclasses import dataclass
from enum import Enum, auto

class AcquisitionState(Enum):
    IDLE = auto()
    CONFIGURED = auto()
    ARMED = auto()
    WAITING = auto()
    ACQUIRING = auto()
    COMPLETE = auto()
    FETCHED = auto()
    ABORTED = auto()
    FAILED = auto()
    UNKNOWN = auto()

@dataclass(frozen=True)
class DigitizeConfig:
    function: str
    points: int
    requested_interval_s: float
    actual_interval_s: float
    offset_points: int
    trigger_source: str
    interval_resolution: str
    channel: int

@dataclass(frozen=True)
class AcquisitionToken:
    alias: str
    channel: int
    generation: int
    session_generation: int

@dataclass(frozen=True)
class WaveformEvidence:
    token: AcquisitionToken
    config: DigitizeConfig
    sample_count: int
    trigger_time_reference: str
    transport_format: str
    instrument_idn: str
    module_model: str
    firmware: str
    sha256: str
~~~

The public ArrayMeasurement should retain backwards-compatible fields (values/unit/format/interval_s/offset_points), adding optional metadata rather than removing or changing existing positional contracts. Preserve single-channel aliases and existing error classes where practical.

### 2.3 Session lease, locks and external-controller contract

Introduce a **session-wide acquisition lease** acquired before the first configuration mutation and held through validated fetch/abort. The same thread/task owning a lease may issue status polls and approved load-step commands without deadlock; other threads' conflicting MEASure, INITiate, SENS, FORM, reset, rediscovery and buffer-affecting commands must fail quickly with a dedicated Busy/Conflict error (or queue by explicit policy). Do not block cancellation or emergency output shutdown. Distinguish session-level lease from the per-command transport mutex; never hold the transport mutex while waiting seconds for external triggers.

If two *independent processes* or applications can control the same hardware, a Python lock is insufficient. Document an exclusive-controller operating requirement, optionally implement an external lease, and detect conflicting instrument-state changes where possible. Do not claim cross-process isolation without it.

~~~python
# Pseudocode — design intent, NOT a drop-in patch.
with driver.acquire_digitize_lease(alias="bench", channel=2) as lease:
    cfg = lease.configure(
        function="voltage", points=1024,
        interval_s=20.48e-6, offset_points=-512,
    )
    token = lease.arm_and_wait_pretrigger_ready(timeout_s=5.0)
    bench.apply_reviewed_current_step(2.5)  # bench controls DUT/load limits
    lease.wait_complete(token, timeout_s=5.0)
    waveform = lease.fetch_verified(token)
# lease.__exit__ aborts unfinished captures and releases ownership
~~~

This is a **proposed convenience API**. Existing call patterns (driver.configure_digitize -> driver.arm_digitize -> driver.fetch_digitized_array) must either obtain implicit coordinated ownership for a bounded lifecycle or document/enforce an explicit acquire-before-use requirement with a deprecation/migration path. A fully compatible design is preferred.

### 2.4 Error and observability contract

Define typed failures, with stable codes and context: InvalidConfiguration, Busy/Conflict, TriggerNotReady, TriggerTimeout, AcquisitionAborted, AcquisitionUncertain, ScpiRejected, BufferStale, FormatMismatch, DataCorrupt, ResourceLimitExceeded, UnsupportedModel. Map them into the existing driver exception hierarchy. Include alias/channel/generation/command/status and cleanup outcome; never expose a partially fetched buffer as a successful waveform. Failures during communication should be marked UNKNOWN if abort cannot be acknowledged. Instrument errors should be checked without clearing unrelated errors from a different owner; specify how error-queue ownership is enforced.

The trace/audit path must record control-plane metadata and hashes, not hundreds of thousands of samples copied into JSONL.

## 3. Finding-by-finding implementation requirements

### F01 — BLOCKER: Pre-trigger readiness race

**Code:** channel.py:634–653; validation_harness.py:75–95.  
**Failure:** WTG_meas=1 signals readiness for a trigger, but does not prove enough pre-trigger samples exist. On a quick command path a single current step is ignored; the instrument waits for another trigger and the test times out.  
**Justification:** K-UG printed p. 213 explicitly warns early triggers are ignored. With -512 points and 20.48 microseconds/point, the nominal fill is **10.48576 ms**.  
**Implementation:** Timestamp INIT:ACQ from a monotonic clock using a conservative reference (e.g. successful command return). After WTG_meas, enforce elapsed time >= abs(negative_offset) × *actual* sample period + a characterized guard. Expose await_pretrigger_ready and require it before intentionally generated bench events; maintain cancellation/timeout checks throughout. For asynchronous, spontaneous events, report that guaranteed pre-history is impossible without arming early enough. Re-read status after barrier; if no longer waiting, inspect whether the acquisition already finished (do not synthesize readiness).
**Sketch:**
~~~python
def pretrigger_fill_s(offset_points: int, actual_interval_s: float) -> float:
    return max(0, -offset_points) * actual_interval_s

# Wait is monotonic, cancellation-aware and bounded by a *total* deadline:
barrier = armed_command_return_t + pretrigger_fill_s(offset, effective_dt) + guard_s
while monotonic() < barrier:
    assert_not_cancelled()
    sleep(min(0.005, barrier - monotonic()))
verify_waiting_or_completed_status()
~~~
**Tests:** Controlled simulated clock around 0/negative/positive offsets; trigger at fill-epsilon and fill+margin; 100+ HIL repetitions under low- and high-latency control; missed trigger times out and aborts.  
**Done when:** Caller cannot issue a driver-managed step before barrier, early-trigger failure cannot hang indefinitely, and timing uses verified effective interval.

### F02 — BLOCKER: No cleanup on timeout/failed poll

**Code:** channel.py:660–694.  
**Failure:** Only an explicit abort_requested callback performs ABOR:ACQ. Normal poll timeout or SCPI failure may leave the hardware waiting.  
**Justification:** K-UG pp. 214–216 documents indefinite external/level trigger waits and ABOR:ACQ cleanup.  
**Implementation:** Centralize abort_if_needed in lease cleanup; enforce finite overall timeout, with separate bounded cleanup deadline. Preserve original exception, attach cleanup errors and state UNKNOWN when communication is lost. An ABOR command on timeout is best effort, not proof of an idle instrument until confirmed. Avoid automatic DUT power toggling from measurement cleanup.
**Sketch:**
~~~python
try:
    coordinator.wait_until_complete(token, deadline=deadline)
except (TimeoutError, KeyboardInterrupt, AcquisitionError) as primary:
    cleanup = coordinator.try_abort_and_verify_idle(token, timeout_s=2.0)
    raise annotate_primary_error(primary, cleanup=cleanup)
finally:
    coordinator.release_if_safe_or_mark_unknown(token)
~~~
**Tests:** No-trigger, mid-trigger cancellation, rejected ABOR, transport disconnect, status query error, second acquisition following verified recovery.  
**Done when:** Every wait returns or errors within configured deadlines, cleanup outcome is explicit, and no subsequent capture silently inherits prior trigger state.

### F03 — HIGH: Reported interval is requested, not effective

**Code:** channel.py:563–589, 712–718.  
**Failure:** The module rounds user intervals to supported resolutions; resulting waveform time axis can be false.  
**Justification:** K-UG pp. 212–213 specifies interval quantization (10.24/20.48/40.96 microsecond increments, dependent on parameter count and RES20/RES40).  
**Implementation:** Establish resolution policy explicitly or query current resolution; write requested interval and then read back TINT? and POIN?/OFFS:POIN?. Fail on unsupported readback or record explicitly if unavailable, never quietly claim requested equals actual. Use readback in ArrayMeasurement and pretrigger barrier. Include requested/actual in metadata and evidence.
**Sketch:**
~~~python
send("SENS:SWE:TINT 23E-6,(@2)")
actual_dt = float(query("SENS:SWE:TINT? (@2)"))
assert actual_dt > 0 and math.isfinite(actual_dt)
metadata.actual_interval_s = actual_dt
times = tuple((i + offset_points) * actual_dt for i in range(points))
~~~
**Tests:** RES20/RES40, exact and non-quantized intervals, 1–8 active parameters where supported, readback mismatch and NaN, timestamp accuracy.  
**Done when:** No client computes times from unverified requested intervals.

### F04 — HIGH: False-success completion and stale generation

**Code:** channel.py:639–719; simulator.py:313–330.  
**Failure:** A clear MEAS_active bit can occur before a trigger or after abort. The simulator never sets MEAS_active and accepts a flat array regardless of acquisition generation.  
**Justification:** K-UG pp. 215–216 describes status bits; K-PR FETC:ARR says buffer validity ends at the next MEAS or INIT.  
**Implementation:** Add explicit generation/token/state machine. INIT allocates new generation; only a valid transition plus successful completion observation permits FETCH. Do not use a single cleared bit as success. Prototype validation against real firmware to determine reliable observation sequences (status bits, trigger event registers, OPC/operation event signals, buffer validity) before finalizing the condition. A fast capture that occurs entirely between polls must still be accepted if provenance is independently established. Rebuild simulator with state transitions including busy, waiting, success, abort, and stale buffer.
**Sketch:**
~~~python
def fetch_verified(self, token: AcquisitionToken) -> ArrayMeasurement:
    state = self._get_state(token)
    if state is not AcquisitionState.COMPLETE:
        raise AcquisitionUncertain("current generation not proven complete")
    raw = self._fetch_current_generation(token)
    array = self._validate_array(raw, expected=self.config.points)
    self._transition(token, AcquisitionState.FETCHED)
    return array
~~~
**Tests:** Fetch without INIT, wait without arm, ignored trigger, abort during waiting, prior successful buffer + next failed INIT, fast trigger before first poll, reset and reconnect.  
**Done when:** Zero stale/aborted generations can produce success and real fast acquisitions still pass.

### F05 — HIGH: Concurrent operations overwrite capture

**Code:** base.py:325–357, channel.py:62–83, 696–719, driver.py:645–677.  
**Failure:** Current lock serializes each SCPI transaction, not the acquisition lifecycle; an unrelated MEAS call overwrites acquisition data and discover_modules rebuilds channel objects.  
**Justification:** K-PR FETC:ARR and K-UG pp. 215–216 specify invalidation by later acquisitions.  
**Implementation:** Mainframe-scoped lease and central command classification; dispatch each typed/raw command through a guard. Block/deny conflicting operations, including rediscovery, reconnect, format changes, reset, simultaneous acquisitions and raw measurement commands. Explicitly allow approved source/load setpoint changes needed to create a transient while a lease is active; do not let that exception bypass output-safety policy. Invalidating acquisition due to external activity must produce BufferStale, not synthetic data.
**Sketch:**
~~~python
if session.acquisition_owner is not None:
    if command_changes_measurement_buffer(scpi_command):
        if caller_token != session.acquisition_owner:
            raise AcquisitionBusyError("active capture owns measurement buffer")
~~~
**Tests:** Two threads, same channel and different channels; command from another alias/mainframe; raw MEAS query, rediscovery, session replacement, concurrent polling, shutdown while waiting.  
**Done when:** Two in-process clients cannot corrupt a waveform, and external-controller limitations are documented.

### F06 — HIGH: Shared memory and data-format assumptions

**Code:** channel.py:580–586, 696–719; scpi.py:76–80.  
**Failure:** Selected measurement is enabled but the other measurement may remain enabled; shared memory is 512 Ki-points. FORMAT may have been changed to REAL; current query path expects comma-separated text.  
**Justification:** K-UG pp. 211–212 and 222; K-PR FORMat and FETCh:ARRay. **REAL arrays are IEEE-754 32-bit**, not 64-bit; do not apply scpi.py's width=8 default to these replies.  
**Implementation:** Before acquisition, inventory active measurement functions on every installed channel and compute requested point allocations, or explicitly configure a supported exclusive acquisition profile with restoration. Verify shared memory and minimum sample interval for parameter count. Read/set FORM:DATA and FORM:BORDer under session lease; choose ASCII or REAL32, with explicit restoration on exit. Introduce a binary-safe ScpiClient/transport path (inspect K-CORE) that reads IEEE-488.2 definite-length blocks to exact declared byte count without converting payload to a Python str.
**Sketch:**
~~~python
# Pseudocode: exact binary I/O must be supplied by scpi-driver-core.
set_format("REAL")
set_border("SWAP")  # choose explicitly and verify; do not assume native host order
block = query_definite_length_block("FETC:ARR:VOLT? (@2)", max_bytes=4 * points)
if len(block) != 4 * points:
    raise DataCorrupt("wrong REAL32 payload length")
samples = struct.unpack("<" + str(points) + "f", block)
~~~
**Tests:** Pre-existing REAL/ASC state, both endian modes, single/multiple active functions, competing channel memory, memory ceiling, nested instrument-format restore.  
**Done when:** Returned data is decoded only after explicit format/protocol validation; shared allocations cannot silently overrun capacity.

### F07 — HIGH: Array integrity not enforced by driver

**Code:** channel.py:696–719 and scpi.py:76–80.  
**Failure:** Empty CSV fields are dropped; returned sample count is unchecked; override can request current after configuring voltage only.  
**Justification:** K-PR FETCH:ARRay format/parameters and K-UG array retrieval specification.  
**Implementation:** Use strict CSV token parsing, expected length check, finite-number policy, vendor-proven overrange sentinel detection, allowed-function validation, and acquired-generation comparison. Preserve raw error context securely without dumping full waveform into exception message. If both functions are enabled, explicitly model and verify both; otherwise reject wrong-function fetch.
**Sketch:**
~~~python
tokens = response.strip().split(",")
if len(tokens) != expected_points or any(not item.strip() for item in tokens):
    raise DriverMalformedResponseError("incomplete waveform CSV")
values = tuple(float(item) for item in tokens)
if any(not math.isfinite(v) for v in values):
    raise DriverMalformedResponseError("invalid non-finite waveform sample")
~~~
**Tests:** zero/short/long array, blank field, malformed exponent, NaN, Inf, excessive values, wrong function, stale buffer, binary header fed to CSV reader, exact 512K valid case.  
**Done when:** No incorrect-sized or wrong-generation waveform is returned as success.

### F08 — HIGH: Partial SCPI configuration and errors

**Code:** channel.py:580–637; validation_harness.py:81–94.  
**Failure:** An intermediate instrument rejection leaves partially updated state while Python records intended configuration. Existing HIL harness manually calls check_errors.  
**Justification:** K-PR command argument/error queue behavior; repository docs/hardware_acceptance_tests.md.  
**Implementation:** Pre-validate all parameters and cross-channel capability/range constraints, acquire lease, perform ordered writes, verify SCPI errors and readbacks before marking CONFIGURED. On failure, invalidate generation, abort active capture when necessary, restore guarded state or mark UNKNOWN. Do not assert atomicity that SCPI cannot provide; use a compensation workflow and clear side-effect ledger.
**Sketch:**
~~~python
try:
    send_all_config_writes()
    check_scpi_errors_owned_by_capture()
    confirm_config_readback()
except Exception:
    state = AcquisitionState.FAILED
    best_effort_abort_and_restore()
    raise
else:
    state = AcquisitionState.CONFIGURED
~~~
**Tests:** Reject each of the 4 sweep writes and each trigger write, query error and transport failure; verify partial settings never reported committed.  
**Done when:** No failed configuration permits arm/fetch until a validated new configuration.

### F09 — MEDIUM: Capability metadata inaccurate or incomplete

**Code:** driver.py:947–1017; capability/capability_model.yaml:352–388; module_capabilities.py:28–39.  
**Failure:** configure_digitize is marked mutating=False; only configure and fetch have capability annotations; array capability flag defaults false for N679xA, while digitizing is true.  
**Justification:** K-LPDS capability model, generated AI contract, conformance inventory.  
**Implementation:** Register all eight API operations with canonical identifiers, accurate mutating/risk flags, required channel capability and lifecycle preconditions. Reconcile supports_array_measurement and supports_digitizing without falsely advertising models. Regenerate YAML, snapshot, lock and conformance inventory via checked-in generator scripts; no manual divergences. Document no automatic DUT energization by config/arm functions.  
**Sketch:**
~~~python
@capability(
    "acquisition.digitize.arm",
    category="acquisition",
    risk_level="low",
    mutating=True,
    preconditions=("supported module", "configured capture", "exclusive lease"),
)
def arm_digitize(...): ...
~~~
**Tests:** Introspection/discovery + generated files consistency; unsupported model; complete eight-method inventory; contract snapshot.  
**Done when:** Every scope action is accurately discoverable and generated artifacts match runtime.

### F10 — MEDIUM: N679 prefix matches unknown models

**Code:** module_capabilities.py:57,75–91.  
**Failure:** Every model beginning with N679 is automatically treated as fully verified. Only one N6791A hardware/firmware setup has bench evidence.  
**Justification:** K-UG load model differences; K-HIL current qualification records.  
**Implementation:** Explicit support registry for N6791A and N6792A, separate vendor_declared from hardware_qualified. Record firmware/mainframe constraints and optional model options. Reject unrecognized N679... by default; do not label N6792A hardware-qualified until tested. Publish capability confidence, allow deliberate reviewed override if appropriate.  
**Sketch:**
~~~python
SUPPORTED_LOAD_MODELS = {"N6791A", "N6792A"}
if model not in SUPPORTED_LOAD_MODELS:
    return ChannelCapabilities(model=model, module_type="unknown")
qualification = "hil_verified" if known_evidence_matches(model, firmware) else "vendor_declared"
~~~
**Tests:** N6791A, N6792A, invented N6799Z, whitespace/case, firmware metadata unknown, simulator placeholder.  
**Done when:** Model family discovery cannot overclaim qualification.

### F11 — MEDIUM: Large arrays and audit duplication

**Code:** channel.py:711–719; scpi.py:76–80; driver.py:458–469,1093–1117.  
**Failure:** ASCII split/list/tuple copies data. Full response may be duplicated into control audit and low-level protocol trace; 524,288 samples can exhaust I/O buffers or increase memory/disk usage.  
**Justification:** K-UG maximum buffer; K-PR REAL32 fetch; K-CORE transport upper bounds.  
**Implementation:** Benchmark and prefer bounded REAL32 fetch; impose max response bytes, sample count, time and memory budgets. Exclude raw waveform payloads from standard audit or replace with length, SHA-256, timestamp, artifact path. Trace large binary blocks using a bounded summary plus optional separate evidence artifact. Avoid altering audit behavior for small scalar replies.  
**Sketch:**
~~~python
audit.log("waveform_fetch", {
    "samples": len(samples),
    "bytes": len(payload),
    "format": "REAL32",
    "sha256": hashlib.sha256(payload).hexdigest(),
    "artifact": artifact_relative_path,
})
~~~
**Tests:** 256 / 1024 / 100000 / 524288 points on TCP and supported VISA; audit+trace off/on; bounded time/RSS/bytes; 1,000 sequential captures without monotonic memory growth.  
**Done when:** Published platform-specific resource limits pass and no full buffer is repeated in routine control JSONL.

### F12 — MEDIUM: No reference timing qualification

**Evidence:** results/load_transient/20261009_n6791a_internal_buffer/*.csv, summary JSON and README.  
**Failure:** 20.48–40.96 microsecond recovery represents one to two samples; no synchronized DSO comparison or uncertainty budget.  
**Justification:** K-UG measurement bandwidth and sampling limitations; repeatability and metrology principles. Do not infer actual analog bandwidth from sample rate.  
**Implementation:** Qualify N6791A versus an independently calibrated DSO using the *same observed node*, matched trigger and known reference events, across two load directions, input voltage levels, slow and fast response classes, and more than one sample interval. Capture both instruments; estimate trigger delay, sampling quantization, filtering/range influence, amplitude accuracy and repeatability. Specify a minimum measurable event duration and a pass/fail uncertainty guard-band. For <5 samples of recovery, report NOT_RESOLVED rather than a precise timing PASS unless separate validation justifies a tighter threshold.
**Sketch:**
~~~python
if recovery_samples < qualified_min_samples:
    result = "NOT_RESOLVED"
else:
    result = evaluate_with_uncertainty(
        measured_time_s, expanded_uncertainty_s, configured_limit_s
    )
~~~
**Tests:** 100 captures per qualification condition, 95% uncertainty interval from empirical residuals plus timebase and trigger components; compare DSO values, never hide inconclusive cases.  
**Done when:** A written qualification report defines when scope-like acquisition may drive production timing limits versus when an external DSO remains required. Tolerance is pre-agreed before evaluating outcomes.

### F13 — MEDIUM: Hardcoded/non-reproducible HIL harness

**Code:** results/.../validation_harness.py:12–18, 41–49.  
**Failure:** Hardcoded absolute paths and unpinned power_supply_test sibling worktree block clean-machine reproduction.  
**Justification:** K-HIL hardware acceptance and retained test evidence.  
**Implementation:** Move a reusable driver-only protocol qualification harness under tests/hardware or examples/hardware; pass resource, channels and safety profile via environment/config; optional downstream DUT analysis belongs to a separate pinned integration test. No test should use workstation-specific paths. Require two independent opt-in gates before any energization, strict voltage/current/power envelope and finally-guarded safe shutdown. Save machine-readable metadata and raw waveform hashes.  
**Sketch:**
~~~python
@pytest.mark.hardware
def test_real_digitize_under_guard(hardware_driver, reviewed_bench_profile):
    assert reviewed_bench_profile.operator_confirmed
    assert reviewed_bench_profile.safe_limits_validated
    # configure; arm; await readiness; produce reviewed event; fetch
    # independent shutdown verification in fixture finalizer
~~~
**Tests:** Fresh checkout smoke test with simulated protocol (no hardware), guarded HIL on approved bench only, invalid config, unexpected module, remote disconnect, output OFF verification.  
**Done when:** A second bench operator can execute an unchanged documented command and reproduce signed/hash-verified evidence.

### F14 — LOW: Version metadata drift

**Code:** pyproject.toml, src/keysight_n6700/version.py, src/keysight_n6700/driver.py:79–92 and plugin manifest.  
**Failure:** Package is v0.6.0 but driver metadata string remains v0.3.1.  
**Justification:** K-LPDS release identity; reproducible test evidence.  
**Implementation:** Derive runtime metadata from authoritative version module, and validate consistency across package, manifest, capability/AI contract, history and changelog. Every merged implementation PR increments the version and updates all generated/dependent metadata. Avoid editing release evidence history retroactively.  
**Sketch:**
~~~python
from .version import __version__
_DRIVER_METADATA = DriverMetadata(version=__version__, ...)
~~~
**Tests:** Unit assertion using importlib.metadata.version, driver metadata, manifest and contract; CI fails on drift.  
**Done when:** Version identity matches across each source and attached evidence.

## 4. PR and dependency schedule

Each PR is **small, testable and merged sequentially**, not a single omnibus feature. Each implementation PR increments the patch version (starting after this planning-only version), adds an explicit CHANGELOG/history entry, and reconciles generated artifacts. When semantic behavior becomes incompatible, make a deliberate minor-version decision with documented migration notes instead of silently breaking an API.

| PR | Work package | Findings closed | Dependencies and minimum acceptance |
|---|---|---|---|
| 01 | State machine/generation token, explicit successful-completion proof, simulator transition faults | F04 | Baseline; offline trace contract; stale-buffer tests |
| 02 | Bounded wait, cancellation, abort, UNKNOWN recovery and no-trigger cleanup | F02 | PR01; all timeout/transport/abort negative tests |
| 03 | Effective readbacks, pretrigger barrier and HIL timing edge checks | F03,F01 | PR01–02; timing quantization + early-trigger regression |
| 04 | Session acquisition lease, raw-command conflict guards, rediscovery invalidation | F05 | PR01–03; deterministic threaded tests |
| 05 | SCPI configuration verification, shared memory/format negotiation, strict array validation | F08,F06,F07 | PR04; corrupt/partial/REAL32/ASC tests |
| 06 | Complete capability registry, model-support qualification and version-source cleanup | F09,F10,F14 | PR05; metadata/contract/model matrix |
| 07 | Binary transfer hardening, memory caps and trace/audit evidence policy | F11 | PR05–06; performance and resource benchmarks |
| 08 | Portable gated HIL harness, DSO correlation, uncertainty and documentation | F12,F13 | All above; qualification matrix and independent review |
| 09 | Release audit, regression soak, independent final review, fixes only via reviewed PRs | ALL | clean CI, HIL approvals, explicit GO/NO-GO |

**Review checkpoint after each PR:** (a) author checks code against finding text; (b) independent reviewer challenges edge cases and backward compatibility; (c) unit + simulator protocol + contract tests; (d) static checks and packaging; (e) negative-path evidence; (f) hardware tests only when the PR actually changes qualified hardware behavior and an operator approved them; (g) resolve all BLOCKER/HIGH comments, merge after green checks; (h) rebase the next branch on updated main. Do not merge a PR solely on a successful happy-path test.

### 4.1 Per-PR minimum artifacts

- Focused diff, design decision record (particularly assumptions), migration notes if needed.
- Tests naming each F-code being closed; false-path/timeout/concurrency test where relevant.
- Changelog/history and consistent version bump for that PR.
- Updated API docs and examples; generated LPDS AI contract/lock, capability snapshot and conformance inventory when relevant.
- Hardware report showing IDN, channel model, firmware, operating envelope, sample rate, points, trigger mode, raw data SHA, pass/fail summary and final OFF/error-queue checks.
- Explicit reviewer outcome: accepted, changes requested, or waiting for HIL; no silent waiver.

## 5. Regression test design and determinism

### 5.1 Offline unit tests

Create a deterministic scripted SCPI backend and fake monotonic clock that can program the exact return of status, timeouts, transport breakage and sample arrays. Add no hardware use to default pytest. Split command emission, parsing, lifecycle and error-state tests; assert outbound SCPI *and* API-visible state. Negative cases must be deterministic, not based on an actual sleep race.

Mandatory categories:
- Input validation: bool-as-int, invalid offsets, negative/nonfinite intervals, unknown model/function/trigger, unsupported firmware.
- Timing: roundoff, RES20/RES40, pre-trigger fill boundary and cancellation while filling.
- Lifecycle: wrong order, stale generation, fast completion, missed trigger, abort, repeated fetch, reconnect, status anomalies and cleared MEAS_active while still waiting.
- Concurrency: two simultaneous captures; MEAS and FETC during lease; source setpoint under approved owner; emergency disable; rediscovery and alias switching.
- Protocol: binary block length/header/trailer/endian, REAL32 payload, partial reads, EOF, giant block denial, malformed ASCII, vendor overrange marker policy.
- Error recovery: SCPI error after each configuration command, rejected ABOR, network outage and cleanup failure with preserved primary failure.

### 5.2 Hardware qualification matrix (gated)

| Axis | Minimum cases / review obligation |
|---|---|
| Module | N6791A qualified; N6792A HIL required before claiming HIL-verified |
| Mainframe/firmware | Record actual *IDN?, SYST:CHAN:MOD? and firmware; qualify additional revisions explicitly |
| Trigger | BUS and positive/negative current level, with early/missing trigger; voltage/external/transient remain unqualified until tested |
| Offset | 0, negative pre-trigger near boundary, positive post-trigger |
| Sample interval | minimum valid, one rounded value, RES20/RES40, long interval |
| Function | Voltage-only, current-only, both only if supported and characterized |
| Buffer | 256, 1024, 100000, large boundary after shared memory verification |
| Transport | Ethernet TCP; VISA if supported by installed CI/hardware |
| Fault | SCPI rejection, late trigger, disconnect/reconnect, operator cancel, invalid waveform |
| DUT safety | caps, operator approval, independent outputs-off/protection verification |
| Metrology | Reference DSO cross-check, slow/fast events, documented uncertainty and resolution floor |

**Do not run 512 Ki-point extreme cases on a live DUT without explicit resource planning and operator-approved settings.** Simulator covers protocol bounds first. Never assume a test passed on hardware solely because the same simulated test passed.

### 5.3 Sample quality and resource gates

Proposed measurable targets (must be confirmed by bench profiling and then frozen *before* acceptance testing):
- All fault tests complete under configured total deadline plus a bounded cleanup allowance (no infinite loop).
- 100% exact point count on verified captures; zero silently accepted malformed or stale waveforms.
- Successful cancellation leaves IDLE when connected; UNKNOWN state is explicit when disconnected.
- Zero dangerous automatic output re-enables during cleanup.
- No increasing retained memory over 1,000 sequential 1,024-point captures, evaluated with peak RSS and heap snapshots.
- No full 512 Ki-point array duplicated into standard audit JSONL.
- At least 100 consecutive correctly positioned triggered waveforms per tested direction; false capture is 0 in the *tested* finite sample (not proof of zero lifetime failure probability).
- Report time-domain acceptance only above a separately established measurement resolution/uncertainty threshold.

## 6. Example end-to-end operating sequence (target behavior)

~~~python
# Illustrative future API; requires implementation, approved bench and live HIL gate.
with N6700.connect_ethernet(resource) as drv:
    load = drv.load(2)
    bench.validate_safety_envelope()
    bench.configure_cc_priority_before_arm()
    with drv.acquire_digitize_lease(channel=2) as capture:
        config = capture.configure(
            function="voltage", points=1024,
            interval_s=20.48e-6, offset_points=-512,
            trigger_source="current", trigger_level=1.5, slope="pos",
        )
        token = capture.arm_and_wait_pretrigger_ready(timeout_s=5.0)
        bench.apply_reviewed_current_step(2.5)
        capture.wait_complete(token, timeout_s=5.0)
        waveform = capture.fetch_verified(token)
        bench.persist_waveform_and_metadata(waveform)
# In surrounding bench finally: safe shutdown + independent OFF verification.
~~~

**Important sequencing:** Real N6791A firmware rejected FUNC CURR while acquisition was active (recorded SCPI error 315). Set priority mode *before* arming and issue only the reviewed CURR level change to create the step. Fetch before ANY MEASure query because MEAS overwrites the last triggered buffer (K-HIL). The example must not lead developers to energize a DUT outside the approved bench harness.

## 7. Production acceptance and final review

**Release gate G1 — Architecture:** Lifecycle, lease scope, type model, total deadlines and error/UNKNOWN behavior approved in PR01 before subsequent coding.

**G2 — Fault safety:** All BLOCKER and HIGH findings closed with deterministic negative tests, explicit instrument behavior where required and no regression to ordinary setpoint/measurement APIs.

**G3 — Data integrity:** Real, ASCII and binary mode correctness; memory boundaries; exact sample provenance; format restoration; no data overwritten between trigger and fetch.

**G4 — HIL safety:** Documented operator consent, physical wiring and load envelope; channel/input/output OFF and error queue inspected after every run including failures.

**G5 — Metrology:** Reference DSO comparisons establish resolution, accuracy limits, bandwidth caveats, uncertainty and an INCONCLUSIVE result when insufficient sample evidence exists. The scope feature must not promise sub-sample timing resolution.

**G6 — Reliability:** Extended fault-injection tests and 24-hour protected-bench soak *when hardware time is approved*; telemetry for timeouts, aborts, bytes, duration and memory. Acceptance requires no unexplained acquisition state, no unsafe leftover output, and transparent failures.

**G7 — Packaging and independent audit:** Clean build and wheel installation, mypy/ruff/pytest/conformance/capability checks, truthful version/contract metadata, migration notes and final independent requirements-vs-code review. Record remaining MEDIUM/LOW risk with explicit owner, rationale and target version instead of silently declaring it fixed.

Final verdict must be one of **GO for specified qualified hardware/use cases**, **GO for guarded engineering only**, or **NO-GO**. A successful DUT transient plot alone never passes the release gate.

## 8. Required documentation updates

For each implementation PR, update the minimal relevant subset: API docs/examples, docs/hardware_acceptance_tests.md, docs/architecture.md, docs/safety.md, review/known_risks.md, CHANGELOG.md, history/, version sources, capability and AI contract snapshots and generated locks. Preserve existing raw characterization data and add new artifacts under uniquely dated directories. The reviewer must cross-check file/line claims against the then-current code (the v0.6.0 line anchors are starting references, not permanent coordinates).

**Planning-only change note:** This document creates the work plan; no F01–F14 issue is considered fixed by publishing it. Versioned planning release must not be advertised as the production qualification of digitizing.

## 9. Traceability and completion checklist

| Finding | Severity | PR | Evidence necessary to close |
|---|---|---|---|
| F01 | BLOCKER | 03 | Pretrigger barrier + early-event HIL tests |
| F02 | BLOCKER | 02 | Timeout/cancel abort and idle reconciliation |
| F03 | HIGH | 03 | Effective TINT query + timestamp tests |
| F04 | HIGH | 01 | Token/state proofs + simulator negative tests |
| F05 | HIGH | 04 | Lease/concurrent MEAS/rediscovery tests |
| F06 | HIGH | 05 | Shared memory and REAL/ASC verified negotiation |
| F07 | HIGH | 05 | Malformed/wrong-generation buffer rejection |
| F08 | HIGH | 05 | Per-command rejection + partial-write recovery |
| F09 | MEDIUM | 06 | Eight capabilities, correct mutation/risk/flags |
| F10 | MEDIUM | 06 | Explicit model allowlist and qualification levels |
| F11 | MEDIUM | 07 | Bounded binary fetch, audit/hash performance tests |
| F12 | MEDIUM | 08 | Independent DSO comparison, uncertainty qualification |
| F13 | MEDIUM | 08 | Clean-checkout gated HIL reproduction |
| F14 | LOW | 06 | Consistent metadata and version CI check |

The coding agent should work in PR order. **After every PR:** re-run checks, review for unaddressed F-codes and regressions, revise if needed, get a green review and merge to main. Do not begin the next PR from a stale base. The final PR is a retrospective audit across all merged code, not a substitute for continuous review.
