"""PR01 regression tests for the conservative acquisition-status observer."""

from __future__ import annotations

import pytest

from keysight_n6700.acquisition import AcquisitionState, AcquisitionTracker
from keysight_n6700.exceptions import DriverPreconditionError


def _armed() -> AcquisitionTracker:
    tracker = AcquisitionTracker()
    tracker.configure()
    tracker.arm()
    return tracker


def test_idle_status_after_init_does_not_mean_complete() -> None:
    tracker = _armed()
    assert tracker.observe(0) is AcquisitionState.ARMED
    assert tracker.snapshot().generation == 1
    assert not tracker.snapshot().observed_waiting


def test_waiting_then_idle_proves_completed_generation() -> None:
    tracker = _armed()
    assert tracker.observe(8 | 32) is AcquisitionState.WAITING
    assert tracker.observe(32) is AcquisitionState.ACQUIRING
    assert tracker.observe(0) is AcquisitionState.COMPLETE
    assert tracker.snapshot().observed_waiting
    assert tracker.snapshot().observed_active
    tracker.fetched()
    assert tracker.state is AcquisitionState.FETCHED


def test_active_without_waiting_is_sufficient_transition_evidence() -> None:
    tracker = _armed()
    assert tracker.observe(32) is AcquisitionState.ACQUIRING
    assert tracker.observe(0) is AcquisitionState.COMPLETE
    assert tracker.snapshot().observed_active


def test_abort_discards_generation_and_requires_reconfigure() -> None:
    tracker = _armed()
    tracker.observe(8)
    tracker.aborted()
    assert tracker.state is AcquisitionState.ABORTED
    with pytest.raises(DriverPreconditionError):
        tracker.fetched()
    with pytest.raises(DriverPreconditionError):
        tracker.arm()
    tracker.configure()
    tracker.arm()
    assert tracker.generation == 2
    assert tracker.state is AcquisitionState.ARMED


def test_invalid_mutations_cannot_take_place_while_waiting() -> None:
    tracker = _armed()
    tracker.observe(8)
    with pytest.raises(DriverPreconditionError):
        tracker.configure()
    with pytest.raises(DriverPreconditionError):
        tracker.arm()
    tracker.triggered()
    assert tracker.snapshot().explicit_trigger_requested


def test_stale_buffer_is_not_returnable_after_new_arm() -> None:
    tracker = _armed()
    tracker.observe(8)
    tracker.observe(0)
    tracker.fetched()
    tracker.arm()
    with pytest.raises(DriverPreconditionError):
        tracker.fetched()
    assert tracker.snapshot().generation == 2
    assert tracker.state is AcquisitionState.ARMED


def test_unknown_state_cannot_be_reused_without_reconciliation() -> None:
    tracker = _armed()
    tracker.unknown()
    assert tracker.state is AcquisitionState.UNKNOWN
    with pytest.raises(DriverPreconditionError):
        tracker.arm()
    with pytest.raises(DriverPreconditionError):
        tracker.configure()
    with pytest.raises(DriverPreconditionError):
        tracker.fetched()


def test_trigger_is_rejected_without_waiting_observation() -> None:
    tracker = _armed()
    with pytest.raises(DriverPreconditionError):
        tracker.triggered()
    tracker.observe(8)
    tracker.triggered()
    assert tracker.explicit_trigger_requested
