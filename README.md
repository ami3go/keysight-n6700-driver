# keysight-n6700-driver

Framework-independent Python driver for Keysight/Agilent N6700-family modular
power systems, with an optional Robot Framework adapter.

- **Supported hardware**: N6700-series mainframes (N6700B, N6701A, N6702A,
  N6705B/C) with power-supply (N673x/N674x/N675x/N676x/N677x), SMU
  (N678xA), and electronic-load (N679xA — N6791A 100W/N6792A 200W) modules.
  Other electronic-load models are supported only through the bundled
  simulator until their command set is verified — see
  `keysight_n6700.module_capabilities.LOAD_PREFIXES`.
- **Transports**: VISA (USB/LAN/GPIB via PyVISA), raw TCP/SCPI (port 5025),
  and a built-in no-hardware simulator.
- **Status**: `untested` (LPDS-001 §9) — LPDS-019 protocol conformance now
  passes against real hardware too (18/24 vectors; the rest need a real SMU
  module or are simulator-only by design, see below), which is the specific
  bar LPDS-001 §9 sets for `stable`. It isn't called `stable` yet because
  that evidence only covers the power-supply/electronic-load path — no real
  SMU, no protection-trip, no remote/local control have been exercised. See
  [`review/known_risks.md`](review/known_risks.md).

## Why this repo exists

This driver follows the [Lab pyDrivers Standard
(LPDS)](https://github.com/ami3go/Lab-equipment-pyDrivers/tree/main/AI_Guides)
defined by the [`Lab-equipment-pyDrivers`](https://github.com/ami3go/Lab-equipment-pyDrivers)
hub: the driver itself (`src/keysight_n6700/`) is plain, framework-independent
Python, fully usable and testable with no automation framework installed. The
Robot Framework binding (`adapters/robotframework/`) is a separate, thin
translation layer with no device logic of its own. It builds on
[`scpi-driver-core`](https://github.com/ami3go/scpi-driver-core) for the
transport and SCPI-client layer, rather than reimplementing that plumbing.

## Install

```bash
pip install "keysight-n6700-driver @ git+https://github.com/ami3go/keysight-n6700-driver"

# Optional extras:
pip install "keysight-n6700-driver[visa] @ git+https://github.com/ami3go/keysight-n6700-driver"           # PyVISA backend
pip install "keysight-n6700-driver[robotframework] @ git+https://github.com/ami3go/keysight-n6700-driver" # Robot Framework adapter
```

For local development, see [`guide/installation.md`](guide/installation.md).

## Quick start

```python
from keysight_n6700 import N6700

with N6700.connect_simulated() as instrument:      # or .connect_visa(resource) / .connect_ethernet(host)
    print(instrument.get_identity())

    instrument.set_dc_voltage(5.0, channel=1)
    instrument.set_dc_current(0.5, channel=1)
    instrument.enable_output(1)

    print(instrument.measure_dc_voltage(1), "V")
    instrument.disable_output(1)
```

More examples: [`examples/python/`](examples/python/).

## Voltage dips, peaks, and controlled slew

Power-supply and SMU channels expose hardware-timed voltage/current LIST,
triggered STEP and N6705-only ARB profiles (built-in shapes, user-defined,
constant-dwell and sequences), BUS/external triggering, continuous initiation,
bounded arm/wait/abort lifecycle methods, and slew control. N6784A voltage
profiles may use an explicit bipolar safety envelope. Configuration requires
the output to be off and never enables it. Analog amplitude, dwell, edges,
overshoot, and trigger jitter are not claimed as physically qualified until
the guarded hardware and independent-oscilloscope gates are completed.

See the [coordinated Arb implementation task](docs/ARB_DRIVER_IMPLEMENTATION_TASK.md), [worked Arb code examples](docs/ARB_WORKED_CODE_EXAMPLES.md), [third-party Arb source-code analysis](docs/ARB_SOURCE_CODE_ANALYSIS.md), the [complete vendor Arb waveform/SCPI catalog](docs/ARB_WAVEFORM_REFERENCE.md) and the [implementation and verification plan](docs/VOLTAGE_TRANSIENT_IMPLEMENTATION.md)
for the API example, SCPI mapping, safety contract, HIL gate, and DSO matrix.

## Robot Framework adapter

```robotframework
*** Settings ***
Library    keysight_n6700_robotframework_adapter.KeysightN6700Library

*** Test Cases ***
Configure One Channel
    Connect To Simulated N6700
    Set N6700 Voltage    1    5V
    Turn On N6700 Output    1
    N6700 Voltage Should Be    1    5V    tolerance=0.01V
```

See [`examples/robot/`](examples/robot/) and
[`guide/writing_robot_tests.md`](guide/writing_robot_tests.md) — the adapter
translates keywords to the exact same public API shown above; it adds no
device logic.

## Documentation

- [`docs/`](docs/) — architecture, configuration, safety, troubleshooting.
- [`docs/VOLTAGE_TRANSIENT_IMPLEMENTATION.md`](docs/VOLTAGE_TRANSIENT_IMPLEMENTATION.md)
  — voltage LIST/ARB/slew usage, implementation decisions, and qualification gates.
- [`capability/capability_model.yaml`](capability/capability_model.yaml) — the
  LPDS-013 self-describing capability model (`get_capability_model()`).
- [`config/schema.json`](config/schema.json) — the LPDS-014 configuration
  schema (`get_driver_configuration_schema()`).
- [`ai/ai_contract.yaml`](ai/ai_contract.yaml) — the LPDS-017 AI-facing
  contract: mental model, state machine, safety rules, error catalogue.
- [`tests/conformance/`](tests/conformance/) — the LPDS-019 call/protocol
  conformance suite (simulator).
- [`scripts/run_hardware_self_check.py`](scripts/run_hardware_self_check.py) /
  [`tests/hardware/`](tests/hardware/) — read-only, guarded-output-test,
  PS-to-load sweep, and LPDS-019 conformance checks against real hardware;
  see [`docs/hardware_acceptance_tests.md`](docs/hardware_acceptance_tests.md).
- [`review/known_risks.md`](review/known_risks.md) — honest scope decisions
  and deferred work for this first release.

## Digitized measurements (scope mode)

Keysight N6700-family instruments can acquire buffered voltage/current
waveforms over SCPI. **Hardware availability depends on the mainframe, installed
power/load module, and module options**. In particular, **the N6705C DC Power
Analyzer includes output-list and digitized measurement capabilities without
requiring Option 054**, even for N677xA modules.

| Mainframe and module | Digitized waveform acquisition | Option 054 requirement |
| --- | --- | --- |
| N6700C + N6775A (without 054) | Not available | Required for digitizing |
| N6700C + N6775A-054 | Available | Installed |
| N6700C + N676xA precision power module | Available | Not required |
| N6700C + N678xA SMU | Available | Not required |
| N6705C + N6775A | Available | Not required |
| N6705C + N6791A electronic load | Available | Not required |
| N6700C + N6791A electronic load | **Observed working on this project's real bench** (see note below) | No Option 054 needed for the load |

**Compatibility qualification:** Keysight's March 2026 specifications guide
lists N6791A/N6792A load-module mainframe compatibility as **N6705C only**.
The repository nevertheless records successful N6791A triggered waveform
captures installed in an N6700C; this is test evidence, **not a general
manufacturer-supported N6700C/N679xA compatibility claim**. See
[`results/load_transient/20261009_n6791a_internal_buffer/`](results/load_transient/20261009_n6791a_internal_buffer/README.md).

**Current driver limitation:** `configure_digitize()` and
`fetch_digitized_array()` currently route through the electronic-load channel
API. Hardware support for digitizing on N6775A (with 054 on N6700C) and
N676xA/N678xA **does not yet mean these channels support that driver API**.
The installed option can be checked without changing instrument state by
sending `SYST:CHAN:OPT? (@1)` (channel 1).

Source: [Keysight N6700 Series Specifications Guide, March 2026](https://www.keysight.com/cd/en/assets/9018-04533/technical-specifications/9018-04533.pdf),
pages 8, 13–14 (module feature tables, N679xA compatibility, Option 054).

## Real-hardware verification

Beyond the simulator, this driver has been run against a real N6700C
mainframe (192.168.0.6, firmware E.02.09.3271): channel 1 an `N6775A`
(power supply), channel 2 an `N6791A` (100W electronic load), with
channel 1's output physically wired into channel 2's load input for a real
closed-loop test.

- **Read-only checks** — `scripts/run_hardware_self_check.py`'s full
  read-only API sweep (identity, module discovery, per-channel
  model/serial/options, measurement, status, setpoint readback, error
  queue, self-test): 43/43 passed.
- **Single-point write test** — channel 1 set to 12V/1A limit, channel 2
  set to current-priority mode at 1.0A and enabled: channel 1 measured
  11.98V/0.9996A, channel 2 measured 11.98V/0.9998A — the load sank the
  commanded current, matching the supply's delivered output, with zero
  SCPI errors.
- **100-point sweep** (`tests/hardware/test_hardware_load_sweep.py`) — 4
  phases × 25 points, all passing:
  - the supply's **CV region**: voltage tracked its setpoint to within
    millivolts across 2V–20V;
  - the supply's **CC crossover**: at its programmed 1.0A limit, current
    pinned and voltage collapsed sharply into current-limiting mode;
  - the load's **CR mode**: measured current matched Ohm's law (I ≈ V/R)
    to within ~0.3%;
  - the load's **CV (voltage-priority) mode**: stayed inactive whenever its
    target was above the bus voltage, and clamped correctly at its own
    `CURR:LIM` once active.

All of this ran under a hard, independent voltage/current safety envelope
(never approached) and a guaranteed-shutdown discipline (both channels
verified off after every phase, regardless of outcome).

- **LPDS-019 conformance, against real hardware** (`tests/hardware/
  test_hardware_protocol_conformance.py`) — LPDS-001 §9 defines `stable`
  status as this suite passing against real hardware specifically, which
  the simulator-only suite can't itself prove. This module captures real
  outbound SCPI via `connect(protocol_trace=...)` (LPDS-008 tracing) and
  checks it against the same `protocol_vectors.yaml` vectors the simulator
  suite uses. Result: **18/24 vectors passed** (19 including
  `enable-output`, which needs its own explicit confirmation). The other 5:
  2 are permanently simulator-only by design, 2 need a real SMU module this
  bench doesn't have, and 1 (`*RST`, resets every channel) is deliberately
  opt-in and wasn't exercised.

This is real evidence for the driver's power-supply and N679xA
electronic-load command paths specifically; a real SMU module,
protection-trip/clear behavior, and remote/local control remain
simulator-only. See [`review/known_risks.md`](review/known_risks.md) and
[`docs/hardware_acceptance_tests.md`](docs/hardware_acceptance_tests.md)
for the full detail and how to reproduce or extend this on your own
hardware.

## Local CI

GitHub Actions runs Ruff, both mypy targets, pytest with coverage, structure
validation, and a generated-artifact freshness check. The same set can be run
offline in a `[dev]` environment:

```bash
scripts/ci_local.sh
```

Set `PYTHON=/path/to/python` to select a specific interpreter. Extra arguments
are forwarded to pytest (for example `scripts/ci_local.sh tests/unit -q`).

## Safety

`connect()` never energizes an output or load input. Typed setpoint methods
(`set_dc_voltage`, `set_dc_current`, `configure_*`) never enable an output —
`enable_output()`/`set_input()` are separate, explicit calls. `disconnect()`
attempts a best-effort shutdown of every installed channel first, unless
`set_auto_shutdown_on_disconnect(False)` was called. Raw SCPI
(`write_scpi`/`query_scpi`) bypasses every typed safeguard. See
[`ai/ai_contract.yaml`](ai/ai_contract.yaml)'s `safety_rules` section and
[`docs/safety.md`](docs/safety.md).

## License

MIT, see [`LICENSE`](LICENSE).
