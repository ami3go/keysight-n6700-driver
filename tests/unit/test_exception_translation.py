from __future__ import annotations

from scpi_driver_core.exceptions import (
    ConfigurationError as CoreConfigurationError,
)
from scpi_driver_core.exceptions import (
    NotConnectedError as CoreNotConnectedError,
)
from scpi_driver_core.exceptions import (
    TransportTimeoutError as CoreTransportTimeoutError,
)

from keysight_n6700._translate import translate_core_error
from keysight_n6700.exceptions import (
    DriverConfigurationError,
    DriverConnectionError,
    DriverTimeoutError,
)


def test_configuration_error_is_translated() -> None:
    translated = translate_core_error(CoreConfigurationError("bad"), operation="connect")
    assert isinstance(translated, DriverConfigurationError)
    assert translated.operation == "connect"


def test_transport_timeout_is_translated_as_retryable() -> None:
    translated = translate_core_error(CoreTransportTimeoutError("timed out"))
    assert isinstance(translated, DriverTimeoutError)
    assert translated.retryable is True


def test_not_connected_is_translated_as_connection_error() -> None:
    translated = translate_core_error(CoreNotConnectedError("closed"))
    assert isinstance(translated, DriverConnectionError)


def test_error_message_format_includes_code_and_recovery() -> None:
    translated = translate_core_error(CoreConfigurationError("bad"), operation="connect")
    text = str(translated)
    assert translated.code in text
    assert "Retryable=" in text
    assert "Recovery=" in text
