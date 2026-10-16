"""Current ARB: 0.1 A -> 0.8 A load pulse for 50 ms.

Current ARBs program amperes instead of volts; use a module/mode that can
source a controlled current (e.g. an N6781A/N6782A SMU on an N6705).

Run: python examples/python/generator/08_current_pulse.py
"""

from __future__ import annotations

from _play import play

from keysight_n6700 import N6700, ArbPulse

CHANNEL = 1

with N6700.connect_simulated() as supply:
    supply.set_dc_voltage(5.0, CHANNEL)   # voltage limit while sourcing current

    pulse = ArbPulse(start_level=0.1, top_level=0.8,
                     start_time_s=0.02, top_time_s=0.05, end_time_s=0.02)
    supply.configure_current_arb_waveform(CHANNEL, pulse, maximum_current_a=1.0)
    play(supply, CHANNEL)
