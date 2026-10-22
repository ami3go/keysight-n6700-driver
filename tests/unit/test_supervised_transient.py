"""ARB-R04: supervised infinite/continuous source runs, finite completion, concurrency."""

from __future__ import annotations

import math
import threading

import pytest
from scpi_driver_core.simulation.scripted import ScriptedScpiTransport

from keysight_n6700 import (
    DriverArgumentValueError,
    DriverOperationUncertainError,
    DriverPreconditionError,
    DriverTimeoutError,
    SupervisedTransientResult,
)
from keysight_n6700.channel import SUPERVISED_MAXIMUM_RUNTIME_S


def _sim(driver):
    return driver._session(None).transport.instrument


def _history(driver) -> list[str]:
    transport = driver._session(None).transport
    assert isinstance(transport, ScriptedScpiTransport)
    return list(transport.history)


def _armed(driver, channel: int = 1, *, repeat="infinite", continuous: bool = False):
    driver.configure_voltage_list(
        channel, [12.0, 6.0], 0.01, maximum_voltage_v=18.0, repeat_count=repeat
    )
    driver.set_dc_voltage(12.0, channel)
    driver.enable_output(channel)
    token = driver.arm_voltage_transient(channel, continuous=continuous)
    driver.wait_voltage_transient_ready(channel, token)
    return token


def _triggered(driver, channel: int = 1, **kwargs):
    token = _armed(driver, channel, **kwargs)
    driver.trigger_voltage_transient(channel, token)
    return token


def _count(driver, prefix: str) -> int:
    return sum(1 for command in _history(driver) if command.startswith(prefix))


# -- normal completion API stays finite-only ------------------------------------


def test_infinite_run_is_refused_by_the_ordinary_completion_wait(driver) -> None:
    token = _triggered(driver)
    with pytest.raises(DriverPreconditionError, match="never completes"):
        driver.wait_voltage_transient_complete(1, token, poll_interval_s=0.0001)
    driver.abort_voltage_transient(1)


# -- stop reasons ---------------------------------------------------------------


def test_deadline_aborts_switches_output_off_and_verifies(driver) -> None:
    token = _triggered(driver)
    result = driver.supervise_voltage_transient(
        1, token, maximum_runtime_s=0.05, poll_interval_s=0.005
    )
    assert isinstance(result, SupervisedTransientResult)
    assert result.reason == "deadline"
    assert result.transient_aborted and result.output_off_commanded
    assert result.output_off_verified is True
    assert result.elapsed_s >= 0.05
    assert driver.get_output_state(1) is False
    assert driver.get_voltage_transient_status(1).state == "aborted"
    assert _count(driver, "ABOR:TRAN") >= 1 and _count(driver, "INIT:CONT:TRAN OFF") >= 1


def test_deadline_can_leave_the_output_alone_when_asked(driver) -> None:
    token = _triggered(driver)
    result = driver.supervise_voltage_transient(
        1, token, maximum_runtime_s=0.03, poll_interval_s=0.005, output_off_on_expiry=False
    )
    assert result.reason == "deadline" and result.transient_aborted
    assert not result.output_off_commanded and result.output_off_verified is None
    assert driver.get_output_state(1) is True
    driver.disable_output(1)


def test_caller_abort_stops_the_profile_but_not_the_output(driver) -> None:
    token = _triggered(driver)
    calls = iter([False, False, True])
    result = driver.supervise_voltage_transient(
        1, token, maximum_runtime_s=5.0, poll_interval_s=0.001,
        abort_requested=lambda: next(calls),
    )
    assert result.reason == "abort_requested" and result.transient_aborted
    assert not result.output_off_commanded and result.output_off_verified is None
    assert driver.get_output_state(1) is True
    assert driver.get_voltage_transient_status(1).state == "aborted"
    driver.disable_output(1)


def test_protection_trip_aborts_and_forces_output_off(driver) -> None:
    token = _triggered(driver)
    sim = _sim(driver)
    sim.channels[1].questionable_status = 1  # OV
    result = driver.supervise_voltage_transient(
        1, token, maximum_runtime_s=5.0, poll_interval_s=0.001
    )
    assert result.reason == "protection"
    assert result.output_off_commanded and result.output_off_verified is True
    assert driver.get_voltage_transient_status(1).state == "aborted"


def test_output_dropping_out_is_treated_as_a_stop(driver) -> None:
    token = _triggered(driver)
    _sim(driver).channels[1].enabled = False
    result = driver.supervise_voltage_transient(
        1, token, maximum_runtime_s=5.0, poll_interval_s=0.001
    )
    assert result.reason == "output_off"
    assert result.transient_aborted and result.output_off_verified is True


def test_finite_run_ends_with_a_clean_completion(driver) -> None:
    _sim(driver).transient_active_polls = 2
    token = _triggered(driver, repeat=1)
    result = driver.supervise_voltage_transient(
        1, token, maximum_runtime_s=5.0, poll_interval_s=0.0005
    )
    assert result.reason == "completed"
    assert not result.transient_aborted and result.output_off_verified is None
    assert driver.get_voltage_transient_status(1).state == "complete"
    assert driver.get_output_state(1) is True
    driver.disable_output(1)


def test_continuous_arm_never_reports_completion_by_itself(driver) -> None:
    token = _triggered(driver, repeat=1, continuous=True)
    result = driver.supervise_voltage_transient(
        1, token, maximum_runtime_s=0.05, poll_interval_s=0.005
    )
    assert result.reason == "deadline"
    assert driver.get_voltage_transient_status(1).continuous is False
    assert _count(driver, "INIT:CONT:TRAN OFF") >= 1


def test_externally_paced_wait_is_bounded_too(driver) -> None:
    token = _armed(driver)  # armed but never triggered: still waiting for a trigger
    result = driver.supervise_voltage_transient(
        1, token, maximum_runtime_s=0.03, poll_interval_s=0.005
    )
    assert result.reason == "deadline" and result.output_off_verified is True


# -- argument and state validation ---------------------------------------------


@pytest.mark.parametrize(
    "runtime", [0, -1.0, math.inf, math.nan, SUPERVISED_MAXIMUM_RUNTIME_S + 1, True, "5"]
)
def test_runtime_must_be_finite_positive_and_capped(driver, runtime) -> None:
    token = _triggered(driver)
    with pytest.raises(DriverArgumentValueError):
        driver.supervise_voltage_transient(1, token, maximum_runtime_s=runtime)
    assert driver.get_voltage_transient_status(1).state == "triggered"
    driver.abort_voltage_transient(1)


@pytest.mark.parametrize("interval", [0, -0.1, math.nan, math.inf, "0.1"])
def test_poll_interval_must_be_a_positive_finite_number(driver, interval) -> None:
    token = _triggered(driver)
    with pytest.raises(DriverArgumentValueError):
        driver.supervise_voltage_transient(1, token, maximum_runtime_s=5.0, poll_interval_s=interval)
    driver.abort_voltage_transient(1)


def test_default_and_oversized_poll_intervals_are_capped_to_the_runtime(driver) -> None:
    token = _triggered(driver)
    result = driver.supervise_voltage_transient(1, token, maximum_runtime_s=0.02)
    assert result.reason == "deadline"
    driver.disable_output(1)
    token = _triggered(driver)
    result = driver.supervise_voltage_transient(
        1, token, maximum_runtime_s=0.02, poll_interval_s=60.0
    )
    assert result.reason == "deadline" and result.elapsed_s < 1.0


def test_maximum_runtime_is_mandatory(driver) -> None:
    token = _triggered(driver)
    with pytest.raises(TypeError):
        driver.supervise_voltage_transient(1, token)  # type: ignore[call-arg]
    driver.abort_voltage_transient(1)


def test_stale_and_foreign_tokens_and_idle_state_are_rejected(driver) -> None:
    token = _triggered(driver)
    driver.abort_voltage_transient(1)
    with pytest.raises(DriverPreconditionError, match="stale or foreign"):
        driver.supervise_voltage_transient(1, token, maximum_runtime_s=1.0)
    driver.disable_output(1)
    other = _armed(driver, repeat=1)
    with pytest.raises(DriverPreconditionError, match="stale or foreign"):
        driver.supervise_voltage_transient(2, other, maximum_runtime_s=1.0)
    driver.abort_voltage_transient(1)
    configured = driver.arm_voltage_transient(1)
    with pytest.raises(DriverPreconditionError, match="not armed/triggered"):
        driver.supervise_voltage_transient(1, configured, maximum_runtime_s=1.0)
    driver.abort_voltage_transient(1)


# -- fault injection ------------------------------------------------------------


def test_superseded_token_is_not_aborted_a_second_time(driver) -> None:
    token = _triggered(driver)
    channel = driver.power_supply(1)
    real = channel.get_protection_status
    fired = []

    def abort_once():
        if not fired:
            fired.append(True)
            driver.abort_voltage_transient(1)  # someone else stops the run
        return real()

    channel.get_protection_status = abort_once  # type: ignore[method-assign]
    before = _count(driver, "ABOR:TRAN")
    result = driver.supervise_voltage_transient(
        1, token, maximum_runtime_s=5.0, poll_interval_s=0.001
    )
    assert result.reason == "superseded"
    assert not result.transient_aborted and result.output_off_verified is None
    assert _count(driver, "ABOR:TRAN") == before + 1  # only the other party's abort
    driver.disable_output(1)


def test_rearm_after_supervised_stop_works(driver) -> None:
    token = _triggered(driver)
    driver.supervise_voltage_transient(1, token, maximum_runtime_s=0.02, poll_interval_s=0.005)
    driver.enable_output(1)
    token = driver.arm_voltage_transient(1)
    driver.wait_voltage_transient_ready(1, token)
    driver.trigger_voltage_transient(1, token)
    result = driver.supervise_voltage_transient(
        1, token, maximum_runtime_s=0.02, poll_interval_s=0.005
    )
    assert result.reason == "deadline"


def test_transport_fault_while_polling_sends_one_cleanup_and_marks_unknown(driver) -> None:
    token = _triggered(driver)
    channel = driver.power_supply(1)
    calls = {"n": 0}
    real_query = channel._query

    def flaky(command, *args, **kwargs):
        if command.startswith("STAT:QUES:COND?"):
            calls["n"] += 1
            if calls["n"] == 2:
                raise DriverTimeoutError("injected connection loss")
        return real_query(command, *args, **kwargs)

    channel._query = flaky  # type: ignore[method-assign]
    before_abort, before_off = _count(driver, "ABOR:TRAN"), _count(driver, "OUTP OFF")
    with pytest.raises(DriverTimeoutError) as caught:
        driver.supervise_voltage_transient(
            1, token, maximum_runtime_s=5.0, poll_interval_s=0.001
        )
    channel._query = real_query  # type: ignore[method-assign]
    assert "supervised_transient_cleanup" in caught.value.details
    assert driver.get_voltage_transient_status(1).state == "unknown"
    assert _count(driver, "ABOR:TRAN") == before_abort + 1  # no retry, no duplicate
    assert _count(driver, "OUTP OFF") == before_off + 1
    assert _count(driver, "TRIG:TRAN (") == 1  # the supervisor never re-triggers
    with pytest.raises(DriverPreconditionError):
        driver.arm_voltage_transient(1)


def test_failed_cleanup_keeps_the_primary_error_and_reports_both(driver) -> None:
    token = _triggered(driver)
    channel = driver.power_supply(1)
    real_query, real_write = channel._query, channel._write

    def dead_query(command, *args, **kwargs):
        if command.startswith("STAT:QUES:COND?"):
            raise DriverTimeoutError("injected connection loss")
        return real_query(command, *args, **kwargs)

    def dead_write(command, *args, **kwargs):
        if command.startswith(("ABOR:TRAN", "OUTP OFF", "INIT:CONT:TRAN OFF")):
            raise DriverTimeoutError("injected write failure")
        return real_write(command, *args, **kwargs)

    channel._query, channel._write = dead_query, dead_write  # type: ignore[method-assign]
    with pytest.raises(DriverTimeoutError, match="connection loss") as caught:
        driver.supervise_voltage_transient(1, token, maximum_runtime_s=5.0, poll_interval_s=0.001)
    channel._query, channel._write = real_query, real_write  # type: ignore[method-assign]
    outcome = caught.value.details["supervised_transient_cleanup"]
    assert "cleanup failures" in outcome and "output off" in outcome and "abort" in outcome
    assert driver.get_voltage_transient_status(1).state == "unknown"


def test_output_that_will_not_switch_off_is_reported_uncertain(driver) -> None:
    token = _triggered(driver)
    channel = driver.power_supply(1)
    channel.output_off = lambda: None  # type: ignore[method-assign]
    with pytest.raises(DriverOperationUncertainError, match="still reads ON"):
        driver.supervise_voltage_transient(1, token, maximum_runtime_s=0.02, poll_interval_s=0.005)
    assert driver.get_voltage_transient_status(1).state == "unknown"
    del channel.output_off
    driver.disable_output(1)


# -- concurrency ----------------------------------------------------------------


def test_supervisor_does_not_hold_the_session_for_the_whole_run(driver) -> None:
    token = _triggered(driver)
    stop = threading.Event()
    box: list[SupervisedTransientResult] = []
    worker = threading.Thread(
        target=lambda: box.append(
            driver.supervise_voltage_transient(
                1, token, maximum_runtime_s=60.0, poll_interval_s=0.005,
                abort_requested=stop.is_set,
            )
        )
    )
    worker.start()
    reader = threading.Thread(target=lambda: [driver.measure_dc_voltage(2) for _ in range(5)])
    reader.start()
    reader.join(timeout=10)
    assert not reader.is_alive(), "reads on another channel blocked behind the supervisor"
    assert worker.is_alive(), "supervisor ended before it was told to stop"
    stop.set()
    worker.join(timeout=10)
    assert not worker.is_alive()
    assert box and box[0].reason == "abort_requested"
    driver.disable_output(1)


def test_supervised_stop_leaves_another_channels_armed_profile_alone(driver) -> None:
    token = _triggered(driver, 1)
    other = _armed(driver, 2, repeat=1)
    driver.supervise_voltage_transient(1, token, maximum_runtime_s=0.03, poll_interval_s=0.005)
    status = driver.get_voltage_transient_status(2)
    assert status.state == "ready" and status.waiting_for_trigger
    assert driver.get_output_state(2) is True
    driver.trigger_voltage_transient(2, other)
    driver.disable_output(2)


# -- trigger dialect and configuration concurrency ------------------------------


def test_lifecycle_never_sends_a_mainframe_global_trigger(driver) -> None:
    token = _triggered(driver)
    driver.supervise_voltage_transient(1, token, maximum_runtime_s=0.03, poll_interval_s=0.005)
    _sim(driver).transient_active_polls = 1
    token = _triggered(driver, repeat=1)
    driver.supervise_voltage_transient(1, token, maximum_runtime_s=1.0, poll_interval_s=0.001)
    commands = _history(driver)
    assert not any(c.strip().upper().startswith("*TRG") for c in commands)
    assert all("(@1)" in c for c in commands if c.startswith(("TRIG:TRAN (", "INIT:TRAN", "ABOR:TRAN")))


def test_external_trigger_run_is_supervised_without_a_software_trigger(driver) -> None:
    driver.configure_voltage_list(
        1, [12.0, 6.0], 0.01, maximum_voltage_v=18.0, repeat_count="infinite"
    )
    driver.configure_voltage_transient_trigger(1, "external")
    driver.set_dc_voltage(12.0, 1)
    driver.enable_output(1)
    token = driver.arm_voltage_transient(1)
    driver.wait_voltage_transient_ready(1, token)
    result = driver.supervise_voltage_transient(
        1, token, maximum_runtime_s=0.03, poll_interval_s=0.005
    )
    assert result.reason == "deadline" and result.output_off_verified is True
    assert _count(driver, "TRIG:TRAN (") == 0 and _count(driver, "*TRG") == 0


def test_simultaneous_source_configurations_do_not_interleave_writes(driver) -> None:
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def configure(channel: int) -> None:
        try:
            barrier.wait(timeout=5)
            for _ in range(5):
                driver.configure_voltage_list(
                    channel, [12.0, 6.0, 3.0], 0.01, maximum_voltage_v=18.0
                )
        except BaseException as exc:  # surfaced below, never swallowed
            errors.append(exc)

    threads = [threading.Thread(target=configure, args=(channel,)) for channel in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not errors
    writes = [
        c for c in _history(driver)
        if not c.split(None, 1)[0].endswith("?")
        and c.startswith(("INIT:CONT:TRAN", "ABOR:TRAN", "LIST:", "VOLT:MODE", "CURR:MODE"))
    ]
    # Collapse consecutive writes to the same channel into runs.  A run may only
    # begin at the start of a configuration transaction; a run starting
    # mid-transaction means two configurations interleaved.
    runs: list[list[str]] = []
    last_channel = None
    for command in writes:
        channel = command.rsplit("(@", 1)[-1]
        if channel != last_channel:
            runs.append([])
            last_channel = channel
        runs[-1].append(command)
    assert runs and all(run[0].startswith("INIT:CONT:TRAN OFF") for run in runs), runs


# -- review findings ------------------------------------------------------------


def test_fault_coinciding_with_a_caller_abort_still_switches_the_output_off(driver) -> None:
    token = _triggered(driver)
    _sim(driver).channels[1].questionable_status = 1
    result = driver.supervise_voltage_transient(
        1, token, maximum_runtime_s=5.0, poll_interval_s=0.001, abort_requested=lambda: True
    )
    assert result.reason == "protection"
    assert result.output_off_commanded and result.output_off_verified is True


def test_fault_appearing_between_the_last_poll_and_the_deadline_is_caught(driver) -> None:
    token = _triggered(driver)
    channel = driver.power_supply(1)
    real = channel.get_protection_status
    calls = {"n": 0}

    def late_trip():
        calls["n"] += 1
        if calls["n"] > 1:  # the in-loop poll saw nothing; the exit check sees the trip
            _sim(driver).channels[1].questionable_status = 1
        return real()

    channel.get_protection_status = late_trip  # type: ignore[method-assign]
    result = driver.supervise_voltage_transient(
        1, token, maximum_runtime_s=0.02, poll_interval_s=0.05, output_off_on_expiry=False
    )
    assert result.reason in {"deadline", "protection"}
    assert result.output_off_commanded and result.output_off_verified is True


def test_failed_completion_check_still_aborts_and_switches_the_output_off(driver) -> None:
    sim = _sim(driver)
    sim.transient_active_polls = 1
    token = _triggered(driver, repeat=1)
    channel = driver.power_supply(1)

    def trip_at_completion():
        raise DriverOperationUncertainError("injected: protection at completion")

    channel._verify_transient_outcome = trip_at_completion  # type: ignore[method-assign]
    before = (_count(driver, "ABOR:TRAN"), _count(driver, "OUTP OFF"))
    with pytest.raises(DriverOperationUncertainError, match="injected") as caught:
        driver.supervise_voltage_transient(1, token, maximum_runtime_s=5.0, poll_interval_s=0.001)
    assert "supervised_transient_cleanup" in caught.value.details
    assert (_count(driver, "ABOR:TRAN"), _count(driver, "OUTP OFF")) == (before[0] + 1, before[1] + 1)
    assert driver.get_voltage_transient_status(1).state == "unknown"


def test_abort_between_checks_is_reported_as_superseded_not_raised(driver) -> None:
    token = _triggered(driver)
    channel = driver.power_supply(1)
    real = channel._token_status
    fired = []

    def abort_then_poll(tok):
        if not fired:
            fired.append(True)
            driver.abort_voltage_transient(1)  # lands after the loop's own generation check
        return real(tok)

    channel._token_status = abort_then_poll  # type: ignore[method-assign]
    before = _count(driver, "ABOR:TRAN")
    result = driver.supervise_voltage_transient(
        1, token, maximum_runtime_s=5.0, poll_interval_s=0.001
    )
    assert result.reason == "superseded" and not result.transient_aborted
    assert _count(driver, "ABOR:TRAN") == before + 1
    driver.disable_output(1)


def test_rearm_between_loop_exit_and_stop_is_not_cancelled(driver) -> None:
    token = _triggered(driver)
    channel = driver.power_supply(1)
    real = channel._finish_supervised_run

    def rearm_first(tok, reason, elapsed, off):
        driver.abort_voltage_transient(1)
        driver.arm_voltage_transient(1)  # a newer generation owned by someone else
        return real(tok, reason, elapsed, off)

    channel._finish_supervised_run = rearm_first  # type: ignore[method-assign]
    before = _count(driver, "ABOR:TRAN")
    result = driver.supervise_voltage_transient(
        1, token, maximum_runtime_s=0.02, poll_interval_s=0.005
    )
    assert result.reason == "superseded"
    assert _count(driver, "ABOR:TRAN") == before + 1  # only the other party's abort
    assert driver.get_voltage_transient_status(1).state == "armed"
    assert driver.get_output_state(1) is True
    driver.abort_voltage_transient(1)
    driver.disable_output(1)


def test_a_raising_abort_callback_is_a_supervision_fault(driver) -> None:
    token = _triggered(driver)

    def broken() -> bool:
        raise RuntimeError("operator GUI closed")

    with pytest.raises(RuntimeError, match="GUI closed"):
        driver.supervise_voltage_transient(
            1, token, maximum_runtime_s=5.0, poll_interval_s=0.001, abort_requested=broken
        )
    assert driver.get_voltage_transient_status(1).state == "unknown"
    assert driver.get_output_state(1) is False
