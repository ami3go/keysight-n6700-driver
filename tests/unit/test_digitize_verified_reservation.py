"""N679-02: no fictitious free-buffer reporting; strict atomic configure/readback.

Exercises both N679xA models with an offline simulator; no physical I/O.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from keysight_n6700 import N6700
from keysight_n6700.exceptions import (
    DriverArgumentValueError,
    DriverOperationUncertainError,
    DriverResourceBusyError,
    DriverUnsupportedOperationError,
)


@pytest.fixture
def dual_load_driver() -> N6700:
    drv = N6700.connect_simulated(
        models={1: "N6733B", 2: "N6791A", 3: "N6792A"}
    )
    yield drv
    drv.disconnect_all()


@pytest.mark.parametrize("channel", [2, 3])
def test_no_verified_live_free_memory_report_is_invented(
    dual_load_driver: N6700, channel: int
) -> None:
    drv = dual_load_driver
    with pytest.raises(DriverUnsupportedOperationError, match="not exposed"):
        drv.get_verified_digitize_available_points(channel)


@pytest.mark.parametrize("channel", [2, 3])
def test_exact_depth_is_verified_and_owner_remains_reserved(
    dual_load_driver: N6700, channel: int
) -> None:
    drv = dual_load_driver
    timing = drv.configure_digitize_verified(
        channel, "voltage", points=1024, interval_s=20.48e-6, offset_points=-100
    )
    assert timing.points == 1024
    assert timing.offset_points == -100
    assert timing.actual_interval_s > 0
    assert drv._digitize_leases.owner("default").channel == channel
    drv.abort_digitize(channel)
    assert drv._digitize_leases.owner("default") is None


@pytest.mark.parametrize("value", [0, -1, 524289, True, 1.5])
def test_bad_depth_fails_without_reserving(dual_load_driver: N6700, value: object) -> None:
    drv = dual_load_driver
    with pytest.raises(DriverArgumentValueError):
        drv.configure_digitize_verified(
            2, "voltage", points=value, interval_s=20.48e-6
        )
    assert drv._digitize_leases.owner("default") is None


def test_shared_budget_overflow_refuses_exact_depth(dual_load_driver: N6700) -> None:
    drv = dual_load_driver
    drv.write_scpi("SENS:FUNC:VOLT ON,(@3)")
    drv.write_scpi("SENS:SWE:POIN 500000,(@3)")
    with pytest.raises(DriverArgumentValueError, match="shared points"):
        drv.configure_digitize_verified(2, "voltage", points=50000, interval_s=20.48e-6)
    assert drv._digitize_leases.owner("default").channel == 2
    drv.abort_digitize(2)


def test_second_channel_cannot_allocate_between_reservation_and_readback(
    dual_load_driver: N6700,
) -> None:
    drv = dual_load_driver
    drv.configure_digitize_verified(2, "voltage", points=100, interval_s=20.48e-6)

    def second() -> None:
        with pytest.raises(DriverResourceBusyError):
            drv.configure_digitize_verified(
                3, "voltage", points=50, interval_s=20.48e-6
            )

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(second).result(timeout=3)
    drv.abort_digitize(2)


def test_inconsistent_readback_is_not_a_verified_receipt(
    dual_load_driver: N6700, monkeypatch: pytest.MonkeyPatch
) -> None:
    drv = dual_load_driver
    channel = drv.load(2)
    original = channel._read_digitize_integral

    def wrong_points(command: str) -> int:
        value = original(command)
        return value + 1 if command == "SENS:SWE:POIN?" else value

    monkeypatch.setattr(channel, "_read_digitize_integral", wrong_points)
    with pytest.raises(DriverOperationUncertainError, match="mismatch"):
        drv.configure_digitize_verified(2, "voltage", points=100, interval_s=20.48e-6)
    assert channel.digitize_timing is None
    drv.abort_digitize(2)


def test_aliases_have_distinct_configured_buffers(dual_load_driver: N6700) -> None:
    drv = dual_load_driver
    drv.connect(
        alias="other", connection_type="simulated",
        models={1: "N6733B", 2: "N6791A", 3: "N6792A"},
    )
    first = drv.configure_digitize_verified(
        2, "voltage", points=128, interval_s=20.48e-6, alias="default"
    )
    second = drv.configure_digitize_verified(
        2, "voltage", points=256, interval_s=20.48e-6, alias="other"
    )
    assert first.points == 128
    assert second.points == 256
    assert drv._digitize_leases.owner("default") is not None
    assert drv._digitize_leases.owner("other") is not None
    drv.abort_digitize(2, alias="default")
    drv.abort_digitize(2, alias="other")
