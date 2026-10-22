# Guarded source Arb HIL runner and evidence contract (ARB-R06)

`scripts/run_arb_hil.py` and `keysight_n6700.arb_hil` run reviewed source Arb cases
against a real instrument, behind explicit gates, and write tamper-evident evidence.
It is separate from the N679xA digitizer suite (`SCOPE_AUTOMATED_HIL.md`).

**Nothing in this repository has run it on hardware.** The bench N6775A has no
Option 054 ([ARB_CAPABILITY_MATRIX.md](ARB_CAPABILITY_MATRIX.md)), so every Arb case
would be `NOT_RUN` there. All tests use a simulator stand-in and are not evidence.

## Two design choices to know about

These were chosen explicitly for this package; both trade safety margin for convenience:

1. **An energizing path exists.** The runner can enable a source output, but only for a
   case that carries its own `energize_ack` in the approved plan *and* is listed in
   `N6700_ARB_HIL_ENERGIZE_CASES`. Without both, the case is `NOT_RUN` and the output is
   never switched on. (The alternative was to ship only non-energizing cases and add the
   energizing step in ARB-R07 behind the already-tested gate.)
2. **Approval has no expiry.** It is bound to the plan's SHA-256 and to the exact
   identified hardware, not to a date. Editing the plan, or any change of mainframe
   serial, firmware, module model or serial, or installed options, invalidates it. A plan
   approved months ago and then rewired on the bench *around* the same instrument would
   still match. If that matters, add a `valid_until` check or re-approve per campaign.

## Gates, in the order they are checked

| # | Gate | Where | Refusal |
|---|---|---|---|
| 1 | Plan parses, `approved: true`, schema 1.0 | before any I/O | `HilRefused` lists every reason |
| 2 | Approval has reference, operator and a **different** reviewer | before any I/O | same |
| 3 | `approval.plan_sha256` equals the SHA-256 of the canonical plan | before any I/O | "edited after approval?" |
| 4 | Fixture named, load described, `emergency_stop_verified: true` | before any I/O | same |
| 5 | Finite envelope; baseline inside it; positive current limit; duration cap 60 s | before any I/O | same |
| 6 | Every case: safe id, supported shape, waveform inside the envelope and duration, watchdog 0-120 s, level matches `energize`, `energize_ack` exact | before any I/O | same |
| 7 | `analog_metrology` cases: valid unexpired calibration block with an existing, hash-matching record, plus acceptance limits and rule defined up front | before any I/O | same |
| 8 | CLI: `--operator` equals `approval.operator`; output directory does not exist | before any I/O | exit 2 |
| 9 | `N6700_HIL_ENABLED=true` **and** `N6700_ARB_HIL_APPROVED=YES_AND_REVIEWED` | before connecting | exit 2 |
| 10 | Not a simulated transport; IDN, mainframe serial, firmware, module model, module serial and options all equal the plan | after connecting, **before any state-changing command** (connecting sends read-only identification queries; no reset, no error clear) | `HilRefused`, no state-changing command sent |
| 11 | Every output on the mainframe reads OFF (including the selected one) | after identity | `HilRefused`; nothing is switched, not even the selected output |
| - | `watchdog_s` is at least the waveform duration plus 2 s; the supervised run's deadline is `watchdog_s` minus 2 s | plan validation | `HilRefused` |
| 12 | Module advertises source Arb (capability matrix) | per case | case `NOT_RUN` |
| 13 | Per-case energization: plan `energize_ack` and the case id in `N6700_ARB_HIL_ENERGIZE_CASES` | per case | case `NOT_RUN` |

`--hardware` is required to touch an instrument; the default is a simulator dry run.

## Running

```bash
# 1. dry run: simulator only, every case NOT_RUN, proves the plan parses and configures
uv run python scripts/run_arb_hil.py --plan my_plan.json --operator "Operator A" --output results/arb_hil/dry-001

# 2. real run: every gate above
export N6700_HIL_ENABLED=true
export N6700_ARB_HIL_APPROVED=YES_AND_REVIEWED
export N6700_ARB_HIL_ENERGIZE_CASES=ARB-HIL-VOLT-SINE-RUN-001   # only for cases you mean to energize
uv run python scripts/run_arb_hil.py --plan my_plan.json --operator "Operator A" \
  --output results/arb_hil/run-001 --hardware
```

Compute `approval.plan_sha256` last, after the final edit, with
`keysight_n6700.arb_hil.plan_sha256(json.load(...))`. [The example plan](arb_hil_plan.example.json)
is unapproved and full of `REPLACE` placeholders, and a test checks it can never run.

## What a case does

- **`protocol_nonenergizing`**: configures the waveform with the output OFF (the driver
  reads every parameter back), records the readbacks, aborts, verifies OFF. Nothing is
  energized.
- **`protocol_energized`**: the same, then selects the BUS trigger source, sets baseline
  voltage and current limit, enables the output, arms, triggers and hands the run to
  `supervise_voltage_transient()` (R04) with the case watchdog as its hard limit, then
  switches OFF and verifies. PASS means the protocol path completed cleanly. It is not a
  waveform accuracy claim.
- **`analog_metrology`**: runs as above but is at best `INCONCLUSIVE`: independent
  calibrated DSO evidence cannot be attached yet (ARB-R07), and the schema refuses a
  metrology `PASS` without it.

Each case runs under a watchdog thread independent of the case body. If the body overruns
its `watchdog_s`, the watchdog aborts and switches the output OFF on its own thread (recorded
as `safety.watchdog_cleanup_verified`), and the case is `FAIL`. The body checks a cooperative
guard before every step that changes the output, so once the watchdog has fired it cannot
switch the output back ON; the watchdog's cleanup and the main thread's are serialised by a
lock. An energized run that the supervisor ends with anything but `completed` (including its
deadline) is a `FAIL`. A `KeyboardInterrupt` finishes the cleanup, writes the evidence and then
propagates. After any case, the abort and OFF readback decide `safety.output_off_verified`;
if OFF cannot be verified the run stops (even with `stop_on_failure: false`) and the summary
is `FAILED_UNVERIFIED_SAFE_STATE`.

## Evidence

A run directory contains `plan.json`, one `<case_id>.json` per case, and `summary.json`
(written last, listing each case file's SHA-256). Every file is written atomically
(temp file then rename); the directory can never be reused.
Writes are flushed and `fsync`ed before the rename. `verify_evidence_dir()` reports a missing
or truncated summary, missing, extra, edited or foreign case files, a case list that differs
from `plan.json`, a `plan.json` that no longer matches the recorded hash, verdicts that differ
between summary and case file, a summary `result` its case verdicts do not support, and schema
violations. It is not a signature: someone able to rewrite every file consistently, including
the hashes, is not detected.

Per-case record (abridged): `schema_version`, `case_id`, `qualification_level`, `dry_run`,
`verdict`, `reason`, `utc_start`/`utc_end`, `authorization_reference`,
`operator_and_reviewer`, `plan_sha256`, `instrument` (IDN, serial, firmware, mainframe,
module model, channel, options), `software`, `fixture`, `requested`, `observed` (readbacks,
error queue, status timeline, DSO metrics, uncertainty), `acceptance`, `safety`
(abort/OFF commanded, `output_off_verified`, `unknown_state`, `watchdog_expired`) and
`artifacts` (with SHA-256).

| Verdict | Meaning |
|---|---|
| `PASS` | executed, safe state verified, error queue empty, not a dry run, not metrology |
| `FAIL` | executed and something went wrong, or the safe state was not verified |
| `INCONCLUSIVE` | executed but the evidence needed to decide is absent (metrology without a DSO) |
| `NOT_RUN` | not executed (dry run, refusal, stopped earlier, capability absent). Never counts as a pass |

`validate_case_evidence()` enforces these: a `PASS` needs `unknown_state == false`,
`output_off_verified == true`, an empty error queue, timestamps and an identified instrument;
a dry run can only be `NOT_RUN`; a metrology `PASS` is rejected. The summary `result` is
`PASS` only if every case passed; `INCOMPLETE` if some were not run or inconclusive;
`FAIL` otherwise. The label is always `PROTOCOL_ONLY_NOT_METROLOGY`.

## Finding: N6700C source Arb trigger dialect

On N6700C firmware E.02.09 the ARB start trigger is mainframe-wide: `TRIG:ARB:SOUR` takes no
channel list and `TRIG:ARB:SOUR BUS,(@1)` is rejected with `-108`
([LOAD_LIST_ARB.md](LOAD_LIST_ARB.md), confirmed on hardware for the N6791A). The driver
still classifies *source* Arb on an N6700C as `channel_scoped`, so an energized case against
an N6700C with Option 054 fails at the trigger-source step, with the output still OFF. The
runner handles this correctly (a test pins it) but the dialect is not fixed: that needs
evidence from an Option 054 source module. Passive cases and N6705C mainframes are
unaffected.

## Not covered

- Any hardware run, and any Option 054 / SMU behaviour (issues #34, ARB-R07).
- A hung SCPI call that holds the session lock: the watchdog calls the normal driver
  shutdown path, which needs that lock. Pair real runs with an independent output-off path
  and the instrument I/O watchdog.
- Current-source, list, user-defined and sequence cases (voltage parametric shapes only).
- Attaching and judging DSO data (ARB-R07).
