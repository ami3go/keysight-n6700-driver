# Troubleshooting

**`DriverDependencyError` / import error mentioning PyVISA** — install the
`visa` extra: `pip install -e ".[visa]"`.

**`DriverConnectionError: alias 'default' is already connected`** — call
`disconnect()` first, or pass `replace=True` to `connect()`.

**`DriverCommandRejectedError` after a raw `write_scpi()` call** — the
instrument's SCPI error queue has entries; `query_scpi("SYST:ERR?")` or
`get_all_device_errors()` to see what was rejected. Raw SCPI bypasses typed
argument validation, so a malformed command reaches the instrument as-is.

**`DriverUnsupportedOperationError: electronic-load SCPI commands are not
verified for this exact module`** — expected for any real load module; see
[`docs/safety.md`](safety.md). Use the simulator (`SIM_LOAD`) for load-mode
development until a real model is verified.

**Robot Framework: `ModuleNotFoundError: No module named 'KeysightN6700Library'`**
— import the fully qualified path:
`Library    keysight_n6700_robotframework_adapter.KeysightN6700Library`, not
the bare class name.

**Timeouts against real hardware** — raise the session's communication
timeout: `driver.set_communication_timeout(15.0)`, or pass `timeout_s=` to
`connect()`.
