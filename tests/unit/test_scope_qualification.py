"""PR08 offline metrology and status guard-band tests; no instrument I/O."""
from __future__ import annotations

import pytest

from keysight_n6700.scope_qualification import WaveformSeries, compare_triggered_events


def step(dt: float, count: int, edge: float) -> WaveformSeries:
    xs = tuple((i - count // 2) * dt for i in range(count))
    ys = tuple(0.0 if x < edge else 10.0 for x in xs)
    return WaveformSeries(xs, ys)


def test_dso_comparison_reports_not_resolved_for_sub_five_sample_limit() -> None:
    dut = step(20.48e-6, 256, 0.0)
    dso = step(1e-6, 4096, 0.0)
    result = compare_triggered_events(
        dut, dso, common_trigger_verified=True, timing_limit_s=35e-6
    )
    assert result.status == "NOT_RESOLVED"
    assert result.time_resolution_floor_s == pytest.approx(102.4e-6)


def test_guardband_pass_and_fail_require_common_origin() -> None:
    dut = step(20.48e-6, 256, 0.0)
    dso = step(1e-6, 4096, 0.0)
    passed = compare_triggered_events(
        dut, dso, common_trigger_verified=True, timing_limit_s=0.0003
    )
    assert passed.status == "PASS"
    failed = compare_triggered_events(
        dut, step(1e-6, 4096, -0.0005), common_trigger_verified=True,
        timing_limit_s=0.0002
    )
    assert failed.status == "FAIL"
    missing = compare_triggered_events(
        dut, dso, common_trigger_verified=False, timing_limit_s=0.0003
    )
    assert missing.status == "INCONCLUSIVE"


def test_constant_signal_is_inconclusive() -> None:
    dut = WaveformSeries(tuple(i * 20.48e-6 for i in range(256)), (1.0,) * 256)
    dso = step(1e-6, 4096, 0.0)
    result = compare_triggered_events(
        dut, dso, common_trigger_verified=True, timing_limit_s=0.0002
    )
    assert result.status == "INCONCLUSIVE"


@pytest.mark.parametrize("limit,jitter", [(0.0, 0), (-1.0, 0), (0.001, -1), (0.001, float("nan"))])
def test_invalid_metrology_policy_fails_closed(limit: float, jitter: float) -> None:
    s = step(20.48e-6, 256, 0)
    with pytest.raises(ValueError):
        compare_triggered_events(s, s, common_trigger_verified=True,
                                 timing_limit_s=limit, known_trigger_jitter_s=jitter)
