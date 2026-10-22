# 3.3 V buck regulation evidence — 2026-10-22

Retained reports from two consecutive passing runs of
`tests/hardware/test_hardware_buck_regulation.py`.

## Bench

- Mainframe: Keysight N6700C, serial `MY56320259`, firmware `E.02.09.3271`,
  raw Ethernet at `192.168.0.6:5025`
- Channel 1 (N6775A) → buck VIN; buck VOUT → channel 2 (N6791A) load input
- DUT: 3.3 V buck, rated 8–18 V input, 0–2.5 A output
- Limits: VIN 12 V (line test 8 / 12 / 18 V), 3.5 A input limit, 1.0 A
  maximum load, default 3 % static / 10 % transient bands

## Files

- `20261022T073408Z.json`, `20261022T073443Z.json`: limits, every static
  reading (VIN, IIN, VOUT, IOUT, efficiency), and both 1024-point LIST-step
  captures (current and VOUT, 204.8 µs/sample).

See `docs/hardware_acceptance_tests.md` §7 for the summarized results.
