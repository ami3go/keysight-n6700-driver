"""The N6700 driver: a plain, framework-independent Python class.

Composes :class:`keysight_n6700.base.BaseInstrument` (LPDS-003 connection
lifecycle) with N6700-specific channel/module semantics. Every method that
touches the instrument goes through :meth:`write_scpi`/:meth:`query_scpi`,
which are the only two places that call into the ``scpi_driver_core``
``ScpiSession``/``ScpiClient`` layer.

Supports several independently addressed sessions (mainframes) per instance,
each identified by ``alias`` — see :mod:`keysight_n6700.base`. Most methods
default ``alias=None``, meaning "the currently active session."
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from scpi_driver_core.execution.guards import ConfirmationGuard
from scpi_driver_core.execution.polling import poll_until
from scpi_driver_core.execution.retry import RetryPolicy
from scpi_driver_core.session import ScpiSession
from scpi_driver_core.tracing.instrumented import InstrumentedTransport
from scpi_driver_core.tracing.jsonl import JsonlTraceSink
from scpi_driver_core.tracing.observer import TraceObserver, Tracer
from scpi_driver_core.tracing.redaction import Redactor
from scpi_driver_core.transport.base import Transport
from scpi_driver_core.transport.models import ReplayPolicy
from scpi_driver_core.transport.tcp import TcpTransport
from scpi_driver_core.transport.visa import VisaTransport

from ._translate import translated
from .base import (
    SUPPORT_STATE_PROJECTION,
    BaseInstrument,
    ConnectionInfo,
    DriverMetadata,
    SessionState,
)
from .capability_model import CapabilityDiscoveryMixin, capability
from .channel import BaseChannel, ElectronicLoadChannel, PowerSupplyChannel, SMUChannel
from .configuration import ConfigurationMixin
from .exceptions import (
    DriverArgumentValueError,
    DriverCommandRejectedError,
    DriverConfigurationError,
    DriverPreconditionError,
    DriverUnsupportedOperationError,
)
from .module_capabilities import ChannelCapabilities, classify_module
from .scpi import SUPPORTED_MANUFACTURERS, format_channel_list, parse_error
from .simulator import build_simulated_transport
from .types import (
    AuditRecord,
    InstrumentIdentity,
    Measurement,
    OperationStatus,
    PowerMeasurement,
    ProtectionClearResult,
    QuestionableStatus,
    RemoteState,
    ScpiErrorRecord,
    SelfTestResult,
    ShutdownChannelResult,
    ShutdownResult,
)

_CONNECTED_STATES = frozenset(
    {SessionState.CONNECTED, SessionState.CONFIGURED, SessionState.BUSY, SessionState.WAITING}
)

_DRIVER_METADATA = DriverMetadata(
    driver_name="keysight_n6700",
    display_name="Keysight/Agilent N6700 modular power system driver",
    version="0.3.1",
    package_name="keysight-n6700-driver",
    manufacturer="Keysight Technologies",
    supported_models=("N67xx series mainframe", "N673x/N674x/N675x/N676x/N677x/N679x", "N678xA"),
    supported_transport_profiles=("visa", "usb", "ethernet_socket", "simulated"),
)


@dataclass
class _AliasDiscovery:
    channel_count: int = 0
    capabilities: dict[int, ChannelCapabilities] = field(default_factory=dict)
    channels: dict[int, BaseChannel] = field(default_factory=dict)


class _AliasFacade:
    """Binds the ``_DriverProtocol`` :mod:`keysight_n6700.channel` expects to one alias."""

    def __init__(self, driver: N6700, alias: str) -> None:
        self._driver = driver
        self._alias = alias

    def query_scpi(self, command: str) -> str:
        return self._driver.query_scpi(command, alias=self._alias)

    def write_scpi(self, command: str) -> None:
        self._driver.write_scpi(command, alias=self._alias)

    def _measure_power(self, channel: int) -> PowerMeasurement:
        return self._driver._measure_power(channel, alias=self._alias)

    def _measure_channel(self, channel: int) -> Measurement:
        return self._driver._measure_channel(channel, alias=self._alias)

    def drain_errors(self) -> list[ScpiErrorRecord]:
        return self._driver.drain_errors(alias=self._alias)

    def check_errors(self) -> None:
        self._driver.check_errors(alias=self._alias)


def _connection_info_dict(info: ConnectionInfo) -> dict[str, Any]:
    return {
        "alias": info.alias,
        "resource": info.resource,
        "connected": info.connected,
        "communication_ok": info.communication_ok,
        "transport": info.transport_kind,
        "identity": dict(info.identity) if info.identity else None,
        "timeout_s": info.timeout_s,
        "state": SUPPORT_STATE_PROJECTION[info.state],
    }


class N6700(BaseInstrument, CapabilityDiscoveryMixin, ConfigurationMixin):
    """Driver for an N6700-family modular power system.

    Connection is non-invasive by default: no reset, no output changes, no
    protection clear, no nonvolatile writes, unless explicitly requested.
    """

    def __init__(self, audit_log_path: str | Path | None = None) -> None:
        super().__init__(_DRIVER_METADATA)
        self._discovery: dict[str, _AliasDiscovery] = {}
        self.audit_log_path = Path(audit_log_path) if audit_log_path else None
        self._auto_shutdown_on_disconnect = True
        self._tracers: dict[str, tuple[Tracer, JsonlTraceSink | None]] = {}
        self._raw_scpi_guard: ConfirmationGuard | None = None

    @capability(
        "safety.raw_scpi.guard",
        category="safety",
        risk_level="low",
        display_name="Set Raw SCPI Guard",
        description="Require a confirmation phrase before write_scpi()/query_scpi() will run.",
    )
    def set_raw_scpi_guard(self, phrase: str) -> None:
        """Guard raw SCPI behind a confirmation phrase (scpi_driver_core's ``ConfirmationGuard``).

        Off by default, matching ``write_scpi``/``query_scpi``'s documented
        safe default of bypassing typed safeguards freely. Call this once
        during setup to make that bypass deliberate; enable it per call site
        with :meth:`enable_raw_scpi`.
        """
        self._raw_scpi_guard = ConfirmationGuard(phrase, name="raw SCPI")

    def enable_raw_scpi(self, phrase: str) -> None:
        """Unlock a previously set raw-SCPI guard for subsequent calls.

        Raises:
            DriverConfigurationError: if no guard was set.
            DriverUnsafeOperationError: if ``phrase`` does not match.
        """
        if self._raw_scpi_guard is None:
            raise DriverConfigurationError(
                "no raw SCPI guard is set; call set_raw_scpi_guard() first", code="LPDS-CFG-003"
            )
        with translated(operation="enable_raw_scpi"):
            self._raw_scpi_guard.enable(phrase)

    def disable_raw_scpi(self) -> None:
        """Re-lock a previously set raw-SCPI guard. No-op if none is set."""
        if self._raw_scpi_guard is not None:
            self._raw_scpi_guard.disable()

    def set_auto_shutdown_on_disconnect(self, enabled: bool) -> None:
        """Whether :meth:`disconnect`/:meth:`disconnect_all` attempt a safe
        shutdown first. See ``settings.safety.auto_shutdown_on_disconnect`` in
        the LPDS-014 configuration model. Defaults to ``True``; set to
        ``False`` only when another safety controller owns output state.
        """
        self._auto_shutdown_on_disconnect = bool(enabled)

    # -- construction shortcuts (kept for one-shot single-session use) -------

    @classmethod
    def connect_usb(cls, resource: str, **options: Any) -> N6700:
        driver = cls()
        driver.connect(resource, connection_type="visa", **options)
        return driver

    @classmethod
    def connect_visa(cls, resource: str, **options: Any) -> N6700:
        driver = cls()
        driver.connect(resource, connection_type="visa", **options)
        return driver

    @classmethod
    def connect_ethernet(cls, host: str, port: int = 5025, **options: Any) -> N6700:
        driver = cls()
        driver.connect(host, connection_type="ethernet", port=port, **options)
        return driver

    @classmethod
    def connect_simulated(cls, **options: Any) -> N6700:
        driver = cls()
        driver.connect(connection_type="simulated", **options)
        return driver

    def __enter__(self) -> N6700:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.disconnect()

    # -- LPDS-003 hooks --------------------------------------------------------

    def _build_transport(
        self, *, alias: str, resource: str, connection_type: str, **options: Any
    ) -> Transport:
        trace = options.pop("protocol_trace", None)
        redactor: Redactor | None = options.pop("protocol_trace_redactor", None)

        transport: Transport
        if connection_type in {"sim", "simulation", "simulated"}:
            transport = build_simulated_transport()
        elif connection_type in {"visa", "usb"}:
            if not resource:
                raise DriverArgumentValueError("resource must contain a VISA resource string")
            transport = VisaTransport(resource)
        elif connection_type in {"ethernet", "socket", "tcp", "tcpip"}:
            if not resource:
                raise DriverArgumentValueError("resource must contain an Ethernet host or IP address")
            port = int(options.get("port", 5025))
            transport = TcpTransport(resource, port)
        else:
            raise DriverArgumentValueError(
                "connection_type must be visa, usb, ethernet/socket, or simulated"
            )

        if trace is None:
            return transport

        observer: TraceObserver
        owned_sink: JsonlTraceSink | None
        if isinstance(trace, (str, Path)):
            owned_sink = JsonlTraceSink(trace)
            observer = owned_sink
        else:
            owned_sink = None
            observer = trace
        tracer = Tracer(observer, redactor=redactor)
        self._tracers[alias] = (tracer, owned_sink)
        return InstrumentedTransport(transport, tracer)

    def _get_tracer(self, alias: str) -> Tracer | None:
        entry = self._tracers.get(alias)
        return entry[0] if entry is not None else None

    def _on_disconnected(self, alias: str) -> None:
        entry = self._tracers.pop(alias, None)
        if entry is not None:
            _tracer, owned_sink = entry
            if owned_sink is not None:
                owned_sink.close()

    def _validate_identity(self, identity: Mapping[str, str]) -> None:
        manufacturer = " ".join(identity["manufacturer"].upper().split())
        if manufacturer not in SUPPORTED_MANUFACTURERS:
            raise DriverCommandRejectedError(
                f"unsupported manufacturer in *IDN? response: {identity['manufacturer']!r}",
                code="LPDS-DEV-005",
            )

    def _apply_safe_state(self, alias: str, session: Any, *, reason: str) -> None:
        if not self._auto_shutdown_on_disconnect:
            return
        with suppress(Exception):
            self.shutdown_all(alias=alias)

    # -- LPDS-002 mandatory universal methods --------------------------------

    @capability(
        "connection.session.open",
        category="connection",
        risk_level="low",
        mutating=True,
        description="Open a session to an N6700 mainframe over VISA, USB, Ethernet, or the simulator.",
        postconditions=("Session alias is registered and selected.", "Instrument identity has been queried."),
    )
    def connect(
        self,
        resource: str | None = None,
        alias: str = "default",
        timeout_s: float | None = None,
        **options: Any,
    ) -> dict[str, Any]:
        """Connect using ``visa``, ``usb``, ``ethernet``/``socket``, or ``simulated``.

        Args:
            resource: VISA resource string, hostname, or IP address. Ignored
                for ``connection_type="simulated"``.
            alias: named session. Defaults to ``"default"``.
            timeout_s: communication timeout for this session.
            connection_type: one of ``visa``, ``usb``, ``ethernet``/``socket``,
                ``simulated``. Defaults to ``visa``.
            port: TCP port for ``ethernet``. Defaults to 5025.
            discover: run module discovery after connecting. Defaults to ``True``.
            reset_on_connect: send ``*RST`` after opening. Defaults to ``False``.
            clear_errors_on_connect: drain the error queue after opening. Defaults to ``False``.
            replace: allow replacing an existing session with the same alias.
            protocol_trace: a path (or open ``TraceObserver``, such as
                ``scpi_driver_core.tracing.observer.RecordingTraceObserver``)
                to record every SCPI write/read at the transport boundary.
                ``None`` (default) disables tracing. A path is wrapped in a
                ``JsonlTraceSink`` and closed automatically on disconnect; an
                observer instance is the caller's to close.
            protocol_trace_redactor: an optional ``scpi_driver_core.tracing.
                redaction.Redactor`` (e.g. ``PatternRedactor``) applied to
                traced payloads before they reach ``protocol_trace``.

        Returns:
            The connection-state dict, see :meth:`get_connection_state`.
        """
        connection_type = str(options.pop("connection_type", "visa")).strip().lower().replace("-", "_")
        port = options.pop("port", 5025)
        discover = bool(options.pop("discover", True))
        reset_on_connect = bool(options.pop("reset_on_connect", False))
        clear_errors_on_connect = bool(options.pop("clear_errors_on_connect", False))
        replace = bool(options.pop("replace", False))
        protocol_trace = options.pop("protocol_trace", None)
        protocol_trace_redactor = options.pop("protocol_trace_redactor", None)
        if options:
            raise DriverArgumentValueError(f"unknown connect() options: {sorted(options)}")

        self._connect_session(
            alias=alias,
            resource=str(resource or ""),
            connection_type=connection_type,
            replace=replace,
            probe=False,
            timeout_s=timeout_s,
            port=port,
            protocol_trace=protocol_trace,
            protocol_trace_redactor=protocol_trace_redactor,
        )
        self._discovery[alias] = _AliasDiscovery()
        if reset_on_connect:
            self.reset_device(alias=alias)
        if clear_errors_on_connect:
            self.clear_device_errors(alias=alias)
        if discover:
            self.discover_modules(alias=alias)
        return self.get_connection_state(alias=alias)

    @capability(
        "connection.session.close",
        category="connection",
        risk_level="low",
        mutating=True,
        side_effects=("May command all outputs/load inputs off.", "Closes transport resources."),
    )
    def disconnect(self, alias: str | None = None) -> None:
        """Safely disconnect one session (or every session if none was ever selected)."""
        if alias is None and not self._aliases:
            return
        key = self._resolve_alias(alias)
        self._disconnect_session(key, raise_on_error=True)
        self._discovery.pop(key, None)

    def select(self, alias: str) -> str:
        """Make ``alias`` the session used by calls that omit ``alias``."""
        if alias not in self._aliases:
            raise DriverPreconditionError(f"unknown N6700 alias {alias!r}", code="LPDS-STA-008")
        self._registry.set_active(alias)
        return alias

    def get_current_alias(self) -> str | None:
        """Return the active session alias, or ``None`` if none is selected."""
        return self._registry.active_alias

    def disconnect_all(self) -> None:
        """Safely disconnect every open session."""
        for alias in list(self._aliases):
            self._disconnect_session(alias, raise_on_error=False)
            self._discovery.pop(alias, None)

    @capability("connection.session.status", category="connection", risk_level="none")
    def get_connection_state(self, alias: str | None = None, refresh: bool = False) -> dict[str, Any]:
        """Return connection/identity state as a plain dict (LPDS-002 §12)."""
        return _connection_info_dict(self._connection_info(alias=alias, refresh=refresh))

    # -- write/query: the single choke point for instrument I/O -------------

    def write_scpi(self, command: str, alias: str | None = None) -> None:
        """Write a raw SCPI command. Prefer typed methods for normal use.

        Rejected with ``DriverUnsafeOperationError`` if :meth:`set_raw_scpi_guard`
        was called and the guard has not been unlocked with
        :meth:`enable_raw_scpi`.
        """

        def action(session: ScpiSession) -> None:
            if self._raw_scpi_guard is not None:
                self._raw_scpi_guard.require_enabled()
            session.client.write(command)

        self._execute_operation(
            "write_scpi", action, alias=alias, required_states=_CONNECTED_STATES
        )
        self._audit("write_scpi", (), {"command": command}, (command,), (), ())

    def query_scpi(
        self,
        command: str,
        alias: str | None = None,
        *,
        retry_attempts: int = 1,
        retry_delay_s: float = 0.0,
    ) -> str:
        """Send a raw SCPI query and return its response string.

        Rejected with ``DriverUnsafeOperationError`` under the same guard as
        :meth:`write_scpi`.

        Args:
            retry_attempts: total attempts, including the first. Values above
                1 assert that resending ``command`` has no side effect
                (``ReplayPolicy.SAFE``) and retry a transport failure, per
                ``scpi_driver_core.execution.retry.RetryPolicy``. Defaults to
                1: no retry, since most SCPI queries are not safe to assume
                idempotent without the caller's explicit say.
            retry_delay_s: pause before the second attempt.
        """
        retry_policy = (
            RetryPolicy(attempts=retry_attempts, initial_delay_s=retry_delay_s)
            if retry_attempts > 1
            else None
        )
        replay_policy = ReplayPolicy.SAFE if retry_policy is not None else ReplayPolicy.NEVER

        def action(session: ScpiSession) -> str:
            if self._raw_scpi_guard is not None:
                self._raw_scpi_guard.require_enabled()
            return session.client.query(
                command, replay_policy=replay_policy, retry_policy=retry_policy
            )

        response = self._execute_operation(
            "query_scpi", action, alias=alias, required_states=_CONNECTED_STATES
        )
        self._audit("query_scpi", (), {"command": command}, (command,), (response,), ())
        return response

    # -- identity / IEEE-488.2 -------------------------------------------------

    @capability("identity.driver.read", category="identity", risk_level="none")
    def get_identity(self, alias: str | None = None, refresh: bool = True) -> str:
        """LPDS-002 mandatory: return ``*IDN?`` as one string."""
        identity = self.idn(alias=alias, refresh=refresh)
        return f"{identity.manufacturer},{identity.model},{identity.serial},{identity.firmware}"

    def idn(self, alias: str | None = None, refresh: bool = False) -> InstrumentIdentity:
        core = self._execute_operation(
            "idn",
            lambda session: session.get_identity(refresh=refresh),
            alias=alias,
            required_states=_CONNECTED_STATES,
        )
        return InstrumentIdentity.from_core(core)

    @capability(
        "system.reset.execute",
        category="system",
        risk_level="high",
        mutating=True,
        may_energize_or_sink_power=True,
        postconditions=("Instrument reset; all prior assumptions about configuration must be re-established.",),
    )
    def reset_device(self, alias: str | None = None, wait_until_ready: bool = True, timeout_s: float | None = None) -> dict[str, Any]:
        """LPDS-002 mandatory-when-applicable: ``*RST`` and wait for completion."""
        self.reset(alias=alias)
        if wait_until_ready:
            self._execute_operation(
                "reset_device",
                lambda session: session.client.query("*OPC?", timeout_s=timeout_s),
                alias=alias,
                required_states=_CONNECTED_STATES,
            )
        return {"alias": self._resolve_alias(alias), "reset": True}

    def reset(self, alias: str | None = None) -> None:
        self.write_scpi("*RST", alias=alias)

    def clear_status(self, alias: str | None = None) -> None:
        self.write_scpi("*CLS", alias=alias)

    def self_test(self, alias: str | None = None) -> SelfTestResult:
        resp = self.query_scpi("*TST?", alias=alias)
        if "," in resp:
            code_s, msg = resp.split(",", 1)
            return SelfTestResult(int(code_s), msg.strip().strip('"'))
        return SelfTestResult(int(resp), "")

    def operation_complete(self, alias: str | None = None) -> bool:
        return self.query_scpi("*OPC?", alias=alias).strip() == "1"

    def wait(self, alias: str | None = None) -> None:
        self.write_scpi("*WAI", alias=alias)

    def status_byte(self, alias: str | None = None) -> int:
        return int(self.query_scpi("*STB?", alias=alias))

    def standard_event_status(self, alias: str | None = None) -> int:
        return int(self.query_scpi("*ESR?", alias=alias))

    def get_remote_state(self, alias: str | None = None) -> RemoteState:
        info = self.get_connection_state(alias=alias)
        if info["transport"] != "simulated":
            raise DriverUnsupportedOperationError(
                "remote/local query is not supported by this transport"
            )
        value = self.query_scpi("SYST:REM?", alias=alias).strip().lower()
        if value in {"local", "remote", "remote_lockout"}:
            return value  # type: ignore[return-value]
        raise DriverUnsupportedOperationError("unexpected SYST:REM? reply")

    def set_remote_state(self, state: RemoteState, alias: str | None = None) -> None:
        info = self.get_connection_state(alias=alias)
        if info["transport"] != "simulated":
            raise DriverUnsupportedOperationError(
                "remote/local control is transport-specific and unsupported here"
            )
        command = {"local": "SYST:LOC", "remote": "SYST:REM", "remote_lockout": "SYST:RWL"}[state]
        self.write_scpi(command, alias=alias)

    def remote_lockout(self, enabled: bool, alias: str | None = None) -> None:
        self.set_remote_state("remote_lockout" if enabled else "remote", alias=alias)

    # -- error queue (LPDS-002 conditional group) ----------------------------

    @capability("system.error.read", category="system", risk_level="none")
    def get_device_error(self, alias: str | None = None) -> dict[str, Any]:
        """LPDS-002: return one entry from the SCPI error queue."""
        record = self.get_error(alias=alias)
        return {"code": record.code, "message": record.message, "is_ok": record.is_ok}

    def get_all_device_errors(self, alias: str | None = None, max_count: int = 100) -> list[dict[str, Any]]:
        errors = self.drain_errors(alias=alias, max_entries=max_count)
        return [{"code": e.code, "message": e.message} for e in errors]

    @capability("system.error.clear", category="system", risk_level="low", mutating=True)
    def clear_device_errors(self, alias: str | None = None) -> None:
        self.drain_errors(alias=alias)

    def device_error_queue_should_be_empty(self, alias: str | None = None) -> None:
        self.check_errors(alias=alias)

    def get_error(self, alias: str | None = None) -> ScpiErrorRecord:
        return parse_error(self.query_scpi("SYST:ERR?", alias=alias))

    def drain_errors(self, alias: str | None = None, max_entries: int = 32) -> list[ScpiErrorRecord]:
        errors: list[ScpiErrorRecord] = []
        for _ in range(max_entries):
            err = self.get_error(alias=alias)
            if err.is_ok:
                break
            errors.append(err)
        return errors

    def check_errors(self, alias: str | None = None) -> None:
        errors = self.drain_errors(alias=alias)
        if errors:
            raise DriverCommandRejectedError(
                "instrument reported SCPI errors",
                scpi_errors=[(e.code, e.message) for e in errors],
                code="LPDS-DEV-006",
            )

    # -- channel discovery (LPDS-002 channel_selection group) ----------------

    def channel_count(self, alias: str | None = None) -> int:
        key = self._resolve_alias(alias)
        discovery = self._discovery.setdefault(key, _AliasDiscovery())
        if discovery.channel_count == 0:
            discovery.channel_count = int(self.query_scpi("SYST:CHAN:COUN?", alias=key))
        return discovery.channel_count

    def _validate_channel(self, channel: int, alias: str | None = None) -> None:
        count = self.channel_count(alias=alias)
        if channel < 1 or channel > count or channel > 4:
            raise DriverArgumentValueError(
                f"invalid channel {channel}; installed count is {count}", code="LPDS-ARG-001"
            )

    @capability("channel.selection.list", category="channel", risk_level="none")
    def list_channels(self, alias: str | None = None) -> list[int]:
        """LPDS-002: return every installed channel number."""
        return list(range(1, self.channel_count(alias=alias) + 1))

    def validate_channel(self, channel: int, alias: str | None = None) -> bool:
        """LPDS-002: whether ``channel`` is installed, without raising."""
        try:
            self._validate_channel(channel, alias=alias)
        except DriverArgumentValueError:
            return False
        return True

    def channel_model(self, channel: int, alias: str | None = None) -> str:
        self._validate_channel(channel, alias=alias)
        return self.query_scpi(f"SYST:CHAN:MOD? {format_channel_list(channel)}", alias=alias).strip().strip('"')

    def channel_options(self, channel: int, alias: str | None = None) -> list[str]:
        self._validate_channel(channel, alias=alias)
        resp = self.query_scpi(f"SYST:CHAN:OPT? {format_channel_list(channel)}", alias=alias)
        normalized = resp.strip().strip('"')
        if normalized in {"", "0", "+0"}:
            return []
        return [item.strip().strip('"') for item in resp.split(",") if item.strip().strip('"')]

    def channel_serial(self, channel: int, alias: str | None = None) -> str:
        self._validate_channel(channel, alias=alias)
        return self.query_scpi(f"SYST:CHAN:SER? {format_channel_list(channel)}", alias=alias).strip().strip('"')

    @capability("channel.module.discover", category="channel", risk_level="none")
    def discover_modules(self, alias: str | None = None) -> dict[int, ChannelCapabilities]:
        """Discover installed modules and build the per-channel API for them."""
        key = self._resolve_alias(alias)
        discovery = self._discovery.setdefault(key, _AliasDiscovery())
        count = self.channel_count(alias=key)
        discovery.capabilities.clear()
        discovery.channels.clear()
        facade = _AliasFacade(self, key)
        for ch in range(1, count + 1):
            model = self.channel_model(ch, alias=key)
            options = self.channel_options(ch, alias=key)
            caps = classify_module(model, options)
            discovery.capabilities[ch] = caps
            if caps.module_type == "smu":
                discovery.channels[ch] = SMUChannel(facade, ch, caps)
            elif caps.module_type == "power_supply":
                discovery.channels[ch] = PowerSupplyChannel(facade, ch, caps)
            elif caps.module_type == "electronic_load":
                discovery.channels[ch] = ElectronicLoadChannel(facade, ch, caps)
            else:
                discovery.channels[ch] = BaseChannel(facade, ch, caps)
        return dict(discovery.capabilities)

    @property
    def channels(self) -> Mapping[int, BaseChannel]:
        """Channels for the active session. Prefer :meth:`channel` for a named alias."""
        key = self._resolve_alias(None)
        return dict(self._discovery.get(key, _AliasDiscovery()).channels)

    def channel(self, channel: int, alias: str | None = None) -> BaseChannel:
        self._validate_channel(channel, alias=alias)
        key = self._resolve_alias(alias)
        discovery = self._discovery.setdefault(key, _AliasDiscovery())
        if channel not in discovery.channels:
            self.discover_modules(alias=key)
        return discovery.channels[channel]

    def get_channel(self, channel: int, alias: str | None = None) -> BaseChannel:
        return self.channel(channel, alias=alias)

    def power_supply(self, channel: int, alias: str | None = None) -> PowerSupplyChannel:
        ch = self.channel(channel, alias=alias)
        if not isinstance(ch, PowerSupplyChannel) or isinstance(ch, ElectronicLoadChannel):
            raise DriverUnsupportedOperationError(f"channel {channel} is not a power-supply/SMU output")
        return ch

    def smu(self, channel: int, alias: str | None = None) -> SMUChannel:
        ch = self.channel(channel, alias=alias)
        if not isinstance(ch, SMUChannel):
            raise DriverUnsupportedOperationError(f"channel {channel} is not an SMU")
        return ch

    def load(self, channel: int, alias: str | None = None) -> ElectronicLoadChannel:
        ch = self.channel(channel, alias=alias)
        if not isinstance(ch, ElectronicLoadChannel):
            raise DriverUnsupportedOperationError(f"channel {channel} is not an electronic load")
        return ch

    # -- source/setpoint (LPDS-002 conditional group) ------------------------

    @capability(
        "source.voltage.set",
        category="source",
        risk_level="medium",
        mutating=True,
        canonical_method="set_dc_voltage",
    )
    def set_dc_voltage(self, value: float, channel: int = 1, alias: str | None = None) -> None:
        """LPDS-002: program the voltage setpoint on ``channel``."""
        self.power_supply(channel, alias=alias).set_voltage_setpoint(value)

    def get_dc_voltage_setpoint(self, channel: int = 1, alias: str | None = None) -> float:
        return self.power_supply(channel, alias=alias).get_voltage_setpoint()

    def get_dc_voltage_limits(self, channel: int = 1, alias: str | None = None) -> dict[str, float]:
        ps = self.power_supply(channel, alias=alias)
        return {"ovp_v": ps.get_ovp(), "range_v": ps.get_voltage_range()}

    @capability(
        "source.current.set",
        category="source",
        risk_level="medium",
        mutating=True,
        canonical_method="set_dc_current",
    )
    def set_dc_current(self, value: float, channel: int = 1, alias: str | None = None) -> None:
        """LPDS-002: program the current limit on ``channel``."""
        self.power_supply(channel, alias=alias).set_current_limit(value)

    def get_dc_current_setpoint(self, channel: int = 1, alias: str | None = None) -> float:
        return self.power_supply(channel, alias=alias).get_current_limit()

    def get_dc_current_limits(self, channel: int = 1, alias: str | None = None) -> dict[str, float]:
        ps = self.power_supply(channel, alias=alias)
        return {"range_a": ps.get_current_range()}

    def set_power_outputs(self, channels: Sequence[int], enabled: bool, alias: str | None = None) -> None:
        for ch in channels:
            self.power_supply(ch, alias=alias).set_output(enabled)

    def set_load_inputs(self, channels: Sequence[int], enabled: bool, alias: str | None = None) -> None:
        for ch in channels:
            self.load(ch, alias=alias).set_input(enabled)

    def set_channel_enabled(self, channels: Sequence[int], enabled: bool, alias: str | None = None) -> None:
        for ch in channels:
            chan = self.channel(ch, alias=alias)
            if isinstance(chan, ElectronicLoadChannel):
                chan.set_input(enabled)
            elif isinstance(chan, PowerSupplyChannel):
                chan.set_output(enabled)
            else:
                raise DriverUnsupportedOperationError(f"channel {ch} cannot be enabled")

    # -- output control (LPDS-002 conditional group) -------------------------

    @capability("source.output.enable", category="source", risk_level="high", mutating=True, may_energize_or_sink_power=True)
    def enable_output(self, channel: int, alias: str | None = None) -> None:
        """LPDS-002: enable one power-supply/SMU output.

        ``channel`` is always explicit — N6700 has no implicit active channel
        (LPDS-002 §9.3 prefers an explicit argument over hidden selection
        state for a multi-channel instrument like this one).
        """
        self.power_supply(channel, alias=alias).output_on()

    @capability("source.output.disable", category="source", risk_level="low", mutating=True)
    def disable_output(self, channel: int, alias: str | None = None) -> None:
        """LPDS-002: disable one power-supply/SMU output. Idempotent."""
        self.power_supply(channel, alias=alias).output_off()

    def get_output_state(self, channel: int, alias: str | None = None) -> bool:
        return self.power_supply(channel, alias=alias).get_output()

    def output_should_be_enabled(self, channel: int, alias: str | None = None) -> None:
        if not self.get_output_state(channel, alias=alias):
            raise DriverPreconditionError(f"channel {channel} output is not enabled", code="LPDS-STA-006")

    def output_should_be_disabled(self, channel: int, alias: str | None = None) -> None:
        if self.get_output_state(channel, alias=alias):
            raise DriverPreconditionError(f"channel {channel} output is not disabled", code="LPDS-STA-007")

    # -- measurement (LPDS-002 conditional group) ----------------------------

    @capability("measure.voltage.dc", category="measure", risk_level="none", canonical_method="measure_dc_voltage")
    def measure_dc_voltage(self, channel: int = 1, alias: str | None = None) -> float:
        """LPDS-002: measure channel voltage in volts."""
        return self.channel(channel, alias=alias).measure_voltage()

    @capability("measure.current.dc", category="measure", risk_level="none", canonical_method="measure_dc_current")
    def measure_dc_current(self, channel: int = 1, alias: str | None = None) -> float:
        """LPDS-002: measure channel current in amperes."""
        return self.channel(channel, alias=alias).measure_current()

    @capability("measure.power.dc", category="measure", risk_level="none", canonical_method="measure_dc_power")
    def measure_dc_power(self, channel: int = 1, alias: str | None = None) -> float | None:
        """LPDS-002: measure (or compute) channel power in watts."""
        return self._measure_power(channel, alias=alias).power_W

    def measure_power(self, channel: int, alias: str | None = None) -> PowerMeasurement:
        """Return the full power measurement (value, source, timestamp) for one channel."""
        return self._measure_power(channel, alias=alias)

    def measure_all(self, alias: str | None = None) -> dict[int, Measurement]:
        key = self._resolve_alias(alias)
        return {ch: self._measure_channel(ch, alias=key) for ch in self._discovery.get(key, _AliasDiscovery()).channels}

    @capability(
        "measure.voltage.wait_in_range",
        category="measure",
        risk_level="none",
        canonical_method="wait_for_voltage_in_range",
    )
    def wait_for_voltage_in_range(
        self,
        channel: int,
        minimum: float,
        maximum: float,
        *,
        timeout_s: float = 10.0,
        poll_interval_s: float = 0.2,
        alias: str | None = None,
    ) -> float:
        """Poll ``measure_dc_voltage`` until it falls within ``[minimum, maximum]``.

        Bounded by ``scpi_driver_core.execution.polling.poll_until``: raises
        ``DriverTimeoutError`` if the range is never entered within
        ``timeout_s``, never blocks longer than that regardless of
        ``poll_interval_s``.

        Returns:
            The last measured voltage, which is within range on success.
        """
        last = 0.0

        def predicate() -> bool:
            nonlocal last
            last = self.measure_dc_voltage(channel, alias=alias)
            return minimum <= last <= maximum

        with translated(operation="wait_for_voltage_in_range"):
            poll_until(
                predicate,
                timeout_s=timeout_s,
                interval_s=poll_interval_s,
                description=f"channel {channel} voltage in [{minimum:.12g}, {maximum:.12g}] V",
            )
        return last

    @capability(
        "measure.current.wait_in_range",
        category="measure",
        risk_level="none",
        canonical_method="wait_for_current_in_range",
    )
    def wait_for_current_in_range(
        self,
        channel: int,
        minimum: float,
        maximum: float,
        *,
        timeout_s: float = 10.0,
        poll_interval_s: float = 0.2,
        alias: str | None = None,
    ) -> float:
        """Poll ``measure_dc_current`` until it falls within ``[minimum, maximum]``.

        See :meth:`wait_for_voltage_in_range`; identical contract for current.
        """
        last = 0.0

        def predicate() -> bool:
            nonlocal last
            last = self.measure_dc_current(channel, alias=alias)
            return minimum <= last <= maximum

        with translated(operation="wait_for_current_in_range"):
            poll_until(
                predicate,
                timeout_s=timeout_s,
                interval_s=poll_interval_s,
                description=f"channel {channel} current in [{minimum:.12g}, {maximum:.12g}] A",
            )
        return last

    def _timestamp(self) -> tuple[str, float]:
        ts = time.time()
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(), ts

    def _measure_power(self, channel: int, alias: str | None = None) -> PowerMeasurement:
        iso, ts = self._timestamp()
        capabilities = self.channel(channel, alias=alias).capabilities
        if capabilities.supports_power_measurement:
            value = float(self.query_scpi(f"MEAS:POW? {format_channel_list(channel)}", alias=alias))
            return PowerMeasurement(channel, value, "instrument", iso, ts)
        try:
            voltage = float(self.query_scpi(f"MEAS:VOLT? {format_channel_list(channel)}", alias=alias))
            current = float(self.query_scpi(f"MEAS:CURR? {format_channel_list(channel)}", alias=alias))
        except Exception:
            return PowerMeasurement(channel, None, "unavailable", iso, ts)
        return PowerMeasurement(channel, voltage * current, "calculated", iso, ts)

    def _measure_channel(self, channel: int, alias: str | None = None) -> Measurement:
        iso, ts = self._timestamp()
        voltage: float | None = None
        current: float | None = None
        with suppress(Exception):
            voltage = float(self.query_scpi(f"MEAS:VOLT? {format_channel_list(channel)}", alias=alias))
        with suppress(Exception):
            current = float(self.query_scpi(f"MEAS:CURR? {format_channel_list(channel)}", alias=alias))

        capabilities = self.channel(channel, alias=alias).capabilities
        if capabilities.supports_power_measurement:
            power = self._measure_power(channel, alias=alias)
        elif voltage is not None and current is not None:
            power = PowerMeasurement(channel, voltage * current, "calculated", iso, ts)
        else:
            power = PowerMeasurement(channel, None, "unavailable", iso, ts)
        return Measurement(channel, voltage, current, power.power_W, power.power_source, iso, ts)

    # -- SMU / load convenience wrappers -------------------------------------

    def set_smu_mode(self, channel: int, mode: Literal["voltage", "current"], alias: str | None = None) -> None:
        self.smu(channel, alias=alias).set_smu_mode(mode)

    def get_smu_mode(self, channel: int, alias: str | None = None) -> Literal["voltage", "current"]:
        return self.smu(channel, alias=alias).get_smu_mode()

    def configure_smu_voltage_priority(self, channel: int, voltage: float, current_limit: float, alias: str | None = None, **kw: Any) -> None:
        self.smu(channel, alias=alias).configure_voltage_priority(voltage, current_limit, **kw)

    def configure_smu_current_priority(self, channel: int, current: float, voltage_limit: float, alias: str | None = None, **kw: Any) -> None:
        self.smu(channel, alias=alias).configure_current_priority(current, voltage_limit, **kw)

    def set_load_mode(self, channel: int, mode: Literal["cc", "cv", "cr", "cp"], alias: str | None = None) -> None:
        self.load(channel, alias=alias).set_load_mode(mode)

    def get_load_mode(self, channel: int, alias: str | None = None) -> Literal["cc", "cv", "cr", "cp"]:
        return self.load(channel, alias=alias).get_load_mode()

    def configure_load_cc(self, channel: int, current: float, *, input_on: bool = False, verify: bool = True, alias: str | None = None, **_: object) -> None:
        self.load(channel, alias=alias).configure_cc(current, input_on=input_on, verify=verify)

    # -- protection / status --------------------------------------------------

    def clear_protection(
        self,
        channel: int,
        *,
        restore_output: bool = False,
        force_output_off_first: bool = True,
        verify_cleared: bool = True,
        alias: str | None = None,
    ) -> ProtectionClearResult:
        return self.channel(channel, alias=alias).clear_protection(
            restore_output=restore_output,
            force_output_off_first=force_output_off_first,
            verify_cleared=verify_cleared,
        )

    def _status_channel_list(self, channel: int | None, alias: str | None) -> str:
        if channel is not None:
            return format_channel_list(channel)
        key = self._resolve_alias(alias)
        installed = self._discovery.get(key, _AliasDiscovery()).channels
        if not installed:
            raise DriverArgumentValueError("no installed N6700 channels were discovered")
        return format_channel_list(sorted(installed))

    @staticmethod
    def _parse_status_response(raw: str) -> int | str:
        stripped = raw.strip()
        if "," in stripped:
            return stripped
        return int(stripped)

    def get_operation_status(self, channel: int | None = None, alias: str | None = None) -> OperationStatus:
        chanlist = self._status_channel_list(channel, alias)
        raw = self.query_scpi(f"STAT:OPER:COND? {chanlist}", alias=alias)
        return OperationStatus(self._parse_status_response(raw))

    def get_questionable_status(self, channel: int | None = None, alias: str | None = None) -> QuestionableStatus:
        chanlist = self._status_channel_list(channel, alias)
        raw = self.query_scpi(f"STAT:QUES:COND? {chanlist}", alias=alias)
        return QuestionableStatus(self._parse_status_response(raw))

    @capability(
        "safety.output.disable_all",
        category="safety",
        risk_level="low",
        mutating=True,
        canonical_method="safe_shutdown",
        postconditions=("Every controllable output/load input has been commanded off, best-effort.",),
    )
    def safe_shutdown(self, alias: str | None = None, timeout_s: float | None = None) -> dict[str, Any]:
        """LPDS-002 mandatory-when-applicable: best-effort shutdown of every channel."""
        result = self.shutdown_all(alias=alias)
        return {"success": result.success, "channels": [r.__dict__ for r in result.results]}

    def shutdown_all(self, alias: str | None = None) -> ShutdownResult:
        key = self._resolve_alias(alias)
        installed = self._discovery.get(key, _AliasDiscovery()).channels
        results: list[ShutdownChannelResult] = []
        for ch_num in sorted(installed):
            try:
                ch = self.channel(ch_num, alias=key)
                if isinstance(ch, ElectronicLoadChannel):
                    ch.input_off()
                elif isinstance(ch, PowerSupplyChannel):
                    ch.output_off()
                results.append(ShutdownChannelResult(ch_num, True, True))
            except Exception as exc:
                results.append(ShutdownChannelResult(ch_num, True, False, str(exc)))
        return ShutdownResult(tuple(results))

    # -- audit logging (LPDS-008 software-verifiable subset) -----------------

    def _audit(
        self,
        operation: str,
        channels: tuple[int, ...],
        requested_values: dict[str, object],
        commands: tuple[str, ...],
        responses: tuple[str, ...],
        errors: tuple[str, ...],
    ) -> None:
        if self.audit_log_path is None:
            return
        record = AuditRecord(
            timestamp_iso=datetime.now(timezone.utc).isoformat(),
            timestamp_unix=time.time(),
            operation=operation,
            channels=channels,
            requested_values=requested_values,
            scpi_commands=commands,
            responses=responses,
            errors=errors,
            duration_s=0.0,
        )
        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record.__dict__, default=str) + "\n")

    # -- driver capability list (LPDS-002 §8, distinct from LPDS-013 model) --

    def get_driver_capabilities(self) -> list[str]:
        """LPDS-002 mandatory: flat list of implemented conditional groups."""
        return [
            "error_queue",
            "channel_selection",
            "output_control",
            "source_setpoint",
            "measurement",
            "device_reset",
            "safe_shutdown",
        ]

    def close(self, alias: str | None = None) -> None:
        """Backward-compatible alias for :meth:`disconnect`."""
        self.disconnect(alias=alias)
