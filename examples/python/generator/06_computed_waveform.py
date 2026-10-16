"""Waveform computed in Python: a 1 Hz triangle sampled every 10 ms.

Constant-dwell ARB shares one dwell time for every point and allows many
more points than the user-defined ARB.

Run: python examples/python/generator/06_computed_waveform.py
"""

from __future__ import annotations

from _play import play

from keysight_n6700 import N6700

CHANNEL = 1
DWELL_S = 0.010
POINTS = 100                      # 100 x 10 ms = one 1 s period

with N6700.connect_simulated() as supply:
    supply.set_dc_current(1.0, CHANNEL)

    # Triangle between 2 V and 6 V.
    levels = [2.0 + 4.0 * (1 - abs(2 * i / POINTS - 1)) for i in range(POINTS)]

    supply.configure_voltage_constant_dwell_arb(
        CHANNEL, levels, DWELL_S, maximum_voltage_v=7.0, repeat_count=3
    )
    play(supply, CHANNEL)
