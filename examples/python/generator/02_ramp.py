"""Linear voltage ramp 0 V -> 10 V in 1 s, then hold 0.5 s (built-in RAMP shape).

Run: python examples/python/generator/02_ramp.py
"""

from __future__ import annotations

from _play import play

from keysight_n6700 import N6700, ArbRamp

CHANNEL = 1

with N6700.connect_simulated() as supply:
    supply.set_dc_current(1.0, CHANNEL)

    ramp = ArbRamp(start_level=0.0, end_level=10.0, rise_time_s=1.0, end_time_s=0.5)
    supply.configure_voltage_arb_waveform(CHANNEL, ramp, maximum_voltage_v=12.0)
    play(supply, CHANNEL)
