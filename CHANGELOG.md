# Changelog

All notable changes to this project are documented here. See
[`history/`](history/) for the detailed per-release record.

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
