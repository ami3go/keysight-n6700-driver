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
   out and faults the connection rather than erroring.

**Update 2026-09-14 (later the same day):** reading the official Keysight
N6705C User's Guide / Programmer's Reference (see `Keysight_documents/`)
resolved finding 2 correctly: `N6791A`/`N6792A` (`N679xA`) are genuine
**Electronic Load Modules** (100W/200W), not power supplies — `FUNC:MODE?`
hung not because it's forbidden for this family specifically, but because
it was never a valid N6700 command for *any* module family. The real
command is plain `FUNCtion` (`FUNC CURRent|VOLTage`, two modes, for the
N678xA SMU; `FUNC CURRent|VOLTage|RESistance|POWer`, four modes, for
N679xA), and the load's input terminals are switched with the ordinary
`OUTP` command (the manual explicitly notes the load's input is called
"Output" throughout). Both `module_capabilities.classify_module()` (now
`electronic_load`, not `power_supply`) and `SMUChannel.set_smu_mode`/
`get_smu_mode` (now `FUNC`, not `FUNC:MODE` — the same bug, just never
noticed for the SMU because no real SMU has been tested yet) were fixed.
See `tests/unit/test_module_capabilities.py` and
`tests/unit/test_electronic_load_channel.py`.

The real N679xA command set (priority mode, level setpoints, input on/off)
is now implemented in `ElectronicLoadChannel`, verified against the
official documentation and, for read-only queries
(`FUNC?`/`VOLT?`/`CURR?`/`OUTP?`), against real hardware.

**Update 2026-09-14 (write path verified):** the write path has now also
been run against real hardware, wired for a real closed-loop test — channel
1 (`N6775A`, power supply) physically connected to channel 2 (`N6791A`
load) by the user, both channels explicitly confirmed by the user
beforehand. Channel 1 set to 12V/1A limit; channel 2 set to current
priority at 1.0A (`FUNC CURR,(@2)` then `CURR 1,(@2)`), both readback-
verified before either channel was enabled. With channel 1 enabled and
channel 2's input still off, channel 1 read ~12V/~0A (open circuit, as
expected). With channel 2's input then turned on (`OUTP ON,(@2)`), channel
1 measured 11.98V/0.9996A and channel 2 measured 11.98V/0.9998A — the load
sank the commanded 1A, matching the supply's delivered current. No SCPI
errors at any point; both channels confirmed OFF after shutdown. This
confirms the entire real N679xA command path this release added: `FUNC`
(priority mode), `CURR` (level), and `OUTP` (input on/off).

**Update 2026-09-14 (100-point CV/CC/CR/load-CV sweep):** a much more
thorough real-hardware run followed the single-point write-path check
above, still with channel 1 (`N6775A`) wired into channel 2 (`N6791A`)'s
load input, capped by a hard 20V/2A safety envelope (independent of any
per-phase expectation — an immediate abort-and-shutdown trigger, never
reached). 100 points across 4 phases, 25 each:

1. **PS CV region** — load held at a fixed 0.2A while the supply's voltage
   setpoint was swept 2V→20V. Supply voltage tracked its setpoint to within
   millivolts at every point; load current stayed at 0.2A throughout.
2. **PS CC crossover** — supply fixed at 20V/1.0A limit, load's current
   setpoint swept 0.2A→1.8A. Below 1.0A: bus held at 20V, current tracked
   the load's setpoint exactly (CV region). At/above 1.0A: supply current
   pinned at 1.000–1.002A and its voltage collapsed to ~0.07V — a sharp,
   correct CC crossover exactly at the programmed limit.
3. **Load CR mode** — fixed 20Ω, supply voltage swept 2V→20V. Measured load
   current matched V/R (Ohm's law) to within ~0.3% at every point.
4. **Load CV (voltage-priority) mode** — supply fixed at 15V/1.0A limit,
   load's own `CURR:LIM` set to 1.0A, load's voltage target swept
   20V→2V. Load stayed inactive (~0A) for every target at or above the bus
   voltage, and clamped at its 1.0A `CURR:LIM` the instant the target
   dropped below it — since the load's `CURR:LIM` and the supply's own
   current limit were both 1.0A, the two loops coupled and the bus voltage
   tracked the falling target rather than settling on a plateau (correct,
   expected behavior given equal limits on both sides, not a bug).

All 100 points passed with zero envelope violations and zero SCPI errors;
both channels confirmed OFF after every phase's guaranteed shutdown. This
is the strongest real-hardware evidence so far for the entire N679xA write
path plus the supply's CV/CC crossover behavior. Implemented as
`tests/hardware/test_hardware_load_sweep.py`
(`test_ps_to_load_cv_cc_cr_sweep`), gated behind its own explicit signals —
see [`docs/hardware_acceptance_tests.md`](../docs/hardware_acceptance_tests.md).

Everything else remains simulator-only, and the *guarded output test* in
`tests/hardware/test_hardware_acceptance.py` (channel 1 alone, no load
attached) specifically has still never been run against real hardware.
Driver status remains `untested` (LPDS-001 §9), not `stable`. Before
calling it `stable`:

- Run the standalone guarded output test (`test_guarded_output_enable_
  measure_disable`) for real, on a channel/voltage/current-limit someone
  has actually reviewed against the wiring — the write-path and sweep runs
  above used separate tests, not this one.
- Repeat the read-only run against a mainframe with a genuine SMU module
  (`N678x`) installed — the one run so far had none, so `get_smu_mode`/SMU
  priority-mode switching remain simulator-only for the write path.
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
