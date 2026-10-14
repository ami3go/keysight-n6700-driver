"""Typed data models used by the N6700 driver's public API."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from scpi_driver_core.models import Identity as _CoreIdentity

RemoteState = Literal["local", "remote", "remote_lockout"]
VoltageTransientMode = Literal["fixed", "list", "arb"]
VoltageTransientState = Literal[
    "idle", "configured", "armed", "ready", "triggered", "complete", "aborted", "unknown"
]


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
    interval_s: float | None = None  # sample spacing; time axis is i * interval_s
    offset_points: int | None = None  # trigger-relative index of the first sample
    raw_sha256: str | None = None  # sha256 of received binary sample bytes


@dataclass(frozen=True)
class VoltageListConfiguration:
    """Validated/read-back voltage LIST configuration for one source channel."""

    channel: int
    levels_v: tuple[float, ...]
    dwell_s: tuple[float, ...]
    current_limit_a: float
    repeat_count: int
    step_mode: Literal["auto", "once"]
    terminate_last: bool
    minimum_voltage_v: float
    maximum_voltage_v: float

    @property
    def duration_s(self) -> float:
        return sum(self.dwell_s) * self.repeat_count


@dataclass(frozen=True)
class VoltageArbConfiguration:
    """Validated/read-back user-defined voltage ARB configuration."""

    channel: int
    levels_v: tuple[float, ...]
    dwell_s: tuple[float, ...]
    repeat_count: int
    terminate_last: bool
    minimum_voltage_v: float
    maximum_voltage_v: float

    @property
    def duration_s(self) -> float:
        return sum(self.dwell_s) * self.repeat_count


@dataclass(frozen=True)
class VoltageTransientToken:
    """Identifies one armed source-transient generation."""

    channel: int
    generation: int


@dataclass(frozen=True)
class VoltageTransientStatus:
    """Non-destructive projection of the source transient trigger state."""

    channel: int
    mode: VoltageTransientMode
    state: VoltageTransientState
    generation: int
    trigger_source: Literal["bus", "external"]
    operation_status: int
    waiting_for_trigger: bool
    transient_active: bool


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
