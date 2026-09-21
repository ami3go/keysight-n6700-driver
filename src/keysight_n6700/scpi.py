"""N6700-specific SCPI formatting and parsing."""

from __future__ import annotations

import math
import re
import struct
from collections.abc import Iterable, Sequence
from typing import Literal

from scpi_driver_core.exceptions import ResponseParseError
from scpi_driver_core.models import ScpiError
from scpi_driver_core.scpi.parsers import parse_csv, parse_float, parse_scpi_error

from .exceptions import (
    DriverArgumentTypeError,
    DriverArgumentValueError,
    DriverIncompleteResponseError,
    DriverMalformedResponseError,
    DriverRangeError,
    DriverUnsupportedValueError,
)
from .types import ScpiErrorRecord

SUPPORTED_MANUFACTURERS = {
    "KEYSIGHT TECHNOLOGIES",
    "AGILENT TECHNOLOGIES",
    "HEWLETT-PACKARD",
}

_NUMERIC_KEYWORDS = frozenset({"MIN", "MAX", "DEF"})


def format_bool(value: bool) -> str:
    if not isinstance(value, bool):
        raise DriverArgumentTypeError(
            f"boolean value expected, got {type(value).__name__}", code="LPDS-ARG-014"
        )
    return "ON" if value else "OFF"


def require_number(
    name: str,
    value: object,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    """Validate a real finite numeric argument before any SCPI is transmitted."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DriverArgumentTypeError(
            f"{name} must be a real number, got {type(value).__name__}", code="LPDS-ARG-010"
        )
    result = float(value)
    if not math.isfinite(result):
        raise DriverRangeError(f"{name} must be finite, got {value!r}", code="LPDS-ARG-011")
    if minimum is not None and result < minimum:
        raise DriverRangeError(
            f"{name}={result:.12g} is below minimum {minimum:.12g}",
            code="LPDS-ARG-012",
            details={"minimum": minimum, "maximum": maximum},
        )
    if maximum is not None and result > maximum:
        raise DriverRangeError(
            f"{name}={result:.12g} exceeds maximum {maximum:.12g}",
            code="LPDS-ARG-012",
            details={"minimum": minimum, "maximum": maximum},
        )
    return result


def format_float(value: float) -> str:
    return f"{require_number('value', value):.12g}"


def format_numeric_param(
    name: str,
    value: float | str,
    *,
    minimum: float | None = 0.0,
    maximum: float | None = None,
) -> str:
    """Format a numeric SCPI parameter or one of the safe MIN/MAX/DEF keywords."""
    if isinstance(value, str):
        token = value.strip().upper()
        if token not in _NUMERIC_KEYWORDS:
            raise DriverUnsupportedValueError(
                f"{name} must be a number or MIN/MAX/DEF, got {value!r}", code="LPDS-ARG-013"
            )
        return token
    number = require_number(name, value, minimum=minimum, maximum=maximum)
    return f"{number:.12g}"


def require_channel(value: object, installed: int = 4) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DriverArgumentTypeError(
            f"channel must be int, got {type(value).__name__}", code="LPDS-ARG-002"
        )
    if not 1 <= value <= min(installed, 4):
        raise DriverArgumentValueError(
            f"invalid channel {value}; installed count is {installed}", code="LPDS-ARG-001"
        )
    return value


def _flatten_channels(channels: int | Sequence[int] | range) -> list[int]:
    if isinstance(channels, bool):
        raise DriverArgumentTypeError("channel must be int, not bool", code="LPDS-ARG-002")
    if isinstance(channels, int):
        return [channels]
    return list(channels)


def format_channel_list(channels: int | Sequence[int] | range) -> str:
    values = _flatten_channels(channels)
    if not values:
        raise DriverArgumentValueError("at least one channel is required", code="LPDS-ARG-003")
    for channel in values:
        require_channel(channel)
    if isinstance(channels, range) and values == list(range(values[0], values[-1] + 1)):
        return f"(@{values[0]}:{values[-1]})"
    return "(@" + ",".join(str(channel) for channel in values) + ")"


def parse_channel_list(text: str) -> list[int]:
    match = re.search(r"\(@([^)]*)\)", text)
    if not match:
        return []
    body = match.group(1).strip()
    if not body:
        return []
    result: list[int] = []
    try:
        for part in body.split(","):
            part = part.strip()
            if ":" in part:
                start, end = (int(item.strip()) for item in part.split(":", 1))
                if end < start:
                    raise ValueError("descending range")
                result.extend(range(start, end + 1))
            else:
                result.append(int(part))
    except ValueError as exc:
        raise DriverMalformedResponseError(
            f"invalid channel list {text!r}", code="LPDS-PRT-007"
        ) from exc
    for channel in result:
        require_channel(channel)
    return result


def parse_csv_floats(response: str) -> list[float]:
    response = response.strip()
    if not response:
        return []
    try:
        return [parse_float(item) for item in parse_csv(response)]
    except ResponseParseError as exc:
        raise DriverMalformedResponseError(
            f"invalid numeric CSV response: {response!r}", code="LPDS-PRT-008"
        ) from exc


def parse_csv_strings(response: str) -> list[str]:
    return parse_csv(response)


def parse_error(response: str) -> ScpiErrorRecord:
    try:
        error: ScpiError = parse_scpi_error(response)
    except ResponseParseError as exc:
        raise DriverMalformedResponseError(
            f"invalid SYST:ERR? response: {response!r}", code="LPDS-PRT-003"
        ) from exc
    return ScpiErrorRecord(code=error.code, message=error.message, raw=error.raw)


def _unpack_reals(
    payload: bytes,
    byte_order: Literal["normal", "swapped"] = "normal",
    *,
    width: Literal[4, 8] = 4,
) -> list[float]:
    if len(payload) % width:
        raise DriverMalformedResponseError(
            "binary REAL payload length is not a multiple of element width",
            code="LPDS-PRT-004",
        )
    endian = ">" if byte_order == "normal" else "<"
    code = "f" if width == 4 else "d"
    count = len(payload) // width
    return list(struct.unpack(f"{endian}{count}{code}", payload))


def _read_definite_block(data: bytes, position: int) -> tuple[bytes, int]:
    if data[position : position + 1] != b"#":
        raise DriverMalformedResponseError("expected '#' binary-block header", code="LPDS-PRT-005")
    if position + 2 > len(data):
        raise DriverIncompleteResponseError("truncated binary-block header", code="LPDS-PRT-004")
    digit_byte = data[position + 1 : position + 2]
    if digit_byte < b"0" or digit_byte > b"9":
        raise DriverMalformedResponseError("invalid binary-block length digit", code="LPDS-PRT-005")
    digits = int(digit_byte)
    if digits == 0:
        raise DriverMalformedResponseError(
            "indefinite-length blocks are unsupported", code="LPDS-PRT-005"
        )
    length_end = position + 2 + digits
    if length_end > len(data):
        raise DriverIncompleteResponseError("truncated binary-block length", code="LPDS-PRT-004")
    length_field = data[position + 2 : length_end]
    if not length_field.isdigit():
        raise DriverMalformedResponseError("invalid binary-block byte count", code="LPDS-PRT-005")
    payload_length = int(length_field)
    payload_end = length_end + payload_length
    if payload_end > len(data):
        raise DriverIncompleteResponseError("truncated binary-block payload", code="LPDS-PRT-004")
    return data[length_end:payload_end], payload_end


def parse_binary_real_array(
    data: bytes,
    byte_order: Literal["normal", "swapped"] = "normal",
    *,
    width: Literal[4, 8] = 4,
) -> list[float]:
    """Parse one N6700 REAL block (IEEE single precision by default)."""
    payload, position = _read_definite_block(data, 0)
    if data[position:].strip(b"\r\n"):
        raise DriverMalformedResponseError("trailing bytes after binary block", code="LPDS-PRT-005")
    return _unpack_reals(payload, byte_order, width=width)


def split_definite_blocks(data: bytes, count: int) -> list[bytes]:
    if count < 1:
        raise DriverArgumentValueError("channel_count must be positive", code="LPDS-ARG-004")
    blocks: list[bytes] = []
    position = 0
    for index in range(count):
        if index:
            if data[position : position + 1] != b",":
                raise DriverMalformedResponseError(
                    "missing ',' separator between binary blocks", code="LPDS-PRT-005"
                )
            position += 1
        payload, position = _read_definite_block(data, position)
        blocks.append(payload)
    if data[position:].strip(b"\r\n"):
        raise DriverMalformedResponseError(
            "trailing bytes after final binary block", code="LPDS-PRT-005"
        )
    return blocks


def parse_multi_binary_real_arrays(
    data: bytes,
    channel_count: int,
    byte_order: Literal["normal", "swapped"] = "normal",
    *,
    width: Literal[4, 8] = 4,
) -> list[list[float]]:
    """Parse one definite-length REAL block per requested channel."""
    return [
        _unpack_reals(block, byte_order, width=width)
        for block in split_definite_blocks(data, channel_count)
    ]


def iter_channel_values(channels: Sequence[int], response: str) -> Iterable[tuple[int, float]]:
    values = parse_csv_floats(response)
    if len(values) != len(channels):
        raise DriverMalformedResponseError(
            f"query returned {len(values)} values for {len(channels)} requested channels",
            code="LPDS-PRT-006",
        )
    return zip(channels, values, strict=True)
