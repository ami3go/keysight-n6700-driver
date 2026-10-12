# Scope Remediation — Final Software Readiness Review

Date: 2026-10-10  
Target: Keysight N6700C + N6791A/N6792A driver digitizer, release v0.6.11  
Reference: [N679xA Scope Remediation Plan](N679XA_SCOPE_REMEDIATION_PLAN.md), [Keysight N6705C Digitizing Measurements](../Keysight_documents/N6791A_manual.pdf.md), [HIL Qualification SOP](SCOPE_HIL_QUALIFICATION.md).

**Decision: NO-GO for unattended 24/7 operation.** This is a software implementation review, not independent physical validation. A passing CI suite demonstrates offline behavior, not calibrated oscilloscope or load transient performance.

## Remediation register

| Finding | Original severity | Implementation | Residual verification |
|---|---|---|---|
| F01 pretrigger fill race | BLOCKER | PR03: waits for the observed waiting state, sample-count × verified time interval and guard; reconfirms state | Physical trigger rate/pretrigger behavior, especially spontaneous external triggers |
| F02 acquisition left armed after wait failure | BLOCKER | PR02: best-effort abort, status-verified idle, UNKNOWN if unverified | Transport disconnect, power loss and device-error injection on real hardware |
| F03 requested vs actual sample interval | HIGH | PR03: readback of effective sample interval, points and offset; metadata uses actual clock | Calibration/timebase characterization |
| F04 stale/premature completion | HIGH | PR01: acquisition generation/state checks with simulator regression | Status register semantics and high-rate triggers on physical N679xA |
| F05 waveform corruption by concurrent MEAS | HIGH | PR04: per-alias in-process acquisition lease over SCPI operations, fetch and abort | **External clients still unguarded**; same-owner raw SCPI commands and reconnect edge cases require further review |
| F06 ASCII/REAL response and global budget | HIGH | PR05: ASCII negotiation; memory/function count checks. PR07: bounded REAL32 block, endian restore | Physical multi-parameter memory behavior, VISA/USB transfer |
| F07 missing/malformed waveform samples | HIGH | PR05: strict count, finite ASCII; PR07: exact REAL32 byte count, finite floats and SHA256 | Physical data corruption/transport timeouts |
| F08 partially accepted configuration | HIGH | PR05: readbacks/error queue; failure enters UNKNOWN and retains acquisition ownership | Hardware command rollback and recovery under injected faults |
| F09 incomplete capability metadata | MEDIUM | PR06: eight mutating methods annotated and generated snapshots synced | Contract conformance checks |
| F10 blanket N679 model acceptance | MEDIUM | PR06: explicit N6791A/N6792A allowlist, vendor-declared vs HIL distinction | N6792A and different firmware remain unqualified |
| F11 large payload / audit disk pressure | MEDIUM | PR07: automatic REAL32 for large captures, bounded block, audit omission/hash | Profiling at 512K points, protocol trace behavior and soak |
| F12 timing claims from one/two samples | MEDIUM | PR08: shared-trigger DSO comparison/guard bands, NOT_RESOLVED floor | **Physical DSO comparison, 100+ repetitions per condition, calibration and reviewer evidence missing** |
| F13 nonportable HIL tooling | MEDIUM | PR08: standalone guarded scripts and evidence schema | Hardware execution; tests must run with real instrument rather than simulator |
| F14 stale runtime version | LOW | PR06: version bound to authoritative distribution version | Package/release metadata CI verification |

## Risk disposition

The implementation addresses identified code pathways, but no real-hardware evidence of the revised code exists. **F12 remains unresolved for release qualification**; F01–F08 and F11 retain hardware-specific validation obligations. The original 2026-10-09 N6791A recordings are not evidence for the newly changed acquisition/REAL32 implementation.

The per-alias lease is **process-local**, and does not prevent a second external controller from issuing a new measurement. For production, enforce network/API control-plane isolation and a lab-wide exclusive instrument lease; do not treat the in-process lease as complete multi-controller exclusion.

## Automated gate

Run:

```bash
pytest tests/unit/test_scope_release_gate.py tests/unit/test_scope_lifecycle_soak.py \
  tests/unit/test_scope_compare_cli.py
python scripts/scope_readiness_audit.py --output results/scope_release_audit.json
```

With no verified HIL manifest the second command **must exit nonzero** and write `NO_GO`. This is expected and is not a CI failure of the software baseline. After physical testing, provide a JSON manifest with the fields in [the template](scope_qualification_manifest.example.json); cite source recordings, independent DSO data and review record. Even with all fields present, the automated verdict is `HUMAN_REVIEW_REQUIRED`; no automated production GO is possible.

The evidence template is **structural only**. It cannot verify signatures, actual physical trigger wiring, data provenance, reviewer independence, safety interlocks or metrological calibration by itself.

## Additional acceptance actions before production

- Qualify each physical mainframe/load model/firmware combination; verify safe shutdown with intentionally missing trigger, power interruption and transport loss.
- Compare N679xA waveform against a calibrated independent oscilloscope sharing verified physical trigger origin. Include bandwidth, pretrigger, switching transients, BUS/voltage/current/external triggers and uncertain measurements, rather than treating a one-tick recovery time as a precise physical measurement.
- Characterize a representative 512K-point capture and sustained capture/reconnect reliability on intended OS, TCP/VISA links and actual mainframe firmware.
- Run a reviewed 24-hour protected bench soak, obtain an independent reviewer sign-off and record unresolved defects with reproducible evidence.

Until those actions are evidenced and approved: **NO-GO for unattended production**. Guarded engineering characterization may be performed under documented operator supervision, independent DUT interlocks and the approved bench procedure.
