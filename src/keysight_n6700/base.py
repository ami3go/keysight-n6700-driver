"""A local BaseInstrument-shaped foundation, per LPDS-003.

LPDS-003 describes a shared ``lpds-core`` package providing this layer
(session registry, 9-state connection state machine, an operation-execution
wrapper, extension hooks, diagnostics export) as a "should", while LPDS-005
§8.4 states a driver "shall" build on one. No such shared package exists yet
for this ecosystem — only the transport/SCPI-client layer
(``scpi-driver-core``, which satisfies LPDS-004) does. Rather than block on a
package that doesn't exist, or silently skip the requirement, this module
implements the LPDS-003 contract locally, composing ``scpi-driver-core``'s
``ScpiSession``/``SessionRegistry``/``ScpiClient`` underneath it. See
``review/known_risks.md`` for the tradeoff this records.

Nothing instrument-specific lives here: no SCPI command text, no channel
model, no N6700 semantics. :class:`keysight_n6700.driver.N6700` is the only
subclass.
"""

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
from scpi_driver_core.session import ScpiSession, SessionRegistry
from scpi_driver_core.tracing.observer import Tracer
from scpi_driver_core.transport.base import Transport

from ._translate import translated
from .exceptions import (
    DriverConnectionError,
    DriverError,
    DriverPreconditionError,
    DriverStateError,
)

__all__ = [
    "SUPPORT_STATE_PROJECTION",
    "ConnectionInfo",
    "DriverMetadata",
    "SessionState",
]

_T = TypeVar("_T")


class SessionState(Enum):
    """The canonical LPDS-003 §13.1 connection state machine."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    CONFIGURED = "configured"
    BUSY = "busy"
    WAITING = "waiting"
    RECOVERING = "recovering"
    ERROR = "error"
    CLOSING = "closing"


#: LPDS-002 §12's reduced public-facing projection of the 9-state machine.
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

#: Legal transitions, LPDS-003 §13.3 (informative subset this driver drives).
_LEGAL_TRANSITIONS: Mapping[SessionState, frozenset[SessionState]] = {
    SessionState.DISCONNECTED: frozenset({SessionState.CONNECTING}),
    SessionState.CONNECTING: frozenset(
        {SessionState.CONNECTED, SessionState.ERROR, SessionState.DISCONNECTED}
    ),
    SessionState.CONNECTED: frozenset(
        {
            SessionState.CONFIGURED,
            SessionState.BUSY,
            SessionState.WAITING,
            SessionState.CLOSING,
            SessionState.ERROR,
        }
    ),
    SessionState.CONFIGURED: frozenset(
        {SessionState.BUSY, SessionState.WAITING, SessionState.CLOSING, SessionState.ERROR}
    ),
    SessionState.BUSY: frozenset(
        {SessionState.CONNECTED, SessionState.CONFIGURED, SessionState.ERROR, SessionState.RECOVERING}
    ),
    SessionState.WAITING: frozenset(
        {SessionState.CONNECTED, SessionState.CONFIGURED, SessionState.ERROR, SessionState.RECOVERING}
    ),
    SessionState.RECOVERING: frozenset(
        {SessionState.CONNECTED, SessionState.ERROR, SessionState.CLOSING}
    ),
    # A single failed operation reverts straight to the prior good state
    # rather than forcing every caller through RECOVERING; RECOVERING is for
    # a deliberate multi-step recovery hook, which this driver does not yet
    # implement (see review/known_risks.md).
    SessionState.ERROR: frozenset(
        {SessionState.CONNECTED, SessionState.CONFIGURED, SessionState.RECOVERING, SessionState.CLOSING}
    ),
    SessionState.CLOSING: frozenset({SessionState.DISCONNECTED}),
}


@dataclass(frozen=True)
class DriverMetadata:
    """Immutable identity of this driver, LPDS-003 §11."""

    driver_name: str
    display_name: str
    version: str
    package_name: str
    manufacturer: str
    supported_models: tuple[str, ...]
    supported_transport_profiles: tuple[str, ...]


@dataclass
class ConnectionInfo:
    """What :meth:`BaseInstrument.get_connection_state` reports for one alias."""

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
    lock: threading.RLock = field(default_factory=threading.RLock)


class BaseInstrument:
    """LPDS-003 connection lifecycle, state machine, and operation execution.

    A concrete driver overrides the protected hooks below; it never touches
    :class:`SessionRegistry` or :class:`SessionState` directly.
    """

    def __init__(self, metadata: DriverMetadata) -> None:
        self._metadata = metadata
        self._registry = SessionRegistry()
        self._aliases: dict[str, _AliasEntry] = {}
        self._registry_lock = threading.RLock()
        super().__init__()

    # -- metadata -----------------------------------------------------------

    @property
    def metadata(self) -> DriverMetadata:
        return self._metadata

    # -- hooks a concrete driver overrides -----------------------------------

    def _build_transport(
        self, *, alias: str, resource: str, connection_type: str, **options: Any
    ) -> Transport:
        raise NotImplementedError

    def _get_tracer(self, alias: str) -> Tracer | None:
        """Return the protocol tracer for ``alias``, if one was set up. None by default.

        Called after :meth:`_build_transport`, so a driver that builds a
        tracer there (typically wrapping the transport in
        ``InstrumentedTransport``) can hand the same instance back here for
        :class:`ScpiSession` to propagate trace context into.
        """
        return None

    def _validate_identity(self, identity: Mapping[str, str]) -> None:
        """Reject an unacceptable ``*IDN?`` reply. No-op by default."""

    def _on_connected(self, alias: str, session: ScpiSession) -> None:
        """Run right after a session reaches CONNECTED. No-op by default."""

    def _apply_safe_state(self, alias: str, session: ScpiSession, *, reason: str) -> None:
        """Best-effort safe-state before disconnect. No-op by default."""

    def _on_disconnected(self, alias: str) -> None:
        """Run after ``alias``'s session has closed. No-op by default.

        For a driver that opened per-alias resources in :meth:`_build_transport`
        (a trace sink, for example), this is where to release them.
        """

    # -- session lifecycle ----------------------------------------------------

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
        with self._registry_lock:
            if alias in self._aliases and not replace:
                raise DriverConnectionError(
                    f"alias {alias!r} is already connected; pass replace=True to reconnect",
                    code="LPDS-CON-001",
                )
            if alias in self._aliases:
                self._disconnect_session(alias, raise_on_error=False)

            transport = self._build_transport(
                alias=alias, resource=resource, connection_type=connection_type, **options
            )
            client = ScpiClient(transport, timeout_s=timeout_s)
            session = ScpiSession(
                alias, client, communication_timeout_s=timeout_s, tracer=self._get_tracer(alias)
            )
            entry = _AliasEntry(session=session, resource=resource)
            self._aliases[alias] = entry
            self._set_state(alias, SessionState.CONNECTING)

            try:
                with translated(operation="connect"):
                    session.open(probe=probe, validate_identity=self._validate_from_core)
            except DriverError:
                self._set_state(alias, SessionState.ERROR)
                self._set_state(alias, SessionState.CLOSING)
                del self._aliases[alias]
                self._on_disconnected(alias)
                raise

            self._registry.register(alias, session, replace=True)
            self._set_state(alias, SessionState.CONNECTED)
            self._on_connected(alias, session)
            return session

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
        entry = self._aliases.get(alias)
        if entry is None:
            if raise_on_error:
                raise DriverPreconditionError(
                    f"no session is connected as alias {alias!r}", code="LPDS-STA-002"
                )
            return
        with suppress(DriverStateError):
            self._set_state(alias, SessionState.CLOSING)
        try:
            self._apply_safe_state(alias, entry.session, reason="disconnect")
        except Exception:
            logging.getLogger(__name__).warning(
                "safe-state hook failed for alias %r during disconnect", alias, exc_info=True
            )
        try:
            entry.session.close()
        finally:
            self._aliases.pop(alias, None)
            with self._registry_lock:
                if alias in self._registry:
                    self._registry.remove(alias)
            self._on_disconnected(alias)

    def _resolve_alias(self, alias: str | None) -> str:
        if alias is not None:
            return alias
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

    # -- the operation-execution wrapper, LPDS-003 §17.1 -----------------------

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
        entry = self._entry(alias)
        key = self._resolve_alias(alias)
        with entry.lock:
            if entry.state not in required_states:
                raise DriverPreconditionError(
                    f"{operation_name} requires state in "
                    f"{sorted(s.value for s in required_states)}, was {entry.state.value}",
                    code="LPDS-STA-005",
                    operation=operation_name,
                )
            previous = entry.state
            self._set_state(key, SessionState.BUSY)
            try:
                with translated(operation=operation_name):
                    return action(entry.session)
            except DriverError:
                self._set_state(key, SessionState.ERROR)
                self._set_state(key, previous if previous != SessionState.BUSY else SessionState.CONNECTED)
                raise
            finally:
                if entry.state is SessionState.BUSY:
                    self._set_state(key, previous if previous != SessionState.BUSY else SessionState.CONNECTED)

    # -- LPDS-002 mandatory universal methods, implemented once here ---------

    def is_connected(self, alias: str | None = None) -> bool:
        try:
            entry = self._entry(alias)
        except DriverError:
            return False
        return entry.session.is_connected

    def _connection_info(self, alias: str | None = None, refresh: bool = False) -> ConnectionInfo:
        """Typed connection info. A concrete driver's public ``get_connection_state``
        (LPDS-002 §12) converts this to a plain dict; kept separate so this
        base class is not pinned to that exact public return schema.
        """
        entry = self._entry(alias)
        key = self._resolve_alias(alias)
        identity: Mapping[str, str] | None = None
        communication_ok: bool | None = None
        if refresh:
            communication_ok = entry.session.check_communication()
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
            transport_kind=entry.session.transport.descriptor.kind if entry.session.is_connected else None,
            identity=identity,
            timeout_s=entry.session.communication_timeout_s,
            state=entry.state,
        )

    def check_communication(self, alias: str | None = None) -> bool:
        return self._session(alias).check_communication()

    def set_communication_timeout(self, timeout_s: float, alias: str | None = None) -> float:
        with translated(operation="set_communication_timeout"):
            self._session(alias).set_communication_timeout(timeout_s)
        return timeout_s

    def get_communication_timeout(self, alias: str | None = None) -> float | None:
        return self._session(alias).communication_timeout_s

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
        return sorted(self._aliases)

    # -- diagnostics export, LPDS-003 §29.2 (software-verifiable subset) -----

    def export_diagnostics(self) -> dict[str, Any]:
        """A JSON-serializable diagnostics snapshot. See LPDS-003 §29.2.

        This intentionally ships the subset that needs no hardware and no
        result-directory infrastructure: summary, driver metadata, and
        per-session state. The full evidence-bundle layout LPDS-008 describes
        for a *test run* (environment.json, events.jsonl, ...) is produced by
        whatever harness runs the tests, not by the driver itself.
        """
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
                for alias, entry in self._aliases.items()
            },
        }
