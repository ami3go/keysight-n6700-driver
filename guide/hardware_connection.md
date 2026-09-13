# Connecting to real hardware

## USB / VISA

```python
from keysight_n6700 import N6700

instrument = N6700.connect_visa("USB0::0x0957::0x0907::MY43014421::INSTR")
```

Requires a VISA backend: either a vendor IO Libraries Suite, or
`pip install -e ".[visa]"` for `pyvisa-py`.

## Ethernet (raw SCPI socket)

```python
instrument = N6700.connect_ethernet("192.168.1.50")  # port defaults to 5025
```

No VISA backend required; this uses `scpi_driver_core.transport.tcp.TcpTransport`
directly.

## First connection checklist

1. Verify wiring, polarity, and DUT/fixture ratings before enabling any
   output.
2. Confirm the instrument answers `*IDN?` with a supported manufacturer
   (`instrument.get_identity()`); `connect()` does this automatically and
   raises `DriverCommandRejectedError` if not.
3. Run `instrument.discover_modules()` (default on `connect()`) and check
   `instrument.get_capability_model()` or `channel_model(n)` against your
   installed modules before assuming any channel's type.
4. Configure setpoints and protection with the output/load input off, then
   enable it explicitly — see [`docs/safety.md`](../docs/safety.md).
