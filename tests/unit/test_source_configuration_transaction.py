from __future__ import annotations

import threading
import time

import pytest
from scpi_driver_core.simulation.scripted import ScriptedScpiTransport

from keysight_n6700 import ArbSine, DriverCommandRejectedError
from keysight_n6700.channel import PowerSupplyChannel


def test_configuration_cleanup_never_masks_primary_failure(driver, monkeypatch) -> None:
    channel = driver.power_supply(1)
    primary = DriverCommandRejectedError("primary ARB programming failure")
    cleanup = DriverCommandRejectedError("cleanup transport failure")
    original_write = channel._write
    primary_seen = False

    def injected_write(command: str) -> None:
        nonlocal primary_seen
        if command.startswith("ARB:FUNC:TYPE") and not primary_seen:
            primary_seen = True
            raise primary
        if primary_seen and command.startswith("INIT:CONT:TRAN OFF"):
            raise cleanup
        original_write(command)

    monkeypatch.setattr(channel, "_write", injected_write)

    with pytest.raises(DriverCommandRejectedError) as caught:
        channel.configure_voltage_arb_waveform(
            ArbSine(1.0, 2.0, 100.0), maximum_voltage_v=5.0
        )

    assert caught.value is primary
    outcome = primary.details["source_transient_configuration_cleanup"]
    assert "cleanup failures" in outcome
    assert "cleanup transport failure" in outcome
    assert channel._transient_state == "unknown"
    assert channel._transient_config is None


def test_two_channel_configuration_transactions_do_not_interleave(driver, monkeypatch) -> None:
    transport = driver._session(None).transport
    assert isinstance(transport, ScriptedScpiTransport)
    transport.clear_history()
    original_write = PowerSupplyChannel._write

    def slow_write(self: PowerSupplyChannel, command: str) -> None:
        original_write(self, command)
        time.sleep(0.0005)

    monkeypatch.setattr(PowerSupplyChannel, "_write", slow_write)
    start = threading.Barrier(3)
    failures: list[BaseException] = []

    def configure(channel: int) -> None:
        try:
            start.wait(timeout=1.0)
            driver.configure_voltage_list(
                channel,
                [1.0, 2.0],
                0.001,
                maximum_voltage_v=5.0,
            )
        except BaseException as exc:  # retained for an actionable watchdog failure
            failures.append(exc)

    threads = [
        threading.Thread(target=configure, args=(channel,), daemon=True)
        for channel in (1, 4)
    ]
    for thread in threads:
        thread.start()
    start.wait(timeout=1.0)
    for thread in threads:
        thread.join(timeout=2.0)

    assert not any(thread.is_alive() for thread in threads), "configuration deadlocked"
    assert failures == []
    channel_order = []
    for command in transport.history:
        channel = 1 if "(@1)" in command else 4 if "(@4)" in command else None
        if channel is not None and (not channel_order or channel_order[-1] != channel):
            channel_order.append(channel)
    assert sorted(channel_order) == [1, 4]
    assert len(channel_order) == 2
