"""ARB sequence: chain several shapes into one test profile.

ramp up -> hold with ripple -> dip -> ramp down

Run: python examples/python/generator/07_sequence.py
"""

from __future__ import annotations

from _play import play

from keysight_n6700 import N6700, ArbPulse, ArbRamp, ArbSequenceStep, ArbSine

CHANNEL = 1

with N6700.connect_simulated() as supply:
    supply.set_dc_current(1.0, CHANNEL)

    steps = [
        ArbSequenceStep(ArbRamp(start_level=0.0, end_level=12.0, rise_time_s=0.5)),
        ArbSequenceStep(ArbSine(offset=12.0, amplitude=0.5, frequency_hz=10.0)),
        ArbSequenceStep(ArbPulse(start_level=12.0, top_level=8.0,
                                 start_time_s=0.1, top_time_s=0.05, end_time_s=0.1)),
        ArbSequenceStep(ArbRamp(start_level=12.0, end_level=0.0, rise_time_s=0.5)),
    ]
    supply.configure_voltage_arb_sequence(CHANNEL, steps, maximum_voltage_v=13.0)
    play(supply, CHANNEL)
