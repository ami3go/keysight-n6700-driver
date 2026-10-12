"""Local LPDS-003-style instrument/session foundation for the N6700 driver."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, TypeVar

from scpi_driver_core.exceptions import ScpiDriverError
from scpi_driver_core.scpi.client import ScpiClient
from scpi_driver_core.session import ScpiSession, SessionRegistry, normalize_alias
from scpi_driver_core.tracing.observer import Tracer
from scpi_driver_core.transport.base import Transport

from ._translate import translated
from .exceptions import (
    RECONNECT_REQUIRED,
    DriverConnectionError,
    DriverError,
    DriverInternalError,
    DriverPreconditionError,
    DriverStateError,
)

__all__ = [
    "SUPPORT_STATE_PROJECTION",
    "BaseInstrument",
    "ConnectionInfo",
    "DriverMetadata",
    "SessionState",
]

_T = TypeVar("_T")
_log = logging.getLogger(__name__)


class SessionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    CONFIGURED = "configured"
    BUSY = "busy"
    WAITING = "waiting"
    RECOVERING = "recovering"
    ERROR = "error"
    CLOSING = "closing"


SUPPORT_STATE_PROJECTION: Mapping[SessionState, str] = {
    SessionState.DISCONNECTED: "disconnected",
    SessionState.CONNECTING: "connecting",
    SessionState.CONNECTED: "connected",
    SessionState.CONFIGURED: "connected",
    SessionState.BUSY: "connected",
    SessionState.WAITING: "connected",
    SessionState.RECOVERING: "recovering",
    SessionState.ERROR: "faulted",
    SessionState.CLOSING: "disconnected",
}

_LEGAL_TRANSITIONS: Mapping[SessionState, frozenset[SessionState]] = {
    SessionState.DISCONNECTED: frozenset({SessionState.CONNECTING}),
    SessionState.CONNECTING: frozenset(
        {SessionState.CONNECTED, SessionState.ERROR, SessionState.DISCONNECTED, SessionState.CLOSING}
    ),
    SessionState.CONNECTED: frozenset(
        {
            SessionState.CONFIGURED,
            SessionState.BUSY,
            SessionState.WAITING,
            SessionState.RECOVERING,
            SessionState.CLOSING,
            SessionState.ERROR,
        }
    ),
    SessionState.CONFIGURED: frozenset(
        {
            SessionState.BUSY,
            SessionState.WAITING,
            SessionState.RECOVERING,
            SessionState.CLOSING,
            SessionState.ERROR,
        }
    ),
    SessionState.BUSY: frozenset(
        {
            SessionState.CONNECTED,
            SessionState.CONFIGURED,
            SessionState.ERROR,
            SessionState.RECOVERING,
            SessionState.CLOSING,
        }
    ),
    SessionState.WAITING: frozenset(
        {
            SessionState.CONNECTED,
            SessionState.CONFIGURED,
            SessionState.ERROR,
            SessionState.RECOVERING,
            SessionState.CLOSING,
        }
    ),
    SessionState.RECOVERING: frozenset(
        {SessionState.CONNECTED, SessionState.ERROR, SessionState.CLOSING}
    ),
    SessionState.ERROR: frozenset(
        {SessionState.CONNECTED, SessionState.CONFIGURED, SessionState.RECOVERING, SessionState.CLOSING}
    ),
    SessionState.CLOSING: frozenset({SessionState.DISCONNECTED}),
}


@dataclass(frozen=True)
class DriverMetadata:
    driver_name: str
    display_name: str
    version: str
    package_name: str
    manufacturer: str
    supported_models: tuple[str, ...]
    supported_transport_profiles: tuple[str, ...]


@dataclass
class ConnectionInfo:
    alias: str
    resource: str
    connected: bool
    communication_ok: bool | None
    transport_kind: str | None
    identity: Mapping[str, str] | None
    timeout_s: float | None
    state: SessionState


@dataclass
class _AliasEntry:
    session: ScpiSession
    resource: str
    state: SessionState = SessionState.DISCONNECTED
    io_timeout_s: float | None = None
    depth: int = 0
    lock: threading.RLock = field(default_factory=threading.RLock)


class BaseInstrument:
    """Connection lifecycle, state machine, locking, and operation execution."""

    def __init__(self, metadata: DriverMetadata) -> None:
        self._metadata = metadata
        self._registry = SessionRegistry()
        self._aliases: dict[str, _AliasEntry] = {}
        self._registry_lock = threading.RLock()
        super().__init__()

    @property
    def metadata(self) -> DriverMetadata:
        return self._metadata

    def _build_transport(
        self, *, alias: str, resource: str, connection_type: str, **options: Any
    ) -> Transport:
        raise NotImplementedError

    def _get_tracer(self, alias: str) -> Tracer | None:
        return None

    def _validate_identity(self, identity: Mapping[str, str]) -> None:
        pass

    def _on_connected(self, alias: str, session: ScpiSession) -> None:
        pass

    def _apply_safe_state(self, alias: str, session: ScpiSession, *, reason: str) -> None:
        pass

    def _on_disconnected(self, alias: str) -> None:
        pass

    def _key(self, alias: str) -> str:
        with translated(operation="alias"):
            return normalize_alias(alias)

    def _set_state(self, alias: str, new_state: SessionState) -> None:
        entry = self._aliases[alias]
        current = entry.state
        if new_state not in _LEGAL_TRANSITIONS.get(current, frozenset()) and new_state is not current:
            raise DriverStateError(
                f"illegal transition {current.value} -> {new_state.value} for alias {alias!r}",
                code="LPDS-STA-001",
            )
        entry.state = new_state

    def _connect_session(
        self,
        *,
        alias: str,
        resource: str,
        connection_type: str,
        replace: bool,
        probe: bool,
        timeout_s: float | None,
        **options: Any,
    ) -> ScpiSession:
        key = self._key(alias)
        with self._registry_lock:
            if key in self._aliases and not replace:
                raise DriverConnectionError(
                    f"alias {key!r} is already connected; pass replace=True to reconnect",
                    code="LPDS-CON-001",
                )
            if key in self._aliases:
                self._disconnect_session(key, raise_on_error=False)

            transport = self._build_transport(
                alias=key, resource=resource, connection_type=connection_type, **options
            )
            with translated(operation="connect"):
                client = ScpiClient(transport, timeout_s=timeout_s)
                session = ScpiSession(
                    key,
                    client,
                    communication_timeout_s=timeout_s,
                    tracer=self._get_tracer(key),
                )
            entry = _AliasEntry(
                session=session,
                resource=resource,
                io_timeout_s=timeout_s,
            )
            self._aliases[key] = entry
            self._set_state(key, SessionState.CONNECTING)

            try:
                with translated(operation="connect"):
                    session.open(probe=probe, validate_identity=self._validate_from_core)
                    self._registry.register(key, session, replace=True)
                self._set_state(key, SessionState.CONNECTED)
                self._on_connected(key, session)
                return session
            except BaseException:
                with suppress(Exception):
                    session.close()
                with suppress(Exception):
                    if key in self._registry:
                        self._registry.remove(key)
                self._aliases.pop(key, None)
                with suppress(Exception):
                    self._on_disconnected(key)
                raise

    def _validate_from_core(self, identity: Any) -> None:
        self._validate_identity(
            {
                "manufacturer": identity.manufacturer,
                "model": identity.model,
                "serial_number": identity.serial_number or "",
                "firmware_version": identity.firmware_version or "",
            }
        )

    def _disconnect_session(self, alias: str, *, raise_on_error: bool) -> None:
        key = self._key(alias)
        entry = self._aliases.get(key)
        if entry is None:
            if raise_on_error:
                raise DriverPreconditionError(
                    f"no session is connected as alias {key!r}", code="LPDS-STA-002"
                )
            return

        failure: BaseException | None = None
        with entry.lock:
            if entry.session.is_connected:
                try:
                    self._apply_safe_state(key, entry.session, reason="disconnect")
                except BaseException as exc:
                    failure = exc
                    _log.error("safe state NOT confirmed for alias %r: %s", key, exc)

            with suppress(DriverStateError):
                self._set_state(key, SessionState.CLOSING)
            try:
                with translated(operation="disconnect"):
                    entry.session.close()
            except BaseException as exc:
                if failure is None:
                    failure = exc
                _log.error("transport close failed for alias %r: %s", key, exc)
            finally:
                self._aliases.pop(key, None)
                with self._registry_lock, suppress(Exception):
                    if key in self._registry:
                        self._registry.remove(key)
                with suppress(Exception):
                    self._on_disconnected(key)

        if failure is not None and raise_on_error:
            if isinstance(failure, Exception):
                raise failure
            raise DriverInternalError(
                f"disconnect failed with {type(failure).__name__}", code="LPDS-INT-003"
            )

    def _resolve_alias(self, alias: str | None) -> str:
        if alias is not None:
            return self._key(alias)
        active = self._registry.active_alias
        if active is None:
            raise DriverPreconditionError(
                "no N6700 session is connected; call connect() first", code="LPDS-STA-003"
            )
        return active

    def _entry(self, alias: str | None) -> _AliasEntry:
        key = self._resolve_alias(alias)
        try:
            return self._aliases[key]
        except KeyError as exc:
            raise DriverPreconditionError(
                f"unknown alias {key!r}; known aliases: {sorted(self._aliases)}",
                code="LPDS-STA-004",
            ) from exc

    def _session(self, alias: str | None) -> ScpiSession:
        return self._entry(alias).session

    def _execute_operation(
        self,
        operation_name: str,
        action: Callable[[ScpiSession], _T],
        *,
        alias: str | None = None,
        timeout_s: float | None = None,
        required_states: frozenset[SessionState] = frozenset(
            {SessionState.CONNECTED, SessionState.CONFIGURED}
        ),
    ) -> _T:
        del timeout_s
        key = self._resolve_alias(alias)
        entry = self._entry(key)
        with entry.lock:
            if entry.depth:
                try:
                    with translated(operation=operation_name):
                        return action(entry.session)
                except DriverError:
                    raise
                except Exception as exc:
                    raise DriverInternalError(
                        f"unexpected {type(exc).__name__}: {exc}",
                        code="LPDS-INT-002",
                        operation=operation_name,
                    ) from exc

            if entry.state not in required_states:
                raise DriverPreconditionError(
                    f"{operation_name} requires state in "
                    f"{sorted(state.value for state in required_states)}, was {entry.state.value}",
                    code="LPDS-STA-005",
                    operation=operation_name,
                )

            previous = entry.state
            self._set_state(key, SessionState.BUSY)
            entry.depth += 1
            try:
                with translated(operation=operation_name):
                    return action(entry.session)
            except DriverError as exc:
                if not entry.session.is_connected:
                    exc.retryable = False
                    exc.recovery_action = RECONNECT_REQUIRED
                    exc.details.setdefault("transport_state", entry.session.transport.state.name)
                    if entry.state is not SessionState.ERROR:
                        self._set_state(key, SessionState.ERROR)
                else:
                    self._set_state(key, SessionState.ERROR)
                    self._set_state(
                        key,
                        previous if previous is not SessionState.BUSY else SessionState.CONNECTED,
                    )
                raise
            except Exception as exc:
                self._set_state(key, SessionState.ERROR)
                if entry.session.is_connected:
                    self._set_state(
                        key,
                        previous if previous is not SessionState.BUSY else SessionState.CONNECTED,
                    )
                raise DriverInternalError(
                    f"unexpected {type(exc).__name__}: {exc}",
                    code="LPDS-INT-002",
                    operation=operation_name,
                ) from exc
            finally:
                entry.depth -= 1
                if entry.state is SessionState.BUSY:
                    self._set_state(
                        key,
                        previous if previous is not SessionState.BUSY else SessionState.CONNECTED,
                    )

    def is_connected(self, alias: str | None = None) -> bool:
        try:
            entry = self._entry(alias)
        except DriverError:
            return False
        return entry.session.is_connected

    def _connection_info(self, alias: str | None = None, refresh: bool = False) -> ConnectionInfo:
        key = self._resolve_alias(alias)
        entry = self._entry(key)
        with entry.lock:
            identity: Mapping[str, str] | None = None
            communication_ok: bool | None = None
            if refresh and entry.session.is_connected:
                communication_ok = self._execute_operation(
                    "check_communication",
                    lambda session: session.check_communication(),
                    alias=key,
                    required_states=frozenset(
                        {
                            SessionState.CONNECTED,
                            SessionState.CONFIGURED,
                            SessionState.BUSY,
                            SessionState.WAITING,
                        }
                    ),
                )
            if entry.session.is_connected:
                try:
                    current = entry.session.get_identity(refresh=False)
                    identity = {
                        "manufacturer": current.manufacturer,
                        "model": current.model,
                        "serial_number": current.serial_number or "",
                        "firmware_version": current.firmware_version or "",
                    }
                except ScpiDriverError:
                    identity = None
            return ConnectionInfo(
                alias=key,
                resource=entry.resource,
                connected=entry.session.is_connected,
                communication_ok=communication_ok,
                transport_kind=(
                    entry.session.transport.descriptor.kind if entry.session.is_connected else None
                ),
                identity=identity,
                timeout_s=entry.io_timeout_s,
                state=entry.state,
            )

    def check_communication(self, alias: str | None = None) -> bool:
        return self._execute_operation(
            "check_communication",
            lambda session: session.check_communication(),
            alias=alias,
            required_states=frozenset(
                {
                    SessionState.CONNECTED,
                    SessionState.CONFIGURED,
                    SessionState.BUSY,
                    SessionState.WAITING,
                }
            ),
        )

    def set_communication_timeout(self, timeout_s: float, alias: str | None = None) -> float:
        key = self._resolve_alias(alias)
        entry = self._entry(key)
        with entry.lock, translated(operation="set_communication_timeout"):
            entry.session.set_communication_timeout(timeout_s)
            entry.io_timeout_s = timeout_s
        return timeout_s

    def get_communication_timeout(self, alias: str | None = None) -> float | None:
        return self._entry(alias).io_timeout_s

    def _io_timeout(self, alias: str | None = None) -> float | None:
        return self._entry(alias).io_timeout_s

    def _reconnect_info(self, alias: str | None = None) -> ConnectionInfo:
        key = self._resolve_alias(alias)
        entry = self._entry(key)
        with entry.lock:
            self._set_state(key, SessionState.RECOVERING)
            try:
                with translated(operation="reconnect"):
                    with suppress(Exception):
                        entry.session.close()
                    entry.session.open(probe=False, validate_identity=self._validate_from_core)
                self._set_state(key, SessionState.CONNECTED)
            except DriverError:
                self._set_state(key, SessionState.ERROR)
                raise
        return self._connection_info(key)

    def get_driver_information(self) -> dict[str, Any]:
        return {
            "driver_name": self._metadata.driver_name,
            "display_name": self._metadata.display_name,
            "version": self._metadata.version,
            "package_name": self._metadata.package_name,
            "manufacturer": self._metadata.manufacturer,
            "supported_models": list(self._metadata.supported_models),
            "supported_transport_profiles": list(self._metadata.supported_transport_profiles),
        }

    def list_connected_aliases(self) -> list[str]:
        with self._registry_lock:
            return sorted(self._aliases)

    def export_diagnostics(self) -> dict[str, Any]:
        with self._registry_lock:
            snapshot = dict(self._aliases)
        return {
            "schema": "keysight_n6700.diagnostics",
            "schema_version": "1.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "driver": self.get_driver_information(),
            "sessions": {
                alias: {
                    "resource": entry.resource,
                    "state": entry.state.value,
                    "connected": entry.session.is_connected,
                    "generation": entry.session.generation,
                }
                for alias, entry in snapshot.items()
            },
        }
