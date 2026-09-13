"""Translate ``scpi_driver_core`` exceptions into the public LPDS-007 hierarchy.

``scpi_driver_core.exceptions.ScpiDriverError`` and its subclasses are
transport/protocol-library-internal (LPDS-004 §17.1): they must never cross
this driver's public API boundary. Every call into the transport/session
layer that a public method makes goes through :func:`translated`, which
catches the core's exceptions and re-raises the matching
:mod:`keysight_n6700.exceptions` class with ``from exc`` so the cause chain is
preserved.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from scpi_driver_core.exceptions import (
    ConfigurationError as CoreConfigurationError,
)
from scpi_driver_core.exceptions import (
    IdentityError as CoreIdentityError,
)
from scpi_driver_core.exceptions import (
    NotConnectedError as CoreNotConnectedError,
)
from scpi_driver_core.exceptions import (
    OperationTimeoutError as CoreOperationTimeoutError,
)
from scpi_driver_core.exceptions import (
    ProtocolError as CoreProtocolError,
)
from scpi_driver_core.exceptions import (
    ResponseParseError as CoreResponseParseError,
)
from scpi_driver_core.exceptions import (
    SafetyGuardError as CoreSafetyGuardError,
)
from scpi_driver_core.exceptions import (
    ScpiCommandError as CoreScpiCommandError,
)
from scpi_driver_core.exceptions import (
    ScpiDriverError as CoreScpiDriverError,
)
from scpi_driver_core.exceptions import (
    ScpiErrorQueueError as CoreScpiErrorQueueError,
)
from scpi_driver_core.exceptions import (
    TransportError as CoreTransportError,
)
from scpi_driver_core.exceptions import (
    TransportTimeoutError as CoreTransportTimeoutError,
)
from scpi_driver_core.exceptions import (
    UnsupportedOperationError as CoreUnsupportedOperationError,
)

from .exceptions import (
    RECONNECT_REQUIRED,
    RETRY_ALLOWED,
    DriverCommandRejectedError,
    DriverConfigurationError,
    DriverConnectionError,
    DriverError,
    DriverIdentityError,
    DriverIncompleteResponseError,
    DriverInternalError,
    DriverMalformedResponseError,
    DriverTimeoutError,
    DriverUnsafeOperationError,
    DriverUnsupportedOperationError,
)

__all__ = ["translate_core_error", "translated"]


def translate_core_error(exc: CoreScpiDriverError, *, operation: str | None = None) -> DriverError:
    """Return the LPDS-007 exception that corresponds to a core exception."""
    if isinstance(exc, CoreConfigurationError):
        return DriverConfigurationError(str(exc), operation=operation, code="LPDS-CFG-001")
    if isinstance(exc, (CoreTransportTimeoutError, CoreOperationTimeoutError)):
        return DriverTimeoutError(
            str(exc),
            operation=operation,
            code="LPDS-TMO-001",
            retryable=True,
            recovery_action=RETRY_ALLOWED,
        )
    if isinstance(exc, CoreNotConnectedError):
        return DriverConnectionError(
            str(exc),
            operation=operation,
            code="LPDS-CON-002",
            recovery_action=RECONNECT_REQUIRED,
        )
    if isinstance(exc, CoreResponseParseError):
        raw = getattr(exc, "raw", None)
        return DriverMalformedResponseError(
            str(exc),
            operation=operation,
            code="LPDS-PRT-001",
            details={"raw": raw} if raw is not None else {},
        )
    if isinstance(exc, CoreScpiCommandError):
        return DriverCommandRejectedError(str(exc), operation=operation, code="LPDS-DEV-001")
    if isinstance(exc, CoreScpiErrorQueueError):
        return DriverCommandRejectedError(str(exc), operation=operation, code="LPDS-DEV-002")
    if isinstance(exc, CoreIdentityError):
        return DriverIdentityError(str(exc), operation=operation, code="LPDS-DEV-003")
    if isinstance(exc, CoreUnsupportedOperationError):
        return DriverUnsupportedOperationError(str(exc), operation=operation, code="LPDS-DEV-004")
    if isinstance(exc, CoreSafetyGuardError):
        return DriverUnsafeOperationError(str(exc), operation=operation, code="LPDS-SAF-001")
    if isinstance(exc, CoreProtocolError):
        return DriverIncompleteResponseError(str(exc), operation=operation, code="LPDS-PRT-002")
    if isinstance(exc, CoreTransportError):
        return DriverConnectionError(str(exc), operation=operation, code="LPDS-TRN-001")
    return DriverInternalError(str(exc), operation=operation, code="LPDS-INT-001")


@contextmanager
def translated(*, operation: str | None = None) -> Iterator[None]:
    """Run a block, translating any ``ScpiDriverError`` raised inside it.

    Usage::

        with translated(operation="connect"):
            session.open(probe=True)
    """
    try:
        yield
    except CoreScpiDriverError as exc:
        raise translate_core_error(exc, operation=operation) from exc
