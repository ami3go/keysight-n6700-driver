"""Voltage dip: 12 V -> 6 V for 20 ms -> back to 12 V (built-in PULSE shape).

Run: python examples/python/generator/01_pulse.py
"""

from __future__ import annotations

from _play import play

from keysight_n6700 import N6700, ArbPulse

CHANNEL = 1

with N6700.connect_simulated() as supply:
    supply.set_dc_current(1.0, CHANNEL)

    pulse = ArbPulse(
        start_level=12.0,   # volts before and after the pulse
        top_level=6.0,      # volts during the pulse
        start_time_s=0.010,
        top_time_s=0.020,
        end_time_s=0.010,
    )
    supply.configure_voltage_arb_waveform(CHANNEL, pulse, maximum_voltage_v=15.0)
    play(supply, CHANNEL)
