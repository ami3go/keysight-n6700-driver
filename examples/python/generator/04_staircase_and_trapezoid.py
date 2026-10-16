"""Two more built-in shapes: a 5-step staircase and a trapezoid.

Run: python examples/python/generator/04_staircase_and_trapezoid.py
"""

from __future__ import annotations

from _play import play

from keysight_n6700 import N6700, ArbStaircase, ArbTrapezoid

CHANNEL = 1

with N6700.connect_simulated() as supply:
    supply.set_dc_current(1.0, CHANNEL)

    # 1 V -> 5 V in 5 equal steps spread over 2 s.
    staircase = ArbStaircase(start_level=1.0, end_level=5.0, steps=5, staircase_time_s=2.0)
    supply.configure_voltage_arb_waveform(CHANNEL, staircase, maximum_voltage_v=6.0)
    play(supply, CHANNEL)

    # 2 V, rise to 8 V in 0.1 s, hold 0.3 s, fall back in 0.1 s.
    trapezoid = ArbTrapezoid(
        start_level=2.0, top_level=8.0,
        rise_time_s=0.1, top_time_s=0.3, fall_time_s=0.1,
    )
    supply.configure_voltage_arb_waveform(CHANNEL, trapezoid, maximum_voltage_v=9.0)
    play(supply, CHANNEL)
