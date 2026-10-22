# Roadmap

Where the driver stands after v0.8.0 and what remains before the status can
move from `untested` to `stable` (LPDS-001 §9). Written 2026-10-22 from
[`review/known_risks.md`](../review/known_risks.md), the open
[`task/`](../task/README.md) specifications and the open GitHub issues. Order
is by dependency and by how cheaply the bench can close each item.

## Where we are

- **Fully tested for the N6775A + N6791A path** on one N6700C (scoped claim,
  see the README). Hardware acceptance §1-§7 pass, including the buck
  regulation run with a real-current load LIST.
- Overall status stays `untested`; package classifier stays Alpha.
- Closed since v0.7.10: transport-fault lease recovery (#14, PR #28), core pin
  (#15), N679xA LIST/ARB (PR #27), exact-depth digitizer config (PR #29).

## Phase 1 — Close software-only items (no bench time)

| Item | Why | Done when |
|---|---|---|
| Issue #21: ARB capability classification | Unknown mainframe may fail open; Option 054 on N6700C unresolved | Exact hardware-context matrix tests; rejected configs issue no SCPI writes; issue closed with evidence |
| Remaining ARB-R04/R05 packages in [`task/N6700_ARB_REMAINING_QUALIFICATION_TASK.md`](../task/N6700_ARB_REMAINING_QUALIFICATION_TASK.md) | Supervised unbounded modes, full waveform/parameter conformance matrix | Checklist items ticked with merged PRs; verify current code first, some may already be resolved |
| ARB-R06: non-energizing HIL runner and evidence schema | Prerequisite for any further physical qualification | Dry-run tests merged |
| Issue #1: verify against real hardware | Stale since v0.1.0 | Close with links to the retained evidence in `results/` |
| Reconcile docs | `known_risks.md` header still says v0.7.0 | Header and open/closed lists match v0.8.0 |

## Phase 2 — Widen coverage on the existing bench (N6775A + N6791A)

| Item | Why | Done when |
|---|---|---|
| Protection trip/clear HIL | Not exercised on hardware at all | Guarded test trips OVP/OCP on the supply and OCP/OPP on the load, then clears; both channels end OFF |
| Remote/local control | `get/set_remote_state`, `remote_lockout` are simulator-gated | Behaviour checked on the N6700C, gate lifted or documented as unsupported |
| `*RST` conformance vector | Opt-in, never run | Vector passes with `N6700_HIL_PROTOCOL_ALLOW_RESET=yes` |
| Soak and fault injection | Needed before any unattended use | Hours-long buck run plus injected transport faults; no stale lease, safe state every time |
| Buck matrix extensions | Only one DUT, one load profile family | CR and CP LIST/ARB into the buck, other step sizes; retained under `results/` |

## Phase 3 — Needs other hardware or equipment

| Item | Needs | Done when |
|---|---|---|
| SMU (N678xA) | An N678x module in the mainframe | Read-only run, LPDS-019 run, and `get/set_smu_mode` write path pass; the 2 skipped vectors pass |
| Source-voltage transient and ARB physical qualification | Independent calibrated DSO ([`VOLTAGE_TRANSIENT_IMPLEMENTATION.md`](VOLTAGE_TRANSIENT_IMPLEMENTATION.md)) | Amplitude, dwell, transition time, overshoot, trigger latency measured, or explicit NO-GO recorded (ARB-R07) |
| Other load/supply models, other mainframes and firmware | The units | Scoped label extended per combination |

## Phase 4 — Release decision

- Complete LPDS-010 Domain J (hardware qualification, performance,
  compatibility).
- Re-run the full acceptance suite on the final release candidate.
- Decide on `stable` (and the Beta classifier). Until Phases 2 and 3 pass,
  keep the current scoped label and `untested`.

## Out of scope for now

- Migrating `base.py` to a shared `lpds-core` (not published yet).
- Full LPDS-008 §11 results-directory tree (harness concern, not driver).
- Unattended production use.
