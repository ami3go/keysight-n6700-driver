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
    def _is_safe_owner_command(cmd: str) -> bool:
        # Explicitly allow only commands used by the existing capture API,
        # status/error inspection, and deliberately commanded source/load
        # setpoints; all unknown raw SCPI is rejected until ownership ends.
        allowed = (
            "SENS:FUNC:", "SENS:SWE:", "TRIG:ACQ:", "TRIG:ACQ ",
            "INIT:ACQ", "ABOR:ACQ", "FETC:ARR:", "STAT:OPER:COND?",
            "SYST:ERR?", "CURR ", "VOLT ", "RES ", "POW ", "FUNC ",
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
                if self._is_output_off(normalized):
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
            yield
