"""Custom point-by-point waveform: each point is a (voltage, dwell) pair.

Run: python examples/python/generator/05_user_defined_points.py
"""

from __future__ import annotations

from _play import play

from keysight_n6700 import N6700

CHANNEL = 1

with N6700.connect_simulated() as supply:
    supply.set_dc_current(1.0, CHANNEL)

    #           volts  seconds
    points = [(12.0, 0.100),
              (4.5, 0.015),   # cold-crank dip
              (6.0, 0.050),
              (8.0, 0.200),
              (12.0, 0.100)]
    levels = [volts for volts, _ in points]
    dwells = [seconds for _, seconds in points]

    supply.configure_voltage_arb(
        CHANNEL, levels_v=levels, dwell_s=dwells, maximum_voltage_v=14.0
    )
    play(supply, CHANNEL)
