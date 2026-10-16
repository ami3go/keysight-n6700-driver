"""Sine ripple: 5 V +/- 1 V at 50 Hz, 10 cycles (built-in SINE shape).

Run: python examples/python/generator/03_sine.py
"""

from __future__ import annotations

from _play import play

from keysight_n6700 import N6700, ArbSine

CHANNEL = 1

with N6700.connect_simulated() as supply:
    supply.set_dc_current(1.0, CHANNEL)

    sine = ArbSine(offset=5.0, amplitude=1.0, frequency_hz=50.0)
    # One repeat = one sine period, so repeat_count is the number of cycles.
    supply.configure_voltage_arb_waveform(
        CHANNEL, sine, maximum_voltage_v=7.0, repeat_count=10
    )
    play(supply, CHANNEL)
