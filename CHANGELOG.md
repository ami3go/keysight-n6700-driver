# Changelog

All notable changes to this project are documented here. See
[`history/`](history/) for the detailed per-release record.

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
