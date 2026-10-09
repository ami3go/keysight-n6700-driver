"""Digitized/buffered measurement (SENS:SWEep + TRIG:ACQuire + FETC:ARRay).

Per the official Keysight N6705C User's Guide / Programmer's Reference (see
``Keysight_documents/`` in this repository): this is the same remote-only
command surface the N679xA "Load Model Differences" table marks available
("SCPI command array readback", "SCPI command programmable sample rate").
Like ``test_electronic_load_channel.py``, these tests build an
``ElectronicLoadChannel`` with real (non-``SIM_LOAD``) capabilities pointed
at the simulator's channel 3, which answers the shared command surface
regardless of the placeholder model name stored in that channel's simulated
state. Unlike the plain load setpoints, there is no simulated-waveform
twin: the simulator replays a flat buffer rather than modeling a real
transient, so these tests exercise configuration/polling/parsing, not
waveform fidelity.
"""

from __future__ import annotations

import pytest

from keysight_n6700 import (
    DriverArgumentValueError,
    DriverOperationUncertainError,
    DriverUnsupportedOperationError,
)
from keysight_n6700.channel import ElectronicLoadChannel
from keysight_n6700.module_capabilities import classify_module


def _real_load_channel(driver, channel: int = 3) -> ElectronicLoadChannel:
    return ElectronicLoadChannel(driver, channel, classify_module("N6791A"))


def test_digitize_round_trip(driver) -> None:
    load = _real_load_channel(driver)
    load.configure_digitize(
        "voltage", points=50, interval_s=10.24e-6, offset_points=-25
    )
    load.configure_digitize_trigger("bus")
    load.arm_digitize()
    load.wait_digitize_armed(timeout_s=1.0)
    load.trigger_digitize()
    load.wait_digitize_complete(timeout_s=1.0)

    result = load.fetch_digitized_array()

    assert result.channel == 3
    assert len(result.values) == 50
    assert result.unit == "V"
    assert result.format == "ascii"
    assert result.interval_s == pytest.approx(10.24e-6)
    assert result.offset_points == -25


def test_digitize_current_uses_explicit_function_override(driver) -> None:
    load = _real_load_channel(driver)
    load.configure_digitize("voltage", points=10, interval_s=10.24e-6)

    result = load.fetch_digitized_array("current")

    assert result.unit == "A"
    assert len(result.values) == 10


def test_digitize_rejects_interval_below_hardware_minimum(driver) -> None:
    load = _real_load_channel(driver)
    with pytest.raises(DriverArgumentValueError):
        load.configure_digitize("voltage", points=10, interval_s=1e-6)


def test_digitize_rejects_fewer_than_one_point(driver) -> None:
    load = _real_load_channel(driver)
    with pytest.raises(DriverArgumentValueError):
        load.configure_digitize("voltage", points=0, interval_s=10.24e-6)


@pytest.mark.parametrize("points", [1.5, True, 524289])
def test_digitize_rejects_invalid_point_count(driver, points) -> None:
    load = _real_load_channel(driver)
    with pytest.raises(DriverArgumentValueError):
        load.configure_digitize("voltage", points=points, interval_s=10.24e-6)


@pytest.mark.parametrize("offset", [-10, 2_000_000_001, 1.5, True])
def test_digitize_rejects_invalid_offset(driver, offset) -> None:
    load = _real_load_channel(driver)
    with pytest.raises(DriverArgumentValueError):
        load.configure_digitize(
            "voltage", points=10, interval_s=10.24e-6, offset_points=offset
        )


def test_level_trigger_requires_fresh_level_and_slope(driver) -> None:
    load = _real_load_channel(driver)
    with pytest.raises(DriverArgumentValueError, match="requires both"):
        load.configure_digitize_trigger("current", level=1.5)


def test_digitize_requires_configure_before_fetch(driver) -> None:
    load = _real_load_channel(driver)
    with pytest.raises(DriverArgumentValueError):
        load.fetch_digitized_array()


def test_digitize_is_not_modeled_by_the_simulator_placeholder(driver) -> None:
    sim_load = driver.load(3)  # channel 3 is SIM_LOAD in the default simulator map
    with pytest.raises(DriverUnsupportedOperationError):
        sim_load.configure_digitize("voltage", points=10, interval_s=10.24e-6)


def test_wait_digitize_complete_honors_abort_request(driver) -> None:
    load = _real_load_channel(driver)
    load.configure_digitize("voltage", points=10, interval_s=10.24e-6)

    with pytest.raises(DriverOperationUncertainError):
        load.wait_digitize_complete(timeout_s=1.0, abort_requested=lambda: True)
