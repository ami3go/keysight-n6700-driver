"""Configure one power-supply channel and measure it, against the simulator.

Run: python examples/python/basic_power_supply.py
"""

from __future__ import annotations

from keysight_n6700 import N6700


def main() -> None:
    with N6700.connect_simulated() as instrument:
        print("Identity:", instrument.get_identity())

        channel = 1
        instrument.set_dc_voltage(5.0, channel)
        instrument.set_dc_current(0.5, channel)
        instrument.enable_output(channel)

        voltage = instrument.measure_dc_voltage(channel)
        current = instrument.measure_dc_current(channel)
        print(f"Channel {channel}: {voltage:.3f} V, {current:.3f} A")

        instrument.disable_output(channel)


if __name__ == "__main__":
    main()
