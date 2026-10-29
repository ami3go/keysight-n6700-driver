"""SENSe sweep/function restore after a digitizer capture.

``MEASure`` on an N6700 channel integrates over ``SENS:SWE:POIN`` x
``SENS:SWE:TINT`` (default 1024 x 20.48 us, about 21 ms). Before this fix a
capture left its own sweep behind (e.g. 48830 x 10.24 us), so every later DC
reading on the load took about 0.5 s. These tests use the simulator's real-load
command surface on channel 3, like ``test_electronic_load_digitize.py``.
"""

from __future__ import annotations

import pytest

from keysight_n6700 import DriverPreconditionError
from keysight_n6700.acquisition import AcquisitionState
from keysight_n6700.channel import ElectronicLoadChannel
from keysight_n6700.module_capabilities import classify_module

PRE = {"VOLT": True, "CURR": True, "POIN": 1024, "TINT": 20.48e-6, "OFFS": 0}


def _load(driver) -> ElectronicLoadChannel:
    return ElectronicLoadChannel(driver, 3, classify_module("N6791A"))


def _set_pre_state(load: ElectronicLoadChannel) -> None:
    load._write("SENS:FUNC:VOLT ON,(@3)")
    load._write("SENS:FUNC:CURR ON,(@3)")
    load._write(f"SENS:SWE:POIN {PRE['POIN']},(@3)")
    load._write("SENS:SWE:TINT 2.048e-05,(@3)")
    load._write("SENS:SWE:OFFS:POIN 0,(@3)")


def _state(load: ElectronicLoadChannel) -> dict:
    sense = load._read_sense_state()
    return {
        "VOLT": sense.voltage_enabled,
        "CURR": sense.current_enabled,
        "POIN": sense.points,
        "TINT": sense.interval_s,
        "OFFS": sense.offset_points,
    }


def _capture(load: ElectronicLoadChannel, points: int = 4096) -> None:
    load.configure_digitize(
        "voltage", points=points, interval_s=10.24e-6, offset_points=-(points // 4)
    )
    load.configure_digitize_trigger("bus")
    load.arm_digitize()
    load.wait_digitize_armed(timeout_s=1.0)
    load.trigger_digitize()
    load.wait_digitize_complete(timeout_s=1.0)


def test_abort_restores_pre_capture_sweep_and_functions(driver) -> None:
    load = _load(driver)
    _set_pre_state(load)
    _capture(load)
    assert _state(load)["POIN"] == 4096 and _state(load)["CURR"] is False

    load.abort_digitize()

    assert _state(load) == pytest.approx(PRE)
    assert load.measurement_restore_status == "restored"
    assert load.digitize_status.state is AcquisitionState.ABORTED


def test_fetch_keeps_capture_so_refetch_works_then_abort_restores(driver) -> None:
    load = _load(driver)
    _set_pre_state(load)
    _capture(load, points=64)

    assert len(load.fetch_digitized_array().values) == 64
    assert len(load.fetch_digitized_array().values) == 64  # re-fetch contract kept
    assert _state(load)["POIN"] == 64

    load.abort_digitize()
    assert _state(load) == pytest.approx(PRE)


def test_reconfigure_keeps_the_first_snapshot(driver) -> None:
    load = _load(driver)
    _set_pre_state(load)
    load.configure_digitize("voltage", points=100, interval_s=10.24e-6)
    load.configure_digitize("current", points=200, interval_s=20.48e-6)

    load.abort_digitize()

    assert _state(load) == pytest.approx(PRE)


def test_restore_writes_offset_zero_first_and_enables_before_disabling(driver, monkeypatch) -> None:
    load = _load(driver)
    load._write("SENS:FUNC:VOLT OFF,(@3)")
    load._write("SENS:FUNC:CURR ON,(@3)")
    load.configure_digitize("voltage", points=100, interval_s=10.24e-6, offset_points=-10)
    writes: list[str] = []
    original = load._write
    monkeypatch.setattr(load, "_write", lambda cmd: (writes.append(cmd), original(cmd))[1])

    load.abort_digitize()

    sense = [w for w in writes if w.startswith("SENS:")]
    assert sense[0] == "SENS:SWE:OFFS:POIN 0,(@3)"
    assert sense.index("SENS:FUNC:CURR ON,(@3)") < sense.index("SENS:FUNC:VOLT OFF,(@3)")
    assert _state(load)["VOLT"] is False and _state(load)["CURR"] is True


def test_failed_restore_does_not_fail_a_verified_abort(driver, monkeypatch) -> None:
    load = _load(driver)
    _set_pre_state(load)
    _capture(load)
    real = load._read_digitize_integral

    def lying(command: str) -> int:
        return 7 if command == "SENS:SWE:POIN?" else real(command)

    monkeypatch.setattr(load, "_read_digitize_integral", lying)
    load.abort_digitize()

    assert load.digitize_status.state is AcquisitionState.ABORTED
    assert load.measurement_restore_status.startswith("failed: DriverOperationUncertainError")
    assert load._digitize_previous_sense is not None  # kept for a later recovery
    monkeypatch.setattr(load, "_read_digitize_integral", real)
    load.restore_measurement_defaults()
    assert load._digitize_previous_sense is None


def test_restore_measurement_defaults_recovers_a_polluted_channel(driver) -> None:
    load = _load(driver)
    load._write("SENS:SWE:POIN 48830,(@3)")
    load._write("SENS:SWE:TINT 1.024e-05,(@3)")
    load._write("SENS:FUNC:CURR OFF,(@3)")
    load._write("SENS:FUNC:VOLT ON,(@3)")

    result = load.restore_measurement_defaults()

    assert result["points"] == 1024 and result["offset_points"] == 0
    assert result["interval_s"] == pytest.approx(20.48e-6)
    assert result["voltage_enabled"] is True
    assert result["current_enabled"] is load.capabilities.supports_simultaneous_vi
    assert load.measurement_restore_status == "restored"


def test_restore_measurement_defaults_refused_while_a_capture_is_configured(driver) -> None:
    load = _load(driver)
    load.configure_digitize("voltage", points=100, interval_s=10.24e-6)
    with pytest.raises(DriverPreconditionError):
        load.restore_measurement_defaults()
    load.abort_digitize()
    load.restore_measurement_defaults()


def test_driver_wrapper_delegates_to_the_load_channel(driver, monkeypatch) -> None:
    load = _load(driver)
    monkeypatch.setattr(driver, "load", lambda channel, alias=None: load)
    assert driver.restore_load_measurement_defaults(3)["points"] == 1024
