# Fully automated N679xA digitizer HIL suite

The executable program is [scripts/run_scope_hil_suite.py](../scripts/run_scope_hil_suite.py). It runs a complete regression matrix **without operator intervention once explicitly authorized**, but **only** on an identified N6791A/N6792A module connected to a real N6700 mainframe. It does not use a simulator for hardware tests. Offline unit tests exercise the same orchestration against a simulator, **not** as HIL evidence.

## Scope

Each matrix case automatically configures a supported voltage/current measurement, verifies the effective sample interval and offset, uses a BUS trigger, waits for capture completion, checks buffer format/count and finite samples, and saves the waveform with SHA-256. Negative cases verify that an explicit abort works and a missing trigger times out and recovers. Each test verifies the selected load input is OFF before continuing; a failed cleanup or OFF readback stops the suite. Other channels must already be OFF; the script refuses to alter them.

A BUS-triggered, load-OFF capture proves SCPI/configuration/acquisition behavior, **not** physical transient accuracy, bandwidth, recovery time or synchronized reference DSO agreement. For those, the separate [DSO qualification procedure](SCOPE_HIL_QUALIFICATION.md) remains mandatory. This HIL test does not enable an output or generate a DUT stimulus. A fully automatic physical transient test additionally requires a characterized stimulus source, wired DSO trigger/reference acquisition, and a reviewed electrical fixture.

## Run procedure (Linux or Windows)

1. Use a bench with an independently approved electrical fixture and hardware limits. Verify the N6700 is powered, reachable, and no other program owns the SCPI session.
2. Copy [the unapproved example](scope_hil_automated.example.json), replace **all** placeholder values including mainframe IDN prefix, load-module serial, address and operator, and review the expected OFF states, limits, cases and physical connections. Set "approved": true only after bench review. The runner rejects an unapproved profile, wrong instrument identity, unknown models and invalid case parameters **before issuing any output command**.
3. Install the driver via the project's pinned requirements (e.g. uv sync --extra dev; for VISA install the optional visa dependency). To run a real suite, set **both** explicit gates and supply the reviewed profile.

On Linux:

~~~bash
export N6700_HIL_ENABLED=true
export N6700_SCOPE_HIL_APPROVED=YES_AND_REVIEWED
uv run python scripts/run_scope_hil_suite.py \
  --plan docs/my-approved-scope-hil.json \
  --operator "Bench operator" \
  --output results/scope_hil/run-001
~~~

On Windows PowerShell:

~~~powershell
$env:N6700_HIL_ENABLED="true"
$env:N6700_SCOPE_HIL_APPROVED="YES_AND_REVIEWED"
uv run python scripts/run_scope_hil_suite.py --plan docs/my-approved-scope-hil.json --operator "Bench operator" --output results/scope_hil/run-001
~~~

It never silently falls back to a simulator. Connection types are limited to Ethernet and VISA. It does not reset the instrument, clear faults at connection or change an unrelated channel. The output directory must not already exist, protecting previous evidence from overwrites.

## Evidence

- summary.json: run metadata, mainframe IDN, module model and serial, case outcomes and per-repetition timing, true/false safe-state confirmations, waveform and raw binary hashes.
- junit.xml: CI-style per-case results; even failed preflight or safe-shutdown produces a failing case.
- CASE-NNN/digitizer.csv: time_s/value columns computed using **instrument-readback** sample spacing and offset, not nominal requested spacing. Negative/abort scenarios produce no fake waveform.
- The overall summary result "PASS" means **protocol HIL passed** and selected output was verified OFF. The fixed qualification label is "PROTOCOL_ONLY_NOT_METROLOGY", regardless of individual case outcomes.

If network or SCPI interaction fails, teardown attempts to abort, confirms the selected load OFF, stops when state cannot be verified and marks the report FAIL. Mechanical wiring, emergency interlock operation, metrological accuracy and long-duration operational reliability are outside this script's acceptance scope.

## Configuration reference

Case fields: name (unique safe path label), scenario (bus, abort, missing_trigger), function (voltage/current), points (2..524288), interval_s (10.24 us..1 s, the actual interval is read back), offset_points (>= -(points-1)), transfer_format (auto/ascii/real32), repetitions (1..200), wait_timeout_s (0.1..120 s; missing-trigger negative cases <=3 s). There is a 5 million waveform-point overall budget and a maximum of 40 distinct case definitions.

When using a -512 pretrigger offset at 20.48 us, the readiness method accounts for at least ~10.49 ms plus its guard after it first observes the armed state. This is **not** a guarantee against a spontaneous external event arriving before history accumulation.

## Physical sign-off

This is a high-value protocol HIL gate but not a substitute for comparing N679xA waveforms to an independently calibrated DSO across 100+ physical transients per condition, testing transport-loss failures and completing an independently approved protected soak. [The final readiness review](SCOPE_FINAL_READINESS_REVIEW.md) remains **NO-GO** for unattended production until that evidence is collected.
