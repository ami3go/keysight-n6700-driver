# Changelog

All notable changes to this project are documented here. See
[`history/`](history/) for the detailed per-release record.

## v0.7.3 — 2026-10-15

Documentation-only: added Stack Overflow and NI Community N6700/N6705
discussions and Keysight engineering articles to
[ARB_WAVEFORM_REFERENCE.md](docs/ARB_WAVEFORM_REFERENCE.md), with
source-specific uncertainty notes, seven recurring engineering patterns,
safety implications, and proposed simulator/HIL tests. Flagged the
N6705C `TRIG:ARB:SOUR` versus existing `TRIG:TRAN:SOUR` command-path
difference for model-specific verification. No runtime changes or
physical hardware qualification.

## v0.7.2 — 2026-10-15

Documentation-only reference update: reviewed public N670x Arb Python source examples (PULSE, UDEF, CDW, sequencing and triggering), relevant SCPI transport projects and official Keysight documentation. Added pinned links, applicability qualifiers and explicit safety warnings to [ARB_WAVEFORM_REFERENCE.md](docs/ARB_WAVEFORM_REFERENCE.md). No verified GitLab Arb implementation found. No driver runtime or hardware qualification changes.

## v0.7.1 — 2026-10-15

Documentation-only: added [N6700 Arb waveform and SCPI function reference](docs/ARB_WAVEFORM_REFERENCE.md), covering supported shapes, type and model/Option 054 restrictions, step and sequence command inventory, distinct point/repeat limits, existing driver coverage, safety requirements and HIL gates. Linked it from the docs index and README. No driver functionality changed or was hardware-qualified.

## v0.7.0 — 2026-10-14

Added bounded source-voltage transient generation for power-supply/SMU
channels: voltage slew, finite LIST and user-defined ARB profiles, BUS or
external trigger setup, generation-token arm/ready/trigger/complete/abort
lifecycle, readback validation, timeout cleanup, simulator support, Robot
Framework keywords, and a guarded N6775A HIL test. Analog timing and waveform
quality remain pending independent oscilloscope qualification; no hardware
was energized for this release.

## v0.6.13 — 2026-10-12

Fixed a real-hardware hang found while running a live DUT load-transient
scope capture against a mainframe with a non-digitizing power-supply channel
(N6775A) alongside a digitizing load channel (N6791A). `configure_digitize`'s
shared-512K-point-budget check queried `SENS:SWE:POIN?` on every channel
reporting an enabled `SENS:FUNC:VOLT?/CURR?` flag; a plain power-supply
channel answers that flag but never replies at all to `SENS:SWE:POIN?` --
no SCPI error, a silent socket-level hang, the same failure mode already
known for `FUNC:MODE?` on N679xA (see `review/known_risks.md`). The budget
loop now only queries channels discovery has classified as digitizing-
capable. Adds `scripts/run_load_transient_test.py`, a guarded real-hardware
harness that energizes a source channel and a load channel together, steps
the load current through a trigger level, and captures the resulting
voltage transient via the load's digitizer -- used to find and confirm this
fix on a real N6700C (channel 1 N6775A, channel 2 N6791A, external DUT power
supply between them). Captured edge landed at sample 511 of an expected 512.

## v0.6.12 — 2026-10-10

Automated operator-gated, real-hardware N6791A/N6792A scope protocol HIL suite. Adds case matrix runner and CLI with exact identity checks, separate real Ethernet/VISA connection, BUS voltage/current acquisitions, pretrigger, ASCII/REAL32, abort and missing-trigger recovery, verified load-OFF safe states, per-waveform SHA256, incremental summary.json/JUnit artifacts, bounded runs, and simulator-only orchestration tests. Hardware runs remain explicitly operator-authorized; NO physical DSO/metrology acceptance is claimed.

## v0.6.11 — 2026-10-10

Final scope software audit: version and capability consistency, human-reviewed qualification evidence checks, and repeated simulated capture regression. The driver is not yet physically qualified for unattended operation; reference DSO testing and extended hardware soak remain required.

## v0.6.10 — 2026-10-10

PR08 reproducible scope qualification tooling (F12/F13 implementation, HIL execution still pending): add guarded passive BUS-trigger capture CLI, signed-off profile requirement, structured CSV/JSON evidence with SHA256/IDN/OFF verification; add independent DSO comparison with shared physical time origin, k=2 sampling uncertainty, resolution-floor NOT_RESOLVED and PASS/FAIL/INCONCLUSIVE classifications, tests and qualification procedure. **No physical N6791A/N6792A or DSO connected or qualified by this release.**

## v0.6.9 — 2026-10-10

PR07 F11: bounded IEEE-488.2 REAL32 digitizer transfer through scpi-driver-core, explicit FORM:BORD SWAP little endian and restore, exact finite float32 validation, SHA-256 evidence hash and optional auto/ASCII/REAL32 transfers (automatic REAL32 for captures of >=4096 samples). Bound transport to requested 4*N bytes and redact large plain-text waveform responses from control audit; pinned core protocol tracing already caps individual event payloads at 4096 bytes. New negative tests and version metadata. Real hardware performance/trace behavior remains to be qualified.

## v0.6.8 — 2026-10-10

PR06 capability and model truthfulness (F09/F10/F14): recognize only N6791A and N6792A as electronic loads with declared digitizing and array support; distinguish vendor-declared capability from hardware qualification. Register all eight acquisition methods with accurate mutation/risk and precondition metadata. Runtime driver metadata uses canonical package version. Generated capability/AI contract/conformance artifacts updated, CI tests added.

## v0.6.7 — 2026-10-10

PR05 digitizer integrity (F06–F08): explicit ASCII array transport negotiation/restoration, readbacks of enabled voltage/current functions and global 512K sample allocation, detection of rejected SCPI configuration, and strict exactly-N finite waveform parsing. Disable unrequested measurement function; reject wrong-function fetch. Adds fault-injection/unit tests. Binary transfer and large-array efficiency are tracked for PR07; no real-hardware claims.

## v0.6.6 — 2026-10-10

PR04 acquisition transaction protection (F05): alias-scoped lease tracks acquisition owner from configuration through verified fetch/abort; raw SCPI rejects MEAS/RESET and unknown buffer-risking calls while acquisition is owned. Conflicting threads and channel rediscovery/disconnect are blocked, emergency OUTP OFF remains permitted. Adds negative concurrency/alias tests. **Does not protect against a separate external SCPI controller or guarantee atomic multiprocess exclusion.**

## v0.6.5 — 2026-10-10

Documentation-only update: added a Keysight-sourced scope/digitizer availability
table to the README, highlighting that N6705C includes digitizing without Option
054, documenting N6700C/N6775A and N676xA/N678xA differences, and
distinguishing the real N6700C/N6791A bench result from the vendor's published
N6705C-only load compatibility statement. Clarified that the driver currently
exposes digitizing only for electronic-load channels. No runtime changes.

## v0.6.4 — 2026-10-10

PR03 of N679xA scope remediation (F01/F03): verifies effective sweep interval, sample count and offset with SCPI readbacks; waveform interval metadata now reflects instrument-quantized timing. `wait_digitize_armed` waits for WTG_meas **plus** negative-offset pretrigger fill and a configurable guard within one readiness timeout, with cooperative cancellation, status reconfirmation and PR02 abort/recovery on error. Simulated rounding and deterministic fake-clock tests cover relevant conditions. Real-hardware qualification remains pending; no additional source/load output controls were changed.

## v0.6.3 — 2026-10-10

PR02 of the N679xA scope remediation (F02): readiness/completion polling failure or timeout now initiates best-effort abort and status-verified idle recovery; unacknowledged recovery leaves acquisition UNKNOWN. Preserve original errors with cleanup metadata; reject invalid/indefinite polling timeouts. Added simulator fault tests. No hardware qualification included.

## v0.6.2 — 2026-10-10

Implemented first stage of the N679xA scope remediation plan (PR01, F04): driver-observed acquisition lifecycle and generation tracking, safer precondition checks for arm/trigger/fetch, simulator waiting/completed/aborted transitions, and deterministic stale-buffer tests. Missed fast transitions, concurrent controllers, timeouts, error recovery, SCPI readbacks, and waveform integrity remain tracked for PR02–PR09. No live bench testing in this release.

## v0.6.1 — 2026-10-10

Planning-only documentation release. Added the detailed [N679xA electronic-load scope remediation plan](docs/N679XA_SCOPE_REMEDIATION_PLAN.md), with all 14 findings, code sketches, acceptance criteria, hardware qualification and ordered review/PR gates. **No scope runtime defect is fixed by this release**, and production qualification remains NO-GO. Synced package/manifest/AI-contract version to 0.6.1; the pre-existing runtime metadata version mismatch remains tracked as F14 for the implementation sequence. See [history/v0.6.1.md](history/v0.6.1.md).

## v0.6.0 — 2026-10-09

Added digitized/buffered measurement (`SENS:SWEep` + `TRIGger:ACQuire` +
`FETC:ARRay`) to `ElectronicLoadChannel`, for the N679xA family: the
Keysight N6705C manual's "Load Model Differences" table marks "SCPI command
array readback" and "SCPI command programmable sample rate" available for
N6791A/N6792A over the remote interface (which is exactly how N6700 talks
to it), at a minimum 10.24 µs/sample for 1-2 parameters, up to 512K points,
with pre/post-trigger offset and a voltage/current level trigger.

- Added: `configure_digitize`, `configure_digitize_trigger`, `arm_digitize`,
  `wait_digitize_armed`, `trigger_digitize`, `wait_digitize_complete`,
  `abort_digitize`, `fetch_digitized_array` on `ElectronicLoadChannel`, with
  thin `N6700` convenience wrappers of the same names.
- Added: `ChannelCapabilities.supports_digitizing` /
  `digitize_min_interval_s`, set for the `N679xA` load family only — the
  manual says the SMU/power-supply families can do this too, but they are
  not classified for it yet (see `review/known_risks.md`). This is the
  first bool+numeric-limit pairing in `ChannelCapabilities`.
- Completed `types.ArrayMeasurement`'s evident original intent (defined,
  exported, never used before this release) by adding `interval_s` and
  trigger-relative `offset_points`, rather than introducing a new type.
- Like `set_load_current_limit`, this is real-hardware-only: `SIM_LOAD`
  raises `DriverUnsupportedOperationError` rather than gaining a
  `SIM:LOAD:*` shadow branch, since there is no meaningful simulated-
  waveform twin. Tested by pointing a channel built with
  `classify_module("N6791A")` directly at the simulator's shared command
  surface, the same way `test_electronic_load_channel.py` already covers
  the other real-load commands.
- Run against the real N6700C at 192.168.0.6 (channel 2, N6791A): the
  software-triggered 256-point smoke test passed, followed by a six-case
  current-level-triggered DUT matrix at 8/13.5/18 V and 0.5↔2.5 A. All
  edges landed at sample 511–512 of 1024, worst VOUT deviation was
  3.357%, and both channels were confirmed off with an empty error queue.
  Raw evidence is retained under
  `results/load_transient/20261009_n6791a_internal_buffer/`.
- Documented two real-firmware sequencing requirements: select CC mode
  before arming (firmware rejects `FUNC CURR` while acquisition is active),
  and fetch the array before any `MEASure` query overwrites the buffer.

## v0.5.0 — 2026-09-14

LPDS-001 §9 defines `stable` status as "LPDS-019 conformance passes against
real hardware" — the simulator-based suite couldn't itself prove that,
since it asserts exact outbound SCPI text via a simulator-only transport
feature. Added `tests/hardware/test_hardware_protocol_conformance.py`,
which realizes the same `protocol_vectors.yaml` vectors against real
hardware using LPDS-008 protocol tracing as the source of outbound history
instead. Run against a real N6700C: 18/24 vectors passed (19 including
`enable-output`, gated behind its own confirmation); the rest are
permanently simulator-only, need a real SMU module, or (`*RST`) are
deliberately opt-in and weren't exercised. Surfaced two vector-specific,
non-driver findings along the way: the real manufacturer string's casing
differs from the simulator's, and `channel_count()`'s expected value is the
simulator's fixed fixture topology, not a real property. Extracted
`tests/conformance/checks.py` so the simulator and real-hardware suites
share the same pass/fail logic. No driver source changed.

## v0.4.0 — 2026-09-14

Reading the official Keysight N6705C User's Guide / Programmer's Reference
(user-provided, `Keysight_documents/`) corrected v0.3.1's real-hardware
finding about `N6791A`:

- Fixed: `N6791A`/`N6792A` (`N679xA`) are genuine Electronic Load Modules
  (100W/200W) with four priority modes (voltage/current/resistance/power),
  not power supplies as v0.3.1 concluded from empirical probing alone.
  `module_capabilities.classify_module()` now returns `electronic_load` for
  this family.
- Fixed: the real priority-mode command is plain `FUNCtion` (`FUNC
  CURRent|VOLTage|RESistance|POWer`), never `FUNC:MODE` — the earlier
  transport hang was because `FUNC:MODE` isn't a valid command for *any*
  module family, not because it was specifically forbidden for loads. Fixed
  in both `ElectronicLoadChannel` and `SMUChannel` (the SMU had the same
  bug, undetected because no real SMU has been hardware-tested yet).
- Added: `ElectronicLoadChannel` now implements the real N679xA command set
  — priority mode, per-mode level setpoints, `OUTP`-based input on/off (the
  manual confirms the load's input is called "Output" throughout), and a
  current limit for non-current-priority modes — plus `configure_cv`/
  `configure_cr`/`configure_cp` convenience methods.
- Known limitation still open: the electronic-load *write* path and the
  guarded output test have not been run against real hardware; only
  read-only load queries have real-hardware evidence so far. See
  [`review/known_risks.md`](review/known_risks.md).

## v0.3.1 — 2026-09-14

First real-hardware run: `scripts/run_hardware_self_check.py`'s read-only
checks passed 43/43 against a real N6700C mainframe. Two real findings
from that run, both fixed:

- Fixed: the script's/`tests/hardware`'s default communication timeout was
  5s (the transport's own default), too short for `*TST?` on real hardware
  (~5.4s here). Both now default to 15s (`--timeout-s`/`N6700_TIMEOUT_S`).
- Fixed: `module_capabilities.classify_module()` didn't recognize the
  `N679x` module family. Confirmed against real hardware that `N6791A`
  answers `VOLT?`/`CURR?`/`MEAS:VOLT?`/`MEAS:CURR?`/`OUTP?` correctly but
  never replies to `FUNC:MODE?` (times out and faults the connection
  instead of erroring) — classified as `power_supply`, not `smu`, so that
  query is never sent to this family.
- Known limitation still open: the guarded output test has not been run
  against real hardware yet, and no run so far has included a genuine SMU
  or electronic-load module. See [`review/known_risks.md`](review/known_risks.md).

## v0.3.0 — 2026-09-14

- Added: `scripts/run_hardware_self_check.py` — a standalone script that
  exercises the driver's full read-only API against a real N6700 and
  writes a JSON/Markdown report, plus one opt-in guarded output test
  (enable/measure/disable one channel) requiring four explicit,
  no-default parameters and a typed confirmation.
- Added: `tests/hardware/` — the same coverage as individually-reported
  pytest tests, gated behind `N6700_HIL_ENABLED`/`N6700_RESOURCE` (and five
  separate explicit signals for the guarded output test). Skipped by
  default; never falls back to the simulator.
- Added: `docs/hardware_acceptance_tests.md` and
  `tests/unit/test_hardware_self_check_script.py` (verifies the script's
  logic against the simulator, since no real hardware was available to
  test it against directly).
- Known limitations: neither tool has actually been run against real
  hardware yet — see [`review/known_risks.md`](review/known_risks.md).

## v0.2.0 — 2026-09-13

- Added: real LPDS-008 protocol tracing via `connect(protocol_trace=...)`,
  built on `scpi_driver_core.tracing` (`InstrumentedTransport`, `Tracer`,
  `JsonlTraceSink`/`RecordingTraceObserver`, optional `Redactor`).
- Added: `wait_for_voltage_in_range`/`wait_for_current_in_range`, bounded
  polling built on `scpi_driver_core.execution.polling.poll_until`.
- Added: optional retry for idempotent raw queries —
  `query_scpi(..., retry_attempts=, retry_delay_s=)`, built on
  `scpi_driver_core.execution.retry`.
- Added: `set_raw_scpi_guard`/`enable_raw_scpi`/`disable_raw_scpi`, an opt-in
  confirmation guard (`scpi_driver_core.execution.guards.ConfirmationGuard`)
  in front of `write_scpi`/`query_scpi`. Off by default; existing raw-SCPI
  behavior is unchanged unless a guard is explicitly set.
- Changed: the Robot Framework adapter's `Wait Until N6700 Voltage Is In
  Range` keyword now delegates to the driver's `wait_for_voltage_in_range`
  instead of its own polling loop; added `Set/Enable/Disable N6700 Raw SCPI
  Guard` keywords.
- All additions are backward compatible; no existing method's default
  behavior changed.

## v0.1.0 — 2026-09-13

- Added: Python-first `keysight_n6700` driver with an optional Robot
  Framework adapter, per the Lab pyDrivers Standard.
- Added: LPDS-013 capability model, LPDS-014 configuration model, LPDS-017 AI
  contract, LPDS-019 call/protocol conformance suite.
- Added: multi-session support (`alias=`) in one driver instance.
- Changed: rebuilt on `scpi-driver-core` for transport/SCPI-client plumbing;
  replaced the previous ad hoc exception hierarchy with LPDS-007's
  `DriverError` hierarchy.
- Packaging: moved to `src/` layout; Robot Framework library moved to
  `adapters/robotframework/`.
- Known limitations: no real-hardware run yet; see
  [`review/known_risks.md`](review/known_risks.md).

See [`history/v0.1.0.md`](history/v0.1.0.md) for the full record.
