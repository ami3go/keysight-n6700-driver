"""Hold two independent sessions in one driver instance.

Run: python examples/python/multi_session.py
"""

from __future__ import annotations

from keysight_n6700 import N6700


def main() -> None:
    driver = N6700()
    driver.connect(connection_type="simulated", alias="bench_a")
    driver.connect(connection_type="simulated", alias="bench_b")
    try:
        driver.set_dc_voltage(3.3, channel=1, alias="bench_a")
        driver.set_dc_voltage(5.0, channel=1, alias="bench_b")
        print("bench_a:", driver.get_dc_voltage_setpoint(1, alias="bench_a"))
        print("bench_b:", driver.get_dc_voltage_setpoint(1, alias="bench_b"))
    finally:
        driver.disconnect_all()


if __name__ == "__main__":
    main()
