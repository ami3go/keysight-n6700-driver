# Examples

## Python (framework-independent)

- `python/basic_power_supply.py` — configure, enable, and measure one channel against the simulator.
- `python/multi_session.py` — hold two independent sessions in one driver instance.
- `python/ethernet_connection.py` — connect to real hardware over raw TCP/SCPI.

Run any of them directly: `python examples/python/basic_power_supply.py`.

## Robot Framework (optional adapter)

- `robot/01_simulator_smoke.robot` — connect, configure, measure, and disconnect using `KeysightN6700Library`.

Requires the `robotframework` extra: `pip install -e ".[robotframework]"`, then
run `robot examples/robot/01_simulator_smoke.robot` from the repository root.
