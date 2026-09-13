"""N6700-specific SCPI formatting and parsing.

Generic parsing (floats, booleans, CSV, ``*IDN?``, ``SYST:ERR?``, IEEE-488.2
binary blocks) is delegated to ``scpi_driver_core.scpi``. Only what is
genuinely specific to the N6700 command set — channel-list syntax and its
multi-channel array replies — lives here.
"""

from __future__ import annotations

import struct
from collections.abc import Iterable, Sequence
from typing import Literal

from scpi_driver_core.exceptions import ResponseParseError
from scpi_driver_core.models import ScpiError
from scpi_driver_core.scpi.binary_block import decode_definite_length_block
from scpi_driver_core.scpi.parsers import parse_csv, parse_scpi_error

from .exceptions import DriverMalformedResponseError
from .types import ScpiErrorRecord

SUPPORTED_MANUFACTURERS = {
    "KEYSIGHT TECHNOLOGIES",
    "AGILENT TECHNOLOGIES",
    "HEWLETT-PACKARD",
}


def format_bool(value: bool) -> str:
    return "ON" if value else "OFF"


def format_float(value: float) -> str:
    return f"{value:.12g}"


def _flatten_channels(channels: int | Sequence[int] | range) -> list[int]:
    if isinstance(channels, int):
        return [channels]
    return list(channels)


def format_channel_list(channels: int | Sequence[int] | range) -> str:
    values = _flatten_channels(channels)
    if not values:
        raise ValueError("at least one channel is required")
    for ch in values:
        if ch < 1 or ch > 4:
            raise ValueError(f"invalid N6700 channel: {ch}")
    if isinstance(channels, range) and values == list(range(values[0], values[-1] + 1)):
        return f"(@{values[0]}:{values[-1]})"
    return "(@" + ",".join(str(ch) for ch in values) + ")"


def parse_channel_list(text: str) -> list[int]:
    import re

    m = re.search(r"\(@([^)]*)\)", text)
    if not m:
        return []
    body = m.group(1).strip()
    if not body:
        return []
    result: list[int] = []
    for part in body.split(","):
        part = part.strip()
        if ":" in part:
            start, end = (int(x.strip()) for x in part.split(":", 1))
            result.extend(range(start, end + 1))
        else:
            result.append(int(part))
    return result


def parse_csv_floats(response: str) -> list[float]:
    response = response.strip()
    if not response:
        return []
    return [float(item) for item in response.split(",") if item.strip()]


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


def parse_binary_real_array(
    data: bytes,
    byte_order: Literal["normal", "swapped"] = "normal",
    *,
    width: Literal[4, 8] = 8,
) -> list[float]:
    payload = decode_definite_length_block(data) if data.startswith(b"#") else data
    if len(payload) % width:
        raise DriverMalformedResponseError(
            "binary REAL payload length is not a multiple of element width",
            code="LPDS-PRT-004",
        )
    endian = ">" if byte_order == "normal" else "<"
    code = "f" if width == 4 else "d"
    count = len(payload) // width
    return list(struct.unpack(f"{endian}{count}{code}", payload))


def parse_multi_binary_real_arrays(
    data: bytes,
    channel_count: int,
    byte_order: Literal["normal", "swapped"] = "normal",
    *,
    width: Literal[4, 8] = 8,
) -> list[list[float]]:
    blocks = data.split(b",")
    if len(blocks) != channel_count:
        raise DriverMalformedResponseError(
            f"expected {channel_count} binary blocks, received {len(blocks)}",
            code="LPDS-PRT-005",
        )
    return [parse_binary_real_array(block, byte_order, width=width) for block in blocks]


def iter_channel_values(channels: Sequence[int], response: str) -> Iterable[tuple[int, float]]:
    values = parse_csv_floats(response)
    if len(values) != len(channels):
        raise DriverMalformedResponseError(
            f"query returned {len(values)} values for {len(channels)} requested channels",
            code="LPDS-PRT-006",
        )
    return zip(channels, values, strict=True)
