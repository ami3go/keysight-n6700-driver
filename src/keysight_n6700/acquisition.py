"""Conservative acquisition-generation tracking for N679xA buffered measurements.

This tracks what the driver *observed*, not an imagined hardware trigger.  In
particular, a cleared MEAS_active bit is insufficient proof of completion
unless WTG_meas (or MEAS_active) was observed during this same generation.

The transport-wide acquisition lease, timeouts and cross-controller arbitration
are separate remediation work (see docs/N679XA_SCOPE_REMEDIATION_PLAN.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .exceptions import DriverPreconditionError


class AcquisitionState(str, Enum):
    """Driver-observed lifecycle of a single channel's acquisition."""

    IDLE = "idle"
    CONFIGURED = "configured"
    ARMED = "armed"
    WAITING = "waiting"
    ACQUIRING = "acquiring"
    COMPLETE = "complete"
    FETCHED = "fetched"
    ABORTED = "aborted"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class AcquisitionSnapshot:
    """Read-only acquisition evidence (not a timestamp or full SCPI trace)."""

    state: AcquisitionState
    generation: int
    observed_waiting: bool
    observed_active: bool
    explicit_trigger_requested: bool


class AcquisitionTracker:
    """Enforce one generation and conservative status-derived completion.

    This tracker cannot prove that another process has not overwritten the
    instrument's global buffer; the per-session lease in PR04 will address
    competing in-process calls. This is deliberately *not* a simulator for
    physical trigger timing.
    """

    def __init__(self) -> None:
        self.state = AcquisitionState.IDLE
        self.generation = 0
        self.observed_waiting = False
        self.observed_active = False
        self.explicit_trigger_requested = False

    def snapshot(self) -> AcquisitionSnapshot:
        return AcquisitionSnapshot(
            state=self.state,
            generation=self.generation,
            observed_waiting=self.observed_waiting,
            observed_active=self.observed_active,
            explicit_trigger_requested=self.explicit_trigger_requested,
        )

    def require(self, *allowed: AcquisitionState) -> None:
        if self.state not in allowed:
            expected = ", ".join(s.value for s in allowed)
            raise DriverPreconditionError(
                f"digitizing requires acquisition state {expected}; was {self.state.value}"
            )

    def configure(self) -> None:
        self.require(
            AcquisitionState.IDLE,
            AcquisitionState.CONFIGURED,
            AcquisitionState.COMPLETE,
            AcquisitionState.FETCHED,
            AcquisitionState.ABORTED,
        )
        self.state = AcquisitionState.CONFIGURED
        self.observed_waiting = False
        self.observed_active = False
        self.explicit_trigger_requested = False

    def arm(self) -> None:
        self.require(AcquisitionState.CONFIGURED, AcquisitionState.FETCHED, AcquisitionState.COMPLETE)
        self.generation += 1
        self.state = AcquisitionState.ARMED
        self.observed_waiting = False
        self.observed_active = False
        self.explicit_trigger_requested = False

    def observe(self, status: int) -> AcquisitionState:
        """Consume operation status *for this generation*.

        WTG_meas=8, MEAS_active=32 (N6705C User's Guide pp. 215-217).
        An idle status before the instrument becomes ready is not completion.
        Rapid valid triggers can be missed by polling; callers must first
        establish some within-generation active/waiting evidence.
        """
        self.require(
            AcquisitionState.ARMED,
            AcquisitionState.WAITING,
            AcquisitionState.ACQUIRING,
            AcquisitionState.COMPLETE,
        )
        if self.state is AcquisitionState.COMPLETE:
            return self.state
        if status & 8:
            self.observed_waiting = True
            self.state = AcquisitionState.WAITING
        elif status & 32:
            self.observed_active = True
            self.state = AcquisitionState.ACQUIRING
        elif self.observed_waiting or self.observed_active:
            self.state = AcquisitionState.COMPLETE
        # Otherwise the status is still ambiguous: INIT may not yet be ready.
        return self.state

    def triggered(self) -> None:
        self.require(AcquisitionState.WAITING)
        self.explicit_trigger_requested = True

    def fetched(self) -> None:
        self.require(AcquisitionState.COMPLETE, AcquisitionState.FETCHED)
        self.state = AcquisitionState.FETCHED

    def aborted(self) -> None:
        self.state = AcquisitionState.ABORTED

    def unknown(self) -> None:
        self.state = AcquisitionState.UNKNOWN
