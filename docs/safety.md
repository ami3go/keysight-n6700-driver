# Safety

- `connect()` is non-invasive: no reset, no output changes, no protection
  clear, no nonvolatile writes, unless explicitly requested
  (`reset_on_connect=True`, `clear_errors_on_connect=True`).
- Typed setpoint methods (`set_dc_voltage`, `set_dc_current`,
  `configure_smu_voltage_priority`, ...) never enable an output or load
  input. `enable_output()` / channel `.set_input(True)` are separate,
  explicit calls, so a script cannot accidentally energize a channel while
  only meaning to program it.
- `disconnect()` / `disconnect_all()` attempt a best-effort shutdown of every
  installed channel first (`shutdown_all()`), unless
  `set_auto_shutdown_on_disconnect(False)` was called — use that only when
  another safety controller (external interlock, a bench manager) already
  owns output state.
- Electronic-load SCPI commands are refused for a real module unless it
  appears in `keysight_n6700.module_capabilities.VERIFIED_LOAD_MODELS`
  (currently empty — only the simulator's `SIM_LOAD` is exempted). Do not add
  a real model to that map without verifying its command set against the
  manufacturer's programming guide.
- `write_scpi()` / `query_scpi()` bypass every typed safeguard above. Use
  them for diagnostics, not as a way to work around a typed method's
  restriction. `set_raw_scpi_guard(phrase)` can require an explicit
  `enable_raw_scpi(phrase)` before either will run — off by default, so
  existing scripts are unaffected unless a guard is configured.
- Nothing in this driver has been verified against real hardware yet (see
  [`review/known_risks.md`](../review/known_risks.md)). Treat any first
  real-hardware run as a new-instrument acceptance test: verify wiring,
  polarity, and DUT limits, and keep an operator present for first
  energization.
