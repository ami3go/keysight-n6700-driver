"""Typed data models used by the N6700 driver's public API."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from scpi_driver_core.models import Identity as _CoreIdentity

RemoteState = Literal["local", "remote", "remote_lockout"]


@dataclass(frozen=True)
class InstrumentIdentity:
    manufacturer: str
    model: str
    serial: str
    firmware: str

    @classmethod
    def from_core(cls, identity: _CoreIdentity) -> InstrumentIdentity:
        return cls(
            manufacturer=identity.manufacturer,
            model=identity.model,
            serial=identity.serial_number or "",
            firmware=identity.firmware_version or "",
        )


@dataclass(frozen=True)
class ScpiErrorRecord:
    code: int
    message: str
    raw: str

    @property
    def is_ok(self) -> bool:
        return self.code == 0


@dataclass(frozen=True)
class SelfTestResult:
    code: int
    message: str

    @property
    def passed(self) -> bool:
        return self.code == 0


@dataclass(frozen=True)
class PowerMeasurement:
    channel: int
    power_W: float | None
    power_source: Literal["instrument", "calculated", "unavailable"]
    timestamp_iso: str
    timestamp_unix: float


@dataclass(frozen=True)
class Measurement:
    channel: int
    voltage_V: float | None
    current_A: float | None
    power_W: float | None
    power_source: Literal["instrument", "calculated", "unavailable"]
    timestamp_iso: str
    timestamp_unix: float


@dataclass(frozen=True)
class ProtectionStatus:
    channel: int
    active: bool
    over_voltage: bool | None = None
    over_current: bool | None = None
    over_temperature: bool | None = None
    power_limit: bool | None = None
    power_fail: bool | None = None
    inhibit: bool | None = None
    oscillation: bool | None = None
    raw_status: int | str | None = None


@dataclass(frozen=True)
class ProtectionClearResult:
    channel: int
    protection_before: ProtectionStatus
    protection_after: ProtectionStatus
    output_state_before: bool | None
    output_state_after: bool | None
    restored_output: bool
    errors: tuple[ScpiErrorRecord, ...] = ()


@dataclass(frozen=True)
class OperationStatus:
    raw_status: int | str


@dataclass(frozen=True)
class QuestionableStatus:
    raw_status: int | str


@dataclass(frozen=True)
class InstrumentStatusSnapshot:
    operation: OperationStatus | None = None
    questionable: QuestionableStatus | None = None
    protections: tuple[ProtectionStatus, ...] = ()


@dataclass(frozen=True)
class ChannelStatusSnapshot:
    channel: int
    output_or_input_enabled: bool | None
    protection: ProtectionStatus


@dataclass(frozen=True)
class ArrayMeasurement:
    channel: int
    values: tuple[float, ...]
    unit: Literal["V", "A", "W"]
    format: Literal["ascii", "real"]


@dataclass(frozen=True)
class ShutdownChannelResult:
    channel: int
    attempted: bool
    success: bool
    error: str | None = None


@dataclass(frozen=True)
class ShutdownResult:
    results: tuple[ShutdownChannelResult, ...] = field(default_factory=tuple)

    @property
    def success(self) -> bool:
        return all(item.success for item in self.results)


@dataclass(frozen=True)
class AuditRecord:
    timestamp_iso: str
    timestamp_unix: float
    operation: str
    channels: tuple[int, ...]
    requested_values: dict[str, object]
    scpi_commands: tuple[str, ...]
    responses: tuple[str, ...]
    errors: tuple[str, ...]
    duration_s: float
    final_output_states: dict[int, bool] | None = None
