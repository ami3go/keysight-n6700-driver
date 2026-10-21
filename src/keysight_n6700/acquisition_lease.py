"""Session-scoped exclusion for digitizer ownership and SCPI buffer integrity.

Coordinates in-process callers sharing *one N6700 instance*. The lease spans
configuration through validated FETCH or confirmed ABORt, not just one I/O
transaction. Independent controllers/processes must use an external lease.
"""
from __future__ import annotations

import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from .exceptions import DriverResourceBusyError, DriverResourceConflictError


@dataclass(frozen=True)
class AcquisitionOwner:
    channel: int
    thread_id: int


class AcquisitionCoordinator:
    """Fail-closed SCPI command policy while a capture owns the mainframe."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._owners: dict[str, AcquisitionOwner] = {}

    def owner(self, alias: str) -> AcquisitionOwner | None:
        with self._lock:
            return self._owners.get(alias)

    def begin(self, alias: str, channel: int) -> None:
        with self._lock:
            owner = self._owners.get(alias)
            desired = AcquisitionOwner(channel, threading.get_ident())
            if owner is not None and owner != desired:
                raise DriverResourceBusyError(
                    f"digitizer on {alias!r} is owned by channel {owner.channel} in another acquisition"
                )
            self._owners[alias] = desired

    def release(self, alias: str, channel: int) -> None:
        with self._lock:
            owner = self._owners.get(alias)
            if owner is None:
                return
            if owner != AcquisitionOwner(channel, threading.get_ident()):
                raise DriverResourceConflictError(
                    "only the originating capture owner may release acquisition ownership"
                )
            del self._owners[alias]

    def invalidate_terminal_session(self, alias: str) -> None:
        """Drop only this alias's bookkeeping after independently proven terminal I/O fault.

        This does NOT send ABORt, establish transport recovery, prove an idle
        acquisition, or verify any output safe-OFF state. The driver must gate
        all subsequent I/O until a new verified session is connected.
        """
        with self._lock:
            self._owners.pop(alias, None)

    def require_idle(self, alias: str, operation: str) -> None:
        with self._lock:
            if alias in self._owners:
                raise DriverResourceBusyError(
                    f"{operation} is blocked while a digitizer acquisition is reserved on {alias}"
                )

    @staticmethod
    def _is_output_off(cmd: str) -> bool:
        return bool(re.match(r"^(OUTP(?:UT)?)(?:\s|:STAT(?:E)?\s)(?:OFF|0)(?:\s*,|$)", cmd))

    @staticmethod
    def _is_emergency_abort(cmd: str) -> bool:
        # Only the source-transient abort is safety-relevant; ABOR:ACQ from a
        # non-owner thread would silently cancel another caller's capture.
        # ABOR:TRAN does not stop continuous initiation, so turning that off
        # is part of the same abort.
        return bool(
            re.match(r"^ABOR(?:T)?:TRAN(?:SIENT)?(?:\s|$)", cmd)
            or re.match(
                r"^INIT(?:IATE)?:CONT(?:INUOUS)?:TRAN(?:SIENT)?\s(?:OFF|0)(?:\s*,|$)", cmd
            )
        )

    @staticmethod
    def _is_safe_owner_command(cmd: str) -> bool:
        # Explicitly allow only commands used by the existing capture API,
        # status/error inspection, and deliberately commanded source/load
        # setpoints; all unknown raw SCPI is rejected until ownership ends.
        allowed = (
            "SENS:FUNC:", "SENS:SWE:", "TRIG:ACQ:", "TRIG:ACQ ",
            "INIT:ACQ", "ABOR:ACQ", "FETC:ARR:", "STAT:OPER:COND?",
            "STAT:OPER:EVEN?", "STAT:OPER:PTR",
            "SYST:ERR?", "SYST:CHAN:COUN?", "FORM?", "FORM ", "FORM:BORD?", "FORM:BORD ",
            "CURR ", "VOLT ", "RES ", "POW ", "FUNC ",
            "LIST:", "ARB:", "TRIG:TRAN", "INIT:TRAN", "INIT:CONT:TRAN", "ABOR:TRAN",
            "OUTP ", "OUTP:", "OUTPUT ",
        )
        return cmd.startswith(allowed)

    @contextmanager
    def command(self, alias: str, command: str) -> Iterator[None]:
        """Atomic lease check versus reservation and subsequent command I/O."""
        with self._lock:
            owner = self._owners.get(alias)
            if owner is not None:
                normalized = " ".join(command.strip().upper().split())
                if self._is_output_off(normalized) or self._is_emergency_abort(normalized):
                    pass  # Emergency output OFF must never be blocked.
                elif owner.thread_id != threading.get_ident():
                    if not normalized.startswith("STAT:OPER:COND?"):
                        raise DriverResourceBusyError(
                            f"digitizer acquisition owns {alias}; conflicting SCPI command rejected"
                        )
                elif not self._is_safe_owner_command(normalized):
                    raise DriverResourceConflictError(
                        f"SCPI command would risk invalidating channel {owner.channel} digitizer data"
                    )
        # The driver holds its alias operation lock across SCPI I/O. Do not
        # retain the coordinator lock while transport callbacks can block: a
        # timeout must not stop another thread's emergency-OFF lease check.
        yield
