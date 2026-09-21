"""Typed data models used by the N6700 driver's public API."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntFlag
from types import MappingProxyType
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
    simultaneous: bool = False


class QuesBit(IntFlag):
    """N6700 Questionable Condition register bits."""

    OV = 1
    OC = 2
    PF = 4
    CP_POS = 8
    OT = 16
    CP_NEG = 32
    OV_NEG = 64
    LIM_POS = 128
    LIM_NEG = 256
    INH = 512
    UNR = 1024
    PROT = 2048
    OSC = 4096


TRIP_BITS = (
    QuesBit.OV
    | QuesBit.OC
    | QuesBit.PF
    | QuesBit.OT
    | QuesBit.OV_NEG
    | QuesBit.INH
    | QuesBit.PROT
    | QuesBit.OSC
)
LIMIT_BITS = QuesBit.CP_POS | QuesBit.CP_NEG | QuesBit.LIM_POS | QuesBit.LIM_NEG


@dataclass(frozen=True)
class ProtectionStatus:
    channel: int
    raw_status: int

    @property
    def raw(self) -> QuesBit:
        return QuesBit(self.raw_status)

    @property
    def tripped(self) -> bool:
        return bool(self.raw & TRIP_BITS)

    @property
    def active(self) -> bool:
        """Backward-compatible alias for :attr:`tripped`."""
        return self.tripped

    @property
    def limiting(self) -> bool:
        return bool(self.raw & LIMIT_BITS)

    @property
    def unregulated(self) -> bool:
        return bool(self.raw & QuesBit.UNR)

    @property
    def over_voltage(self) -> bool:
        return bool(self.raw & (QuesBit.OV | QuesBit.OV_NEG))

    @property
    def over_current(self) -> bool:
        return bool(self.raw & QuesBit.OC)

    @property
    def over_temperature(self) -> bool:
        return bool(self.raw & QuesBit.OT)

    @property
    def power_limit(self) -> bool:
        return bool(self.raw & (QuesBit.CP_POS | QuesBit.CP_NEG))

    @property
    def power_fail(self) -> bool:
        return bool(self.raw & QuesBit.PF)

    @property
    def inhibit(self) -> bool:
        return bool(self.raw & QuesBit.INH)

    @property
    def oscillation(self) -> bool:
        return bool(self.raw & QuesBit.OSC)


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
    raw_status: int | Mapping[int, int]


@dataclass(frozen=True)
class QuestionableStatus:
    raw_status: int | Mapping[int, int]


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
        """True only when at least one channel was verified off and all succeeded."""
        return bool(self.results) and all(item.success for item in self.results)


@dataclass(frozen=True)
class AuditRecord:
    timestamp_iso: str
    timestamp_unix: float
    operation: str
    channels: tuple[int, ...]
    requested_values: Mapping[str, object]
    scpi_commands: tuple[str, ...]
    responses: tuple[str, ...]
    errors: tuple[str, ...]
    duration_s: float
    final_output_states: Mapping[int, bool] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "requested_values", MappingProxyType(dict(self.requested_values)))
        if self.final_output_states is not None:
            object.__setattr__(
                self,
                "final_output_states",
                MappingProxyType(dict(self.final_output_states)),
            )
