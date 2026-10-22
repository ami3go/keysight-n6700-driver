"""Framework-independent driver for Keysight/Agilent N6700 modular power systems."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager, suppress
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from scpi_driver_core.exceptions import ResponseParseError
from scpi_driver_core.execution.guards import ConfirmationGuard
from scpi_driver_core.execution.polling import poll_until
from scpi_driver_core.execution.retry import RetryPolicy
from scpi_driver_core.scpi.errors import ScpiErrorQueue
from scpi_driver_core.scpi.parsers import parse_bool, parse_csv, parse_float, parse_int
from scpi_driver_core.session import ScpiSession
from scpi_driver_core.tracing.instrumented import InstrumentedTransport
from scpi_driver_core.tracing.jsonl import JsonlTraceSink
from scpi_driver_core.tracing.observer import TraceObserver, Tracer
from scpi_driver_core.tracing.redaction import Redactor
from scpi_driver_core.transport.base import Transport
from scpi_driver_core.transport.models import ReplayPolicy, TransportState
from scpi_driver_core.transport.tcp import TcpTransport
from scpi_driver_core.transport.visa import VisaTransport

from ._translate import translated
from .acquisition import DigitizeTiming
from .acquisition_lease import AcquisitionCoordinator
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
    OPERATOR_ACTION_REQUIRED,
    DriverArgumentValueError,
    DriverCommandRejectedError,
    DriverConfigurationError,
    DriverError,
    DriverMalformedResponseError,
    DriverOperationUncertainError,
    DriverPreconditionError,
    DriverUnsupportedOperationError,
    DriverUnsupportedValueError,
)
from .module_capabilities import ChannelCapabilities, classify_module
from .scpi import (
    SUPPORTED_MANUFACTURERS,
    format_bool,
    format_channel_list,
    parse_error,
    require_channel,
    require_number,
)
from .simulator import build_simulated_transport
from .types import (
    ArbSequenceConfiguration,
    ArbWaveformConfiguration,
    ArrayMeasurement,
    AuditRecord,
    ConstantDwellArbConfiguration,
    CurrentArbConfiguration,
    CurrentListConfiguration,
    InstrumentIdentity,
    LoadProfileConfiguration,
    LoadProfileMode,
    LoadTransientStatus,
    LoadTransientToken,
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
    StepTransientConfiguration,
    VoltageArbConfiguration,
    VoltageListConfiguration,
    VoltageTransientStatus,
    VoltageTransientToken,
)
from .version import __version__
from .waveforms import ArbSequenceStep, ArbShape, RepeatCount

_CONNECTED_STATES = frozenset(
    {SessionState.CONNECTED, SessionState.CONFIGURED, SessionState.BUSY, SessionState.WAITING}
)
_log = logging.getLogger(__name__)

_DRIVER_METADATA = DriverMetadata(
    driver_name="keysight_n6700",
    display_name="Keysight/Agilent N6700 modular power system driver",
    version=__version__,
    package_name="keysight-n6700-driver",
    manufacturer="Keysight Technologies",
    supported_models=(
        "N67xx series mainframe",
        "N673x/N674x/N675x/N676x/N677x",
        "N678xA SMU",
        "N679xA Electronic Load Module",
    ),
    supported_transport_profiles=("visa", "usb", "ethernet_socket", "simulated"),
)


@dataclass
class _AliasDiscovery:
    channel_count: int = 0
    capabilities: dict[int, ChannelCapabilities] = field(default_factory=dict)
    channels: dict[int, BaseChannel] = field(default_factory=dict)


class _AliasFacade:
    """Bind the channel API to one normalized session alias.

    Typed operations intentionally use the driver's private I/O path. The
    public raw-SCPI confirmation guard applies only to explicit raw calls.
    """

    def __init__(self, driver: N6700, alias: str) -> None:
        self._driver = driver
        self._alias = alias

    def query_scpi(self, command: str) -> str:
        return self._driver._query(command, alias=self._alias, operation="channel")

    def write_scpi(self, command: str) -> None:
        self._driver._write(command, alias=self._alias, operation="channel")

    def write_checked(self, command: str) -> None:
        self._driver.write_checked(command, alias=self._alias, operation="channel")

    def operation_lock(self) -> AbstractContextManager[None]:
        return self._driver._operation_lock(alias=self._alias)

    def _measure_power(self, channel: int) -> PowerMeasurement:
        return self._driver._measure_power(channel, alias=self._alias)

    def _measure_channel(self, channel: int) -> Measurement:
        return self._driver._measure_channel(channel, alias=self._alias)

    def _fetch_power(self, channel: int) -> PowerMeasurement:
        return self._driver._fetch_power(channel, alias=self._alias)

    def _fetch_channel(self, channel: int) -> Measurement:
        return self._driver._fetch_channel(channel, alias=self._alias)

    def drain_errors(self) -> list[ScpiErrorRecord]:
        return self._driver.drain_errors(alias=self._alias)

    def check_errors(self) -> None:
        self._driver.check_errors(alias=self._alias)

    def set_channel_enabled(self, channels: list[int], enabled: bool) -> None:
        self._driver.set_channel_enabled(channels, enabled, alias=self._alias)

    def _begin_digitize(self, channel: int) -> None:
        self._driver._begin_digitize(channel, alias=self._alias)

    def _end_digitize(self, channel: int) -> None:
        self._driver._end_digitize(channel, alias=self._alias)

    def _query_binary_scpi(self, command: str, *, max_bytes: int) -> bytes:
        return self._driver._query_binary_scpi(command, max_bytes=max_bytes, alias=self._alias)

    def _channel_supports_digitizing(self, channel: int) -> bool:
        caps = self._driver._discovery.get(self._alias, _AliasDiscovery()).capabilities.get(channel)
        return bool(caps and caps.supports_digitizing)


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
    """Driver for N6700-family modular power systems."""

    def __init__(self, audit_log_path: str | Path | None = None) -> None:
        super().__init__(_DRIVER_METADATA)
        self._discovery: dict[str, _AliasDiscovery] = {}
        self.audit_log_path = Path(audit_log_path) if audit_log_path else None
        self._auto_shutdown_on_disconnect = True
        self._strict_error_checking = True
        self._default_timeout_s: float | None = None
        self._tracers: dict[str, tuple[Tracer, JsonlTraceSink | None]] = {}
        self._raw_scpi_guard: ConfirmationGuard | None = None
        self._last_shutdown: dict[str, ShutdownResult] = {}
        self._digitize_leases = AcquisitionCoordinator()
        self._apply_configuration()

    def _apply_configuration(self) -> None:
        configuration = getattr(self, "_configuration", {})
        settings = configuration.get("settings", {}) if isinstance(configuration, dict) else {}
        safety = settings.get("safety", {}) if isinstance(settings, dict) else {}
        timeouts = settings.get("timeouts", {}) if isinstance(settings, dict) else {}
        logging_settings = settings.get("logging", {}) if isinstance(settings, dict) else {}
        self._auto_shutdown_on_disconnect = bool(
            safety.get("auto_shutdown_on_disconnect", self._auto_shutdown_on_disconnect)
        )
        self._strict_error_checking = bool(
            safety.get("strict_error_checking", self._strict_error_checking)
        )
        timeout = timeouts.get("communication_timeout_s")
        if timeout is not None:
            self._default_timeout_s = require_number("communication_timeout_s", timeout, minimum=1e-9)
        path = logging_settings.get("audit_log_path")
        if path and self.audit_log_path is None:
            self.audit_log_path = Path(path)

    def _begin_digitize(self, channel: int, alias: str | None = None) -> None:
        key = self._resolve_alias(alias)
        entry = self._entry(key)
        # Serialize reservation with SCPI commands using the alias lock.
        # A terminal transport may never start/restart an acquisition.
        with entry.lock:
            if entry.state not in _CONNECTED_STATES or entry.session.transport.state is not TransportState.OPEN:
                raise DriverPreconditionError(
                    "digitizer requires a live verified session; disconnect and reconnect after a fault"
                )
            self._digitize_leases.begin(key, channel)

    def _end_digitize(self, channel: int, alias: str | None = None) -> None:
        key = self._resolve_alias(alias)
        with self._entry(key).lock:
            self._digitize_leases.release(key, channel)

    def _require_session_replacement_allowed(self, alias: str, session: ScpiSession) -> None:
        # Called by base._connect_session while holding the global registry
        # lock AND old alias's operation lock, immediately before teardown.
        self._reconcile_terminal_transport_lease(alias)
        self._digitize_leases.require_idle(alias, "session replacement")

    def _on_terminal_transport_fault(self, alias: str, session: ScpiSession) -> None:
        # Called only after the byte transport reports a terminal fault.
        # Resource bookkeeping release MUST NOT be confused with a proven ABORt
        # or OFF state on the mainframe.
        if session.transport.state not in {TransportState.FAULTED, TransportState.CLOSED}:
            return
        self._digitize_leases.invalidate_terminal_session(alias)
        discovery = self._discovery.get(alias)
        if discovery is not None:
            for previous_channel in discovery.channels.values():
                previous_channel._invalidate_discovery()
            discovery.channels.clear()
            discovery.capabilities.clear()

    def _reconcile_terminal_transport_lease(self, alias: str) -> None:
        # Also covers faults detected before disconnect/replace, even when no
        # later SCPI command reached _execute_operation.
        entry = self._entry(alias)
        if entry.session.transport.state in {TransportState.FAULTED, TransportState.CLOSED}:
            self._on_terminal_transport_fault(alias, entry.session)

    def _channel_supports_digitizing(self, channel: int, alias: str | None = None) -> bool:
        """Whether ``channel`` has a real SWEep/digitizer subsystem at all.

        Real hardware found the hard way: a plain power-supply channel can
        answer SENS:FUNC:VOLT?/CURR? (ordinary measurement enables) but never
        replies at all to SENS:SWE:POIN? -- no SCPI error, a silent hang.
        Based on discovery-time module classification, not a live probe.
        """
        key = self._resolve_alias(alias)
        caps = self._discovery.get(key, _AliasDiscovery()).capabilities.get(channel)
        return bool(caps and caps.supports_digitizing)

    @capability(
        "safety.raw_scpi.guard",
        category="safety",
        risk_level="low",
        display_name="Set Raw SCPI Guard",
        description="Require a confirmation phrase before public raw SCPI access.",
    )
    def set_raw_scpi_guard(self, phrase: str) -> None:
        self._raw_scpi_guard = ConfirmationGuard(phrase, name="raw SCPI")

    def enable_raw_scpi(self, phrase: str) -> None:
        if self._raw_scpi_guard is None:
            raise DriverConfigurationError(
                "no raw SCPI guard is set; call set_raw_scpi_guard() first", code="LPDS-CFG-003"
            )
        with translated(operation="enable_raw_scpi"):
            self._raw_scpi_guard.enable(phrase)

    def disable_raw_scpi(self) -> None:
        if self._raw_scpi_guard is not None:
            self._raw_scpi_guard.disable()

    def set_auto_shutdown_on_disconnect(self, enabled: bool) -> None:
        self._auto_shutdown_on_disconnect = bool(enabled)

    def set_strict_error_checking(self, enabled: bool) -> None:
        self._strict_error_checking = bool(enabled)

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

    def __exit__(self, exc_type: object, exc: BaseException | None, tb: object) -> None:
        try:
            self.disconnect_all()
        except DriverError as cleanup_error:
            if exc is None:
                raise
            _log.error("N6700 cleanup failed while handling %r: %s", exc, cleanup_error)
            if hasattr(exc, "add_note"):
                exc.add_note(f"N6700 cleanup also failed: {cleanup_error}")

    def _build_transport(
        self, *, alias: str, resource: str, connection_type: str, **options: Any
    ) -> Transport:
        trace = options.pop("protocol_trace", None)
        redactor: Redactor | None = options.pop("protocol_trace_redactor", None)
        port = options.pop("port", 5025)

        transport: Transport
        if connection_type in {"sim", "simulation", "simulated"}:
            transport = build_simulated_transport(
                models=options.pop("models", None),
                mainframe_model=options.pop("mainframe_model", "N6705C"),
                sequence_step_base=options.pop("sequence_step_base", 0),
            )
        elif connection_type in {"visa", "usb"}:
            if not resource:
                raise DriverArgumentValueError("resource must contain a VISA resource string")
            transport = VisaTransport(resource)
        elif connection_type in {"ethernet", "socket", "tcp", "tcpip"}:
            if not resource:
                raise DriverArgumentValueError("resource must contain an Ethernet host or IP address")
            if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
                raise DriverArgumentValueError(f"invalid TCP port {port!r}", code="LPDS-ARG-005")
            transport = TcpTransport(resource, port)
        else:
            raise DriverArgumentValueError(
                "connection_type must be visa, usb, ethernet/socket, or simulated"
            )
        if options:
            raise DriverArgumentValueError(f"unsupported transport options: {sorted(options)}")

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
        discovery = self._discovery.pop(alias, None)
        if discovery is not None:
            for previous_channel in discovery.channels.values():
                previous_channel._invalidate_discovery()
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

    def _shutdown_session(self, alias: str, session: ScpiSession) -> ShutdownResult:
        """Guard-free and state-machine-free shutdown for emergency/lifecycle use."""
        entry = self._aliases.get(alias)
        timeout = entry.io_timeout_s if entry is not None else None
        with translated(operation="shutdown"):
            count = parse_int(session.client.query("SYST:CHAN:COUN?", timeout_s=timeout))
            if count < 1:
                return ShutdownResult()
            chanlist = "(@1)" if count == 1 else f"(@1:{count})"
            session.client.write(f"OUTP OFF,{chanlist}", timeout_s=timeout)
            session.client.query("*OPC?", timeout_s=timeout)
            response = session.client.query(f"OUTP? {chanlist}", timeout_s=timeout)
            raw_states = parse_csv(response)
            if len(raw_states) != count:
                raise DriverOperationUncertainError(
                    f"OUTP? returned {len(raw_states)} states for {count} channels",
                    code="LPDS-SAF-011",
                    recovery_action=OPERATOR_ACTION_REQUIRED,
                )
            states = [parse_bool(item) for item in raw_states]
        return ShutdownResult(
            tuple(
                ShutdownChannelResult(
                    channel=index,
                    attempted=True,
                    success=not state,
                    error="output still ON after OUTP OFF" if state else None,
                )
                for index, state in enumerate(states, start=1)
            )
        )

    def _apply_safe_state(self, alias: str, session: ScpiSession, *, reason: str) -> None:
        if not self._auto_shutdown_on_disconnect:
            return
        try:
            result = self.shutdown_all(alias=alias)
        except DriverError:
            self._last_shutdown[alias] = ShutdownResult()
            raise
        self._last_shutdown[alias] = result
        if not result.success:
            raise DriverOperationUncertainError(
                f"safe state not confirmed on {reason}",
                code="LPDS-SAF-010",
                details={"channels": [asdict(item) for item in result.results]},
                recovery_action=OPERATOR_ACTION_REQUIRED,
            )

    def get_last_shutdown_result(self, alias: str = "default") -> ShutdownResult | None:
        return self._last_shutdown.get(self._key(alias))

    @capability(
        "connection.session.open",
        category="connection",
        risk_level="low",
        mutating=True,
        description="Open a session to an N6700 mainframe.",
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
            models: simulator-only channel-to-module mapping.
            mainframe_model: simulator-only mainframe identity (default ``N6705C``).
            sequence_step_base: simulator-only ARB sequence convention, 0 or 1.

        Returns:
            The connection-state dict, see :meth:`get_connection_state`.
        """
        key = self._key(alias)
        connection_type = str(options.pop("connection_type", "visa")).strip().lower().replace("-", "_")
        port = options.pop("port", 5025)
        discover = bool(options.pop("discover", True))
        reset_on_connect = bool(options.pop("reset_on_connect", False))
        clear_errors_on_connect = bool(options.pop("clear_errors_on_connect", False))
        replace_existing = bool(options.pop("replace", False))
        protocol_trace = options.pop("protocol_trace", None)
        protocol_trace_redactor = options.pop("protocol_trace_redactor", None)
        simulation_options = {
            name: options.pop(name)
            for name in ("models", "mainframe_model", "sequence_step_base")
            if name in options
        }
        if simulation_options and connection_type not in {"sim", "simulation", "simulated"}:
            raise DriverArgumentValueError(
                "models, mainframe_model, and sequence_step_base are simulator-only options"
            )
        if options:
            raise DriverArgumentValueError(f"unknown connect() options: {sorted(options)}")
        effective_timeout = timeout_s if timeout_s is not None else self._default_timeout_s

        self._connect_session(
            alias=key,
            resource=str(resource or ""),
            connection_type=connection_type,
            replace=replace_existing,
            probe=False,
            timeout_s=effective_timeout,
            port=port,
            protocol_trace=protocol_trace,
            protocol_trace_redactor=protocol_trace_redactor,
            **simulation_options,
        )
        self._discovery[key] = _AliasDiscovery()
        try:
            if reset_on_connect:
                self.reset_device(alias=key)
            if clear_errors_on_connect:
                self.clear_device_errors(alias=key)
            if discover:
                self.discover_modules(alias=key)
            return self.get_connection_state(alias=key)
        except BaseException:
            with suppress(Exception):
                self._disconnect_session(key, raise_on_error=False)
            self._discovery.pop(key, None)
            raise

    @capability(
        "connection.session.close",
        category="connection",
        risk_level="low",
        mutating=True,
        side_effects=("May command all outputs/load inputs off.", "Closes transport resources."),
    )
    def disconnect(self, alias: str | None = None) -> None:
        if alias is None and not self._aliases:
            return
        key = self._resolve_alias(alias)
        with self._entry(key).lock:
            self._reconcile_terminal_transport_lease(key)
            self._digitize_leases.require_idle(key, "disconnect")
            try:
                self._disconnect_session(key, raise_on_error=True)
            finally:
                self._discovery.pop(key, None)

    def select(self, alias: str) -> str:
        key = self._key(alias)
        if key not in self._aliases:
            raise DriverPreconditionError(f"unknown N6700 alias {key!r}", code="LPDS-STA-008")
        self._registry.set_active(key)
        return key

    def get_current_alias(self) -> str | None:
        return self._registry.active_alias

    def disconnect_all(self) -> None:
        """Safely disconnect every open session."""
        failures: list[tuple[str, BaseException]] = []
        for alias in list(self._aliases):
            try:
                with self._entry(alias).lock:
                    self._reconcile_terminal_transport_lease(alias)
                    self._digitize_leases.require_idle(alias, "disconnect_all")
                    self._disconnect_session(alias, raise_on_error=True)
            except BaseException as exc:
                failures.append((alias, exc))
            finally:
                self._discovery.pop(alias, None)
        if failures:
            raise DriverOperationUncertainError(
                f"{len(failures)} session(s) did not disconnect cleanly",
                code="LPDS-CON-010",
                details={alias: str(exc) for alias, exc in failures},
                recovery_action=OPERATOR_ACTION_REQUIRED,
            ) from failures[0][1]

    @capability("connection.session.status", category="connection", risk_level="none")
    def get_connection_state(self, alias: str | None = None, refresh: bool = False) -> dict[str, Any]:
        return _connection_info_dict(self._connection_info(alias=alias, refresh=refresh))

    def reconnect(self, alias: str | None = None) -> dict[str, Any]:
        return _connection_info_dict(self._reconnect_info(alias))

    def _write(self, command: str, *, alias: str | None, operation: str) -> None:
        key = self._resolve_alias(alias)
        timeout = self._io_timeout(key)
        self._execute_operation(
            operation,
            lambda session: session.client.write(command, timeout_s=timeout),
            alias=key,
            required_states=_CONNECTED_STATES,
        )

    def _query(
        self,
        command: str,
        *,
        alias: str | None,
        operation: str,
        retry_attempts: int = 1,
        retry_delay_s: float = 0.0,
    ) -> str:
        key = self._resolve_alias(alias)
        timeout = self._io_timeout(key)
        retry_policy = (
            RetryPolicy(attempts=retry_attempts, initial_delay_s=retry_delay_s)
            if retry_attempts > 1
            else None
        )
        replay_policy = ReplayPolicy.SAFE if retry_policy is not None else ReplayPolicy.NEVER
        return self._execute_operation(
            operation,
            lambda session: session.client.query(
                command,
                timeout_s=timeout,
                replay_policy=replay_policy,
                retry_policy=retry_policy,
            ),
            alias=key,
            required_states=_CONNECTED_STATES,
        )

    def _require_raw_allowed(self, operation: str) -> None:
        if self._raw_scpi_guard is not None:
            with translated(operation=operation):
                self._raw_scpi_guard.require_enabled()

    def write_scpi(self, command: str, alias: str | None = None) -> None:
        """Write a raw SCPI command. Prefer typed methods for normal use.

        Rejected with ``DriverUnsafeOperationError`` if :meth:`set_raw_scpi_guard`
        was called and the guard has not been unlocked with
        :meth:`enable_raw_scpi`.
        """
        self._require_raw_allowed("write_scpi")

        def action(session: ScpiSession) -> None:
            with self._digitize_leases.command(self._resolve_alias(alias), command):
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
        self._require_raw_allowed("query_scpi")
        retry_policy = (
            RetryPolicy(attempts=retry_attempts, initial_delay_s=retry_delay_s)
            if retry_attempts > 1
            else None
        )
        replay_policy = ReplayPolicy.SAFE if retry_policy is not None else ReplayPolicy.NEVER

        def action(session: ScpiSession) -> str:
            with self._digitize_leases.command(self._resolve_alias(alias), command):
                return session.client.query(
                    command, replay_policy=replay_policy, retry_policy=retry_policy
                )

        response = self._execute_operation(
            "query_scpi", action, alias=alias, required_states=_CONNECTED_STATES
        )
        self._audit("query_scpi", (), {"command": command}, (command,), (response,), ())
        return response

    def write_checked(
        self,
        command: str,
        *,
        alias: str | None = None,
        operation: str = "write",
    ) -> None:
        """Write and drain the SCPI error queue under the same client lock."""
        key = self._resolve_alias(alias)
        if not self._strict_error_checking:
            self._write(command, alias=key, operation=operation)
            return
        timeout = self._io_timeout(key)

        def action(session: ScpiSession) -> None:
            with self._digitize_leases.command(key, command), session.client.operation_lock():
                session.client.write(command, timeout_s=timeout)
                errors = ScpiErrorQueue(client=session.client).drain(timeout_s=timeout)
            if errors:
                raise DriverCommandRejectedError(
                    f"{command!r} was rejected by the instrument",
                    code="LPDS-DEV-006",
                    operation=operation,
                    scpi_errors=[(item.code, item.message) for item in errors],
                )

        self._execute_operation(operation, action, alias=key, required_states=_CONNECTED_STATES)

    def _query_binary_scpi(
        self,
        command: str,
        *,
        max_bytes: int,
        alias: str | None = None,
    ) -> bytes:
        """Bounded binary query using pinned scpi-driver-core's block parser."""
        if max_bytes < 1 or max_bytes > 4 * 512 * 1024:
            raise DriverArgumentValueError("binary digitizer response exceeds 2 MiB cap")

        def action(session: ScpiSession) -> bytes:
            with self._digitize_leases.command(self._resolve_alias(alias), command):
                return session.client.query_binary_block(
                    command, maximum_size=max_bytes, consume_terminator=True
                )

        payload = self._execute_operation(
            "_query_binary_scpi", action, alias=alias, required_states=_CONNECTED_STATES
        )
        self._audit(
            "query_binary_scpi", (), {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()},
            (command,), (), (),
        )
        return payload

    # -- identity / IEEE-488.2 -------------------------------------------------

    @capability("identity.driver.read", category="identity", risk_level="none")
    def get_identity(self, alias: str | None = None, refresh: bool = True) -> str:
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
        postconditions=("Instrument reset; configuration assumptions must be re-established.",),
    )
    def reset_device(
        self,
        alias: str | None = None,
        wait_until_ready: bool = True,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        self.reset(alias=alias)
        if wait_until_ready:
            key = self._resolve_alias(alias)
            effective = timeout_s if timeout_s is not None else self._io_timeout(key)
            self._execute_operation(
                "reset_device",
                lambda session: session.client.query("*OPC?", timeout_s=effective),
                alias=key,
                required_states=_CONNECTED_STATES,
            )
        return {"alias": self._resolve_alias(alias), "reset": True}

    def reset(self, alias: str | None = None) -> None:
        self.write_checked("*RST", alias=alias, operation="reset")

    def clear_status(self, alias: str | None = None) -> None:
        self._write("*CLS", alias=alias, operation="clear_status")

    def self_test(self, alias: str | None = None) -> SelfTestResult:
        response = self._query("*TST?", alias=alias, operation="self_test")
        if "," in response:
            code_s, message = response.split(",", 1)
        else:
            code_s, message = response, ""
        try:
            code = parse_int(code_s)
        except ResponseParseError as exc:
            raise DriverMalformedResponseError(f"invalid *TST? reply {response!r}", code="LPDS-PRT-015") from exc
        return SelfTestResult(code, message.strip().strip('"'))

    def operation_complete(self, alias: str | None = None) -> bool:
        response = self._query("*OPC?", alias=alias, operation="operation_complete")
        try:
            return parse_bool(response)
        except ResponseParseError as exc:
            raise DriverMalformedResponseError(f"invalid *OPC? reply {response!r}", code="LPDS-PRT-015") from exc

    def wait(self, alias: str | None = None) -> None:
        self._write("*WAI", alias=alias, operation="wait")

    def status_byte(self, alias: str | None = None) -> int:
        return self._query_int("*STB?", alias=alias, operation="status_byte")

    def standard_event_status(self, alias: str | None = None) -> int:
        return self._query_int("*ESR?", alias=alias, operation="standard_event_status")

    def _query_int(self, command: str, *, alias: str | None, operation: str) -> int:
        response = self._query(command, alias=alias, operation=operation)
        try:
            return parse_int(response)
        except ResponseParseError as exc:
            raise DriverMalformedResponseError(
                f"invalid integer reply {response!r} for {command}", code="LPDS-PRT-015"
            ) from exc

    def _query_float(self, command: str, *, alias: str | None, operation: str) -> float:
        response = self._query(command, alias=alias, operation=operation)
        try:
            return parse_float(response)
        except ResponseParseError as exc:
            raise DriverMalformedResponseError(
                f"invalid float reply {response!r} for {command}", code="LPDS-PRT-015"
            ) from exc

    _REMOTE_TO_TOKEN: Mapping[RemoteState, str] = {
        "local": "LOC",
        "remote": "REM",
        "remote_lockout": "RWL",
    }

    def get_remote_state(self, alias: str | None = None) -> RemoteState:
        reply = self._query("SYST:COMM:RLST?", alias=alias, operation="get_remote_state").strip().upper()
        for state, token in self._REMOTE_TO_TOKEN.items():
            if reply.startswith(token):
                return state
        raise DriverMalformedResponseError(
            f"unexpected SYST:COMM:RLST? reply {reply!r}", code="LPDS-PRT-012"
        )

    def set_remote_state(self, state: RemoteState, alias: str | None = None) -> None:
        if state not in self._REMOTE_TO_TOKEN:
            raise DriverUnsupportedValueError(f"unknown remote state {state!r}", code="LPDS-ARG-030")
        self.write_checked(
            f"SYST:COMM:RLST {self._REMOTE_TO_TOKEN[state]}",
            alias=alias,
            operation="set_remote_state",
        )

    def remote_lockout(self, enabled: bool, alias: str | None = None) -> None:
        self.set_remote_state("remote_lockout" if enabled else "remote", alias=alias)

    @capability("system.error.read", category="system", risk_level="none")
    def get_device_error(self, alias: str | None = None) -> dict[str, Any]:
        record = self.get_error(alias=alias)
        return {"code": record.code, "message": record.message, "is_ok": record.is_ok}

    def get_all_device_errors(
        self, alias: str | None = None, max_count: int = 100
    ) -> list[dict[str, Any]]:
        errors = self.drain_errors(alias=alias, max_entries=max_count)
        return [{"code": error.code, "message": error.message} for error in errors]

    @capability("system.error.clear", category="system", risk_level="low", mutating=True)
    def clear_device_errors(self, alias: str | None = None) -> None:
        self.drain_errors(alias=alias)

    def device_error_queue_should_be_empty(self, alias: str | None = None) -> None:
        self.check_errors(alias=alias)

    def get_error(self, alias: str | None = None) -> ScpiErrorRecord:
        return parse_error(self._query("SYST:ERR?", alias=alias, operation="get_error"))

    def drain_errors(
        self, alias: str | None = None, max_entries: int = 32
    ) -> list[ScpiErrorRecord]:
        if max_entries < 1:
            raise DriverArgumentValueError("max_entries must be positive", code="LPDS-ARG-006")
        key = self._resolve_alias(alias)
        timeout = self._io_timeout(key)

        def action(session: ScpiSession) -> list[ScpiErrorRecord]:
            queue = ScpiErrorQueue(client=session.client, maximum_entries=max_entries)
            return [
                ScpiErrorRecord(item.code, item.message, item.raw)
                for item in queue.drain(timeout_s=timeout)
            ]

        return self._execute_operation("drain_errors", action, alias=key, required_states=_CONNECTED_STATES)

    def check_errors(self, alias: str | None = None) -> None:
        errors = self.drain_errors(alias=alias)
        if errors:
            raise DriverCommandRejectedError(
                "instrument reported SCPI errors",
                scpi_errors=[(error.code, error.message) for error in errors],
                code="LPDS-DEV-006",
            )

    def channel_count(self, alias: str | None = None) -> int:
        key = self._resolve_alias(alias)
        discovery = self._discovery.setdefault(key, _AliasDiscovery())
        if discovery.channel_count == 0:
            discovery.channel_count = self._query_int(
                "SYST:CHAN:COUN?", alias=key, operation="channel_count"
            )
        return discovery.channel_count

    def _validate_channel(self, channel: int, alias: str | None = None) -> None:
        require_channel(channel, self.channel_count(alias=alias))

    @capability("channel.selection.list", category="channel", risk_level="none")
    def list_channels(self, alias: str | None = None) -> list[int]:
        return list(range(1, self.channel_count(alias=alias) + 1))

    def validate_channel(self, channel: int, alias: str | None = None) -> bool:
        try:
            self._validate_channel(channel, alias=alias)
        except DriverError:
            return False
        return True

    def channel_model(self, channel: int, alias: str | None = None) -> str:
        self._validate_channel(channel, alias=alias)
        return self._query(
            f"SYST:CHAN:MOD? {format_channel_list(channel)}",
            alias=alias,
            operation="channel_model",
        ).strip().strip('"')

    def channel_options(self, channel: int, alias: str | None = None) -> list[str]:
        self._validate_channel(channel, alias=alias)
        response = self._query(
            f"SYST:CHAN:OPT? {format_channel_list(channel)}",
            alias=alias,
            operation="channel_options",
        )
        normalized = response.strip().strip('"')
        if normalized in {"", "0", "+0"}:
            return []
        return [item.strip().strip('"') for item in response.split(",") if item.strip().strip('"')]

    def channel_serial(self, channel: int, alias: str | None = None) -> str:
        self._validate_channel(channel, alias=alias)
        return self._query(
            f"SYST:CHAN:SER? {format_channel_list(channel)}",
            alias=alias,
            operation="channel_serial",
        ).strip().strip('"')

    @capability("channel.module.discover", category="channel", risk_level="none")
    def discover_modules(self, alias: str | None = None) -> dict[int, ChannelCapabilities]:
        key = self._resolve_alias(alias)
        self._digitize_leases.require_idle(key, "discover_modules")
        entry = self._entry(key)
        with entry.lock:
            discovery = self._discovery.setdefault(key, _AliasDiscovery())
            # A stale channel handle must not outlive a discovery attempt, even
            # when a model/option query times out part way through rediscovery.
            for previous_channel in discovery.channels.values():
                previous_channel._invalidate_discovery()
            discovery.channels.clear()
            discovery.capabilities.clear()
            identity = self.idn(alias=key, refresh=True)
            count = self.channel_count(alias=key)
            facade = _AliasFacade(self, key)
            new_capabilities: dict[int, ChannelCapabilities] = {}
            new_channels: dict[int, BaseChannel] = {}
            for ch in range(1, count + 1):
                model = self.channel_model(ch, alias=key)
                options = self.channel_options(ch, alias=key)
                caps = classify_module(
                    model,
                    options,
                    mainframe_model=identity.model,
                    firmware_version=identity.firmware,
                )
                if caps.module_type in {"power_supply", "smu"}:
                    chanlist = format_channel_list(ch)
                    caps = replace(
                        caps,
                        min_voltage=self._query_float(
                            f"VOLT? MIN,{chanlist}", alias=key, operation="discover_modules"
                        ),
                        max_voltage=self._query_float(
                            f"VOLT? MAX,{chanlist}", alias=key, operation="discover_modules"
                        ),
                        min_current=self._query_float(
                            f"CURR? MIN,{chanlist}", alias=key, operation="discover_modules"
                        ),
                        max_current=self._query_float(
                            f"CURR? MAX,{chanlist}", alias=key, operation="discover_modules"
                        ),
                    )
                new_capabilities[ch] = caps
                if caps.module_type == "smu":
                    new_channels[ch] = SMUChannel(facade, ch, caps)
                elif caps.module_type == "power_supply":
                    new_channels[ch] = PowerSupplyChannel(facade, ch, caps)
                elif caps.module_type == "electronic_load":
                    new_channels[ch] = ElectronicLoadChannel(facade, ch, caps)
                else:
                    new_channels[ch] = BaseChannel(facade, ch, caps)
            discovery.channel_count = count
            discovery.capabilities = new_capabilities
            discovery.channels = new_channels
            return dict(new_capabilities)

    @property
    def channels(self) -> Mapping[int, BaseChannel]:
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
        result = self.channel(channel, alias=alias)
        if not isinstance(result, PowerSupplyChannel) or isinstance(result, ElectronicLoadChannel):
            raise DriverUnsupportedOperationError(f"channel {channel} is not a power-supply/SMU output")
        return result

    def smu(self, channel: int, alias: str | None = None) -> SMUChannel:
        result = self.channel(channel, alias=alias)
        if not isinstance(result, SMUChannel):
            raise DriverUnsupportedOperationError(f"channel {channel} is not an SMU")
        return result

    def load(self, channel: int, alias: str | None = None) -> ElectronicLoadChannel:
        result = self.channel(channel, alias=alias)
        if not isinstance(result, ElectronicLoadChannel):
            raise DriverUnsupportedOperationError(f"channel {channel} is not an electronic load")
        return result

    @capability(
        "source.voltage.set",
        category="source",
        risk_level="medium",
        mutating=True,
        canonical_method="set_dc_voltage",
    )
    def set_dc_voltage(self, value: float, channel: int = 1, alias: str | None = None) -> None:
        self.power_supply(channel, alias=alias).set_voltage_setpoint(value)

    def get_dc_voltage_setpoint(self, channel: int = 1, alias: str | None = None) -> float:
        return self.power_supply(channel, alias=alias).get_voltage_setpoint()

    def get_dc_voltage_limits(self, channel: int = 1, alias: str | None = None) -> dict[str, float]:
        ps = self.power_supply(channel, alias=alias)
        return {"ovp_v": ps.get_ovp(), "range_v": ps.get_voltage_range()}

    @capability(
        "source.voltage.slew.set",
        category="source",
        risk_level="medium",
        mutating=True,
        canonical_method="set_voltage_slew_rate",
    )
    def set_voltage_slew_rate(
        self, channel: int, rate_v_per_s: float | str, alias: str | None = None
    ) -> float:
        """Set voltage slew rate in V/s and return the instrument readback."""
        return self.power_supply(channel, alias=alias).set_voltage_slew_rate(rate_v_per_s)

    def get_voltage_slew_rate(self, channel: int, alias: str | None = None) -> float:
        return self.power_supply(channel, alias=alias).get_voltage_slew_rate()

    @capability(
        "source.voltage.transient.list.configure",
        category="source",
        risk_level="high",
        mutating=True,
        canonical_method="configure_voltage_list",
        preconditions=("Target source output is OFF.", "Caller supplied an approved voltage envelope.",),
    )
    def configure_voltage_list(
        self,
        channel: int,
        levels_v: Sequence[float],
        dwell_s: float | Sequence[float],
        *,
        maximum_voltage_v: float,
        minimum_voltage_v: float = 0.0,
        repeat_count: RepeatCount = 1,
        step_mode: Literal["auto", "once"] = "auto",
        terminate_last: bool = False,
        maximum_duration_s: float = 60.0,
        begin_step_triggers: bool | Sequence[bool] = False,
        end_step_triggers: bool | Sequence[bool] = False,
        alias: str | None = None,
    ) -> VoltageListConfiguration:
        """Configure and verify a bounded voltage LIST without enabling the output."""
        return self.power_supply(channel, alias=alias).configure_voltage_list(
            levels_v,
            dwell_s,
            maximum_voltage_v=maximum_voltage_v,
            minimum_voltage_v=minimum_voltage_v,
            repeat_count=repeat_count,
            step_mode=step_mode,
            terminate_last=terminate_last,
            maximum_duration_s=maximum_duration_s,
            begin_step_triggers=begin_step_triggers,
            end_step_triggers=end_step_triggers,
        )

    @capability(
        "source.voltage.transient.arb.configure",
        category="source",
        risk_level="high",
        mutating=True,
        canonical_method="configure_voltage_arb",
        preconditions=("Target source output is OFF.", "Caller supplied an approved voltage envelope.",),
    )
    def configure_voltage_arb(
        self,
        channel: int,
        levels_v: Sequence[float],
        dwell_s: float | Sequence[float],
        *,
        maximum_voltage_v: float,
        minimum_voltage_v: float = 0.0,
        repeat_count: RepeatCount = 1,
        terminate_last: bool = False,
        maximum_duration_s: float = 60.0,
        begin_step_triggers: bool | Sequence[bool] = False,
        alias: str | None = None,
    ) -> VoltageArbConfiguration:
        """Configure and verify a bounded user-defined voltage ARB."""
        return self.power_supply(channel, alias=alias).configure_voltage_arb(
            levels_v,
            dwell_s,
            maximum_voltage_v=maximum_voltage_v,
            minimum_voltage_v=minimum_voltage_v,
            repeat_count=repeat_count,
            terminate_last=terminate_last,
            maximum_duration_s=maximum_duration_s,
            begin_step_triggers=begin_step_triggers,
        )

    def configure_voltage_transient_trigger(
        self,
        channel: int,
        source: Literal["bus", "external"] = "bus",
        alias: str | None = None,
    ) -> None:
        self.power_supply(channel, alias=alias).configure_voltage_transient_trigger(source)

    @capability(
        "source.voltage.transient.arm",
        category="source",
        risk_level="high",
        mutating=True,
        canonical_method="arm_voltage_transient",
        may_energize_or_sink_power=True,
        preconditions=("A verified LIST/ARB/STEP profile exists.", "The source output is already ON.",),
    )
    def arm_voltage_transient(
        self, channel: int, alias: str | None = None, *, continuous: bool = False
    ) -> VoltageTransientToken:
        """Arm the configured source profile; ``continuous`` re-arms after every run."""
        return self.power_supply(channel, alias=alias).arm_voltage_transient(continuous=continuous)

    def wait_voltage_transient_ready(
        self,
        channel: int,
        token: VoltageTransientToken,
        *,
        timeout_s: float = 5.0,
        poll_interval_s: float = 0.01,
        alias: str | None = None,
    ) -> VoltageTransientStatus:
        return self.power_supply(channel, alias=alias).wait_voltage_transient_ready(
            token, timeout_s=timeout_s, poll_interval_s=poll_interval_s
        )

    @capability(
        "source.voltage.transient.trigger",
        category="source",
        risk_level="critical",
        mutating=True,
        canonical_method="trigger_voltage_transient",
        may_energize_or_sink_power=True,
        preconditions=("The matching generation is armed and reports WTG_transient.",),
    )
    def trigger_voltage_transient(
        self, channel: int, token: VoltageTransientToken, alias: str | None = None
    ) -> None:
        self.power_supply(channel, alias=alias).trigger_voltage_transient(token)

    def wait_voltage_transient_complete(
        self,
        channel: int,
        token: VoltageTransientToken,
        *,
        timeout_s: float | None = None,
        poll_interval_s: float = 0.01,
        alias: str | None = None,
    ) -> VoltageTransientStatus:
        """Wait for clean completion; ``timeout_s=None`` derives it from the profile."""
        return self.power_supply(channel, alias=alias).wait_voltage_transient_complete(
            token, timeout_s=timeout_s, poll_interval_s=poll_interval_s
        )

    @capability(
        "source.voltage.transient.abort",
        category="source",
        risk_level="low",
        mutating=True,
        canonical_method="abort_voltage_transient",
    )
    def abort_voltage_transient(self, channel: int, alias: str | None = None) -> None:
        self.power_supply(channel, alias=alias).abort_voltage_transient()

    def get_voltage_transient_status(
        self, channel: int, alias: str | None = None
    ) -> VoltageTransientStatus:
        return self.power_supply(channel, alias=alias).get_voltage_transient_status()

    # -- electronic-load LIST/ARB profiles (N679xA) ---------------------------

    @capability(
        "load.transient.list.configure",
        category="load",
        risk_level="high",
        mutating=True,
        canonical_method="configure_load_list",
        preconditions=(
            "Target load input is OFF.",
            "Load priority mode matches the profile mode.",
            "Caller supplied an approved level envelope (and maximum_current_a for cv/cr/cp).",
        ),
    )
    def configure_load_list(
        self,
        channel: int,
        mode: LoadProfileMode,
        levels: Sequence[float],
        dwell_s: float | Sequence[float],
        *,
        maximum_level: float,
        minimum_level: float = 0.0,
        maximum_current_a: float | None = None,
        repeat_count: RepeatCount = 1,
        step_mode: Literal["auto", "once"] = "auto",
        terminate_last: bool = False,
        maximum_duration_s: float = 60.0,
        begin_step_triggers: bool | Sequence[bool] = False,
        end_step_triggers: bool | Sequence[bool] = False,
        alias: str | None = None,
    ) -> LoadProfileConfiguration:
        """Configure and verify a bounded N679xA load LIST without enabling the input."""
        return self.load(channel, alias=alias).configure_load_list(
            mode,
            levels,
            dwell_s,
            maximum_level=maximum_level,
            minimum_level=minimum_level,
            maximum_current_a=maximum_current_a,
            repeat_count=repeat_count,
            step_mode=step_mode,
            terminate_last=terminate_last,
            maximum_duration_s=maximum_duration_s,
            begin_step_triggers=begin_step_triggers,
            end_step_triggers=end_step_triggers,
        )

    @capability(
        "load.transient.arb.configure",
        category="load",
        risk_level="high",
        mutating=True,
        canonical_method="configure_load_arb",
        preconditions=(
            "Target load input is OFF.",
            "Load priority mode matches the profile mode.",
            "Caller supplied an approved level envelope (and maximum_current_a for cv/cr/cp).",
        ),
    )
    def configure_load_arb(
        self,
        channel: int,
        mode: LoadProfileMode,
        levels: Sequence[float],
        dwell_s: float | Sequence[float],
        *,
        maximum_level: float,
        minimum_level: float = 0.0,
        maximum_current_a: float | None = None,
        repeat_count: RepeatCount = 1,
        terminate_last: bool = False,
        maximum_duration_s: float = 60.0,
        begin_step_triggers: bool | Sequence[bool] = False,
        alias: str | None = None,
    ) -> LoadProfileConfiguration:
        """Configure and verify a user-defined N679xA load ARB without enabling the input."""
        return self.load(channel, alias=alias).configure_load_arb(
            mode,
            levels,
            dwell_s,
            maximum_level=maximum_level,
            minimum_level=minimum_level,
            maximum_current_a=maximum_current_a,
            repeat_count=repeat_count,
            terminate_last=terminate_last,
            maximum_duration_s=maximum_duration_s,
            begin_step_triggers=begin_step_triggers,
        )

    @capability(
        "load.transient.cdw_arb.configure",
        category="load",
        risk_level="high",
        mutating=True,
        canonical_method="configure_load_constant_dwell_arb",
        preconditions=(
            "Target load input is OFF.",
            "Load priority mode matches the profile mode.",
            "Caller supplied an approved level envelope (and maximum_current_a for cv/cr/cp).",
        ),
    )
    def configure_load_constant_dwell_arb(
        self,
        channel: int,
        mode: LoadProfileMode,
        levels: Sequence[float],
        dwell_s: float,
        *,
        maximum_level: float,
        minimum_level: float = 0.0,
        maximum_current_a: float | None = None,
        repeat_count: RepeatCount = 1,
        terminate_last: bool = False,
        maximum_duration_s: float = 60.0,
        alias: str | None = None,
    ) -> LoadProfileConfiguration:
        """Configure and verify a constant-dwell N679xA load ARB without enabling the input."""
        return self.load(channel, alias=alias).configure_load_constant_dwell_arb(
            mode,
            levels,
            dwell_s,
            maximum_level=maximum_level,
            minimum_level=minimum_level,
            maximum_current_a=maximum_current_a,
            repeat_count=repeat_count,
            terminate_last=terminate_last,
            maximum_duration_s=maximum_duration_s,
        )

    @capability(
        "load.transient.trigger.configure",
        category="load",
        risk_level="medium",
        mutating=True,
        canonical_method="configure_load_transient_trigger",
        preconditions=("A verified load profile exists and is not armed.",),
        side_effects=("An ARB profile also sets the mainframe-wide TRIG:ARB:SOUR.",),
    )
    def configure_load_transient_trigger(
        self,
        channel: int,
        source: Literal["bus", "external"] = "bus",
        alias: str | None = None,
    ) -> None:
        self.load(channel, alias=alias).configure_load_transient_trigger(source)

    @capability(
        "load.transient.arm",
        category="load",
        risk_level="high",
        mutating=True,
        canonical_method="arm_load_transient",
        may_energize_or_sink_power=True,
        preconditions=("A verified load profile exists.", "The load input is already ON.",),
    )
    def arm_load_transient(self, channel: int, alias: str | None = None) -> LoadTransientToken:
        """Arm the configured load profile."""
        return self.load(channel, alias=alias).arm_load_transient()

    def wait_load_transient_ready(
        self,
        channel: int,
        token: LoadTransientToken,
        *,
        timeout_s: float = 5.0,
        poll_interval_s: float = 0.01,
        alias: str | None = None,
    ) -> LoadTransientStatus:
        return self.load(channel, alias=alias).wait_load_transient_ready(
            token, timeout_s=timeout_s, poll_interval_s=poll_interval_s
        )

    @capability(
        "load.transient.trigger",
        category="load",
        risk_level="critical",
        mutating=True,
        canonical_method="trigger_load_transient",
        may_energize_or_sink_power=True,
        preconditions=("The matching generation is armed and reports WTG_transient.",),
    )
    def trigger_load_transient(
        self, channel: int, token: LoadTransientToken, alias: str | None = None
    ) -> None:
        self.load(channel, alias=alias).trigger_load_transient(token)

    def wait_load_transient_complete(
        self,
        channel: int,
        token: LoadTransientToken,
        *,
        timeout_s: float | None = None,
        poll_interval_s: float = 0.01,
        alias: str | None = None,
    ) -> LoadTransientStatus:
        """Wait for clean completion; ``timeout_s=None`` derives it from the profile."""
        return self.load(channel, alias=alias).wait_load_transient_complete(
            token, timeout_s=timeout_s, poll_interval_s=poll_interval_s
        )

    @capability(
        "load.transient.abort",
        category="load",
        risk_level="low",
        mutating=True,
        canonical_method="abort_load_transient",
    )
    def abort_load_transient(self, channel: int, alias: str | None = None) -> None:
        self.load(channel, alias=alias).abort_load_transient()

    @capability(
        "load.transient.clear",
        category="load",
        risk_level="low",
        mutating=True,
        canonical_method="clear_load_profile",
    )
    def clear_load_profile(self, channel: int, alias: str | None = None) -> None:
        """Abort and return every load quantity to FIX."""
        self.load(channel, alias=alias).clear_load_profile()

    def get_load_transient_status(self, channel: int, alias: str | None = None) -> LoadTransientStatus:
        return self.load(channel, alias=alias).get_load_transient_status()

    # -- current slew, current/shape/sequence/step transients -----------------

    @capability(
        "source.current.slew.set",
        category="source",
        risk_level="medium",
        mutating=True,
        canonical_method="set_current_slew_rate",
    )
    def set_current_slew_rate(
        self, channel: int, rate_a_per_s: float | str, alias: str | None = None
    ) -> float:
        """Set current slew rate in A/s (N678xA SMU) and return the readback."""
        return self.power_supply(channel, alias=alias).set_current_slew_rate(rate_a_per_s)

    def get_current_slew_rate(self, channel: int, alias: str | None = None) -> float:
        return self.power_supply(channel, alias=alias).get_current_slew_rate()

    @capability(
        "source.current.transient.list.configure",
        category="source",
        risk_level="high",
        mutating=True,
        canonical_method="configure_current_list",
        preconditions=("Target source output is OFF.", "Caller supplied an approved current envelope.",),
    )
    def configure_current_list(
        self,
        channel: int,
        levels_a: Sequence[float],
        dwell_s: float | Sequence[float],
        *,
        maximum_current_a: float,
        minimum_current_a: float = 0.0,
        repeat_count: RepeatCount = 1,
        step_mode: Literal["auto", "once"] = "auto",
        terminate_last: bool = False,
        maximum_duration_s: float = 60.0,
        begin_step_triggers: bool | Sequence[bool] = False,
        end_step_triggers: bool | Sequence[bool] = False,
        alias: str | None = None,
    ) -> CurrentListConfiguration:
        """Configure and verify a bounded current LIST without enabling the output."""
        return self.power_supply(channel, alias=alias).configure_current_list(
            levels_a,
            dwell_s,
            maximum_current_a=maximum_current_a,
            minimum_current_a=minimum_current_a,
            repeat_count=repeat_count,
            step_mode=step_mode,
            terminate_last=terminate_last,
            maximum_duration_s=maximum_duration_s,
            begin_step_triggers=begin_step_triggers,
            end_step_triggers=end_step_triggers,
        )

    @capability(
        "source.current.transient.arb.configure",
        category="source",
        risk_level="high",
        mutating=True,
        canonical_method="configure_current_arb",
        preconditions=("Target source output is OFF.", "Caller supplied an approved current envelope.",),
    )
    def configure_current_arb(
        self,
        channel: int,
        levels_a: Sequence[float],
        dwell_s: float | Sequence[float],
        *,
        maximum_current_a: float,
        minimum_current_a: float = 0.0,
        repeat_count: RepeatCount = 1,
        terminate_last: bool = False,
        maximum_duration_s: float = 60.0,
        begin_step_triggers: bool | Sequence[bool] = False,
        alias: str | None = None,
    ) -> CurrentArbConfiguration:
        """Configure and verify a bounded user-defined current ARB."""
        return self.power_supply(channel, alias=alias).configure_current_arb(
            levels_a,
            dwell_s,
            maximum_current_a=maximum_current_a,
            minimum_current_a=minimum_current_a,
            repeat_count=repeat_count,
            terminate_last=terminate_last,
            maximum_duration_s=maximum_duration_s,
            begin_step_triggers=begin_step_triggers,
        )

    @capability(
        "source.voltage.transient.arb_waveform.configure",
        category="source",
        risk_level="high",
        mutating=True,
        canonical_method="configure_voltage_arb_waveform",
        preconditions=("Target source output is OFF.", "Caller supplied an approved voltage envelope.",),
    )
    def configure_voltage_arb_waveform(
        self,
        channel: int,
        waveform: ArbShape,
        *,
        maximum_voltage_v: float,
        minimum_voltage_v: float = 0.0,
        repeat_count: RepeatCount = 1,
        terminate_last: bool = False,
        maximum_duration_s: float = 60.0,
        alias: str | None = None,
    ) -> ArbWaveformConfiguration:
        """Configure and verify a built-in voltage ARB shape (step, ramp, sine, ...)."""
        return self.power_supply(channel, alias=alias).configure_voltage_arb_waveform(
            waveform,
            maximum_voltage_v=maximum_voltage_v,
            minimum_voltage_v=minimum_voltage_v,
            repeat_count=repeat_count,
            terminate_last=terminate_last,
            maximum_duration_s=maximum_duration_s,
        )

    @capability(
        "source.current.transient.arb_waveform.configure",
        category="source",
        risk_level="high",
        mutating=True,
        canonical_method="configure_current_arb_waveform",
        preconditions=("Target source output is OFF.", "Caller supplied an approved current envelope.",),
    )
    def configure_current_arb_waveform(
        self,
        channel: int,
        waveform: ArbShape,
        *,
        maximum_current_a: float,
        minimum_current_a: float = 0.0,
        repeat_count: RepeatCount = 1,
        terminate_last: bool = False,
        maximum_duration_s: float = 60.0,
        alias: str | None = None,
    ) -> ArbWaveformConfiguration:
        """Configure and verify a built-in current ARB shape (step, ramp, sine, ...)."""
        return self.power_supply(channel, alias=alias).configure_current_arb_waveform(
            waveform,
            maximum_current_a=maximum_current_a,
            minimum_current_a=minimum_current_a,
            repeat_count=repeat_count,
            terminate_last=terminate_last,
            maximum_duration_s=maximum_duration_s,
        )

    @capability(
        "source.voltage.transient.constant_dwell.configure",
        category="source",
        risk_level="high",
        mutating=True,
        canonical_method="configure_voltage_constant_dwell_arb",
        preconditions=("Target source output is OFF.", "Caller supplied an approved voltage envelope.",),
    )
    def configure_voltage_constant_dwell_arb(
        self,
        channel: int,
        levels_v: Sequence[float],
        dwell_s: float,
        *,
        maximum_voltage_v: float,
        minimum_voltage_v: float = 0.0,
        repeat_count: RepeatCount = 1,
        terminate_last: bool = False,
        maximum_duration_s: float = 60.0,
        alias: str | None = None,
    ) -> ConstantDwellArbConfiguration:
        """Configure and verify a constant-dwell voltage ARB (up to 65,535 points)."""
        return self.power_supply(channel, alias=alias).configure_voltage_constant_dwell_arb(
            levels_v,
            dwell_s,
            maximum_voltage_v=maximum_voltage_v,
            minimum_voltage_v=minimum_voltage_v,
            repeat_count=repeat_count,
            terminate_last=terminate_last,
            maximum_duration_s=maximum_duration_s,
        )

    @capability(
        "source.current.transient.constant_dwell.configure",
        category="source",
        risk_level="high",
        mutating=True,
        canonical_method="configure_current_constant_dwell_arb",
        preconditions=("Target source output is OFF.", "Caller supplied an approved current envelope.",),
    )
    def configure_current_constant_dwell_arb(
        self,
        channel: int,
        levels_a: Sequence[float],
        dwell_s: float,
        *,
        maximum_current_a: float,
        minimum_current_a: float = 0.0,
        repeat_count: RepeatCount = 1,
        terminate_last: bool = False,
        maximum_duration_s: float = 60.0,
        alias: str | None = None,
    ) -> ConstantDwellArbConfiguration:
        """Configure and verify a constant-dwell current ARB (up to 65,535 points)."""
        return self.power_supply(channel, alias=alias).configure_current_constant_dwell_arb(
            levels_a,
            dwell_s,
            maximum_current_a=maximum_current_a,
            minimum_current_a=minimum_current_a,
            repeat_count=repeat_count,
            terminate_last=terminate_last,
            maximum_duration_s=maximum_duration_s,
        )

    @capability(
        "source.voltage.transient.arb_sequence.configure",
        category="source",
        risk_level="high",
        mutating=True,
        canonical_method="configure_voltage_arb_sequence",
        preconditions=("Target source output is OFF.", "Caller supplied an approved voltage envelope.",),
    )
    def configure_voltage_arb_sequence(
        self,
        channel: int,
        steps: Sequence[ArbSequenceStep],
        *,
        maximum_voltage_v: float,
        minimum_voltage_v: float = 0.0,
        sequence_count: RepeatCount = 1,
        terminate_last: bool = False,
        maximum_duration_s: float = 60.0,
        alias: str | None = None,
    ) -> ArbSequenceConfiguration:
        """Configure and verify a voltage ARB sequence of up to 100 shapes."""
        return self.power_supply(channel, alias=alias).configure_voltage_arb_sequence(
            steps,
            maximum_voltage_v=maximum_voltage_v,
            minimum_voltage_v=minimum_voltage_v,
            sequence_count=sequence_count,
            terminate_last=terminate_last,
            maximum_duration_s=maximum_duration_s,
        )

    @capability(
        "source.current.transient.arb_sequence.configure",
        category="source",
        risk_level="high",
        mutating=True,
        canonical_method="configure_current_arb_sequence",
        preconditions=("Target source output is OFF.", "Caller supplied an approved current envelope.",),
    )
    def configure_current_arb_sequence(
        self,
        channel: int,
        steps: Sequence[ArbSequenceStep],
        *,
        maximum_current_a: float,
        minimum_current_a: float = 0.0,
        sequence_count: RepeatCount = 1,
        terminate_last: bool = False,
        maximum_duration_s: float = 60.0,
        alias: str | None = None,
    ) -> ArbSequenceConfiguration:
        """Configure and verify a current ARB sequence of up to 100 shapes."""
        return self.power_supply(channel, alias=alias).configure_current_arb_sequence(
            steps,
            maximum_current_a=maximum_current_a,
            minimum_current_a=minimum_current_a,
            sequence_count=sequence_count,
            terminate_last=terminate_last,
            maximum_duration_s=maximum_duration_s,
        )

    @capability(
        "source.transient.arb_sequence.store",
        category="source",
        risk_level="low",
        mutating=True,
        canonical_method="store_arb_sequence",
        preconditions=("The channel ARB function is a sequence.",),
    )
    def store_arb_sequence(self, channel: int, filename: str, alias: str | None = None) -> None:
        """Save the channel's ARB sequence to an instrument ``.seq`` file."""
        self.power_supply(channel, alias=alias).store_arb_sequence(filename)

    @capability(
        "source.voltage.transient.arb_sequence.load",
        category="source",
        risk_level="high",
        mutating=True,
        canonical_method="load_voltage_arb_sequence",
        preconditions=("Target source output is OFF.", "Caller supplied an approved voltage envelope.",),
    )
    def load_voltage_arb_sequence(
        self,
        channel: int,
        filename: str,
        *,
        maximum_voltage_v: float,
        minimum_voltage_v: float = 0.0,
        maximum_duration_s: float = 60.0,
        allow_infinite: bool = False,
        alias: str | None = None,
    ) -> ArbSequenceConfiguration:
        """Load a ``.seq`` file and accept it only inside the voltage envelope."""
        return self.power_supply(channel, alias=alias).load_voltage_arb_sequence(
            filename,
            maximum_voltage_v=maximum_voltage_v,
            minimum_voltage_v=minimum_voltage_v,
            maximum_duration_s=maximum_duration_s,
            allow_infinite=allow_infinite,
        )

    @capability(
        "source.current.transient.arb_sequence.load",
        category="source",
        risk_level="high",
        mutating=True,
        canonical_method="load_current_arb_sequence",
        preconditions=("Target source output is OFF.", "Caller supplied an approved current envelope.",),
    )
    def load_current_arb_sequence(
        self,
        channel: int,
        filename: str,
        *,
        maximum_current_a: float,
        minimum_current_a: float = 0.0,
        maximum_duration_s: float = 60.0,
        allow_infinite: bool = False,
        alias: str | None = None,
    ) -> ArbSequenceConfiguration:
        """Load a ``.seq`` file and accept it only inside the current envelope."""
        return self.power_supply(channel, alias=alias).load_current_arb_sequence(
            filename,
            maximum_current_a=maximum_current_a,
            minimum_current_a=minimum_current_a,
            maximum_duration_s=maximum_duration_s,
            allow_infinite=allow_infinite,
        )

    @capability(
        "source.voltage.transient.step.configure",
        category="source",
        risk_level="high",
        mutating=True,
        canonical_method="configure_voltage_step",
        preconditions=("Target source output is OFF.", "Caller supplied an approved voltage envelope.",),
    )
    def configure_voltage_step(
        self,
        channel: int,
        triggered_voltage_v: float,
        *,
        maximum_voltage_v: float,
        minimum_voltage_v: float = 0.0,
        trigger_output: bool = False,
        alias: str | None = None,
    ) -> StepTransientConfiguration:
        """Configure and verify a triggered voltage step (``VOLT:MODE STEP``)."""
        return self.power_supply(channel, alias=alias).configure_voltage_step(
            triggered_voltage_v,
            maximum_voltage_v=maximum_voltage_v,
            minimum_voltage_v=minimum_voltage_v,
            trigger_output=trigger_output,
        )

    @capability(
        "source.current.transient.step.configure",
        category="source",
        risk_level="high",
        mutating=True,
        canonical_method="configure_current_step",
        preconditions=("Target source output is OFF.", "Caller supplied an approved current envelope.",),
    )
    def configure_current_step(
        self,
        channel: int,
        triggered_current_a: float,
        *,
        maximum_current_a: float,
        minimum_current_a: float = 0.0,
        trigger_output: bool = False,
        alias: str | None = None,
    ) -> StepTransientConfiguration:
        """Configure and verify a triggered current step (``CURR:MODE STEP``)."""
        return self.power_supply(channel, alias=alias).configure_current_step(
            triggered_current_a,
            maximum_current_a=maximum_current_a,
            minimum_current_a=minimum_current_a,
            trigger_output=trigger_output,
        )

    @capability(
        "source.current.set",
        category="source",
        risk_level="medium",
        mutating=True,
        canonical_method="set_dc_current",
    )
    def set_dc_current(self, value: float, channel: int = 1, alias: str | None = None) -> None:
        self.power_supply(channel, alias=alias).set_current_limit(value)

    def get_dc_current_setpoint(self, channel: int = 1, alias: str | None = None) -> float:
        return self.power_supply(channel, alias=alias).get_current_limit()

    def get_dc_current_limits(self, channel: int = 1, alias: str | None = None) -> dict[str, float]:
        ps = self.power_supply(channel, alias=alias)
        return {"range_a": ps.get_current_range()}

    def _set_outputs_verified(
        self,
        channels: Sequence[int],
        enabled: bool,
        *,
        alias: str | None,
        operation: str,
    ) -> None:
        key = self._resolve_alias(alias)
        unique = sorted(set(channels))
        if not unique:
            raise DriverArgumentValueError("at least one channel is required", code="LPDS-ARG-003")
        for channel_number in unique:
            self._validate_channel(channel_number, alias=key)
        chanlist = format_channel_list(unique)
        self.write_checked(
            f"OUTP {format_bool(enabled)},{chanlist}", alias=key, operation=operation
        )
        self._query("*OPC?", alias=key, operation=operation)
        response = self._query(f"OUTP? {chanlist}", alias=key, operation=operation)
        try:
            states = [parse_bool(item) for item in parse_csv(response)]
        except ResponseParseError as exc:
            raise DriverMalformedResponseError(
                f"invalid OUTP? reply {response!r}", code="LPDS-PRT-016"
            ) from exc
        if len(states) != len(unique) or any(state != enabled for state in states):
            raise DriverOperationUncertainError(
                f"output state mismatch: {dict(zip(unique, states, strict=False))}",
                code="LPDS-STA-020",
            )

    def set_power_outputs(
        self, channels: Sequence[int], enabled: bool, alias: str | None = None
    ) -> None:
        key = self._resolve_alias(alias)
        unique = sorted(set(channels))
        for channel_number in unique:
            self.power_supply(channel_number, alias=key)
        self._set_outputs_verified(unique, enabled, alias=key, operation="set_power_outputs")

    def set_load_inputs(
        self, channels: Sequence[int], enabled: bool, alias: str | None = None
    ) -> None:
        key = self._resolve_alias(alias)
        unique = sorted(set(channels))
        loads = [self.load(channel_number, alias=key) for channel_number in unique]
        real_channels = [load.channel for load in loads if load.capabilities.model != "SIM_LOAD"]
        sim_channels = [load.channel for load in loads if load.capabilities.model == "SIM_LOAD"]
        if real_channels:
            self._set_outputs_verified(real_channels, enabled, alias=key, operation="set_load_inputs")
        for channel_number in sim_channels:
            self.load(channel_number, alias=key).set_input(enabled)

    def set_channel_enabled(
        self, channels: Sequence[int], enabled: bool, alias: str | None = None
    ) -> None:
        key = self._resolve_alias(alias)
        unique = sorted(set(channels))
        regular: list[int] = []
        simulated_loads: list[int] = []
        for channel_number in unique:
            item = self.channel(channel_number, alias=key)
            if isinstance(item, ElectronicLoadChannel) and item.capabilities.model == "SIM_LOAD":
                simulated_loads.append(channel_number)
            elif isinstance(item, (PowerSupplyChannel, ElectronicLoadChannel)):
                regular.append(channel_number)
            else:
                raise DriverUnsupportedOperationError(f"channel {channel_number} cannot be enabled")
        if regular:
            self._set_outputs_verified(regular, enabled, alias=key, operation="set_channel_enabled")
        for channel_number in simulated_loads:
            self.load(channel_number, alias=key).set_input(enabled)

    @capability("source.output.enable", category="source", risk_level="high", mutating=True, may_energize_or_sink_power=True)
    def enable_output(self, channel: int, alias: str | None = None) -> None:
        self.set_power_outputs([channel], True, alias=alias)

    @capability("source.output.disable", category="source", risk_level="low", mutating=True)
    def disable_output(self, channel: int, alias: str | None = None) -> None:
        self.set_power_outputs([channel], False, alias=alias)

    def get_output_state(self, channel: int, alias: str | None = None) -> bool:
        return self.power_supply(channel, alias=alias).get_output()

    def output_should_be_enabled(self, channel: int, alias: str | None = None) -> None:
        if not self.get_output_state(channel, alias=alias):
            raise DriverPreconditionError(f"channel {channel} output is not enabled", code="LPDS-STA-006")

    def output_should_be_disabled(self, channel: int, alias: str | None = None) -> None:
        if self.get_output_state(channel, alias=alias):
            raise DriverPreconditionError(f"channel {channel} output is not disabled", code="LPDS-STA-007")

    @capability("measure.voltage.dc", category="measure", risk_level="none", canonical_method="measure_dc_voltage")
    def measure_dc_voltage(self, channel: int = 1, alias: str | None = None) -> float:
        return self.channel(channel, alias=alias).measure_voltage()

    @capability("measure.current.dc", category="measure", risk_level="none", canonical_method="measure_dc_current")
    def measure_dc_current(self, channel: int = 1, alias: str | None = None) -> float:
        return self.channel(channel, alias=alias).measure_current()

    @capability("measure.power.dc", category="measure", risk_level="none", canonical_method="measure_dc_power")
    def measure_dc_power(self, channel: int = 1, alias: str | None = None) -> float | None:
        return self._measure_power(channel, alias=alias).power_W

    def measure_power(self, channel: int, alias: str | None = None) -> PowerMeasurement:
        return self._measure_power(channel, alias=alias)

    def measure_all(self, alias: str | None = None) -> dict[int, Measurement]:
        key = self._resolve_alias(alias)
        if not self._discovery.get(key, _AliasDiscovery()).channels:
            self.discover_modules(alias=key)
        return {
            channel_number: self._measure_channel(channel_number, alias=key)
            for channel_number in self._discovery[key].channels
        }

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
        minimum_v = require_number("minimum", minimum)
        maximum_v = require_number("maximum", maximum)
        if minimum_v > maximum_v:
            raise DriverArgumentValueError("minimum must not exceed maximum", code="LPDS-ARG-021")
        last = 0.0

        def predicate() -> bool:
            nonlocal last
            last = self.measure_dc_voltage(channel, alias=alias)
            return minimum_v <= last <= maximum_v

        with translated(operation="wait_for_voltage_in_range"):
            poll_until(
                predicate,
                timeout_s=timeout_s,
                interval_s=poll_interval_s,
                description=f"channel {channel} voltage in [{minimum_v:.12g}, {maximum_v:.12g}] V",
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
        minimum_a = require_number("minimum", minimum)
        maximum_a = require_number("maximum", maximum)
        if minimum_a > maximum_a:
            raise DriverArgumentValueError("minimum must not exceed maximum", code="LPDS-ARG-021")
        last = 0.0

        def predicate() -> bool:
            nonlocal last
            last = self.measure_dc_current(channel, alias=alias)
            return minimum_a <= last <= maximum_a

        with translated(operation="wait_for_current_in_range"):
            poll_until(
                predicate,
                timeout_s=timeout_s,
                interval_s=poll_interval_s,
                description=f"channel {channel} current in [{minimum_a:.12g}, {maximum_a:.12g}] A",
            )
        return last

    @staticmethod
    def _timestamp() -> tuple[str, float]:
        timestamp = time.time()
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(), timestamp

    def _measure_power(self, channel: int, alias: str | None = None) -> PowerMeasurement:
        chan = self.channel(channel, alias=alias)
        iso, timestamp = self._timestamp()
        if chan.capabilities.supports_power_measurement:
            power = chan._query_float(f"MEAS:POW? {format_channel_list(channel)}")
            return PowerMeasurement(channel, power, "instrument", iso, timestamp)
        voltage = chan.measure_voltage()
        current = chan.measure_current()
        return PowerMeasurement(channel, voltage * current, "calculated", iso, timestamp)

    def _measure_channel(self, channel: int, alias: str | None = None) -> Measurement:
        chan = self.channel(channel, alias=alias)
        iso, timestamp = self._timestamp()
        if chan.capabilities.supports_simultaneous_vi and chan.capabilities.supports_power_measurement:
            power = chan._query_float(f"MEAS:POW? {format_channel_list(channel)}")
            voltage = chan.fetch_voltage()
            current = chan.fetch_current()
            return Measurement(channel, voltage, current, power, "instrument", iso, timestamp, True)
        voltage = chan.measure_voltage()
        current = chan.measure_current()
        return Measurement(
            channel,
            voltage,
            current,
            voltage * current,
            "calculated",
            iso,
            timestamp,
            False,
        )

    def _fetch_power(self, channel: int, alias: str | None = None) -> PowerMeasurement:
        chan = self.channel(channel, alias=alias)
        iso, timestamp = self._timestamp()
        if chan.capabilities.supports_power_measurement:
            power = chan._query_float(f"FETC:POW? {format_channel_list(channel)}")
            return PowerMeasurement(channel, power, "instrument", iso, timestamp)
        voltage = chan.fetch_voltage()
        current = chan.fetch_current()
        return PowerMeasurement(channel, voltage * current, "calculated", iso, timestamp)

    def _fetch_channel(self, channel: int, alias: str | None = None) -> Measurement:
        chan = self.channel(channel, alias=alias)
        iso, timestamp = self._timestamp()
        voltage = chan.fetch_voltage()
        current = chan.fetch_current()
        if chan.capabilities.supports_power_measurement:
            power = chan._query_float(f"FETC:POW? {format_channel_list(channel)}")
            source: Literal["instrument", "calculated", "unavailable"] = "instrument"
        else:
            power = voltage * current
            source = "calculated"
        return Measurement(
            channel,
            voltage,
            current,
            power,
            source,
            iso,
            timestamp,
            chan.capabilities.supports_simultaneous_vi,
        )

    def set_smu_mode(
        self, channel: int, mode: Literal["voltage", "current"], alias: str | None = None
    ) -> None:
        self.smu(channel, alias=alias).set_smu_mode(mode)

    def get_smu_mode(
        self, channel: int, alias: str | None = None
    ) -> Literal["voltage", "current"]:
        return self.smu(channel, alias=alias).get_smu_mode()

    def configure_smu_voltage_priority(
        self,
        channel: int,
        voltage: float,
        current_limit: float,
        alias: str | None = None,
        **kwargs: Any,
    ) -> None:
        self.smu(channel, alias=alias).configure_voltage_priority(voltage, current_limit, **kwargs)

    def configure_smu_current_priority(
        self,
        channel: int,
        current: float,
        voltage_limit: float,
        alias: str | None = None,
        **kwargs: Any,
    ) -> None:
        self.smu(channel, alias=alias).configure_current_priority(current, voltage_limit, **kwargs)

    def set_load_mode(
        self, channel: int, mode: Literal["cc", "cv", "cr", "cp"], alias: str | None = None
    ) -> None:
        self.load(channel, alias=alias).set_load_mode(mode)

    def get_load_mode(
        self, channel: int, alias: str | None = None
    ) -> Literal["cc", "cv", "cr", "cp"]:
        return self.load(channel, alias=alias).get_load_mode()

    def configure_load_cc(
        self,
        channel: int,
        current: float,
        *,
        input_on: bool = False,
        verify: bool = True,
        alias: str | None = None,
    ) -> None:
        self.load(channel, alias=alias).configure_cc(current, input_on=input_on, verify=verify)

    def configure_load_cv(
        self,
        channel: int,
        voltage: float,
        *,
        current_limit: float | None = None,
        input_on: bool = False,
        verify: bool = True,
        alias: str | None = None,
    ) -> None:
        self.load(channel, alias=alias).configure_cv(
            voltage, current_limit=current_limit, input_on=input_on, verify=verify
        )

    def configure_load_cr(
        self,
        channel: int,
        resistance: float,
        *,
        input_on: bool = False,
        verify: bool = True,
        alias: str | None = None,
    ) -> None:
        self.load(channel, alias=alias).configure_cr(resistance, input_on=input_on, verify=verify)

    def configure_load_cp(
        self,
        channel: int,
        power: float,
        *,
        input_on: bool = False,
        verify: bool = True,
        alias: str | None = None,
    ) -> None:
        self.load(channel, alias=alias).configure_cp(power, input_on=input_on, verify=verify)

    # -- digitized/buffered measurement ---------------------------------------

    @capability(
        "acquisition.digitize.configure",
        category="acquisition",
        risk_level="low",
        mutating=True,
        description="Configure acquisition function and sweep settings.",
        preconditions=("N679xA load module with digitizing.",),
        canonical_method="configure_digitize",
    )
    def configure_digitize(
        self,
        channel: int,
        function: Literal["voltage", "current"],
        *,
        points: int,
        interval_s: float,
        offset_points: int = 0,
        alias: str | None = None,
    ) -> None:
        """Select the measured quantity, sample interval, point count, and trigger offset for a buffered acquisition."""
        self.load(channel, alias=alias).configure_digitize(
            function, points=points, interval_s=interval_s, offset_points=offset_points
        )

    def get_verified_digitize_available_points(
        self, channel: int, alias: str | None = None
    ) -> int:
        """Fail closed: no N6700 SCPI command proves live free shared point capacity.

        The documented `SENS:SWE:POIN?` query reports configured sweep
        *allocation*, not free points. A sum subtracted from the 512-Ki
        nominal maximum is not a verified instrument report, nor is it an
        atomic budget under out-of-process SCPI access.

        For an exact requested depth, prefer `configure_digitize_verified`
        and retain its acquisition lease through fetch or confirmed abort.
        """
        self.load(channel, alias=alias)._require_digitizing()
        raise DriverUnsupportedOperationError(
            "instrument-verified available digitizer points are not exposed by a supported "
            "SCPI readback; use configure_digitize_verified for a requested exact point count"
        )

    def configure_digitize_verified(
        self,
        channel: int,
        function: Literal["voltage", "current"],
        *,
        points: int,
        interval_s: float,
        offset_points: int = 0,
        alias: str | None = None,
    ) -> DigitizeTiming:
        """Reserve and verify an exact acquisition configuration in one session transaction.

        Holds this session's reentrant operation lock from the first setting
        through configured points/offset/interval, shared-memory consistency
        checks and error-queue readback. The driver-wide alias acquisition
        lease then remains owned by this same thread/channel until validated
        fetch or confirmed abort. If any step fails, no timing receipt is
        returned and the capture must be explicitly aborted or the faulted
        session torn down. Never silently reduce `points`.

        This proves a configured point count *at readback time*, not free
        capacity or cross-process exclusivity. Caller must explicitly secure
        the physical mainframe against external independent controllers.
        """
        key = self._resolve_alias(alias)
        with self._operation_lock(alias=key):
            target = self.load(channel, alias=key)
            target.configure_digitize(
                function, points=points, interval_s=interval_s, offset_points=offset_points
            )
            result = target.digitize_timing
            if result is None or result.points != points or result.offset_points != offset_points:
                raise DriverPreconditionError(
                    "verified acquisition timing not available after configuration"
                )
            return result

    @capability(
        "acquisition.digitize.trigger.configure",
        category="acquisition",
        risk_level="low",
        mutating=True,
        description="Configure acquisition trigger source and threshold.",
        preconditions=("N679xA load module with digitizing.",),
        canonical_method="configure_digitize_trigger",
    )
    def configure_digitize_trigger(
        self,
        channel: int,
        source: Literal["bus", "voltage", "current", "external", "transient"],
        *,
        level: float | None = None,
        slope: Literal["pos", "neg"] | None = None,
        source_channel: int | None = None,
        alias: str | None = None,
    ) -> None:
        self.load(channel, alias=alias).configure_digitize_trigger(
            source, level=level, slope=slope, source_channel=source_channel
        )

    @capability(
        "acquisition.digitize.arm",
        category="acquisition",
        risk_level="low",
        mutating=True,
        description="Arm the configured digitizer acquisition.",
        preconditions=("Digitizer configured with verified settings.",),
        canonical_method="arm_digitize",
    )
    def arm_digitize(self, channel: int, alias: str | None = None) -> None:
        self.load(channel, alias=alias).arm_digitize()

    @capability(
        "acquisition.digitize.wait_ready",
        category="acquisition",
        risk_level="low",
        mutating=True,
        description="Wait for trigger readiness and pretrigger history.",
        preconditions=("Digitizer acquisition armed.",),
        canonical_method="wait_digitize_armed",
    )
    def wait_digitize_armed(
        self,
        channel: int,
        timeout_s: float = 5.0,
        alias: str | None = None,
        *,
        pretrigger_guard_s: float = 0.005,
        abort_requested: Callable[[], bool] | None = None,
    ) -> None:
        """Wait for WTG_meas and a bounded, sample-accurate pretrigger fill."""
        self.load(channel, alias=alias).wait_digitize_armed(
            timeout_s=timeout_s,
            pretrigger_guard_s=pretrigger_guard_s,
            abort_requested=abort_requested,
        )

    @capability(
        "acquisition.digitize.trigger.now",
        category="acquisition",
        risk_level="low",
        mutating=True,
        description="Issue a software acquisition trigger.",
        preconditions=("Digitizer waiting for a trigger.",),
        canonical_method="trigger_digitize",
    )
    def trigger_digitize(self, channel: int, alias: str | None = None) -> None:
        self.load(channel, alias=alias).trigger_digitize()

    @capability(
        "acquisition.digitize.wait_complete",
        category="acquisition",
        risk_level="low",
        mutating=True,
        description="Wait for acquisition completion or abort on failure.",
        preconditions=("Digitizer acquisition armed.",),
        canonical_method="wait_digitize_complete",
    )
    def wait_digitize_complete(
        self,
        channel: int,
        timeout_s: float,
        *,
        abort_requested: Callable[[], bool] | None = None,
        alias: str | None = None,
    ) -> None:
        self.load(channel, alias=alias).wait_digitize_complete(timeout_s, abort_requested=abort_requested)

    @capability(
        "acquisition.digitize.abort",
        category="acquisition",
        risk_level="low",
        mutating=True,
        description="Abort digitizer acquisition and verify idle.",
        preconditions=("Digitizer session connected.",),
        canonical_method="abort_digitize",
    )
    def abort_digitize(self, channel: int, alias: str | None = None) -> None:
        self.load(channel, alias=alias).abort_digitize()

    @capability(
        "waveform.digitize.fetch",
        category="waveform",
        risk_level="low",
        mutating=True,
        description="Read validated waveform array and release ownership.",
        preconditions=("Current acquisition completed.",),
        canonical_method="fetch_digitized_array",
    )
    def fetch_digitized_array(
        self,
        channel: int,
        function: Literal["voltage", "current"] | None = None,
        alias: str | None = None,
        *,
        transfer_format: Literal["auto", "ascii", "real32"] = "auto",
    ) -> ArrayMeasurement:
        """Retrieve validated buffered samples, using REAL32 for large captures by default."""
        return self.load(channel, alias=alias).fetch_digitized_array(
            function, transfer_format=transfer_format
        )

    # -- protection / status --------------------------------------------------

    def clear_protection(
        self,
        channel: int,
        *,
        restore_output: bool = False,
        force_output_off_first: bool = True,
        verify_cleared: bool = True,
        timeout_s: float = 1.0,
        alias: str | None = None,
    ) -> ProtectionClearResult:
        return self.channel(channel, alias=alias).clear_protection(
            restore_output=restore_output,
            force_output_off_first=force_output_off_first,
            verify_cleared=verify_cleared,
            timeout_s=timeout_s,
        )

    def _status_channels(self, channel: int | None, alias: str | None) -> list[int]:
        if channel is not None:
            self._validate_channel(channel, alias=alias)
            return [channel]
        return self.list_channels(alias=alias)

    def _parse_status_response(self, raw: str, channels: Sequence[int]) -> int | Mapping[int, int]:
        parts = parse_csv(raw)
        if len(parts) != len(channels):
            raise DriverMalformedResponseError(
                f"status query returned {len(parts)} values for {len(channels)} channels",
                code="LPDS-PRT-017",
            )
        try:
            values = [parse_int(part) for part in parts]
        except ResponseParseError as exc:
            raise DriverMalformedResponseError(f"invalid status reply {raw!r}", code="LPDS-PRT-017") from exc
        if len(values) == 1:
            return values[0]
        return dict(zip(channels, values, strict=True))

    def get_operation_status(
        self, channel: int | None = None, alias: str | None = None
    ) -> OperationStatus:
        channels = self._status_channels(channel, alias)
        chanlist = format_channel_list(channels)
        raw = self._query(f"STAT:OPER:COND? {chanlist}", alias=alias, operation="get_operation_status")
        return OperationStatus(self._parse_status_response(raw, channels))

    def get_questionable_status(
        self, channel: int | None = None, alias: str | None = None
    ) -> QuestionableStatus:
        channels = self._status_channels(channel, alias)
        chanlist = format_channel_list(channels)
        raw = self._query(f"STAT:QUES:COND? {chanlist}", alias=alias, operation="get_questionable_status")
        return QuestionableStatus(self._parse_status_response(raw, channels))

    @capability(
        "safety.output.disable_all",
        category="safety",
        risk_level="low",
        mutating=True,
        canonical_method="safe_shutdown",
        postconditions=("Every channel has been commanded off and verified by readback.",),
    )
    def safe_shutdown(
        self, alias: str | None = None, timeout_s: float | None = None
    ) -> dict[str, Any]:
        del timeout_s
        result = self.shutdown_all(alias=alias)
        return {"success": result.success, "channels": [asdict(item) for item in result.results]}

    def shutdown_all(self, alias: str | None = None) -> ShutdownResult:
        key = self._resolve_alias(alias)
        entry = self._entry(key)
        with entry.lock:
            installed = self._discovery.get(key, _AliasDiscovery()).channels
            abort_errors: dict[int, str] = {}
            for ch_num in sorted(installed):
                try:
                    ch = self.channel(ch_num, alias=key)
                    if isinstance(ch, ElectronicLoadChannel):
                        ch._emergency_abort_load_transient()
                    elif isinstance(ch, PowerSupplyChannel):
                        ch._emergency_abort_voltage_transient()
                except Exception as exc:
                    abort_errors[ch_num] = f"transient abort failed: {exc}"

            result = self._shutdown_session(key, entry.session)
            if abort_errors:
                merged: list[ShutdownChannelResult] = []
                for item in result.results:
                    abort_error = abort_errors.get(item.channel)
                    error = "; ".join(
                        part for part in (item.error, abort_error) if part
                    ) or None
                    merged.append(
                        replace(item, success=item.success and abort_error is None, error=error)
                    )
                result = ShutdownResult(tuple(merged))
            self._last_shutdown[key] = result
        return result

    @capability(
        "safety.watchdog.enable",
        category="safety",
        risk_level="low",
        mutating=True,
        description="Enable the N6700 hardware I/O watchdog.",
    )
    def enable_io_watchdog(self, delay_s: float, alias: str | None = None) -> None:
        delay = require_number("delay_s", delay_s, minimum=1.0, maximum=3600.0)
        self.write_checked(
            f"OUTP:PROT:WDOG:DEL {delay:.12g}", alias=alias, operation="enable_io_watchdog"
        )
        self.write_checked("OUTP:PROT:WDOG ON", alias=alias, operation="enable_io_watchdog")

    def disable_io_watchdog(self, alias: str | None = None) -> None:
        self.write_checked("OUTP:PROT:WDOG OFF", alias=alias, operation="disable_io_watchdog")

    def get_io_watchdog(self, alias: str | None = None) -> dict[str, float | bool]:
        enabled_raw = self._query("OUTP:PROT:WDOG?", alias=alias, operation="get_io_watchdog")
        delay = self._query_float(
            "OUTP:PROT:WDOG:DEL?", alias=alias, operation="get_io_watchdog"
        )
        try:
            enabled = parse_bool(enabled_raw)
        except ResponseParseError as exc:
            raise DriverMalformedResponseError(
                f"invalid watchdog state {enabled_raw!r}", code="LPDS-PRT-018"
            ) from exc
        return {"enabled": enabled, "delay_s": delay}

    def _audit(
        self,
        operation: str,
        channels: tuple[int, ...],
        requested_values: dict[str, object],
        commands: tuple[str, ...],
        responses: tuple[str, ...],
        errors: tuple[str, ...],
        *,
        duration_s: float = 0.0,
    ) -> None:
        if self.audit_log_path is None:
            return
        # Never duplicate an entire waveform into control-plane JSONL.
        # The protocol JsonlTraceSink also truncates RX payloads at 4096
        # bytes by default, including the IEEE binary-block read path.
        responses = tuple(
            value if len(value) <= 4096 else
            f"<omitted {len(value)} characters sha256={hashlib.sha256(value.encode('utf-8')).hexdigest()}>"
            for value in responses
        )
        record = AuditRecord(
            timestamp_iso=datetime.now(timezone.utc).isoformat(),
            timestamp_unix=time.time(),
            operation=operation,
            channels=channels,
            requested_values=requested_values,
            scpi_commands=commands,
            responses=responses,
            errors=errors,
            duration_s=duration_s,
        )
        payload = {f.name: getattr(record, f.name) for f in fields(record)}
        payload["requested_values"] = dict(payload["requested_values"])
        if payload["final_output_states"] is not None:
            payload["final_output_states"] = dict(payload["final_output_states"])
        try:
            self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, default=str) + "\n")
        except OSError:
            _log.warning("audit log write failed", exc_info=True)

    def get_driver_capabilities(self) -> list[str]:
        return [
            "error_queue",
            "channel_selection",
            "output_control",
            "source_setpoint",
            "measurement",
            "device_reset",
            "safe_shutdown",
            "io_watchdog",
        ]

    def close(self, alias: str | None = None) -> None:
        self.disconnect(alias=alias)
