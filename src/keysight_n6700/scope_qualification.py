"""Independent, offline timing qualification for digitizer/DSO evidence.

A reference DSO waveform must share a physical trigger-time origin with the
N679xA acquisition. This module never sends SCPI or energizes a DUT.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Literal


@dataclass(frozen=True)
class WaveformSeries:
    times_s: tuple[float, ...]
    values: tuple[float, ...]

    def validate(self) -> None:
        if len(self.times_s) != len(self.values) or len(self.times_s) < 5:
            raise ValueError("waveforms need at least five paired samples")
        if any(not math.isfinite(x) for x in self.times_s + self.values):
            raise ValueError("non-finite waveform coordinate")
        if any(a >= b for a, b in zip(self.times_s, self.times_s[1:], strict=False)):
            raise ValueError("sample times must increase strictly")

    @property
    def sample_period_s(self) -> float:
        self.validate()
        diffs = [b - a for a, b in zip(self.times_s, self.times_s[1:], strict=False)]
        return sorted(diffs)[len(diffs) // 2]


@dataclass(frozen=True)
class EventComparison:
    status: Literal["PASS", "FAIL", "INCONCLUSIVE", "NOT_RESOLVED"]
    measured_delta_s: float | None
    expanded_uncertainty_s: float
    limit_s: float
    time_resolution_floor_s: float
    acquired_event_time_s: float | None
    reference_event_time_s: float | None
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _edge_event(series: WaveformSeries) -> float | None:
    """Return first sample at or beyond the median-baseline/plateau midpoint."""
    series.validate()
    n = max(2, len(series.values) // 10)
    start = sorted(series.values[:n])[n // 2]
    end = sorted(series.values[-n:])[n // 2]
    change = end - start
    if abs(change) <= 1e-12:
        return None
    threshold = start + change / 2.0
    for index, value in enumerate(series.values):
        if (change > 0 and value >= threshold) or (change < 0 and value <= threshold):
            return series.times_s[index]
    return None


def compare_triggered_events(
    acquired: WaveformSeries,
    reference: WaveformSeries,
    *,
    common_trigger_verified: bool,
    timing_limit_s: float,
    known_trigger_jitter_s: float = 0.0,
    minimum_resolved_samples: int = 5,
) -> EventComparison:
    """Compare 50% step-crossing times with explicit sampling uncertainty.

    Does not estimate recovery time or analog bandwidth.  No cross-correlation
    "alignment" is applied: it could erase a real trigger-time error.  Both
    series MUST already share a verified physical trigger origin.

    Uses a conservative type-B sampling term dt/sqrt(12) for each instrument
    and expands combined uncertainty by k=2. The jitter term must be a
    standard uncertainty estimate from reference hardware characterization.
    """
    acquired.validate()
    reference.validate()
    if not math.isfinite(timing_limit_s) or timing_limit_s <= 0:
        raise ValueError("timing_limit_s must be finite and positive")
    if not math.isfinite(known_trigger_jitter_s) or known_trigger_jitter_s < 0:
        raise ValueError("trigger jitter must be nonnegative and finite")
    if minimum_resolved_samples < 2:
        raise ValueError("minimum_resolved_samples must be at least 2")
    dut_dt = acquired.sample_period_s
    ref_dt = reference.sample_period_s
    expanded = 2 * math.sqrt(
        dut_dt * dut_dt / 12
        + ref_dt * ref_dt / 12
        + known_trigger_jitter_s * known_trigger_jitter_s
    )
    floor = dut_dt * minimum_resolved_samples
    dut_edge = _edge_event(acquired)
    ref_edge = _edge_event(reference)
    def result(
        status: Literal["PASS", "FAIL", "INCONCLUSIVE", "NOT_RESOLVED"],
        delta: float | None,
        reason: str,
    ) -> EventComparison:
        return EventComparison(
            status=status,
            measured_delta_s=delta,
            expanded_uncertainty_s=expanded,
            limit_s=timing_limit_s,
            time_resolution_floor_s=floor,
            acquired_event_time_s=dut_edge,
            reference_event_time_s=ref_edge,
            reason=reason,
        )

    if not common_trigger_verified:
        return result("INCONCLUSIVE", None, "no verified common physical trigger")
    if dut_edge is None or ref_edge is None:
        return result("INCONCLUSIVE", None, "missing valid step crossing")
    delta = abs(dut_edge - ref_edge)
    if timing_limit_s < floor:
        return result("NOT_RESOLVED", delta, "limit shorter than instrument resolution floor")
    if delta + expanded <= timing_limit_s:
        return result("PASS", delta, "within guard-banded timing limit")
    if delta - expanded > timing_limit_s:
        return result("FAIL", delta, "exceeds guard-banded timing limit")
    return result("INCONCLUSIVE", delta, "measurement overlaps uncertainty band")
