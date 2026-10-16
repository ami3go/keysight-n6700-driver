"""ARB sequence memory, quality, pacing, and cleanup boundaries."""

from __future__ import annotations

import pytest
from scpi_driver_core.simulation.scripted import ScriptedScpiTransport

from keysight_n6700 import (
    ArbExponential,
    ArbPulse,
    ArbRamp,
    ArbSequenceStep,
    ArbSine,
    ArbStaircase,
    ArbStep,
    ArbTrapezoid,
    ArbUserDefined,
    DriverArgumentValueError,
    DriverCommandRejectedError,
    DriverOperationUncertainError,
)
from keysight_n6700.waveforms import sequence_waveform_points


def _transport(driver) -> ScriptedScpiTransport:
    transport = driver._session(None).transport
    assert isinstance(transport, ScriptedScpiTransport)
    return transport


@pytest.mark.parametrize(
    ("waveform", "quality", "points"),
    [
        (ArbStep(1.0, 2.0), 16, 2),
        (ArbRamp(1.0, 2.0, 0.01), 16, 16),
        (ArbStaircase(1.0, 2.0, 5, 0.01), 16, 6),
        (ArbSine(1.0, 2.0, 100.0), 16, 16),
        (ArbPulse(1.0, 2.0, 0.01), 16, 3),
        (ArbTrapezoid(1.0, 2.0, 0.01, 0.01, 0.01), 100, 100),
        (ArbExponential(1.0, 2.0, 0.01, 0.01), 100, 100),
        (ArbUserDefined((1.0, 2.0, 3.0), (0.01,)), 16, 3),
    ],
)
def test_sequence_generated_point_model(waveform, quality, points) -> None:
    assert sequence_waveform_points(waveform, quality) == points


def test_sequence_rejects_minimum_quality_point_overflow_before_reset(driver) -> None:
    transport = _transport(driver)
    transport.clear_history()
    steps = [ArbSequenceStep(ArbRamp(1.0, 2.0, 0.01)) for _ in range(32)]
    with pytest.raises(DriverArgumentValueError, match="512 generated points"):
        driver.configure_voltage_arb_sequence(1, steps, maximum_voltage_v=5.0)
    assert not any(command.startswith("ARB:") for command in transport.history)


@pytest.mark.parametrize(("count", "quality", "points"), [(5, 100, 500), (31, 16, 496)])
def test_sequence_reads_quality_and_effective_point_budget(driver, count, quality, points) -> None:
    steps = [ArbSequenceStep(ArbRamp(1.0, 2.0, 0.01)) for _ in range(count)]
    config = driver.configure_voltage_arb_sequence(1, steps, maximum_voltage_v=5.0)
    assert config.quality == quality
    assert config.generated_points == points


@pytest.mark.parametrize("reply", ["15", "101"])
def test_sequence_rejects_out_of_range_quality_readback(driver, reply) -> None:
    transport = _transport(driver)
    transport.on("ARB:SEQ:QUAL? (@1)", reply)
    with pytest.raises(DriverOperationUncertainError, match="sequence quality"):
        driver.configure_voltage_arb_sequence(
            1, [ArbSequenceStep(ArbStep(1.0, 2.0))], maximum_voltage_v=5.0
        )
    assert driver.power_supply(1)._transient_state == "unknown"


def test_sequence_rejects_unbounded_counts_before_mutation(driver) -> None:
    transport = _transport(driver)
    transport.clear_history()
    with pytest.raises(DriverArgumentValueError, match="sequence_count must be finite"):
        driver.configure_voltage_arb_sequence(
            1, [ArbSequenceStep(ArbStep(1.0, 2.0))],
            maximum_voltage_v=5.0, sequence_count="infinite",
        )
    assert not any(command.startswith("ARB:") for command in transport.history)

    with pytest.raises(DriverArgumentValueError, match="trigger-paced"):
        ArbSequenceStep(ArbStep(1.0, 2.0), repeat_count=2)


def test_interrupted_sequence_upload_runs_cleanup(driver, monkeypatch) -> None:
    channel = driver.power_supply(1)
    original_write = channel._write

    def interrupt_second_step(command: str) -> None:
        if command.startswith("ARB:SEQ:STEP:FUNC:SHAP PULS,1"):
            raise DriverCommandRejectedError("injected sequence upload failure")
        original_write(command)

    monkeypatch.setattr(channel, "_write", interrupt_second_step)
    steps = [
        ArbSequenceStep(ArbStep(1.0, 2.0)),
        ArbSequenceStep(ArbPulse(1.0, 2.0, 0.01)),
    ]
    with pytest.raises(DriverCommandRejectedError, match="injected sequence"):
        channel.configure_voltage_arb_sequence(steps, maximum_voltage_v=5.0)
    assert channel._transient_state == "unknown"
    assert channel._transient_config is None
