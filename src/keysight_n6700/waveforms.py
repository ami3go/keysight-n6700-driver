"""Built-in ARB waveform shapes and ARB sequence steps.

Each shape mirrors one ``[SOURce:]ARB:<VOLTage|CURRent>:<shape>`` command
group of the N6705 Programmer's Reference.  Shapes are unit-agnostic: a
level is volts for a voltage ARB and amperes for a current ARB, which the
channel decides when the shape is programmed.  Only the model-independent
limits are checked here; the channel adds the model-dependent time limit and
the caller-approved level envelope.
"""

from __future__ import annotations

import math
import numbers
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from typing import ClassVar, Literal

from .exceptions import DriverArgumentValueError

ParameterKind = Literal["level", "time", "frequency", "count"]
RepeatCount = int | Literal["infinite"]

# Programmer's Reference, [SOURce:]ARB:<VOLT|CURR>:SINusoid:FREQuency.
SINE_MINIMUM_FREQUENCY_HZ = 3.8147e-5
SINE_MAXIMUM_FREQUENCY_HZ = 1.0e4
# Every timing parameter of the seven parametric shapes has this documented
# model-independent ceiling.  UDEF dwell is separate: N678xA uses a 268.435 s
# timebase while other modules use 262.144 s.
PARAMETRIC_MAXIMUM_TIME_S = 262.144
# [SOURce:]ARB:<VOLT|CURR>:STAircase:NSTeps (0 steps is rejected: no staircase).
STAIRCASE_MAXIMUM_STEPS = 500
USER_DEFINED_MAXIMUM_POINTS = 511
SEQUENCE_MAXIMUM_POINTS = 511
SEQUENCE_MINIMUM_QUALITY = 16
SEQUENCE_MAXIMUM_QUALITY = 100


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise DriverArgumentValueError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise DriverArgumentValueError(f"{name} must be finite")
    return result


@dataclass(frozen=True)
class _Shape:
    """Common behaviour of the parametric (non user-defined) shapes."""

    shape: ClassVar[str]
    # (attribute, SCPI suffix below ARB:<VOLT|CURR>:, kind)
    _parameters: ClassVar[tuple[tuple[str, str, ParameterKind], ...]]

    def __post_init__(self) -> None:
        for attribute, _, kind in self._parameters:
            raw = getattr(self, attribute)
            if kind == "count":
                if isinstance(raw, bool) or not isinstance(raw, numbers.Real):
                    raise DriverArgumentValueError(f"{attribute} must be an integer")
                number = float(raw)
                if not number.is_integer():
                    raise DriverArgumentValueError(f"{attribute} must be an integer")
                value: float = int(number)
            else:
                value = _finite(raw, attribute)
            if kind == "time" and value < 0:
                raise DriverArgumentValueError(f"{attribute} must be >= 0 s")
            if kind == "time" and value > PARAMETRIC_MAXIMUM_TIME_S:
                raise DriverArgumentValueError(
                    f"{attribute} must be <= {PARAMETRIC_MAXIMUM_TIME_S:g} s"
                )
            object.__setattr__(self, attribute, value)

    def scpi_parameters(self) -> tuple[tuple[str, float, ParameterKind], ...]:
        return tuple(
            (suffix, getattr(self, attribute), kind)
            for attribute, suffix, kind in self._parameters
        )

    @classmethod
    def from_scpi_parameters(cls, values: Mapping[str, float]) -> _Shape:
        return cls(**{attribute: values[suffix] for attribute, suffix, _ in cls._parameters})

    @property
    def level_bounds(self) -> tuple[float, float]:
        levels = [
            getattr(self, attribute) for attribute, _, kind in self._parameters if kind == "level"
        ]
        return min(levels), max(levels)

    @property
    def times_s(self) -> tuple[float, ...]:
        return tuple(
            getattr(self, attribute) for attribute, _, kind in self._parameters if kind == "time"
        )

    @property
    def duration_s(self) -> float:
        return sum(self.times_s)


@dataclass(frozen=True)
class ArbStep(_Shape):
    """Hold ``start_level`` for ``start_time_s`` after the trigger, then step to ``end_level``."""

    start_level: float
    end_level: float
    start_time_s: float = 0.0

    shape: ClassVar[str] = "STEP"
    _parameters: ClassVar[tuple[tuple[str, str, ParameterKind], ...]] = (
        ("start_level", "STEP:STAR", "level"),
        ("end_level", "STEP:END", "level"),
        ("start_time_s", "STEP:STAR:TIM", "time"),
    )


@dataclass(frozen=True)
class ArbRamp(_Shape):
    """Delay, linear ramp from ``start_level`` to ``end_level``, then hold."""

    start_level: float
    end_level: float
    rise_time_s: float
    start_time_s: float = 0.0
    end_time_s: float = 0.0

    shape: ClassVar[str] = "RAMP"
    _parameters: ClassVar[tuple[tuple[str, str, ParameterKind], ...]] = (
        ("start_level", "RAMP:STAR", "level"),
        ("end_level", "RAMP:END", "level"),
        ("start_time_s", "RAMP:STAR:TIM", "time"),
        ("rise_time_s", "RAMP:RTIM", "time"),
        ("end_time_s", "RAMP:END:TIM", "time"),
    )


@dataclass(frozen=True)
class ArbStaircase(_Shape):
    """Delay, ``steps`` equal steps over ``staircase_time_s``, then hold."""

    start_level: float
    end_level: float
    steps: int
    staircase_time_s: float
    start_time_s: float = 0.0
    end_time_s: float = 0.0

    shape: ClassVar[str] = "STA"
    _parameters: ClassVar[tuple[tuple[str, str, ParameterKind], ...]] = (
        ("start_level", "STA:STAR", "level"),
        ("end_level", "STA:END", "level"),
        ("start_time_s", "STA:STAR:TIM", "time"),
        ("staircase_time_s", "STA:TIM", "time"),
        ("end_time_s", "STA:END:TIM", "time"),
        ("steps", "STA:NST", "count"),
    )

    def __post_init__(self) -> None:
        super().__post_init__()
        if not 1 <= self.steps <= STAIRCASE_MAXIMUM_STEPS:
            raise DriverArgumentValueError(
                f"steps must be within 1..{STAIRCASE_MAXIMUM_STEPS}"
            )


@dataclass(frozen=True)
class ArbSine(_Shape):
    """One period of ``offset + amplitude * sin(2*pi*f*t)`` per repeat."""

    amplitude: float
    offset: float
    frequency_hz: float

    shape: ClassVar[str] = "SIN"
    _parameters: ClassVar[tuple[tuple[str, str, ParameterKind], ...]] = (
        ("amplitude", "SIN:AMPL", "level"),
        ("offset", "SIN:OFFS", "level"),
        ("frequency_hz", "SIN:FREQ", "frequency"),
    )

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.amplitude < 0:
            raise DriverArgumentValueError("amplitude is a peak value and must be >= 0")
        if not SINE_MINIMUM_FREQUENCY_HZ <= self.frequency_hz <= SINE_MAXIMUM_FREQUENCY_HZ:
            raise DriverArgumentValueError(
                f"frequency_hz must be within {SINE_MINIMUM_FREQUENCY_HZ:g}.."
                f"{SINE_MAXIMUM_FREQUENCY_HZ:g} Hz"
            )

    @property
    def level_bounds(self) -> tuple[float, float]:
        return self.offset - self.amplitude, self.offset + self.amplitude

    @property
    def duration_s(self) -> float:
        return 1.0 / self.frequency_hz


@dataclass(frozen=True)
class ArbPulse(_Shape):
    """Delay at ``start_level``, ``top_time_s`` at ``top_level``, then ``end_time_s`` at start."""

    start_level: float
    top_level: float
    top_time_s: float
    start_time_s: float = 0.0
    end_time_s: float = 0.0

    shape: ClassVar[str] = "PULS"
    _parameters: ClassVar[tuple[tuple[str, str, ParameterKind], ...]] = (
        ("start_level", "PULS:STAR", "level"),
        ("top_level", "PULS:TOP", "level"),
        ("start_time_s", "PULS:STAR:TIM", "time"),
        ("top_time_s", "PULS:TOP:TIM", "time"),
        ("end_time_s", "PULS:END:TIM", "time"),
    )


@dataclass(frozen=True)
class ArbTrapezoid(_Shape):
    """Delay, rise to ``top_level``, hold, fall back to ``start_level``, then hold."""

    start_level: float
    top_level: float
    rise_time_s: float
    top_time_s: float
    fall_time_s: float
    start_time_s: float = 0.0
    end_time_s: float = 0.0

    shape: ClassVar[str] = "TRAP"
    _parameters: ClassVar[tuple[tuple[str, str, ParameterKind], ...]] = (
        ("start_level", "TRAP:STAR", "level"),
        ("top_level", "TRAP:TOP", "level"),
        ("start_time_s", "TRAP:STAR:TIM", "time"),
        ("rise_time_s", "TRAP:RTIM", "time"),
        ("top_time_s", "TRAP:TOP:TIM", "time"),
        ("fall_time_s", "TRAP:FTIM", "time"),
        ("end_time_s", "TRAP:END:TIM", "time"),
    )


@dataclass(frozen=True)
class ArbExponential(_Shape):
    """Delay, then an exponential approach from ``start_level`` toward ``end_level``.

    The curve stays between the two levels, so they bound the envelope.
    """

    start_level: float
    end_level: float
    time_s: float
    time_constant_s: float
    start_time_s: float = 0.0

    shape: ClassVar[str] = "EXP"
    _parameters: ClassVar[tuple[tuple[str, str, ParameterKind], ...]] = (
        ("start_level", "EXP:STAR", "level"),
        ("end_level", "EXP:END", "level"),
        ("start_time_s", "EXP:STAR:TIM", "time"),
        ("time_s", "EXP:TIM", "time"),
        ("time_constant_s", "EXP:TCON", "time"),
    )

    @property
    def duration_s(self) -> float:
        # The time constant shapes the curve; it does not add run time.
        return self.start_time_s + self.time_s


@dataclass(frozen=True)
class ArbUserDefined:
    """Point-by-point waveform: one level and dwell per point (up to 511 points).

    ``begin_step_triggers`` optionally emits a trigger at the start of the
    matching point; empty means no trigger output.
    """

    levels: tuple[float, ...]
    dwell_s: tuple[float, ...]
    begin_step_triggers: tuple[bool, ...] = ()

    shape: ClassVar[str] = "UDEF"

    def __post_init__(self) -> None:
        levels = _finite_sequence(self.levels, "levels")
        if len(levels) > USER_DEFINED_MAXIMUM_POINTS:
            raise DriverArgumentValueError(
                f"levels exceeds {USER_DEFINED_MAXIMUM_POINTS} points"
            )
        dwells = _finite_sequence(self.dwell_s, "dwell_s")
        if len(dwells) == 1:
            dwells = dwells * len(levels)
        if len(dwells) != len(levels):
            raise DriverArgumentValueError("dwell_s must contain one value or one value per point")
        if any(value < 0 for value in dwells):
            raise DriverArgumentValueError("dwell_s values must be >= 0 s")
        triggers = _bool_sequence(self.begin_step_triggers, len(levels), "begin_step_triggers")
        object.__setattr__(self, "levels", levels)
        object.__setattr__(self, "dwell_s", dwells)
        object.__setattr__(self, "begin_step_triggers", triggers)

    @property
    def level_bounds(self) -> tuple[float, float]:
        return min(self.levels), max(self.levels)

    @property
    def times_s(self) -> tuple[float, ...]:
        return self.dwell_s

    @property
    def duration_s(self) -> float:
        return sum(self.dwell_s)


ArbShape = (
    ArbStep | ArbRamp | ArbStaircase | ArbSine | ArbPulse | ArbTrapezoid | ArbExponential
    | ArbUserDefined
)


def sequence_waveform_points(waveform: ArbShape, quality: int) -> int:
    """Return sequence memory points for one shape at instrument ``quality``.

    The guide assigns ``quality`` points to each smooth waveform as a whole,
    three to a pulse, two to a step, one per UDEF point, and one more point
    than the number of staircase transitions. Repeats do not consume more
    waveform memory.
    """
    if not SEQUENCE_MINIMUM_QUALITY <= quality <= SEQUENCE_MAXIMUM_QUALITY:
        raise DriverArgumentValueError(
            f"quality must be within {SEQUENCE_MINIMUM_QUALITY}.."
            f"{SEQUENCE_MAXIMUM_QUALITY}"
        )
    if isinstance(waveform, (ArbRamp, ArbTrapezoid, ArbExponential, ArbSine)):
        return quality
    if isinstance(waveform, ArbPulse):
        return 3
    if isinstance(waveform, ArbStep):
        return 2
    if isinstance(waveform, ArbStaircase):
        return waveform.steps + 1
    if isinstance(waveform, ArbUserDefined):
        return len(waveform.levels)
    raise DriverArgumentValueError("waveform must be an ARB shape")

PARAMETRIC_SHAPES: dict[str, type[_Shape]] = {
    cls.shape: cls
    for cls in (ArbStep, ArbRamp, ArbStaircase, ArbSine, ArbPulse, ArbTrapezoid, ArbExponential)
}

_SHAPE_NAMES: dict[str, type[_Shape] | type[ArbUserDefined]] = {
    "step": ArbStep,
    "ramp": ArbRamp,
    "staircase": ArbStaircase,
    "sine": ArbSine,
    "sinusoid": ArbSine,
    "pulse": ArbPulse,
    "trapezoid": ArbTrapezoid,
    "exponential": ArbExponential,
    "user_defined": ArbUserDefined,
    "udef": ArbUserDefined,
}


def shape_from_name(name: str, parameters: Mapping[str, object]) -> ArbShape:
    """Build a shape from a name such as ``'ramp'`` and keyword parameters.

    Used by keyword-driven callers (Robot Framework) that cannot construct
    dataclasses directly.
    """
    cls = _SHAPE_NAMES.get(str(name).strip().lower())
    if cls is None:
        raise DriverArgumentValueError(
            f"unknown ARB shape {name!r}; expected one of {sorted(_SHAPE_NAMES)}"
        )
    allowed = {item.name for item in fields(cls)}
    unknown = set(parameters) - allowed
    if unknown:
        raise DriverArgumentValueError(
            f"{cls.__name__} does not accept {sorted(unknown)}; allowed: {sorted(allowed)}"
        )
    try:
        return cls(**parameters)  # type: ignore[arg-type,return-value]
    except TypeError as exc:
        raise DriverArgumentValueError(f"invalid {cls.__name__} parameters: {exc}") from exc


def _finite_sequence(values: Sequence[float], name: str) -> tuple[float, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise DriverArgumentValueError(f"{name} must be a numeric sequence")
    result = tuple(_finite(value, f"{name}[{index}]") for index, value in enumerate(values))
    if not result:
        raise DriverArgumentValueError(f"{name} must not be empty")
    return result


def _bool_sequence(values: bool | Sequence[bool], points: int, name: str) -> tuple[bool, ...]:
    if isinstance(values, bool):
        return (values,) * points if values else ()
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise DriverArgumentValueError(f"{name} must be a boolean or a boolean sequence")
    result = tuple(values)
    if not result:
        return ()
    if any(not isinstance(value, bool) for value in result):
        raise DriverArgumentValueError(f"{name} must contain booleans")
    if len(result) == 1:
        result = result * points
    if len(result) != points:
        raise DriverArgumentValueError(f"{name} must contain one value or one value per point")
    return result if any(result) else ()


def normalize_trigger_flags(
    values: bool | Sequence[bool], points: int, name: str
) -> tuple[bool, ...]:
    """Expand a bool or per-point flags; all-false collapses to ``()``."""
    return _bool_sequence(values, points, name)


def normalize_repeat_count(value: RepeatCount, maximum: int, name: str = "repeat_count") -> RepeatCount:
    """Validate a finite repeat count or the explicit ``'infinite'`` opt-in."""
    if isinstance(value, str):
        if value.strip().lower() in {"infinite", "inf", "infinity"}:
            return "infinite"
        raise DriverArgumentValueError(f"{name} must be an integer within 1..{maximum} or 'infinite'")
    message = f"{name} must be an integer within 1..{maximum} or 'infinite'"
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise DriverArgumentValueError(message)
    if not math.isfinite(value) or int(value) != value or not 1 <= int(value) <= maximum:
        raise DriverArgumentValueError(message)
    return int(value)


def repeat_scpi(value: RepeatCount) -> str:
    return "INF" if value == "infinite" else str(value)


def repeated_duration(duration_s: float, repeat: RepeatCount) -> float:
    return math.inf if repeat == "infinite" else duration_s * repeat


@dataclass(frozen=True)
class ArbSequenceStep:
    """One waveform of an ARB sequence.

    ``pacing='trigger'`` holds the last value of the step until a trigger
    arrives; only a trigger-paced step may repeat ``'infinite'`` times.
    """

    waveform: ArbShape
    repeat_count: RepeatCount = 1
    pacing: Literal["dwell", "trigger"] = "dwell"

    def __post_init__(self) -> None:
        if not isinstance(self.waveform, (_Shape, ArbUserDefined)):
            raise DriverArgumentValueError("waveform must be an ARB shape such as ArbPulse")
        if self.pacing not in {"dwell", "trigger"}:
            raise DriverArgumentValueError("pacing must be 'dwell' or 'trigger'")
        repeat = normalize_repeat_count(self.repeat_count, 16_777_216)
        if self.pacing == "dwell" and repeat != 1:
            raise DriverArgumentValueError(
                "sequence step repeat_count is only valid with trigger-paced execution"
            )
        if repeat == "infinite" and self.pacing != "trigger":
            raise DriverArgumentValueError(
                "only a trigger-paced sequence step may repeat 'infinite' times"
            )
        object.__setattr__(self, "repeat_count", repeat)

    @property
    def duration_s(self) -> float:
        return repeated_duration(self.waveform.duration_s, self.repeat_count)
