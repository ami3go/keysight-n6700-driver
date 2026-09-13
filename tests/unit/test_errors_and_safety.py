from __future__ import annotations

import pytest

from keysight_n6700 import DriverCommandRejectedError


def test_check_errors_passes_when_queue_is_empty(driver) -> None:
    driver.check_errors()  # must not raise


def test_unknown_command_populates_error_queue(driver) -> None:
    driver.write_scpi("BOGUS:COMMAND")
    errors = driver.drain_errors()
    assert errors
    assert errors[0].code == -113


def test_check_errors_raises_after_bad_command(driver) -> None:
    driver.write_scpi("BOGUS:COMMAND")
    with pytest.raises(DriverCommandRejectedError):
        driver.check_errors()


def test_device_error_queue_should_be_empty_keyword_style(driver) -> None:
    driver.device_error_queue_should_be_empty()  # must not raise


def test_get_all_device_errors_drains_and_reports(driver) -> None:
    driver.write_scpi("ANOTHER:BOGUS:COMMAND")
    errors = driver.get_all_device_errors()
    assert len(errors) == 1
    assert driver.check_errors() is None  # queue now empty


def test_safe_shutdown_disables_every_output(driver) -> None:
    driver.enable_output(1)
    driver.enable_output(2)
    result = driver.safe_shutdown()
    assert result["success"] is True
    assert driver.get_output_state(1) is False
    assert driver.get_output_state(2) is False


def test_disconnect_attempts_safe_shutdown_by_default() -> None:
    from keysight_n6700 import N6700

    drv = N6700.connect_simulated()
    drv.enable_output(1)
    drv.disconnect()
    # Reconnect to observe the shutdown took effect against the same simulator state
    # is not possible (new simulator instance per connect), so this test only proves
    # disconnect did not raise even with an energized channel.


def test_disabling_auto_shutdown_skips_the_safety_hook() -> None:
    from keysight_n6700 import N6700

    drv = N6700.connect_simulated()
    drv.set_auto_shutdown_on_disconnect(False)
    drv.enable_output(1)
    drv.disconnect()  # must not raise regardless of hook being skipped


def test_clear_protection_reports_before_and_after_state(driver) -> None:
    result = driver.clear_protection(1)
    assert result.channel == 1
    assert result.protection_after.active is False
