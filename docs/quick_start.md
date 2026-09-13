# Quick start

```python
from keysight_n6700 import N6700

with N6700.connect_simulated() as instrument:
    print(instrument.get_identity())
    instrument.discover_modules()

    instrument.set_dc_voltage(5.0, channel=1)
    instrument.set_dc_current(0.5, channel=1)
    instrument.enable_output(1)

    print("voltage:", instrument.measure_dc_voltage(1))
    print("current:", instrument.measure_dc_current(1))

    instrument.disable_output(1)
```

Replace `connect_simulated()` with `connect_visa("USB0::0x0957::0x0907::MY...::INSTR")`
or `connect_ethernet("192.168.1.50")` for real hardware.

Several independent sessions can share one driver instance via `alias`:

```python
driver = N6700()
driver.connect(connection_type="simulated", alias="bench_a")
driver.connect(connection_type="simulated", alias="bench_b")
driver.set_dc_voltage(3.3, channel=1, alias="bench_a")
driver.set_dc_voltage(5.0, channel=1, alias="bench_b")
driver.disconnect_all()
```

More: [`examples/python/`](../examples/python/), [`examples/robot/`](../examples/robot/).
