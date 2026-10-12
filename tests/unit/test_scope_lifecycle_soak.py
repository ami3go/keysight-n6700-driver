"""PR09 repeat acquisition regression under simulation; NEVER use hardware."""
from __future__ import annotations

from keysight_n6700 import N6700
from keysight_n6700.channel import ElectronicLoadChannel
from keysight_n6700.module_capabilities import classify_module


def test_repeated_digitize_generations_do_not_mix_buffers() -> None:
    with N6700.connect_simulated() as driver:
        ch = ElectronicLoadChannel(driver, 3, classify_module("N6791A"))
        for generation in range(1, 201):
            n = 128 if generation % 2 else 4096
            function = "voltage" if generation % 3 else "current"
            ch.configure_digitize(function, points=n, interval_s=20.48e-6)
            ch.configure_digitize_trigger("bus")
            ch.arm_digitize()
            ch.wait_digitize_armed(timeout_s=1)
            ch.trigger_digitize()
            ch.wait_digitize_complete(timeout_s=1)
            waveform = ch.fetch_digitized_array()
            assert ch.digitize_status.generation == generation
            assert len(waveform.values) == n
            assert waveform.format == ("real" if n >= 4096 else "ascii")
            assert driver._digitize_leases.owner("default") is None
        assert driver.query_scpi("FORM?") == "ASC"
