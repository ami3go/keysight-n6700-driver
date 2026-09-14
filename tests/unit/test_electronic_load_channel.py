"""Real N679xA electronic-load command generation.

Per the official Keysight N6705C User's Guide / Programmer's Reference (see
``Keysight_documents/`` in this repository): priority mode is
``FUNC VOLTage|CURRent|RESistance|POWer`` (four modes), the matching level is
set with ``VOLTage``/``CURRent``/``RESistance``/``POWer``, and the load's
input is switched with the ordinary ``OUTP`` command (Note 1: the load's
input terminals are referred to as "Output" throughout the manual). These
tests build an ``ElectronicLoadChannel`` with real (non-``SIM_LOAD``)
capabilities pointed at the simulator's channel 3, which still answers the
shared VOLT/CURR/RES/POW/FUNC/OUTP command surface regardless of the
placeholder model name stored in that channel's simulated state.
"""

from __future__ import annotations

import pytest

from keysight_n6700 import DriverUnsupportedOperationError
from keysight_n6700.channel import ElectronicLoadChannel
from keysight_n6700.module_capabilities import classify_module


def _real_load_channel(driver, channel: int = 3) -> ElectronicLoadChannel:
    return ElectronicLoadChannel(driver, channel, classify_module("N6791A"))


def test_priority_mode_roundtrip_uses_plain_func(driver) -> None:
    load = _real_load_channel(driver)
    load.set_load_mode("cr")
    assert load.get_load_mode() == "cr"
    load.set_load_mode("cp")
    assert load.get_load_mode() == "cp"


def test_input_uses_outp_not_sim_load_inp(driver) -> None:
    load = _real_load_channel(driver)
    load.set_input(True)
    assert load.get_input() is True
    assert driver.query_scpi("OUTP? (@3)").strip() not in {"0", "+0"}
    load.set_input(False)
    assert load.get_input() is False


def test_cc_mode_sets_current(driver) -> None:
    load = _real_load_channel(driver)
    load.configure_cc(0.5)
    assert load.get_load_mode() == "cc"
    assert load.get_load_current() == pytest.approx(0.5)


def test_cv_mode_sets_voltage_and_current_limit(driver) -> None:
    load = _real_load_channel(driver)
    load.configure_cv(10.0, current_limit=5.0)
    assert load.get_load_mode() == "cv"
    assert load.get_load_voltage() == pytest.approx(10.0)
    assert load.get_load_current_limit() == pytest.approx(5.0)


def test_cr_mode_sets_resistance(driver) -> None:
    load = _real_load_channel(driver)
    load.configure_cr(100.0)
    assert load.get_load_mode() == "cr"
    assert load.get_load_resistance() == pytest.approx(100.0)


def test_cp_mode_sets_power(driver) -> None:
    load = _real_load_channel(driver)
    load.configure_cp(50.0)
    assert load.get_load_mode() == "cp"
    assert load.get_load_power() == pytest.approx(50.0)


def test_current_limit_is_not_modeled_by_the_simulator_placeholder(driver) -> None:
    sim_load = driver.load(3)  # channel 3 is SIM_LOAD in the default simulator map
    with pytest.raises(DriverUnsupportedOperationError):
        sim_load.set_load_current_limit(1.0)
    with pytest.raises(DriverUnsupportedOperationError):
        sim_load.get_load_current_limit()
