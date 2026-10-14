# Hardware acceptance testing

Everything else in this repository runs against the bundled simulator. These
are the only things that talk to a real N6700. None of them run unless you
explicitly enable it — there is no default resource and no silent fallback
to the simulator.

## Before you connect anything

Read [`docs/safety.md`](safety.md) and
[`guide/hardware_connection.md`](../guide/hardware_connection.md) first.
Verify wiring, polarity, and DUT/fixture ratings; know what is physically
connected to every channel before running the guarded output test.

## 1. `scripts/run_hardware_self_check.py` — the standalone report

Exercises the driver's entire read-only API (identity, module discovery,
per-channel model/serial/options/measurement/status/setpoint-readback, the
error queue, self-test, capability/configuration introspection) and writes a
JSON + Markdown report to `results/hardware_self_check/<timestamp>/`.

```bash
python scripts/run_hardware_self_check.py --resource "USB0::0x0957::0x0907::MY43014421::INSTR"
python scripts/run_hardware_self_check.py --resource 192.168.1.50 --connection-type ethernet
```

`--timeout-s` defaults to 15s, not the transport's own 5s default: on a real
N6700C, `*TST?` alone took ~5.4s, which faulted the connection under the
transport default and failed every check after it. Raise `--timeout-s`
further if your instrument's self-test takes longer.

Add the guarded output test with `--output-test` plus all three of
`--output-channel`, `--output-voltage`, and `--output-current-limit` — no
defaults exist for these, so you must type the exact values you intend.
It sets the setpoint with the output off, prints exactly what it is about to
do, waits for a typed `yes` (or `--yes` if you have already reviewed the
command), enables the output, waits for the voltage to settle
(`wait_for_voltage_in_range`), measures it, and disables the output again in
a `finally` block regardless of outcome — then calls `safe_shutdown()` as a
second net. It refuses to run if the channel isn't a discovered
power-supply/SMU channel, or if that channel's output is already on.

```bash
python scripts/run_hardware_self_check.py --resource 192.168.1.50 --connection-type ethernet \
  --output-test --output-channel 1 --output-voltage 5.0 --output-current-limit 0.5
```

Run `python scripts/run_hardware_self_check.py --help` for every option.

## 2. `pytest -m hardware` — per-check pass/fail in your normal test runner

Same coverage, individually reported per pytest test rather than one script
run, for iterative hardware debugging. Gated by environment variables so no
IDE/CI "run all tests" button can accidentally reach real hardware:

| Variable | Required for | Meaning |
|---|---|---|
| `N6700_HIL_ENABLED` | any hardware test | must be `true` |
| `N6700_RESOURCE` | any hardware test | VISA resource string, or host/IP for `ethernet` |
| `N6700_CONNECTION_TYPE` | optional | `visa` (default), `usb`, `ethernet`, `socket` |
| `N6700_PORT` | ethernet/socket only | default `5025` |
| `N6700_TIMEOUT_S` | optional | default `15.0`; see the `--timeout-s` note above |
| `N6700_HIL_OUTPUT_TEST_ENABLED` | guarded output test | must be `true` |
| `N6700_HIL_OUTPUT_CHANNEL` | guarded output test | channel number, no default |
| `N6700_HIL_OUTPUT_VOLTAGE` | guarded output test | volts, no default |
| `N6700_HIL_OUTPUT_CURRENT_LIMIT` | guarded output test | amps, no default |
| `N6700_HIL_OUTPUT_CONFIRM` | guarded output test | must be `yes` — a fifth, separate signal confirming you reviewed the four above |

```bash
N6700_HIL_ENABLED=true N6700_RESOURCE=192.168.1.50 N6700_CONNECTION_TYPE=ethernet \
  pytest tests/hardware -v

# + the guarded output test
N6700_HIL_ENABLED=true N6700_RESOURCE=192.168.1.50 N6700_CONNECTION_TYPE=ethernet \
N6700_HIL_OUTPUT_TEST_ENABLED=true N6700_HIL_OUTPUT_CHANNEL=1 \
N6700_HIL_OUTPUT_VOLTAGE=5.0 N6700_HIL_OUTPUT_CURRENT_LIMIT=0.5 N6700_HIL_OUTPUT_CONFIRM=yes \
  pytest tests/hardware -v
```

Without `N6700_HIL_ENABLED`/`N6700_RESOURCE`, every test under
`tests/hardware/` skips with a message explaining why (`tests/hardware/
conftest.py`) — `pytest` with no arguments never touches hardware.

## 3. `test_hardware_load_sweep.py` — PS-to-load 100-point sweep

A power-supply/SMU channel physically wired into an electronic-load
channel's input, both energized together across 100 points (4 phases × 25):
the supply's CV region, its CC (current-limiting) crossover, the load's CR
mode, and the load's CV (voltage-priority) mode. Unlike the guarded output
test, this does not take caller-supplied voltage/current — every setpoint
(2V–20V, 0.2A–2A) is fixed and already reviewed, because the safe envelope
depends on both channels' ratings interacting, not just one. A hard 20V/2A
safety cap, independent of any phase's expectation, aborts and shuts down
immediately if ever approached.

| Variable | Meaning |
|---|---|
| `N6700_HIL_SWEEP_TEST_ENABLED` | must be `true` |
| `N6700_HIL_SWEEP_PS_CHANNEL` | the power-supply/SMU channel number, no default |
| `N6700_HIL_SWEEP_LOAD_CHANNEL` | the electronic-load channel number, no default |
| `N6700_HIL_SWEEP_CONFIRM` | must be `yes` — attests the PS channel's output is physically wired to the load channel's input, correct polarity, both otherwise disconnected |

```bash
N6700_HIL_ENABLED=true N6700_RESOURCE=192.168.1.50 N6700_CONNECTION_TYPE=ethernet \
N6700_HIL_SWEEP_TEST_ENABLED=true N6700_HIL_SWEEP_PS_CHANNEL=1 N6700_HIL_SWEEP_LOAD_CHANNEL=2 \
N6700_HIL_SWEEP_CONFIRM=yes \
  pytest tests/hardware/test_hardware_load_sweep.py -v
```

Refuses to run if either channel isn't the discovered type it expects, or if
either is already energized. Writes every point's requested and measured
values to `results/hardware_load_sweep/<timestamp>.json`.

## 4. `test_hardware_protocol_conformance.py` — LPDS-019 vectors, for real

LPDS-001 §9 defines `stable` status as "LPDS-019 conformance passes against
real hardware." The simulator-based suite
(`tests/conformance/test_driver_call_protocol_conformance.py`) can't be
pointed at real hardware directly — it asserts exact outbound SCPI text via
`ScriptedScpiTransport.history`, a simulator-only feature. This module
realizes the *same* `tests/conformance/data/protocol_vectors.yaml` vectors
against real hardware instead, using `connect(protocol_trace=
RecordingTraceObserver())` (LPDS-008 tracing) to capture the real outbound
bytes at the transport boundary.

| Variable | Meaning |
|---|---|
| `N6700_HIL_PROTOCOL_TEST_ENABLED` | must be `true` |
| `N6700_HIL_PROTOCOL_ALLOW_RESET` | must be `yes` to include the `reset-device` vector (`*RST`, resets every channel) |
| `N6700_HIL_PROTOCOL_OUTPUT_CONFIRM` | must be `yes` to include the `enable-output` vector (energizes a real output) |

```bash
N6700_HIL_ENABLED=true N6700_RESOURCE=192.168.1.50 N6700_CONNECTION_TYPE=ethernet \
N6700_HIL_PROTOCOL_TEST_ENABLED=true \
  pytest tests/hardware/test_hardware_protocol_conformance.py -v
```

Vectors that don't apply to real hardware are skipped with a clear reason,
not silently dropped: `connect-simulated` and `set-load-mode` (simulator-
only syntax) always skip; `set-smu-mode`/`get-smu-mode` skip if this
mainframe has no real SMU module installed. Every other vector's channel
number is remapped from the simulator's default map onto whatever channel
this mainframe actually has that module type on.

## 5. `test_hardware_load_digitize.py` — digitized/buffered measurement smoke test

A software round-trip check for the v0.6.0 digitizing capability
(`configure_digitize`/`arm_digitize`/`fetch_digitized_array`, etc.): uses a
software (`BUS`) trigger, not a level trigger on a real DUT event, so it
needs no wiring beyond the load module itself. It only proves the
instrument accepts the configuration, arms, triggers, and returns a buffer
of the requested length — not that a real transient was captured
correctly. See `review/known_risks.md`.

| Variable | Meaning |
|---|---|
| `N6700_HIL_DIGITIZE_ENABLED` | must be `true` |
| `N6700_HIL_DIGITIZE_CHANNEL` | the electronic-load channel number, no default |
| `N6700_HIL_DIGITIZE_CONFIRM` | must be `yes` — attests the channel is a genuine, already-reviewed N679xA module |

```bash
N6700_HIL_ENABLED=true N6700_RESOURCE=192.168.1.50 N6700_CONNECTION_TYPE=ethernet \
N6700_HIL_DIGITIZE_ENABLED=true N6700_HIL_DIGITIZE_CHANNEL=2 N6700_HIL_DIGITIZE_CONFIRM=yes \
  pytest tests/hardware/test_hardware_load_digitize.py -v
```

## 6. `test_hardware_voltage_transient.py` — guarded N6775A voltage dip

This test programs and triggers one real finite voltage LIST on an N6775A.
It is a command/lifecycle HIL check, not analog waveform qualification. It
requires the selected output to start OFF, rejects any other module model,
and always aborts the transient and disables the output during teardown.

| Variable | Meaning |
|---|---|
| `N6700_HIL_VOLTAGE_TRANSIENT_ENABLED` | must be `true` |
| `N6700_HIL_VOLTAGE_TRANSIENT_CHANNEL` | reviewed N6775A channel, no default |
| `N6700_HIL_VOLTAGE_TRANSIENT_BASELINE_V` | normal source voltage, no default |
| `N6700_HIL_VOLTAGE_TRANSIENT_DIP_V` | dip voltage below baseline, no default |
| `N6700_HIL_VOLTAGE_TRANSIENT_DWELL_S` | dip dwell, limited by the test to 1 s |
| `N6700_HIL_VOLTAGE_TRANSIENT_CURRENT_LIMIT_A` | reviewed current limit, no default |
| `N6700_HIL_VOLTAGE_TRANSIENT_MAXIMUM_V` | approved DUT/fixture envelope, no default |
| `N6700_HIL_VOLTAGE_TRANSIENT_CONFIRM` | exact phrase `GENERATE_REVIEWED_DIP` |

```bash
N6700_HIL_ENABLED=true N6700_RESOURCE=192.168.1.50 N6700_CONNECTION_TYPE=ethernet \
N6700_HIL_VOLTAGE_TRANSIENT_ENABLED=true N6700_HIL_VOLTAGE_TRANSIENT_CHANNEL=1 \
N6700_HIL_VOLTAGE_TRANSIENT_BASELINE_V=12 N6700_HIL_VOLTAGE_TRANSIENT_DIP_V=6 \
N6700_HIL_VOLTAGE_TRANSIENT_DWELL_S=0.02 \
N6700_HIL_VOLTAGE_TRANSIENT_CURRENT_LIMIT_A=1 \
N6700_HIL_VOLTAGE_TRANSIENT_MAXIMUM_V=18 \
N6700_HIL_VOLTAGE_TRANSIENT_CONFIRM=GENERATE_REVIEWED_DIP \
  pytest tests/hardware/test_hardware_voltage_transient.py -v
```

Passing proves profile readback, WTG readiness, channel-scoped trigger,
completion, empty error queue, inactive protection, and final output OFF on
that instrument. The independent DSO matrix and release gates are specified
in [`VOLTAGE_TRANSIENT_IMPLEMENTATION.md`](VOLTAGE_TRANSIENT_IMPLEMENTATION.md).

### Retained real load-transient evidence

On 2026-10-09 the updated driver was also used as the VOUT waveform source
for the complete downstream load-transient matrix: 8 V, 13.5 V, and 18 V;
both 0.5 A → 2.5 A and 2.5 A → 0.5 A. The N6791A triggered on its own
measured current crossing 1.5 A and captured 1024 voltage samples at
20.48 µs/sample with 512 pre-trigger points.

All six edges landed at sample 511 or 512. Worst measured VOUT deviation
was 3.357% (110.55 mV), recovery into the downstream test's 2% band was
20.48–40.96 µs, no source/load protection became active, and final
verification found both channels off with an empty SCPI error queue. The
summary, exact harness, and all raw CSV waveforms are retained in
`results/load_transient/20261009_n6791a_internal_buffer/`.

The run exposed two mandatory sequencing rules for consumers:

1. Select CC mode before arming. Re-sending `FUNC CURR` while acquisition
   is armed is rejected by real firmware with SCPI error 315.
2. Call `FETC:ARR:*?` immediately after acquisition completion. Any later
   `MEASure` query starts a new acquisition and overwrites the triggered
   buffer.

## What none of these prove

Passing all six is real evidence the driver's read-only calls, one basic
enable/measure/disable cycle, the power-supply/electronic-load interaction,
(for the vectors this mainframe's topology supports) LPDS-019 conformance
itself, a digitize configuration/arm/trigger/fetch round trip, and one
guarded source-voltage transient lifecycle all work
against your specific instrument and wiring. It does not cover SMU
priority-mode switching on real SMU hardware (no SMU vector can pass
without one installed), protection-trip/clear behavior, remote/local
control, or every possible trigger/input topology. A current-level-triggered
DUT transient is covered by the retained matrix above, while the digitize
smoke test itself uses a software trigger. See
[`review/known_risks.md`](../review/known_risks.md).

## What it already found

The read-only script has been run against one real N6700C (2 channels:
N6775A + N6791A) and, after the timeout fix above, passed all 43 checks. It
also found a real classification gap the simulator could never have
surfaced: `N6791A` answers `VOLT?`/`CURR?`/`MEAS:VOLT?`/`MEAS:CURR?`/
`OUTP?` correctly but never replies at all to `FUNC:MODE?` — it times out
and faults the connection rather than returning a SCPI error.

Reading the official Keysight N6705C documentation (see
`Keysight_documents/`) explained why: `N6791A`/`N6792A` are genuine
**Electronic Load Modules**, not power supplies, and `FUNC:MODE` was never
a valid command for *any* module family on this instrument — the real
command is plain `FUNCtion`. `module_capabilities.classify_module()` now
classifies `N679x` as `electronic_load` (four priority modes: voltage,
current, resistance, power), and `ElectronicLoadChannel`/`SMUChannel` both
send `FUNC`/`FUNC?`, not `FUNC:MODE`. The load's input is switched with the
ordinary `OUTP` command — the manual explicitly says the load's input
terminals are referred to as "Output" throughout. See
`tests/unit/test_module_capabilities.py` and
`tests/unit/test_electronic_load_channel.py`.

**The write path has since been confirmed too.** Channel 1 (`N6775A`) was
physically wired to channel 2 (`N6791A`)'s load input and both channels
were energized together: channel 1 set to 12V/1A limit, channel 2 set to
current priority at 1.0A (`FUNC CURR,(@2)`, `CURR 1,(@2)`), then both
enabled (`OUTP ON,(@1)`, `OUTP ON,(@2)`). Result: channel 1 measured
11.98V/0.9996A, channel 2 measured 11.98V/0.9998A — the load sank the
commanded 1A, matching the supply's delivered current, with no SCPI errors
and both channels confirmed off after shutdown.

**Then a 100-point sweep across all four modes**, using the same wiring
(`test_hardware_load_sweep.py`, section 3 above): the supply's voltage
setpoint tracked to within millivolts across its full CV region; at the
programmed 1.0A current limit, the supply crossed sharply into CC mode
(current pinned at ~1.00A, voltage collapsing to ~0.07V); the load's CR
mode matched Ohm's law (I ≈ V/R) to within ~0.3% at every point; and the
load's CV (voltage-priority) mode stayed inactive whenever its target was
above the bus voltage and clamped correctly at its own `CURR:LIM` once
active. Zero envelope violations, zero SCPI errors, across all 100 points.
See [`review/known_risks.md`](../review/known_risks.md) for the full
per-phase breakdown.
