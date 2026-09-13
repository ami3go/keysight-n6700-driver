# Architecture

```text
adapters/robotframework/   Robot Framework keyword library (optional, thin)
        │  imports only the public API below
        ▼
src/keysight_n6700/
  driver.py          N6700 — the one public driver class
  base.py            BaseInstrument — LPDS-003 session lifecycle/state machine
  channel.py          typed per-channel API (power supply / SMU / load)
  module_capabilities.py   module-family classification
  capability_model.py      LPDS-013 capability discovery (@capability decorator)
  configuration.py         LPDS-014 configuration model
  exceptions.py             LPDS-007 public exception hierarchy
  _translate.py             scpi_driver_core exceptions -> LPDS-007 exceptions
  scpi.py                    N6700-specific SCPI formatting/parsing
  simulator.py               no-hardware simulator (ScriptedScpiTransport-based)
        │
        ▼
scpi_driver_core          Transport (VISA/TCP/simulated) + ScpiClient + ScpiSession
```

## Layers

- **`base.BaseInstrument`** owns session/alias registration, the LPDS-003
  9-state connection state machine, and `_execute_operation`, the one choke
  point every state-changing call passes through (state validation, the
  operation lock, exception translation). It knows nothing about SCPI text.
- **`driver.N6700`** is the only subclass. It builds the right
  `scpi_driver_core` transport for a `connection_type`, translates `*IDN?`
  manufacturer acceptance, and implements every public method — both the
  LPDS-002 canonical names (`set_dc_voltage`, `enable_output`, ...) and the
  richer typed accessors (`power_supply(channel)`, `smu(channel)`, ...).
- **`channel.py`** holds per-module-type command construction
  (`PowerSupplyChannel`, `SMUChannel`, `ElectronicLoadChannel`). It talks to
  the driver only through a small `_DriverProtocol` (`query_scpi`/
  `write_scpi`/...), so it never depends on session/alias plumbing directly.
- **`capability_model.py`** and **`configuration.py`** are mixins `N6700`
  composes. Nothing device-specific lives in either; they are reusable
  patterns any LPDS driver could adopt.
- **`adapters/robotframework/`** holds one `N6700()` instance and translates
  Robot argument strings (`"5V"`, `"yes"`, `"1,2,4"`) and results
  (dataclasses -> plain dicts) around calls into the driver's public API.
  Session/alias/safety semantics all come from the driver, not the adapter.

## Beyond the transport/session layer

The driver also composes `scpi_driver_core.execution`/`tracing` directly,
rather than reimplementing them:

- **Tracing** — `connect(protocol_trace=...)` wraps the built transport in
  `InstrumentedTransport`, which emits one `Tracer` event per write/read/
  open/close. `N6700._get_tracer`/`_on_disconnected` (overriding `base.py`
  hooks of the same name) hand the tracer to `ScpiSession` for context and
  close an owned `JsonlTraceSink` on disconnect.
- **Polling** — `wait_for_voltage_in_range`/`wait_for_current_in_range` call
  `scpi_driver_core.execution.polling.poll_until` rather than a hand-rolled
  loop; this is also why the Robot Framework adapter no longer contains its
  own polling loop for the equivalent keyword.
- **Retry** — `query_scpi(..., retry_attempts=)` passes a `RetryPolicy` and
  `ReplayPolicy.SAFE` straight through to `ScpiClient.query`; the driver
  adds nothing beyond exposing the option.
- **Guard** — `set_raw_scpi_guard`/`enable_raw_scpi`/`disable_raw_scpi` wrap
  one `scpi_driver_core.execution.guards.ConfirmationGuard` per driver
  instance, checked inside `write_scpi`/`query_scpi`.

## Why `scpi-driver-core` instead of a hand-rolled transport

Earlier drivers in this ecosystem (see the predecessor RFDS-standard
`rf_keysight_n6700` project) each wrote their own `Transport` protocol,
VISA/TCP backends, and SCPI client. `scpi-driver-core` extracts that into one
shared, framework-independent package (LPDS-004's transport layer), so this
driver only implements what is genuinely N6700-specific: channel-list syntax,
module classification, and instrument semantics.

## What this driver does *not* provide (see `review/known_risks.md`)

There is no shared `lpds-core` package yet for the LPDS-003 `BaseInstrument`
layer (session registry, state machine, diagnostics export) — only the
transport/SCPI-client layer is shared. `base.py` implements LPDS-003 locally,
built on `scpi-driver-core`'s primitives, as a documented, honest stand-in.
