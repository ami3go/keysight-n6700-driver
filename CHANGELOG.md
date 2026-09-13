# Changelog

All notable changes to this project are documented here. See
[`history/`](history/) for the detailed per-release record.

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
