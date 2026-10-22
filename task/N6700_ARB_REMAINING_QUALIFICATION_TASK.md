# Task: N6700 Arb remaining hardening, qualification and release readiness

**Task ID:** N6700-ARB-POST-0710 · **Revision:** 1.0 · **Prepared:** 2026-10-16  
**Repository:** https://github.com/ami3go/keysight-n6700-driver  
**Verified baseline:** `main@2f4a8333f8fb8496ef29cec63a24be15a3c33886`, Python package **0.7.10**  
**Core dependency:** `ami3go/scpi-driver-core@9c5dd50a03031f65e1aa5cf13832db7372f043b9` (both base and `[visa]`)  
**Status:** **READY for sequential software investigation; NO-GO for unsupervised physical Arb generation**  
**Nature:** Implementation-ready follow-up task; **not** a claim that remaining issues are resolved.

## 1. Purpose and verified starting point

This task supersedes *the incomplete-status portion* of [the original Arb implementation task](../docs/ARB_DRIVER_IMPLEMENTATION_TASK.md); retain that document as the original requirements and SCPI reference. Do not reimplement functionality already present.

The following are **merged software features**, not yet proven on physical Arb output:

| Original package | Current source of evidence | Outcome / qualification limit |
| --- | --- | --- |
| DRV-ARB-A | [PR #20](https://github.com/ami3go/keysight-n6700-driver/pull/20), [PR #22](https://github.com/ami3go/keysight-n6700-driver/pull/22), `module_capabilities.py` | Mainframe/module/firmware/Option 054 context, fail-closed classification, stale-handle revocation implemented |
| DRV-ARB-B/C | [PR #22](https://github.com/ami3go/keysight-n6700-driver/pull/22), `channel.py` | Shared-session configuration locking, exception-preserving cleanup and typed Arb/TRAN trigger dialect implemented |
| DRV-ARB-D/E | `waveforms.py`, `channel.py`, `test_source_waveforms.py`, `test_current_arb.py` | Seven parametric voltage/current shapes and UDEF implemented and simulator-tested |
| DRV-ARB-F/G | `test_arb_cdw.py`, `test_arb_sequence.py`, `channel.py` | Bounded ASCII constant-dwell and finite normal sequences implemented and simulator-tested |
| DRV-ARB-H | `pyproject.toml`, `test_scpi_driver_core_integration.py` | Immutable hardened core pin integrated in 0.7.10, *not* yet vendor-hardware qualified |
| DRV-ARB-I | [hardware qualification plan](../docs/ARB_COMPLETION_FIX_PLAN.md) | Plan prepared; **physical Arb and independent DSO evidence outstanding** |

As of the baseline, `main` CI passed [run 38902487336](https://github.com/ami3go/keysight-n6700-driver/actions/runs/38902487336), including Python 3.10 and 3.13. This is **software evidence only**. Existing real N6700C supply/load tests in [known risks](../review/known_risks.md) do **not** qualify the new Arb waveforms.

Open tracked concerns include [#14: digitizer lease after transport fault](https://github.com/ami3go/keysight-n6700-driver/issues/14), [#15: stale core pin](https://github.com/ami3go/keysight-n6700-driver/issues/15), and [#21: Arb capability classification](https://github.com/ami3go/keysight-n6700-driver/issues/21). Issues #15/#21 may be **historically resolved by v0.7.9–0.7.10**, but remain open at the baseline: **verify current code and tests, then close with evidence; do not treat an open issue as proof of a current defect**. Review [PR #24](https://github.com/ami3go/keysight-n6700-driver/pull/24) (basic Arb GUI; open at task creation) before touching examples or release integration.

## 2. Non-negotiable engineering rules

1. **Strictly sequential PRs**: implement one package, test, independent technical/safety review, fix findings, bump driver version, regenerate metadata, wait for *green CI on the final head*, merge, then repeat from latest `main`. Never start a dependent package before its predecessor is merged.
2. Use a **bounded watchdog** for every long step: `timeout` on local build/test commands, `pytest --timeout=30 --timeout-method=thread` for offline tests, CI job timeout, and finite per-case HIL deadlines. A timeout is a failure requiring diagnosis, not permission to skip tests.
3. **Safety before convenience**: configuration with confirmed source output OFF; full request validation before mutation; model/option/firmware and DUT envelope checks; no hidden `OUTP ON`, `*RST`, `*TRG`, protection clear or surprise trigger. A write timeout is **indeterminate hardware state**, never automatically retried.
4. No *new* Python package layout migration and no Keysight-specific behavior in `scpi-driver-core`. Preserve existing public Python, Robot and LPDS interfaces unless a narrowly justified safety fix has compatibility tests.
5. Use the instrument's live state and readbacks as authority. Track **UNKNOWN/INCONCLUSIVE**, not success, if output, trigger, cleanup, identity or capture cannot be verified. Preserve the initiating exception; attach cleanup errors.
6. Keep finite `wait_voltage_transient_complete()` semantics. Existing explicitly requested `repeat_count="infinite"` has a documented, separate supervised use case. Do **not** silently remove it: either prove a bounded supervisory/watchdog contract or design a backward-compatible explicitly gated path.
7. Guard physical tests independently: no configuration that energizes hardware until signed/site-approved authorization, approved fixture and limits, calibrated independent DSO, and explicit human initiation are present. **Do not infer authorization from an existing HIL environment variable.**
8. Every code or documentation PR increments the Python distribution version and synchronizes `pyproject.toml`, `src/keysight_n6700/version.py`, `plugin_manifest.json`, `ai/ai_contract.yaml`, generated lock, `CHANGELOG.md`, `history/vX.Y.Z.md`. Adapter version changes only for actual adapter public API changes.

## 3. Ordered package / acceptance matrix

| Order | ID | Priority | PR scope | Entry dependency | Required merge gate |
| --- | --- | --- | --- | --- | --- |
| 1 | **ARB-R01** | P0 | Transport fault / digitizer acquisition lease recovery | Current `main`, issue #14 | Fault-injection tests prove no permanent lease deadlock, safe shutdown and truthful UNKNOWN state |
| 2 | **ARB-R02** | P0 | Audit capability matrix and close/reopen issue #21 accurately | R01 merged | Exact hardware-context positive/negative matrix, no SCPI writes for rejected configurations |
| 3 | **ARB-R03** | P1 | Core dependency compatibility and release evidence audit, issue #15 | R02 merged | Both immutable pins identical; old/new behavior and fault tests; issue disposition documented |
| 4 | **ARB-R04** | P0/P1 | Supervised infinite-repeat lifecycle and cross-channel concurrency | R03 merged | No indefinite/unmonitored run or incorrect complete; finite abort, output-state evidence, API compatibility |
| 5 | **ARB-R05** | P1 | Protocol conformance and Arb regression matrix | R04 merged | Command golden vectors, limit/readback/fault tests for every shape/current/CDW/sequence family |
| 6 | **ARB-R06** | P1 | Hardware-safe HIL runner, metrology evidence schema and dry run | R05 merged | Hardware opt-in cannot be bypassed; reports deterministic PASS/FAIL/INCONCLUSIVE/NOT_RUN |
| 7 | **ARB-R07** | Release gate | Authorized physical Arb/DSO characterization | R06 approved, explicit site authorization | Signed independent measurement evidence + safety pass, otherwise NO-GO |
| 8 | **ARB-R08** | Release | Downstream compatibility, documentation, production-readiness decision | R07 evidence; software-only subset may be prepared earlier | Explicit supported hardware matrix and qualification label, release artifacts and regressions |

The status of an individual package must be updated in this file when merged: branch, PR, merge SHA, release version, CI URL, findings disposition and (if applicable) HIL evidence ID. **Do not mark physical qualification complete from simulator data**.

## 4. ARB-R01 — faulted transport and acquisition lease recovery (P0)

**Problem:** [issue #14](https://github.com/ami3go/keysight-n6700-driver/issues/14) records a real N6700C/N6791A digitizer query timeout: transport faults, `abort_digitize()` fails, acquisition lease remains held, later `OUTP?` on another channel and `disconnect()` can be blocked. Emergency output-OFF and subsequent OFF *verification* are separate requirements.

**Inspect:** `src/keysight_n6700/acquisition_lease.py`, `driver.py`, `channel.py`, `test_digitize_session_lease.py`, `test_digitize_integrity.py`, core transport state and `ScpiSession.operation_lock()`.

**Steps:**
1. Reproduce the documented fault with a deterministic transport stub raising a transport timeout during `SENS:SWE:POIN?`. Assert lease owner/generation before and after, and distinguish transport FAULTED from acquisition still active.
2. Specify recovery state transitions: faulted session permanently fences further unsafe writes, invalidates acquisition ownership/tokens, and marks measurement/output state **UNKNOWN** until confirmed on a new valid session. Releasing a *software* lease must **never** be interpreted as hardware safe.
3. Guarantee emergency shutdown attempts are never rejected purely because another acquisition owns a lease; allow only carefully reviewed non-destructive status reads, respecting transport state and mainframe shared resources. No broad bypass for arbitrary SCPI.
4. Make disconnect/close able to release local resources even if transport shutdown fails; keep primary error plus cleanup diagnostics. Reconnect must establish a new capability fingerprint, deny stale handles and re-read output state before rearming.
5. Test second-channel read-only OFF verification on an **open** transport, repeated abort, concurrent acquisition fault/close, timeout during output-off, and failures during recovery. Enforce a bounded deadlock watchdog.

**Acceptance:** no indefinite lease lockout; no unauthorized mutation or retry; no false OFF/COMPLETE assertion; explicit recovery log with original error and uncertain hardware state; regressions for scope and voltage transients remain green.

## 5. ARB-R02 — vendor-verified identity and capability matrix

**Inspect:** issue #21, `module_capabilities.py`, `discover_modules()`, `tests/unit/test_arb_capability_gates.py`, [Keysight N6700C guide](https://www.keysight.com/nz/en/assets/9018-04957/service-manuals/9018-04957.pdf), [N6705C guide](https://www.keysight.com/us/en/assets/9018-01649/user-manuals/9018-01649.pdf), [Arb command catalog](../docs/ARB_WAVEFORM_REFERENCE.md).

**Steps:**
1. Build an evidence table of *exact mainframe variant × exact module model/suffix × installed options (including 054) × firmware × capability*; separate LIST, STEP, UDEF, parametric voltage/current Arb, CDW, sequence, SMU priority and electronic load. Cite specific vendor table/page in the PR; do not infer capability from module prefix alone.
2. Test N6700C N6775A with and without 054, supported N6705C combinations, N676x/N678x exceptions, unsupported models/firmware, empty/invalid option responses, empty/unknown `*IDN?`, changed channel identity, and stale capability references.
3. Validate mainframe capability and individual shape dialect independently. A simulator's advertised N6705C identity must not justify a different physical chassis. All denied requests must fail **before Arb/LIST mutations**; ordinary DC commands must continue working where safe.
4. If PRs #20/#22 already satisfy requirements, record test and primary-source evidence and close #21; otherwise fix defects via a versioned PR.

**Acceptance:** no fail-open unknown identity; independently traceable capability decisions; negative tests assert outbound SCPI mutation log unchanged.

## 6. ARB-R03 — pinned core and integration issue reconciliation

**Inspect:** `pyproject.toml`, `test_scpi_driver_core_integration.py`, [core hardening task](https://github.com/ami3go/scpi-driver-core/blob/main/task/N6700_ARB_CORE_HARDENING_TASK.md), [core compatibility report](https://github.com/ami3go/scpi-driver-core/blob/main/docs/n6700-core-compatibility.md), issue #15.

**Steps:**
1. Confirm both runtime and `[visa]` pins exactly match reviewed `9c5dd50a03031f65e1aa5cf13832db7372f043b9`; compare relevant lock-order fix `e5a1b04` and updated deadline/byte APIs against current core.
2. Validate isolated wheel + sdist installations against the pinned commit and reproduce old-pin/new-pin compatibility for LIST/UDEF, ARB current/voltage, N679xA acquisition, REAL32, binary blocks, alias locks, timeout behavior, exception translation and trace redaction.
3. Verify there is no accidental floating `@main` or unreviewed dependency. If all correct, **close #15 as already fixed** with commit, tested compatibility run and explicit release evidence; otherwise propose a narrowly versioned compatibility fix.

**Acceptance:** pin reproducibility plus full Python/Robot regression evidence, with no spurious changes to the generic core.

## 7. ARB-R04 — run supervision, finite completion and concurrency

**Inspect:** `waveforms.py` (`normalize_repeat_count`, `repeated_duration`), `channel.py` (`_require_duration`, arm/ready/trigger/wait/abort, generation tokens), `driver.py` facade, `tests/unit/test_voltage_transient.py`.

**Steps:**
1. Map all modes that admit `"infinite"`: LIST, single-shape Arb, UDEF and continuous initiation. Document the difference between vendor repeat-count limit, one-pass time and **unbounded wall-clock output**.
2. Retain compatibility while ensuring the normal finite completion API rejects unbounded runs; an explicitly opted-in infinite run needs a supervisor-owned abort path, maximum allowed elapsed time, transport-failure handling and emergency output-OFF on watchdog expiry.
3. Prove no global `*TRG`; channel-scoped Arb/TRAN trigger-source dialects, generation tokens, WTG/active status and stale/cross-channel tokens work for BUS and external triggers. Never claim exact external-edge capture without instrument evidence.
4. Fault-inject a dropped trigger, stalled completion, protection trip, re-arm after abort, connection loss, interleaved acquisition on another channel, and two simultaneous source configurations; no SCPI interleaving inside a configuration transaction.
5. Verify no waiting loop holds the shared session lock for its full duration and no auto-retry duplicates `INIT`, trigger or output changes.

**Acceptance:** finite ordinary operations; explicitly supervised infinite behavior only; bounded termination/UNKNOWN state; deterministic fault and concurrency tests.

## 8. ARB-R05 — complete protocol and parameter conformance

Implement **tests and corrections for actual gaps**, not a second generator. Use vendor commands from [Arb reference](../docs/ARB_WAVEFORM_REFERENCE.md), [completed offline plan](../docs/ARB_COMPLETION_FIX_PLAN.md), `test_source_waveforms.py`, `test_current_arb.py`, `test_arb_cdw.py`, `test_arb_sequence.py`, and `test_scpi_driver_core_integration.py`.

| Area | Required positive/negative protocol evidence |
| --- | --- |
| STEP/RAMP/STA/SIN/PULS/TRAP/EXP/UDEF | Model/shape grammar, endpoints/extrema, time/frequency/step limits, range and OVP/current compliance, quantized effective readback, no output enable during configuration |
| SMU current Arb | Current priority, `VOLT:LIM` compliance, approved signed-current quadrant per exact model, ordinary supply treated as current-limit modulation only |
| CDW | Exact 65,535-point capability versus 64-KiB ASCII command cap, preflight before mutation, accepted-point count, quantized dwell, incomplete/oversized transfer and cleanup |
| Sequence | Step indexing probe (0/1), generated-point budget <= 511, quality query/readback, finite repeats and dwell pacing, interrupted upload and foreign channel |
| Triggers & scope | BUS/EXT, source/acquisition ownership, abort and protection races, status-bit transitions, lease interaction, trace/error-queue evidence |

Use boundary, property/fuzz (where deterministic) and replayable golden SCPI vectors. The full dataset should include unsupported and malformed response cases. Derive min/max values from referenced vendor documents, not guesses.

**Acceptance:** all supported simulated cases pass; incorrect dialect or hardware identity fails closed; report rejected/untested combinations separately, not as green coverage.

## 9. ARB-R06 — nonenergizing HIL runner and metrology evidence contract

Prepare and merge a **hardware-disabled-by-default** qualification suite by extending `tests/hardware/test_hardware_voltage_transient.py`, `tests/hardware/conftest.py`, and the existing guarded HIL patterns. Keep source-Arb evidence separate from electronic-load digitizer/oscilloscope acceptance.

**Authorization gate (all mandatory before ANY output enable):**
- Signed site authorization naming operator, bench, instrument identities/serials, module/options/firmware, wiring, fixture, approved output envelope, date/time and emergency stop procedure.
- Independent physical confirmation of passive/protected load, correct polarity, wiring, OVP/OCP/compliance, cable/probe ratings and interlock.
- Explicit per-run opt-in and a **separate** per-case energization acknowledgement. Dry-run/simulator is the default.
- Calibrated independent DSO plus known bandwidth/sample rate, probe attenuation and measurement uncertainty appropriate for target waveform.

**Proposed machine-readable per-case evidence (JSON + referenced binary files):**

```json
{
  "schema_version": "1.0",
  "case_id": "ARB-HIL-VOLT-PULSE-001",
  "qualification_level": "analog_metrology",
  "verdict": "INCONCLUSIVE",
  "reason": "example schema only, not a test result",
  "utc_start": null,
  "utc_end": null,
  "authorization_reference": null,
  "operator_and_reviewer": null,
  "instrument": {"idn": null, "serial": null, "firmware": null, "mainframe": null, "module_model": null, "channel": null, "options": []},
  "software": {"driver_commit": null, "core_commit": null, "python_version": null},
  "fixture": {"wiring_revision": null, "load": null, "emergency_stop_verified": null},
  "requested": {"waveform_type": null, "levels": null, "timebase_s": null, "repeat": null, "trigger": null, "approved_envelope": null},
  "observed": {"scpi_readbacks": null, "error_queue": null, "status_timeline": null, "dso_metrics": null, "uncertainty": null},
  "acceptance": {"vendor_limit_source": null, "numeric_limits": null, "pass_fail_rule": null},
  "safety": {"abort_attempted": null, "output_off_commanded": null, "output_off_verified": null, "unknown_state": null},
  "artifacts": {"scpi_trace": null, "raw_dso_data": null, "calibration_record": null, "sha256": {}}
}
```

Schema is a **proposal**; replace `null` with real evidence during an authorized run. Record exact measurement units, timestamps, uncertainty budget, raw waveform/sample interval and reproducible SHA-256 hashes. A FAIL or INCONCLUSIVE on safety/verification **blocks qualification**; an unrun HIL case is **NOT_RUN**, never PASS. Define numeric tolerances from the vendor spec and calibrated fixture *before* performing each test; no invented universal threshold.

**Software tests:** opt-in absent/partial/stale; mismatched serial/option/firmware; missing calibration; invalid envelope; unsupported shape; permission denial; watchdog expiry; runner process crash; partial report write; forced output-off failure. All must fail closed **without energization**. Use atomic evidence writes and a watchdog/cleanup independent of the test body.

## 10. ARB-R07 — separately authorized real hardware / independent DSO

Do **not** start from this task alone. Prepare a detailed site-signed test procedure, then run only after separate authorization.

**Minimum matrix:** N6700C source with/without Option 054 and N6705C where actually available; supported N6775A and exact available N676x/N678x modules; unsupported models; voltage and approved current STEP/RAMP/STA/SIN/PULS/TRAP/EXP/UDEF; CDW short/long; finite sequences; BUS and external timing/jitter; load dependence, overshoot, amplitude/frequency/duration; two-channel isolation and acquisition lease coexistence; OVP/OCP trips; timeout/unplug/network loss; abort/failed cleanup and confirmed output OFF. Do not claim unavailable hardware configurations have passed.

**Gates:**
1. Static fixture review and raw `*IDN?`/module/option/firmware snapshot before output.
2. Low-risk waveform first at preapproved limits with independent DSO capture.
3. Distinguish requested SCPI values, accepted readbacks and actual analog measurements; record errors/status/timebase.
4. Run fault and cross-channel cases only if covered by separate safe test authorization and a tested independent hardware interlock.
5. Finish with explicit abort, output OFF and independent output-state verification. Any uncertainty leaves **NO-GO**.
6. Independent reviewer validates raw data, calibration and trace hashes; create a signed, immutable release-evidence index.

**Outcome levels:** `offline_simulation`, `protocol_HIL`, `analog_metrology`, `unattended_production`. A lower level never implies a higher level. Without completed site approval, keep `ARB-R07=BLOCKED_NOT_AUTHORIZED`.

## 11. ARB-R08 — downstream, release and documentation closure

1. Re-run [Waveform Studio issue #126](https://github.com/ami3go/power_supply_test/issues/126) compatibility against the exact **released and pinned** driver/core commits. Keep physical RUN disabled in the application until its own ARM/session and fixture safety gates pass. Do not silently change a downstream pin.
2. Document a checked mainframe/module/options/firmware **capability table**, supported waveform feature and qualification level in the driver README and `docs/VOLTAGE_TRANSIENT_IMPLEMENTATION.md`.
3. Audit the merged examples and open GUI PR #24 for input validation, background-thread cancellation, safe output-off/teardown, unknown instrument state, explicit authorization and watch-dogged infinite profiles. Treat GUI examples as non-qualified until independently reviewed.
4. Deliver wheel/sdist installability and immutable core dependency check, public API/Robot compatibility, release version synchronization and changelog with links to real evidence. Keep `review/known_risks.md` accurate.
5. Close issues #14/#15/#21 **only** after linking exact fix/tests or state precisely why each remains open. No unsupported production, HIL or analog-metrology claims.

## 12. One-package-at-a-time execution protocol

For each package:

1. Fetch latest `main`, inspect code, issues, open PRs and applicable primary Keysight guide before editing. Record observed baseline SHA and existing tests.
2. Write or update the package-specific acceptance cases and reproducible failure test first; reject unsupported assumptions.
3. Implement the smallest safe patch within the repository's existing `src/keysight_n6700/` layout. Add dedicated unit/contract/negative/fault and concurrency tests.
4. Run focused test with watchdog, then full suite (`pytest --timeout=30 --timeout-method=thread`), `ruff check src adapters tests scripts`, strict driver/adapter mypy, structure validator, contract/capability generators and wheel/sdist build. Use a hard outer shell/CI timeout as well; investigate hangs, never mask them.
5. Ask for a **genuine independent technical/safety code review**: concurrency/lock order, power/energy envelope, failure preservation, side effects, readback truth, transport timeout, trigger isolation, state ownership, evidence integrity, backwards compatibility. Address *all blocking findings* in the same PR.
6. Bump the version for that PR; update synchronized release artifacts and record tests, reviewer, SHA and limitations. Open PR; wait for **both** GitHub Python matrix jobs on the **last pushed SHA**, then merge. Re-run main regression after merge before proceeding.
7. If independent review, required CI or physical authorization is unavailable, mark **BLOCKED**, preserve the branch/PR and do not declare completion or proceed to dependent work.

Suggested safe CI shell commands (software only):

```bash
timeout 180s pytest -q --timeout=30 --timeout-method=thread tests/unit/test_arb_capability_gates.py tests/unit/test_source_waveforms.py tests/unit/test_current_arb.py tests/unit/test_arb_cdw.py tests/unit/test_arb_sequence.py tests/unit/test_scpi_driver_core_integration.py
timeout 900s pytest -q --timeout=30 --timeout-method=thread
timeout 180s ruff check src adapters tests scripts
timeout 180s mypy src/keysight_n6700
timeout 180s mypy --ignore-missing-imports adapters/robotframework/keysight_n6700_robotframework_adapter
timeout 180s python scripts/validate_structure.py
timeout 180s python -m build
```

The values above are **software watchdog ceilings**, not hardware safety reaction limits. Real tests need stricter site-determined limits and an independent hardware stop mechanism.

## 13. Final definition of done

- [x] R01 (PR #28, issue #14 closed 2026-10-22): no stale lease after transport fault; safe cleanup and explicit UNKNOWN state, issue #14 resolved
- [x] R02 ([ARB_CAPABILITY_MATRIX.md](../docs/ARB_CAPABILITY_MATRIX.md), `tests/unit/test_arb_capability_matrix.py`): vendor-verified matrix; #21 closed by the merging PR
- [x] R03 (issue #15 closed 2026-10-21, pins verified): pinned core and old/new compatibility evidence; issue #15 disposition recorded
- [x] R04 (source side; load-side supervisor deferred, see [SUPERVISED_RUNS.md](../docs/SUPERVISED_RUNS.md)): supervised unbounded-mode contract and finite completion/abort/cross-channel regression complete
- [ ] R05: complete waveform protocol, negative-boundary, malformed-response and failure matrix green
- [ ] R06: HIL evidence schema + nonenergizing gated runner and dry-run tests merged
- [ ] R07: site-approved physical HIL + independently calibrated DSO **or explicit NO-GO** documented
- [ ] R08: downstream compatibility, docs, release metadata, issue closure and production readiness decision complete
- [ ] Every implementation PR has a version increment, independent review, final-head green CI and merge/main regression record
- [ ] No fake physical results, no unattended output without operator authorization, no claimed OFF if output cannot be verified

**First implementation action:** start **ARB-R01** by reproducing issue #14 in a fault-injected test. Do not start with a broad rewrite or a hardware Arb run.
