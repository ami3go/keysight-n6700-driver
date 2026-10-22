"""Typed data models used by the N6700 driver's public API."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntFlag
from types import MappingProxyType
from typing import ClassVar, Literal

from scpi_driver_core.models import Identity as _CoreIdentity

from .waveforms import ArbSequenceStep, ArbShape, RepeatCount, repeated_duration

RemoteState = Literal["local", "remote", "remote_lockout"]
TransientQuantity = Literal["voltage", "current"]
VoltageTransientMode = Literal["fixed", "list", "arb", "step"]
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
    repeat_count: RepeatCount
    step_mode: Literal["auto", "once"]
    terminate_last: bool
    minimum_voltage_v: float
    maximum_voltage_v: float
    begin_step_triggers: tuple[bool, ...] = ()  # LIST:TOUT:BOST; () means none
    end_step_triggers: tuple[bool, ...] = ()  # LIST:TOUT:EOST; () means none

    quantity: ClassVar[TransientQuantity] = "voltage"

    @property
    def duration_s(self) -> float:
        return repeated_duration(sum(self.dwell_s), self.repeat_count)


@dataclass(frozen=True)
class CurrentListConfiguration:
    """Validated/read-back current LIST configuration; voltage stays FIXed."""

    channel: int
    levels_a: tuple[float, ...]
    dwell_s: tuple[float, ...]
    voltage_setpoint_v: float
    repeat_count: RepeatCount
    step_mode: Literal["auto", "once"]
    terminate_last: bool
    minimum_current_a: float
    maximum_current_a: float
    begin_step_triggers: tuple[bool, ...] = ()
    end_step_triggers: tuple[bool, ...] = ()

    quantity: ClassVar[TransientQuantity] = "current"

    @property
    def duration_s(self) -> float:
        return repeated_duration(sum(self.dwell_s), self.repeat_count)


@dataclass(frozen=True)
class VoltageArbConfiguration:
    """Validated/read-back user-defined voltage ARB configuration."""

    channel: int
    levels_v: tuple[float, ...]
    dwell_s: tuple[float, ...]
    repeat_count: RepeatCount
    terminate_last: bool
    minimum_voltage_v: float
    maximum_voltage_v: float
    begin_step_triggers: tuple[bool, ...] = ()  # ARB:VOLT:UDEF:BOST; () means none

    quantity: ClassVar[TransientQuantity] = "voltage"

    @property
    def duration_s(self) -> float:
        return repeated_duration(sum(self.dwell_s), self.repeat_count)


@dataclass(frozen=True)
class CurrentArbConfiguration:
    """Validated/read-back user-defined current ARB configuration."""

    channel: int
    levels_a: tuple[float, ...]
    dwell_s: tuple[float, ...]
    repeat_count: RepeatCount
    terminate_last: bool
    minimum_current_a: float
    maximum_current_a: float
    begin_step_triggers: tuple[bool, ...] = ()

    quantity: ClassVar[TransientQuantity] = "current"

    @property
    def duration_s(self) -> float:
        return repeated_duration(sum(self.dwell_s), self.repeat_count)


@dataclass(frozen=True)
class ArbWaveformConfiguration:
    """Validated/read-back built-in ARB shape (levels in V or A per ``quantity``)."""

    channel: int
    quantity: TransientQuantity
    waveform: ArbShape
    repeat_count: RepeatCount
    terminate_last: bool
    minimum_level: float
    maximum_level: float

    @property
    def duration_s(self) -> float:
        return repeated_duration(self.waveform.duration_s, self.repeat_count)


@dataclass(frozen=True)
class ConstantDwellArbConfiguration:
    """Validated/read-back constant-dwell ARB: one dwell shared by every point."""

    channel: int
    quantity: TransientQuantity
    levels: tuple[float, ...]
    dwell_s: float
    repeat_count: RepeatCount
    terminate_last: bool
    minimum_level: float
    maximum_level: float

    @property
    def duration_s(self) -> float:
        return repeated_duration(self.dwell_s * len(self.levels), self.repeat_count)


@dataclass(frozen=True)
class ArbSequenceConfiguration:
    """Validated/read-back ARB sequence (levels in V or A per ``quantity``).

    ``duration_s`` excludes time spent waiting for triggers on
    trigger-paced steps.
    """

    channel: int
    quantity: TransientQuantity
    steps: tuple[ArbSequenceStep, ...]
    sequence_count: RepeatCount
    terminate_last: bool
    minimum_level: float
    maximum_level: float
    quality: int
    generated_points: int

    @property
    def trigger_paced(self) -> bool:
        return any(step.pacing == "trigger" for step in self.steps)

    @property
    def duration_s(self) -> float:
        return repeated_duration(sum(step.duration_s for step in self.steps), self.sequence_count)


@dataclass(frozen=True)
class StepTransientConfiguration:
    """Validated/read-back triggered step (``VOLT:MODE STEP`` / ``CURR:MODE STEP``).

    On the trigger the output moves to ``triggered_level`` and stays there.
    """

    channel: int
    quantity: TransientQuantity
    triggered_level: float
    trigger_output: bool
    minimum_level: float
    maximum_level: float

    @property
    def duration_s(self) -> float:
        return 0.0


SourceTransientConfiguration = (
    VoltageListConfiguration
    | CurrentListConfiguration
    | VoltageArbConfiguration
    | CurrentArbConfiguration
    | ArbWaveformConfiguration
    | ConstantDwellArbConfiguration
    | ArbSequenceConfiguration
    | StepTransientConfiguration
)


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
    quantity: TransientQuantity = "voltage"  # which source quantity the profile drives
    continuous: bool = False  # armed with INIT:CONT:TRAN ON


LoadProfileMode = Literal["cc", "cv", "cr", "cp"]
LoadProfileKind = Literal["list", "arb", "constant_dwell_arb"]


@dataclass(frozen=True)
class LoadProfileConfiguration:
    """Validated/read-back N679xA LIST or ARB profile for one load channel.

    ``levels`` are in the unit of ``mode`` (A, V, ohm or W). ``dwell_s`` holds
    one value per level, except a constant-dwell ARB, which holds its single
    shared dwell. ``worst_case_current_a`` bounds the sinking current from
    instrument-enforced settings: ``CURR:LIM`` in CV, ``maximum_level /
    VOLT:INH:VON`` in CP, ``VOLT:RANG / minimum_level`` in CR; ``None`` in CC,
    where the levels are the current.
    """

    channel: int
    kind: LoadProfileKind
    mode: LoadProfileMode
    levels: tuple[float, ...]
    dwell_s: tuple[float, ...]
    repeat_count: RepeatCount
    terminate_last: bool
    minimum_level: float
    maximum_level: float
    worst_case_current_a: float | None
    step_mode: Literal["auto", "once"] = "auto"
    begin_step_triggers: tuple[bool, ...] = ()  # LIST:TOUT:BOST / UDEF:BOST; () means none
    end_step_triggers: tuple[bool, ...] = ()  # LIST:TOUT:EOST; () means none

    @property
    def duration_s(self) -> float:
        one_pass = (
            self.dwell_s[0] * len(self.levels)
            if self.kind == "constant_dwell_arb"
            else sum(self.dwell_s)
        )
        return repeated_duration(one_pass, self.repeat_count)


@dataclass(frozen=True)
class LoadTransientToken:
    """Identifies one armed load-profile generation."""

    channel: int
    generation: int


@dataclass(frozen=True)
class LoadTransientStatus:
    """Non-destructive projection of a load channel's transient trigger state."""

    channel: int
    kind: LoadProfileKind | None
    mode: LoadProfileMode | None
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
