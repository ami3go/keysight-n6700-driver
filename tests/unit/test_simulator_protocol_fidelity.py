"""Regression coverage for the write/query reply-queuing boundary.

A write-only command must never queue a reply; the scripted transport's
inbound buffer is one continuous byte stream, so a stray reply from an
earlier write is read by whichever query comes next, silently returning the
wrong data instead of the requested one. This bit the simulator once (see
git history for the fix) and it stays covered here.
"""

from __future__ import annotations

import pytest

from keysight_n6700 import N6700


def test_unmatched_write_does_not_corrupt_a_later_query() -> None:
    drv = N6700.connect_simulated()
    try:
        drv.write_scpi("BOGUS:COMMAND")  # write-only, unmatched -> pushes -113
        # The next real query must see its own reply, not a stale empty one.
        assert drv.get_identity().startswith("KEYSIGHT TECHNOLOGIES")
    finally:
        drv.disconnect()


def test_error_queue_reports_the_pushed_error_exactly_once() -> None:
    drv = N6700.connect_simulated()
    try:
        drv.write_scpi("BOGUS:COMMAND")
        errors = drv.drain_errors()
        assert [e.code for e in errors] == [-113]
        drv.check_errors()  # queue now empty; must not raise
    finally:
        drv.disconnect()


def test_many_consecutive_writes_then_one_query_stay_aligned() -> None:
    drv = N6700.connect_simulated()
    try:
        for _ in range(5):
            drv.write_scpi("*CLS")
        assert drv.query_scpi("*OPC?").strip() == "1"
    finally:
        drv.disconnect()


def test_voltage_limit_write_queues_no_error_and_round_trips() -> None:
    """VOLT:LIM used to fall through to the LIST header loop.

    The fallthrough shadowed the command parameter, so a successful write
    queued a bogus ``Undefined header: LIST:TOUT:EOST`` error.
    """
    drv = N6700.connect_simulated()
    try:
        smu = drv.smu(2)
        smu.set_voltage_limit(5.0)
        assert smu.get_voltage_limit() == pytest.approx(5.0)
        assert drv.drain_errors() == []
    finally:
        drv.disconnect()


def test_undefined_header_error_names_the_offending_command() -> None:
    drv = N6700.connect_simulated()
    try:
        drv.write_scpi("VOLT:BOGUS 1,(@1)")
        errors = drv.drain_errors()
        assert [error.code for error in errors] == [-113]
        assert "VOLT:BOGUS" in errors[0].message
        assert "LIST:TOUT" not in errors[0].message
    finally:
        drv.disconnect()
