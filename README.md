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
- **Status**: `untested` (LPDS-001 §9) — the driver has been run against
  real hardware (a power-supply channel and an N6791A load, see below), but
  not enough of the surface (no real SMU, no protection-trip, no
  remote/local) to call it `stable`. Unit tests and the LPDS-019
  protocol-conformance suite pass against the simulator regardless. See
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
- [`capability/capability_model.yaml`](capability/capability_model.yaml) — the
  LPDS-013 self-describing capability model (`get_capability_model()`).
- [`config/schema.json`](config/schema.json) — the LPDS-014 configuration
  schema (`get_driver_configuration_schema()`).
- [`ai/ai_contract.yaml`](ai/ai_contract.yaml) — the LPDS-017 AI-facing
  contract: mental model, state machine, safety rules, error catalogue.
- [`tests/conformance/`](tests/conformance/) — the LPDS-019 call/protocol
  conformance suite.
- [`scripts/run_hardware_self_check.py`](scripts/run_hardware_self_check.py) /
  [`tests/hardware/`](tests/hardware/) — read-only, guarded-output-test, and
  PS-to-load sweep checks against real hardware; see
  [`docs/hardware_acceptance_tests.md`](docs/hardware_acceptance_tests.md).
- [`review/known_risks.md`](review/known_risks.md) — honest scope decisions
  and deferred work for this first release.

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
verified off after every phase, regardless of outcome). This is real
evidence for the driver's power-supply and N679xA electronic-load command
paths specifically; a real SMU module, protection-trip/clear behavior, and
remote/local control remain simulator-only. See
[`review/known_risks.md`](review/known_risks.md) and
[`docs/hardware_acceptance_tests.md`](docs/hardware_acceptance_tests.md)
for the full detail and how to reproduce or extend this on your own
hardware.

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
