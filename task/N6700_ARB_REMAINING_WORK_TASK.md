# N6700 Arb — Remaining Work, Safety Closure and Physical Qualification

**Task ID:** N6700-ARB-002
**Revision:** 1.0
**Prepared:** 2026-10-16
**Repository:** https://github.com/ami3go/keysight-n6700-driver
**Audited baseline:** `main@2f4a8333f8fb8496ef29cec63a24be15a3c33886`; package `v0.7.10`
**Status:** IMPLEMENTATION TASK — software implemented; production / analog acceptance **NOT QUALIFIED**
**Primary upstream plan:** [ARB_DRIVER_IMPLEMENTATION_TASK.md](../docs/ARB_DRIVER_IMPLEMENTATION_TASK.md)
**Implementation closure report:** [ARB_COMPLETION_FIX_PLAN.md](../docs/ARB_COMPLETION_FIX_PLAN.md)
**Core pinned:** `scpi-driver-core@9c5dd50a03031f65e1aa5cf13832db7372f043b9` (normal and `[visa]`)

> **This document defines future work; it does not implement the items or authorize hardware operation.** Baseline references and findings must be refreshed from `main` at the beginning of each work package. Do not treat a green simulator run as measurement accuracy, electrical safety, or unattended production readiness.

## 1. Current verified inventory — do not repeat completed work

At baseline, [PR #20](https://github.com/ami3go/keysight-n6700-driver/pull/20) (v0.7.9) and v0.7.10 implement the A–H software packages in the original plan. They include mainframe/Option 054 gates, shared-session configuration locking and primary-error preservation, typed `TRIG:TRAN` versus `TRIG:ARB` selection, seven parametric shapes, UDEF, current waveforms, bounded ASCII CDW, sequence point/quality budgeting, and an immutable reviewed core pin. Automated software checks on `main` are green ([run 38902487336](https://github.com/ami3go/keysight-n6700-driver/actions/runs/38902487336), Python 3.10/3.13).

| Area | Confirmed in v0.7.10 | Still required |
| --- | --- | --- |
| DRV-ARB-A through H | Implemented / offline-tested | Adversarial residual review; regressions where findings require them |
| DRV-ARB-I | HIL plan only | Authorized instrument/DSO campaign, objective evidence and independent sign-off |
| Capability identity | Fail-closed source matrix on current driver | Real instrument/model/firmware/Option 054 cross-check and downstream re-pin |
| Runtime bounds | Time and finite sequence restrictions | Audit `"infinite"` in single-waveform/LIST/UDEF/CDW public APIs |
| Fault handling | Best-effort source cleanup; source session locks | Investigate stale digitizer lease after transport fault ([#14](https://github.com/ami3go/keysight-n6700-driver/issues/14)) |
| Core | Immutable dev11 SHA in both extras | Real TCP/VISA loss/recovery, concurrent acquisition regression |
| Downstream | Waveform Studio has issue #126 | Compatibility and allowlist reconciliation in `ami3go/power_supply_test` |
| GitHub triage | Code has changed since reports | Close issues #21 and #15 **only after reproductions and evidence** |

**Important residual source observation:** `waveforms.normalize_repeat_count()` still accepts `"infinite"`; `PowerSupplyChannel._require_duration()` checks only one pass when repeat is infinite. The finite-only contract therefore needs a deliberate safety decision, not an assumption that all exposed workflows are finite. The normal sequence configuration separately requires finite sequence counts.

## 2. Non-negotiable engineering constraints

1. Never energize a real source, initiate Arb, enable output, bypass interlock or clear a protection latch in CI, a simulator job, or a task-planning PR. Physical testing needs new, test-specific written site approval, named responsible operator, approved DUT/load, wiring drawing and independent hardware E-stop.
2. Fail closed on unknown mainframe, module/options, firmware dialect, SCPI readback, output/protection state, trigger ownership or accepted waveform count. A timeout during an output-changing write is **UNKNOWN**, not proof of output-OFF.
3. Keep all source envelopes finite and prevalidated against installed module limits, configured OVP/OCP/compliance, cable/fixture/DUT ratings and approved operator limits before the first modifying command. Use absolute extrema for bipolar outputs.
4. Preserve shared-session locking and lock order; no non-idempotent auto-retries, mainframe-global `*TRG` for single-channel work, unreviewed SCPI binary input grammar or indefinite blocking polls.
5. Emergency shutdown is **best effort**, never a substitute for a physical interlock; independent readback and/or external measurement must establish the actual safe state.
6. Preserve existing public Python/Robot caller compatibility unless a documented, reviewed safety breaking change is unavoidable. Do not change source layout or introduce duplicate generic SCPI transport logic.
7. **One work package at a time:** inspect latest `main`, write tests, change code, run focused and full suites, independent review, remedy findings, bump version, PR/CI/merge, then post-merge regression. Do not begin the next package while an earlier gate is unresolved.
8. Every long-running test, instrument wait and unattended subprocess requires an explicit finite monotonic deadline, external watchdog/supervisor and cleanup result logging. A watchdog must not be the sole means of removing dangerous electrical energy.

## 3. Dependency-ordered work packages and PR gates

| ID | Priority | Proposed branch | Depends on | Deliverable | Exit gate |
| --- | --- | --- | --- | --- | --- |
| RW-00 | P0 | `docs/arb-release-baseline` | None | Refresh facts, issue/PR triage, signed review checklist | Reviewed evidence inventory |
| RW-01 | P0 | `fix/arb-finite-run-contract` | RW-00 | Define finite-run policy and close `infinite` safety loophole | All normal finite APIs have bounded worst-case execution |
| RW-02 | P0 | `fix/digitizer-fault-lease-recovery` | RW-01 | Fault-safe lease and disconnect/read-only verification recovery | No stale lease deadlock; UNKNOWN on unresolved hardware state |
| RW-03 | P0 | `test/arb-hil-preflight` | RW-02 | No-energy HIL harness, operator authorization and report schema | CI proves physical test cannot run accidentally |
| RW-04 | P0 | `test/arb-hil-identity-protocol` | RW-03 + site approval | Model/option/firmware, command readback and trigger dialect validation | Recorded exact instrument/SCPI matrix |
| RW-05 | P0 | `test/arb-hil-voltage-dso` | RW-04 | Independent analog voltage and timing evidence | All approved voltage vectors pass or are explicitly NO-GO |
| RW-06 | P0 | `test/arb-hil-current-dso` | RW-05 | SMU/current-limit and compliance evidence | Correct model/quadrant/protection behavior |
| RW-07 | P1 | `test/arb-hil-cdw-sequence` | RW-06 | CDW/sequence point counts and channel/trigger synchronization | Memory, duration and sequence evidence |
| RW-08 | P0 | `test/arb-hil-fault-recovery` | RW-07 | Controlled failure, watchdog, recovery and safe-state evidence | No unverified energized or stuck channel |
| RW-09 | P1 | `docs/arb-release-and-downstream` | RW-08 | Evidence package, peer review, downstream pin/capability compatibility | Explicit readiness decision, not implied PASS |
| RW-10 | Optional | `feat/arb-composite-planner` | RW-09 | Host-side finite composition from samples, e.g. sine + triangle | Offline math, clipping/memory and independent analog evidence |

**Scheduling exception:** RW-03's offline fixture/evidence infrastructure can be designed before physical equipment is available, but no HIL acquisition may run until RW-01/02, RW-04 preflight and new written site authorization are satisfied. RW-09 downstream **software-only** compatibility probes may be developed early; physical RUN remains disabled.

### RW-00 — baseline refresh and independent residual review

- Re-fetch `main`, compare the source APIs and generated contracts to [v0.7.10 history](../history/v0.7.10.md), [original task](../docs/ARB_DRIVER_IMPLEMENTATION_TASK.md), and [completion fix plan](../docs/ARB_COMPLETION_FIX_PLAN.md). Capture commit SHA, driver/core versions, generated manifest hashes and CI run.
- Review open [issue #14](https://github.com/ami3go/keysight-n6700-driver/issues/14) (real-hardware transport-fault/lease problem), [#15](https://github.com/ami3go/keysight-n6700-driver/issues/15) (historic stale core pin), [#21](https://github.com/ami3go/keysight-n6700-driver/issues/21) (historic fail-open Arb capability). New code likely addresses #15 and #21, but close only after reproducing their original negative cases and attaching release/CI evidence.
- Enumerate every public API accepting `RepeatCount`, `"infinite"` or trigger-paced execution; differentiate finite human-supervised run, continuous mode and read-only data planning. Record behavioral compatibility impact.
- Check outstanding independent review and released documentation for unsupported wording: **software/simulator-qualified is not DSO/HIL-qualified**.
- Deliver: `docs/arb_qualification/baseline_review.md` listing findings with severity, owners, precise functions/tests, commit SHAs and PASS/BLOCKED status.

### RW-01 — finite-execution and non-idempotent action safety

**Evidence:** `src/keysight_n6700/waveforms.py::normalize_repeat_count` accepts `"infinite"`; `src/keysight_n6700/channel.py::_require_duration` treats it as one pass.

- Make the default public `configure_voltage_list`, `configure_current_list`, voltage/current UDEF, standard shape and constant-dwell methods finite-only. Either reject `"infinite"` before **any** mutating SCPI or introduce a separate **explicitly named, supervised continuous-mode** API with a guaranteed independent stop path, bounded authorization and per-run deadline. Do not silently reinterpret `infinite` as one pass.
- Define worst-case time using **read-back/quantized** dwell/frequency × points × repeats, plus approved trigger wait budget and setup margin. Validate with overflow-safe finite arithmetic; zeros, NaN, infinity, enormous counts and quantization rounding must not silently bypass checks.
- For trigger-paced steps, require an explicit finite trigger schedule and wall-clock timeout; otherwise reject on the normal finite API.
- Exercise output-OFF configuration, abort/stop, physical-state uncertainty, generation tokens and no SCPI mutation on validation failure.
- Tests: `infinite` for every affected API, huge finite repeats, duration overflow, zero-duration pulses, dropped external triggers, abort racing with wait, wrong-channel abort, cleanup timeout and permanent UNKNOWN. Prove old finite callers remain compatible.
- Exit: no normal API can create an unbounded run while returning a finite `maximum_duration_s`; every intentional continuous mode is separately supervised and documented.

### RW-02 — faulted transport / stale acquisition lease (#14)

- Reproduce the N6700C N6775A-source/N6791A-load report **without hardware first**: active digitizer lease, timeout on `SENS:SWE:POIN?`, transport transitions to FAULTED, failed `abort_digitize`, then `OUTP?` on another channel and `disconnect()`. Distinguish **lease conflict** from the correctly rejected I/O on a faulted transport.
- Ensure session/alias-scoped leases are invalidated or released on a verifiably faulted/disconnected transport; do not allow stale-generation callbacks to release a new lease. Preserve acquisition integrity when transport is still healthy.
- Define explicitly safe read-only queries when connected; on a FAULTED transport return a typed connection/uncertainty error and permit a **new, verified session** for independent OFF verification. Never claim a query succeeded through a faulted transport.
- Ensure `safe_shutdown`, `abort_digitize` and `disconnect` always terminate ownership locally within bounded time even after transport failure; log all failed physical commands. Do not silently report OFF.
- Tests: fault injection in configure/arm/fetch/abort, unrelated channel readback, alias isolation, concurrent source configure vs digitizer lease, lock-order watchdog, cleanup failure and reconnect with changed module fingerprint.
- Exit: no permanent software resource deadlock; physical state remains UNKNOWN until independently confirmed. Relate findings to `scpi-driver-core` without broad unpinned upgrades.

### RW-03 — guarded HIL harness and evidence contract (software-only)

- Create `tests/hardware/test_arb_qualification.py` and `scripts/run_arb_qualification.py` (or integrate existing HIL conventions); use `pytest.mark.hardware`, default skip, **two separate opt-ins** (environment/CLI plus signed site manifest), exact expected `*IDN?` / firmware/module/options, physical interlock check and source initial OFF verification.
- Require reviewed wiring diagram, resistor/load wattage, DUT current/voltage/temperature limits, independent E-stop, equipment calibration IDs/dates, DSO timebase/sample rate/probe attenuation and test envelope. Use passive or protected loads, not an unqualified DUT.
- Separate **plan mode** (never sends mutating SCPI) from guarded **execute mode**. Every energizing vector requires bounded energy and a `try/finally` safe-off attempt, physical inhibit/relay oversight, and a supervisory watchdog with finite hard ceiling. No uncontrolled auto-restart after process death.
- Reuse `protocol_trace` and existing audit hooks. Write results atomically under `results/arb_qualification/<run_id>/` and retain raw DSO traces instead of only screenshots. Simulators must carry `simulation` labels.
- Exit: offline CI proves absent/expired/mismatched manifest, unsupported model, unknown output state, missing calibration, overrange, failed E-stop check and wrong operator **cannot** cause output-enable or trigger.

### RW-04 — model / firmware / option / protocol HIL matrix

- Record original text of `*IDN?`, `SYST:CHAN:MOD?`, `SYST:CHAN:OPT?` and firmware, not just derived capability booleans. Capture instrument error queue before and after each commanded operation.
- Matrix: N6700C source models requiring Option 054 with/without that option; N6705C with relevant modules; reviewed N676x and N678x SMUs; N6783A exceptional mode; N6791A/N6792A as loads **never generic source Arb**; unknown models and unknown firmware fail closed.
- Confirm `TRIG:ARB:SOUR` / `TRIG:TRAN:SOUR` dialect, returned mode/readbacks and channel-local `TRIG:TRAN (@n)` or approved equivalent. Never substitute unverified mainframe-global `*TRG`.
- Test negative cases with **zero** source Arb mutations, including unknown options and changed module identity after reconnection; positive cases need an actual, safe programmed/readback vector and error-queue evidence.
- Do not hardcode a claimed support cell from simulator success; record primary Keysight manual section / equipment test ID next to each accepted cell.

### RW-05 — analog voltage qualification with independent oscilloscope

- With site authorization, measure STEP, RAMP, STAIRCASE, SIN, PULSE, TRAP, EXP and UDEF on approved source modules at low/high admissible envelopes and load conditions.
- Capture amplitude/offset, rise/fall, dwell/period, frequency, slew, overshoot/undershoot, settling, output current, trigger-to-output latency and short-term jitter where meaningful. Include edge cases and ± polarity only on approved bipolar models.
- Compare requested vs SCPI effective readback vs **independent DSO** measurement; quantify instrument/probe/timing uncertainty, trace alignment, scope bandwidth and sampling error. Use preapproved absolute/relative limits and guard-bands, **never invent universal Keysight tolerances**.
- Verify OFF before programming, visible/supervised ON/trigger, finite completion, `INIT:CONT:TRAN OFF`, abort, and independently verified OFF after each case.
- Report a test as FAIL or INDETERMINATE, not PASS, whenever timebase or amplitude uncertainty dominates tolerance.

### RW-06 — analog current, compliance and quadrant qualification

- Distinguish ordinary supply **current-limit modulation** from actual SMU **current-source** operation; inspect priority and other quantity's fixed mode before and after the run.
- Validate N678xA capability matrix, negative-current quadrant only on approved models, N6784A bipolar voltage case separately, `VOLT:LIM`/compliance, OCP/OVP and live range vs DUT/fixture.
- Measure waveform current with a calibrated shunt + independent DSO or equivalent traceable sensor; quantify shunt burden, bandwidth, drift, compliance clipping and load interaction.
- Test current clipping/protection and wrong-role refusal using a safe controlled fixture; unauthorized polarity or mode must be refused before mutation.

### RW-07 — CDW, sequence and trigger synchronization qualification

- Verify 1/maximum allowed UDEF point count; CDW point/dwell boundaries, accepted effective count and 64 KiB ASCII preflight. **Do not** turn on binary CDW transmission without confirmed vendor input grammar and independent HIL framing evidence.
- Verify 0/1 sequence-step convention across tested firmware, `ARB:SEQ:QUAL?`, generated point budgets, min/max step indices, dwell-paced finite repeats, interrupted upload cleanup and exact sequence order.
- Measure BUS/external triggers, missing/multiple triggers, late/early edges, latency/jitter, per-step pacing and parallel acquisition. Test channel 1 Arb while channel 2 digitizes, respecting session and acquisition leases and cross-channel protection coupling.
- Reject 100-step+ and generated-point overflow before sequence reset; require finite trigger supply/time budgets.

### RW-08 — failure recovery, protective behavior and soak

- Safety-test matrix: SCPI -113/-241, malformed/inconsistent readback, protection trip, control-thread cancellation, LAN/VISA link loss, transport timeout after possible write, power interruption, early/never-arriving trigger, watchdog expiry, failed cleanup write, disconnect mid-ARM, two-channel concurrent activity.
- Use a fault simulator for destructive cases first. On physical instruments, execute only approved, non-destructive fault injections under operator control; **do not deliberately defeat protective interlocks or stress a DUT**.
- A software exception or successful `ABOR:TRAN` alone is not safe-off proof. Retain independent output OFF verification, instrument error/protection state, lease state, source generation and watchdog outcome.
- Run a bounded soak with an operator-approved count, elapsed limit and electrical duty cycle; record memory/resource growth, output-after-fault states and orphaned lock/lease checks. Stop on the first unsafe or unexplained failure.
- Exit: all approved cases have evidence and no unexplained energized state. Otherwise classification is NO-GO, with containment and remediation task.

### RW-09 — reports, traceability, review and downstream compatibility

Use an evidence manifest schema with at least:

```json
{
  "schema_version": "1.0",
  "run_id": "UTC timestamp plus random identifier",
  "qualification_level": "simulation|protocol_hil|analog_metrology|unattended_bench",
  "status": "PASS|FAIL|INDETERMINATE|NO_GO",
  "git": {"driver_sha": "", "scpi_core_sha": "", "test_harness_sha": ""},
  "instrument": {"idn_raw": "", "firmware": "", "channel": 1, "module": "", "options": []},
  "authorization": {"site_approval_id": "", "operator": "", "fixture_review_id": ""},
  "equipment": {"dso_id": "", "probe_id": "", "calibration_refs": []},
  "limits": {"voltage_v": [], "current_a": [], "power_w": null, "duration_s": null},
  "requested": {}, "scpi_readback": {}, "measured": {},
  "uncertainty": {"expanded": {}, "coverage_factor": null, "guard_band_rule": ""},
  "result_files": {"trace_jsonl": "", "dso_raw": "", "measurements_csv": ""},
  "sha256": {}, "cleanup": {"abort": "", "output_off_verified": false},
  "review": {"reviewer": "", "date": "", "decision": ""}
}
```

- Use real machine-readable values in actual artifacts, not placeholder strings; retain original SCPI trace, signed authorization, command/readback snapshots, equipment metadata, calibration references, raw and processed DSO waveforms, plots, uncertainty budgets, computed per-vector pass/fail, error queues and file SHA-256 hashes.
- Each test result must distinguish **not run**, **software PASS**, **protocol-HIL PASS**, **analog PASS**, and **approved unattended bench**; no roll-up PASS when a mandatory case is skipped, indeterminate or missing evidence. The report generator must validate evidence completeness and hash integrity before issuing PASS.
- Have an independent reviewer evaluate safety/risk, model applicability, SCPI grammar, analog uncertainty, cross-channel behavior, watchdogs and all failure evidence. Record review corrections and final disposition in `docs/arb_qualification/readiness_review.md`.
- Re-test downstream [Waveform Studio issue #126](https://github.com/ami3go/power_supply_test/issues/126): confirm supported driver SHA, versioned dependency pin, Option 054 matrix, session/ARM authorization, safety limits and FAIL-closed physical RUN. Update downstream only in **its own** PR/commit/version change; never silently modify both repositories together.
- Verify/close historic issues #15 and #21 only against the current tagged version; #14 only after its real fault scenario has passing unit and approved HIL evidence. An open documentation/example GUI PR is not authorization to run physical waveforms.

### RW-10 (optional) — safe host-side composite waveform planner

After the qualification gates, design an **offline-only** composer for sums such as sine + triangle, sine + sine and ramp + pulse, producing finite sampled vectors for UDEF/CDW rather than assuming an instrument supports simultaneous shapes. Audit timebase, interpolation/aliasing, aggregate min/max envelopes, point count, encoded command size, spectral distortion, dwell limits and effective output frequency; refuse clipping or out-of-range waveforms **before** any SCPI. Test sample math and approximate analog behavior with independent DSO if later enabled. This is optional and must not block critical safety closure.

## 4. Watchdog and execution discipline

- For each long local command (`pytest`/build/soak/SCPI poll/DSO acquisition), define a budget and externally enforce it. Example **offline** pattern: `timeout --signal=TERM --kill-after=5s 15m python -m pytest -q tests/unit`. Choose the budget based on workload, record timeout as FAIL, and prevent orphaned child processes.
- For physical work, use monotonic deadlines at each SCPI call, per-trigger wait and run total, with an independent equipment-level inhibit or relay. A Python thread or process watchdog cannot guarantee output OFF after a machine or transport failure.
- Work **serially** in work-package order. Merge each reviewed PR with a version increment and passing CI before starting the next; re-fetch `main` after merging to prevent conflicting branches or stale dependency pins.
- CI gates: Ruff, strict mypy driver/adapter, Python 3.10 and 3.13 pytest with watchdog, conformance/simulator, safety fault-injection, structure validation, generator freshness (AI contract, capability model, conformance inventory), install/wheel/sdist, and dependent-driver compatibility when applicable. Do not require physical hardware in CI.
- Version release: update `pyproject.toml`, `src/keysight_n6700/version.py`, plugin manifest, generated `ai/ai_contract.yaml` and SHA256 lock, `CHANGELOG.md`, `history/vX.Y.Z.md` and index; attach exact reviewer, test and CI links. Robot adapter version changes only when its independently versioned public API changes.

## 5. Exit criteria

- [ ] RW-00: fresh baseline/review and historic issues reconciled with evidence
- [ ] RW-01: finite normal waveform execution; intentional continuous mode explicitly supervised or rejected
- [ ] RW-02: issue #14 transport-fault lease reproducer and bounded safe recovery pass
- [ ] RW-03: evidence schema and dual-gated no-energy HIL harness pass offline CI
- [ ] RW-04: exact approved mainframe/module/options/firmware HIL capability/trigger matrix retained
- [ ] RW-05: voltage shape DSO amplitude/time/jitter measurements with guard-banded verdicts
- [ ] RW-06: current source/current-limit/quadrant and compliance evidence
- [ ] RW-07: CDW/sequence, step base, trigger and acquisition isolation qualified
- [ ] RW-08: supervised fault recovery and bounded soak, no unknown energized state
- [ ] RW-09: independent review, report integrity, and downstream compatibility verdict
- [ ] Every work package: dedicated versioned PR, no blockers, green watchdog CI, reviewed merge and post-merge regression
- [ ] Physical approval: named operator/site sign-off, calibration, risk controls, full raw evidence and explicit unattended-bench decision
- [ ] Any deferred or impossible-to-run hardware case is marked **BLOCKED/NOT QUALIFIED**, not PASS

**Start with RW-00, then RW-01.** Real hardware work cannot start until the software safety gates, test-specific operator/site authorization and independent protective controls are satisfied.
