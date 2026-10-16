# ARB completion fix plan

**Branch:** `fix/arb-completion`
**Baseline:** `d2e4276` (`main`, 2026-10-15)
**Prepared:** 2026-10-16
**Status:** implementation in progress; simulator/offline claims only

This plan closes the gaps found after merging the waveform-generator review.
It supplements the broader [ARB driver implementation task](ARB_DRIVER_IMPLEMENTATION_TASK.md)
and keeps physical qualification separate from software completion.

## Safety and scope

- Never energize hardware as part of this branch. Hardware tests remain opt-in,
  identity-gated, envelope-gated, and operator-authorized.
- Fail closed when the mainframe, module, installed options, firmware dialect,
  point budget, or output state is unknown.
- Validate a complete request before its first mutating SCPI command.
- Preserve the first failure and attach cleanup failures as diagnostic context.
- Serialize each multi-command source configuration across the shared mainframe
  session; per-channel locking alone is insufficient.
- Simulator success is protocol-model evidence, not analog qualification.

## Ordered work packages

### P0-1 — exact capability context and Option 054 gating

1. Normalize mainframe, module, options, and firmware into an immutable context.
2. Unknown mainframe context must not imply ARB support.
3. Implement the reviewed N6700C/N6705C module-and-option matrix. In particular,
   ordinary N673x/N674x/N675x/N677x modules in N6700C require Option 054; unknown
   combinations fail closed. Keep LIST/STEP capability independent where the
   vendor documentation supports it.
4. Invalidate cached channels/configurations when rediscovery changes the
   capability fingerprint.
5. Add a full negative matrix and prove rejected calls emit no ARB writes.

### P0-2 — configuration error preservation and session serialization

1. Expose one alias/session operation transaction guard through the driver
   facade, with a documented channel-lock then session-lock order.
2. Hold the session guard around complete prepare/program/readback/error-check
   transactions, not around individual writes only.
3. Make cleanup best effort, preserve the original exception object/type, attach
   cleanup results, and leave local state `unknown` whenever cleanup is uncertain.
4. Add failure injection at program, query, error-drain, and cleanup positions;
   add two-channel interleaving and bounded deadlock tests.

### P0-3 — typed trigger-command dialect

1. Represent approved trigger dialects as internal typed capabilities rather
   than caller-provided SCPI strings.
2. Keep the confirmed channel-scoped `TRIG:TRAN` lifecycle for supported paths.
3. Add `TRIG:ARB` only for a mainframe/firmware combination supported by primary
   vendor documentation or retained HIL evidence; otherwise reject it.
4. Never use mainframe-global `*TRG` for an ordinary single-channel run.
5. Add golden command vectors, cross-channel isolation, and unknown-dialect tests.

### P1-4 — finite standard voltage waveform completion

Audit STEP, RAMP, STAircase, SINusoid, PULSe, TRAPezoid, EXPonential, and UDEF
for exact parameter ranges, quantization, readbacks, finite duration, OVP/range
headroom, and safe failure state. Fill missing shape-by-parameter negative tests.

### P1-5 — current ARB safety matrix

1. Separate current-programming capability from voltage-source/current-limit
   behavior for ordinary supplies.
2. Gate SMU current waveforms on priority mode and reviewed quadrant/model
   support; reject negative current unless that exact combination is approved.
3. Verify current range, voltage compliance, protection state, readback, and
   wrong-family refusal in offline tests.

### P1-6 — constant-dwell transfer bounds

1. Confirm vendor point limits and ASCII/binary grammar from primary references.
2. Preflight encoded command length against an explicit bound before any write.
3. Do not fragment a vector unless the vendor grammar explicitly permits it.
4. Test minimum, maximum, oversize, malformed readback, quantized dwell, and
   cleanup after transfer failure.

### P1-7 — sequence point/quality/trigger bounds

1. Compute the vendor-generated waveform-point budget for every shape and step.
2. Query and validate sequence quality where the installed dialect supports
   `ARB:SEQ:QUAL?`; fail closed when required information is unavailable.
3. Keep the runtime 0/1 step-base probe, but bound total steps, points, duration,
   repeats, and the finite trigger schedule before reset/programming.
4. Reject unbounded trigger-paced sequences from the normal completion API.
5. Add boundary, interrupted-upload, cleanup, and channel-isolation tests.

### P1-8 — reviewed core dependency integration

1. Inspect the latest `scpi-driver-core` changes against the companion hardening
   task; do not update to an unreviewed branch head.
2. Pin normal and `[visa]` requirements to the same immutable reviewed commit.
3. Run old/new compatibility checks for LIST/UDEF, ARB, digitizer, tracing,
   timeouts, binary blocks, and session locking before accepting the new pin.

### P1-9 — documentation, generated contracts, and release metadata

1. Replace stale “not implemented” tables/examples with the actual API and its
   qualification level.
2. Keep hardware status explicit: implemented and offline-tested does not mean
   protocol-HIL, analog-metrology, or unattended-production qualified.
3. Regenerate capability, AI-contract, and conformance artifacts.
4. Add a new version, changelog/history entry, and build both wheel and sdist.

### Release gate — guarded HIL and independent DSO evidence

Prepare but do not execute the hardware matrix without separate authorization.
It must cover supported and rejected mainframe/module/option combinations,
voltage/current shapes, BUS/external triggering, two-channel isolation,
timeouts and protection trips, cleanup/output-OFF verification, sequence
numbering/quality, N6784A bipolar behavior, raw DSO data, uncertainty, hashes,
and operator/site approval.

## Commit and verification policy

Each work package receives its own commit after its focused tests pass. After
all software packages:

```text
pytest -q
ruff check .
mypy src/keysight_n6700
python scripts/generate_capability_model.py
python scripts/generate_ai_contract.py
python scripts/generate_conformance_inventory.py
python -m build
```

The final branch must have no generated diff, no untracked release artifact,
and no claim that skipped hardware tests passed.

## Completion checklist

- [ ] P0-1 exact capability/Option 054 matrix
- [ ] P0-2 error preservation and shared-session transaction lock
- [ ] P0-3 typed and verified trigger dialect
- [ ] P1-4 standard voltage shape audit
- [ ] P1-5 current ARB safety matrix
- [ ] P1-6 bounded CDW transfer
- [ ] P1-7 bounded sequence quality/point/trigger model
- [ ] P1-8 reviewed core pin and compatibility suite
- [ ] P1-9 documentation/contracts/version/build
- [ ] Hardware plan prepared; physical execution remains separately authorized
