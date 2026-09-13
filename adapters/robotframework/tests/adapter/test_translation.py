"""Adapter conformance: translation correctness only.

Per LPDS-019 Appendix B, an adapter's own tests prove that it forwards
arguments and results faithfully — they do not re-verify device logic, which
the driver's own unit and conformance tests already cover.
"""

from __future__ import annotations

import pytest
from keysight_n6700_robotframework_adapter import KeysightN6700Library


@pytest.fixture
def library() -> KeysightN6700Library:
    lib = KeysightN6700Library()
    lib.connect_to_simulated_n6700()
    try:
        yield lib
    finally:
        lib.disconnect_all_n6700()


def test_connect_returns_the_alias(library: KeysightN6700Library) -> None:
    assert library.get_current_n6700_alias() == "default"


def test_engineering_notation_is_converted_before_reaching_the_driver(library: KeysightN6700Library) -> None:
    library.set_n6700_voltage(1, "250mV")
    assert library.get_n6700_voltage_setpoint(1) == pytest.approx(0.25)


def test_boolean_keyword_string_forms_are_normalized(library: KeysightN6700Library) -> None:
    library.set_n6700_output(1, "yes")
    assert library.get_n6700_output_state(1) is True
    library.set_n6700_output(1, "off")
    assert library.get_n6700_output_state(1) is False


def test_channel_list_string_is_parsed(library: KeysightN6700Library) -> None:
    channels = library.set_n6700_outputs("1,2", True)
    assert channels == [1, 2]
    assert library.get_n6700_output_state(1) is True
    assert library.get_n6700_output_state(2) is True


def test_measurement_result_is_serialized_to_plain_dict(library: KeysightN6700Library) -> None:
    result = library.measure_n6700_channel(1)
    assert isinstance(result, dict)
    assert set(result) >= {"channel", "voltage_V", "current_A", "power_W"}


def test_assertion_keyword_raises_on_mismatch(library: KeysightN6700Library) -> None:
    library.set_n6700_voltage(1, "5V")
    library.turn_on_n6700_output(1)
    library.n6700_voltage_should_be(1, "5V", "0.01V")
    with pytest.raises(AssertionError):
        library.n6700_voltage_should_be(1, "9V", "0.01V")


def test_strict_errors_flag_fails_after_bad_raw_command() -> None:
    lib = KeysightN6700Library(strict_errors=True)
    lib.connect_to_simulated_n6700()
    try:
        with pytest.raises(Exception):  # noqa: B017 - driver raises a DriverCommandRejectedError
            lib.write_n6700_scpi("BOGUS:COMMAND")
    finally:
        lib.disconnect_all_n6700()


def test_multiple_aliases_stay_independent() -> None:
    lib = KeysightN6700Library()
    lib.connect_to_simulated_n6700(alias="a")
    lib.connect_to_simulated_n6700(alias="b")
    try:
        lib.set_n6700_voltage(1, "1V", alias="a")
        lib.set_n6700_voltage(1, "2V", alias="b")
        assert lib.get_n6700_voltage_setpoint(1, alias="a") == 1.0
        assert lib.get_n6700_voltage_setpoint(1, alias="b") == 2.0
    finally:
        lib.disconnect_all_n6700()
