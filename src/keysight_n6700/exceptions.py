"""Public exception hierarchy, per LPDS-007 (Error and Exception Standard).

Every exception a caller of this driver's public API can catch is rooted at
:class:`DriverError` and follows the canonical LPDS-007 taxonomy. Nothing from
``scpi_driver_core`` (its ``ScpiDriverError`` hierarchy is transport/protocol
-library-internal) or from PyVISA crosses this boundary: :mod:`keysight_n6700.
_translate` maps every lower-layer failure to one of these classes with
``raise ... from exc`` so the original cause is preserved.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

__all__ = [
    "DriverArgumentTypeError",
    "DriverArgumentValueError",
    "DriverChecksumError",
    "DriverCommandRejectedError",
    "DriverConfigurationError",
    "DriverConnectionError",
    "DriverDependencyError",
    "DriverDeviceBusyError",
    "DriverDeviceError",
    "DriverError",
    "DriverIdentityError",
    "DriverIncompleteResponseError",
    "DriverInterlockError",
    "DriverInternalError",
    "DriverLimitViolationError",
    "DriverMalformedResponseError",
    "DriverOperationUncertainError",
    "DriverPreconditionError",
    "DriverProtocolError",
    "DriverProtocolSyncError",
    "DriverRangeError",
    "DriverResourceBusyError",
    "DriverResourceConflictError",
    "DriverResourceError",
    "DriverResourceNotFoundError",
    "DriverSafetyError",
    "DriverStateError",
    "DriverTimeoutError",
    "DriverTransportClosedError",
    "DriverTransportError",
    "DriverTransportOpenError",
    "DriverUnexpectedResponseError",
    "DriverUnsafeOperationError",
    "DriverUnsupportedOperationError",
    "DriverUnsupportedValueError",
    "DriverValidationError",
    "DriverWriteError",
    "RecoveryAction",
]

#: LPDS-007 §16.2 recovery classification vocabulary.
RecoveryAction = str
NONE_REQUIRED: RecoveryAction = "NONE_REQUIRED"
RETRY_ALLOWED: RecoveryAction = "RETRY_ALLOWED"
RECONNECT_REQUIRED: RecoveryAction = "RECONNECT_REQUIRED"
RESYNC_REQUIRED: RecoveryAction = "RESYNC_REQUIRED"
READBACK_REQUIRED: RecoveryAction = "READBACK_REQUIRED"
RESET_REQUIRED: RecoveryAction = "RESET_REQUIRED"
OPERATOR_ACTION_REQUIRED: RecoveryAction = "OPERATOR_ACTION_REQUIRED"
EMERGENCY_SHUTDOWN_REQUIRED: RecoveryAction = "EMERGENCY_SHUTDOWN_REQUIRED"
NOT_RECOVERABLE: RecoveryAction = "NOT_RECOVERABLE"


class DriverError(Exception):
    """Root of the public exception hierarchy (LPDS-007 §5).

    Args:
        message: human-readable description of the failure.
        code: an ``LPDS-<DOMAIN>-<NNN>`` or ``LPDS-N6700-<NNN>`` error code.
        retryable: whether resending the same call, unmodified, could succeed.
        severity: one of ``"low"``, ``"medium"``, ``"high"``, ``"critical"``.
        details: structured context (arguments, channel, observed value, ...).
        recovery_action: one of the LPDS-007 §16.2 recovery values.
        operation: the public method name that raised this error, if known.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "LPDS-N6700-000",
        retryable: bool = False,
        severity: str = "medium",
        details: Mapping[str, Any] | None = None,
        recovery_action: RecoveryAction = NONE_REQUIRED,
        operation: str | None = None,
    ) -> None:
        self.code = code
        self.retryable = retryable
        self.severity = severity
        self.details: dict[str, Any] = dict(details or {})
        self.recovery_action = recovery_action
        self.operation = operation
        self.message = message
        super().__init__(self._format())

    def _format(self) -> str:
        operation = self.operation or "operation"
        recovery = self.recovery_action if self.recovery_action != NONE_REQUIRED else "none"
        context = ", ".join(f"{key}={value!r}" for key, value in self.details.items())
        context_part = f" {context}." if context else ""
        return (
            f"[{self.code}] {operation} failed: {self.message}.{context_part} "
            f"Retryable={'yes' if self.retryable else 'no'}. Recovery={recovery}."
        )


# -- configuration / validation ------------------------------------------------


class DriverConfigurationError(DriverError):
    """Invalid or inconsistent driver/session configuration."""


class DriverValidationError(DriverError):
    """A caller-supplied argument failed validation before transmission."""


class DriverArgumentTypeError(DriverValidationError):
    """An argument had the wrong type."""


class DriverArgumentValueError(DriverValidationError):
    """An argument had an unacceptable value."""


class DriverRangeError(DriverValidationError):
    """A numeric argument was outside its documented range."""


class DriverUnsupportedValueError(DriverValidationError):
    """An argument named a value this driver/module does not support."""


# -- state ----------------------------------------------------------------


class DriverStateError(DriverError):
    """An operation was attempted from a state that does not allow it."""


class DriverPreconditionError(DriverStateError):
    """A documented precondition (e.g. "must be connected") was not met."""


class DriverOperationUncertainError(DriverStateError):
    """A state-changing operation's outcome could not be confirmed."""


# -- connection / transport -------------------------------------------------


class DriverConnectionError(DriverError):
    """The driver could not establish or maintain a session."""


class DriverTransportError(DriverError):
    """A failure at the byte-transport boundary."""


class DriverTransportOpenError(DriverTransportError):
    """The transport resource could not be acquired."""


class DriverTransportClosedError(DriverTransportError):
    """I/O was attempted on a transport that is not open."""


class DriverWriteError(DriverTransportError):
    """A write to the instrument failed."""


class DriverReadError(DriverTransportError):
    """A read from the instrument failed."""


class DriverTimeoutError(DriverTransportError):
    """An operation exceeded its bounded timeout."""


# -- protocol ---------------------------------------------------------------


class DriverProtocolError(DriverError):
    """The instrument's response violated the expected protocol."""


class DriverMalformedResponseError(DriverProtocolError):
    """A response could not be parsed into the documented type."""


class DriverIncompleteResponseError(DriverProtocolError):
    """A response was truncated or shorter than the protocol requires."""


class DriverUnexpectedResponseError(DriverProtocolError):
    """A response was well-formed but not what the protocol expects here."""


class DriverChecksumError(DriverProtocolError):
    """A framed payload failed its checksum/length check."""


class DriverProtocolSyncError(DriverProtocolError):
    """The command/response stream lost synchronization."""


# -- device -------------------------------------------------------------


class DriverDeviceError(DriverError):
    """The instrument itself reported a fault."""


class DriverCommandRejectedError(DriverDeviceError):
    """The instrument rejected a command, typically via its SCPI error queue."""

    def __init__(
        self,
        message: str,
        *,
        scpi_errors: Sequence[tuple[int, str]] | None = None,
        **kwargs: Any,
    ) -> None:
        self.scpi_errors = tuple(scpi_errors or ())
        details = dict(kwargs.pop("details", None) or {})
        if self.scpi_errors:
            details["scpi_errors"] = list(self.scpi_errors)
        super().__init__(message, details=details, **kwargs)


class DriverDeviceBusyError(DriverDeviceError):
    """The instrument reported it cannot service the request right now."""


class DriverIdentityError(DriverDeviceError):
    """The ``*IDN?`` reply was missing, unparsable, or unacceptable."""


class DriverUnsupportedOperationError(DriverDeviceError):
    """The installed module, transport, or verified command set lacks this."""


# -- resources ------------------------------------------------------------


class DriverResourceError(DriverError):
    """A named resource (session, channel, alias) could not be used as asked."""


class DriverResourceNotFoundError(DriverResourceError):
    """A named resource does not exist."""


class DriverResourceBusyError(DriverResourceError):
    """A resource is exclusively held elsewhere."""


class DriverResourceConflictError(DriverResourceError):
    """Two requested resource assignments conflict."""


# -- safety -----------------------------------------------------------------


class DriverSafetyError(DriverError):
    """A guarded, potentially hazardous operation was refused."""


class DriverUnsafeOperationError(DriverSafetyError):
    """The requested operation would be unsafe given known state."""


class DriverInterlockError(DriverSafetyError):
    """A safety interlock or inhibit condition blocked the operation."""


class DriverLimitViolationError(DriverSafetyError):
    """A safety limit (OVP/OCP/protection) tripped or would be exceeded."""


# -- other ------------------------------------------------------------------


class DriverDependencyError(DriverError):
    """A required optional dependency (e.g. PyVISA) is not installed."""


class DriverInternalError(DriverError):
    """An invariant inside the driver was violated. Always a driver bug."""
