# Known risks and deliberate scope decisions — v0.1.0

This driver targets **software-verifiable full LPDS conformance**: every
requirement checkable without real hardware or organization-scale tooling is
implemented and tested for real, not stubbed. What real hardware or a larger
team would add is listed here explicitly, per LPDS-001 §32's allowance for a
solo/small-team project to document a gap honestly rather than fake
compliance or silently drop it.

## Real hardware: one read-only run done, most of the surface still untouched

Updated 2026-09-14: `scripts/run_hardware_self_check.py`'s read-only checks
have now actually been run against a real N6700C mainframe (192.168.0.6,
firmware E.02.09.3271, channel 1 a power-supply-family module, channel 2 an
`N6791A`) — 43/43 passed. This is the first real-hardware contact this
driver has had. It immediately found two real things the simulator could
not have:

1. The default communication timeout (5s, the transport's own default) is
   too short for `*TST?` on real hardware (~5.4s here), which faulted the
   connection and cascaded into every later check failing. Fixed: both the
   script's `--timeout-s` and `tests/hardware`'s `N6700_TIMEOUT_S` now
   default to 15s instead of the transport default.
2. `N6791A` (channel 2) answers `VOLT?`/`CURR?`/`MEAS:VOLT?`/`MEAS:CURR?`/
   `OUTP?` correctly but never replies at all to `FUNC:MODE?` — it times
   out and faults the connection rather than erroring. `classify_module()`
   didn't recognize the `N679x` prefix at all before this (fell to
   `unknown`, correctly skipping typed queries rather than guessing); it's
   now classified as `power_supply`, specifically *not* `smu`, so
   `FUNC:MODE?` is never sent to this family. See
   `tests/unit/test_module_capabilities.py`.

Everything else remains simulator-only, and the *guarded output test*
specifically has still never been run against real hardware (it requires a
human to supply and confirm channel/voltage/current-limit, which hasn't
happened yet). Driver status remains `untested` (LPDS-001 §9), not
`stable`. Before calling it `stable`:

- Run the guarded output test for real, on a channel/voltage/current-limit
  someone has actually reviewed against the wiring.
- Repeat the read-only run against a mainframe with a genuine SMU module
  (`N678x`) installed — the one run so far had none, so `get_smu_mode`/SMU
  priority-mode switching remain simulator-only. Same for electronic-load
  modules and protection-trip/clear behavior.
- Verify `get_remote_state`/`set_remote_state`/`remote_lockout`, which are
  currently gated to the simulator transport only (see `docs/troubleshooting.md`)
  because real N6700 remote/local SCPI behavior has not been checked.
- Complete the full LPDS-010 review checklist's Domain J (hardware
  qualification, performance, compatibility), which is entirely
  hardware-dependent and not attempted here.

## No `lpds-core` shared package exists yet

LPDS-003 (`BaseInstrument`) describes a shared package most drivers in this
ecosystem would build on; LPDS-005 §8.4 states a driver "shall" declare a
compatible `lpds-core` version. No such package has been published yet —
only the transport/SCPI-client layer (`scpi-driver-core`, satisfying LPDS-004)
exists. `src/keysight_n6700/base.py` implements the LPDS-003 contract
(9-state machine, session registry, `_execute_operation` wrapper, exception
translation, a reduced diagnostics export) **locally**, composing
`scpi-driver-core`'s `ScpiSession`/`SessionRegistry`/`ScpiClient` underneath.
If `lpds-core` is published later, migrating this driver to depend on it
instead of its local `base.py` is the natural next step — the hook surface
(`_build_transport`, `_validate_identity`, `_on_connected`,
`_apply_safe_state`) was written to make that swap plausible, but it has not
been attempted or proven.

## LPDS-008 evidence/logging is a software-verifiable subset

As of the `scpi-driver-core` integration pass, `connect(..., protocol_trace=...)`
wraps the transport in `InstrumentedTransport` and gives it a real
`Tracer`, so every write/read/open/close at the transport boundary is
recorded with sequence numbers, both clocks, and session/generation context
— to a `JsonlTraceSink` (path), a `RecordingTraceObserver` (in-memory, for
tests), or any custom `TraceObserver`. An optional `Redactor`
(`protocol_trace_redactor=`) can scrub sensitive payloads before they are
recorded. This is real LPDS-008 protocol-level evidence, not a
driver-specific approximation.

What is still not implemented is the full LPDS-008 §11 *results-directory*
tree (`events.jsonl` correlated against `measurements.csv`,
`evidence_manifest.json`, coverage/, attachments/, ...) — that is
infrastructure a *test harness* running against this driver would produce
by consuming the trace above and this driver's return values, not something
the driver itself should hard-code the shape of before a harness exists to
consume it. `export_diagnostics()` (driver/session summary) and
`N6700(audit_log_path=...)`'s higher-level JSONL audit trail remain the
other software-verifiable evidence pieces shipped directly by the driver.

## LPDS-019 conformance: 25 realized vectors, 48 documented exclusions

`tests/conformance/data/protocol_vectors.yaml` covers connection lifecycle,
identity, the error queue, voltage/current setpoints, output control,
measurement, SMU/load mode selection, and one protocol-error path — a
representative slice across every conditional capability group, each a real
transport-boundary assertion. The remaining 48 device-facing methods are in
`exclusions.yaml`, each with a specific, reviewed reason (most: "same
protocol shape as an already-vectored method," a few: "planned for the next
conformance pass"). `test_every_device_facing_method_has_a_vector_or_exclusion`
enforces that this list cannot silently rot — a newly added device-facing
method fails CI until it gets a vector or an exclusion entry.

## LPDS-015 plugin/adapter packaging: one distribution, not two

LPDS-015 §8.2 says an adapter "should be packaged as its own independent
distribution." This repository ships the driver and the Robot Framework
adapter from **one** `pyproject.toml`/distribution
(`keysight-n6700-driver`), with the adapter as an optional extra
(`pip install ".[robotframework]"`) rather than a second PyPI package. This
matches the hub's own top-level `CONTRIBUTING.md` guidance (`adapters/` as a
subfolder of one driver repo) and this ecosystem's solo/small-team scale.
Both `lpds.drivers` and `lpds.adapters` entry points are still declared and
discoverable independently; installing the driver's core dependencies never
pulls in `robotframework`. Splitting the adapter into its own repository and
distribution remains straightforward if a second adapter (pytest, CLI, ...)
is added later and the coupling becomes awkward.

## LPDS-011 release process: no SBOM, no signed release yet

`release/` and a full `make_release.py`-equivalent build/sign/publish
pipeline are not implemented in v0.1.0. `version.py` is the single
authoritative version source; `CHANGELOG.md`/`history/v0.1.0.md` are
maintained by hand. Automating the full LPDS-011 §15 11-step release build
(SBOM, detached checksums, GitHub release automation) is deferred until this
driver has external consumers who need that level of assurance, per
LPDS-005's own explicit "add rigor once you need it" philosophy.

## Review checklist: not run item-by-item across all 8 LPDS-010 domains

Domains A (release identity), B (install/import), D (public API), E
(transport/protocol), F (errors), G (safety/resources), and K (AI
contracts) are substantively covered by what is built and tested here.
Domain C (architecture) is covered by this document and `docs/architecture.md`.
Domain J (hardware qualification) cannot be attempted without hardware (see
above). A full line-by-line pass through LPDS-010's ~150 checklist items has
not been performed and documented as such; treat this file as the honest
summary in its place for v0.1.0.
