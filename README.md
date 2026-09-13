# keysight-n6700-driver

Framework-independent Python driver for Keysight/Agilent N6700-family modular
power systems, with an optional Robot Framework adapter.

- **Supported hardware**: N6700-series mainframes (N6700B, N6701A, N6702A,
  N6705B/C) with power-supply (N673x/N674x/N675x/N676x/N677x) and SMU
  (N678xA) modules. Electronic-load modules are supported only through the
  bundled simulator until a real model's command set is verified — see
  `keysight_n6700.module_capabilities.VERIFIED_LOAD_MODELS`.
- **Transports**: VISA (USB/LAN/GPIB via PyVISA), raw TCP/SCPI (port 5025),
  and a built-in no-hardware simulator.
- **Status**: `untested` — unit tests and the LPDS-019 protocol-conformance
  suite pass against the simulator; nothing has been run against real
  hardware yet. See [`review/known_risks.md`](review/known_risks.md).

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
- [`review/known_risks.md`](review/known_risks.md) — honest scope decisions
  and deferred work for this first release.

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
