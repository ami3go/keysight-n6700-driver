"""Type-specific channel APIs."""

from __future__ import annotations

import hashlib
import math
import threading
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from time import monotonic, sleep
from typing import ClassVar, Literal, NamedTuple, Protocol, TypeVar

from scpi_driver_core.execution.polling import poll_until

from ._translate import translated
from .acquisition import AcquisitionSnapshot, AcquisitionState, AcquisitionTracker, DigitizeTiming
from .exceptions import (
    DriverArgumentValueError,
    DriverCommandRejectedError,
    DriverError,
    DriverMalformedResponseError,
    DriverOperationUncertainError,
    DriverPreconditionError,
    DriverTimeoutError,
    DriverUnsupportedOperationError,
)
from .module_capabilities import ChannelCapabilities
from .scpi import (
    format_bool,
    format_channel_list,
    format_float,
    parse_digitize_ascii,
    parse_digitize_real32,
)
from .types import (
    ArbSequenceConfiguration,
    ArbWaveformConfiguration,
    ArrayMeasurement,
    ChannelStatusSnapshot,
    ConstantDwellArbConfiguration,
    CurrentArbConfiguration,
    CurrentListConfiguration,
    Measurement,
    PowerMeasurement,
    ProtectionClearResult,
    ProtectionStatus,
    ScpiErrorRecord,
    SourceTransientConfiguration,
    StepTransientConfiguration,
    TransientQuantity,
    VoltageArbConfiguration,
    VoltageListConfiguration,
    VoltageTransientMode,
    VoltageTransientStatus,
    VoltageTransientToken,
)
from .waveforms import (
    PARAMETRIC_MAXIMUM_TIME_S,
    PARAMETRIC_SHAPES,
    SEQUENCE_MAXIMUM_POINTS,
    SEQUENCE_MAXIMUM_QUALITY,
    SEQUENCE_MINIMUM_QUALITY,
    USER_DEFINED_MAXIMUM_POINTS,
    ArbSequenceStep,
    ArbShape,
    ArbUserDefined,
    RepeatCount,
    normalize_repeat_count,
    normalize_trigger_flags,
    repeat_scpi,
    sequence_waveform_points,
)

_ConfigT = TypeVar("_ConfigT", bound=SourceTransientConfiguration)


class _ListReadback(NamedTuple):
    levels: tuple[float, ...]
    dwell_s: tuple[float, ...]
    repeat_count: RepeatCount
    begin_step_triggers: tuple[bool, ...]
    end_step_triggers: tuple[bool, ...]
    minimum: float
    maximum: float


class _DriverProtocol(Protocol):
    def query_scpi(self, command: str) -> str: ...

    def write_scpi(self, command: str) -> None: ...

    def operation_lock(self) -> AbstractContextManager[None]: ...

    def _measure_power(self, channel: int) -> PowerMeasurement: ...

    def _measure_channel(self, channel: int) -> Measurement: ...

    def drain_errors(self) -> list[ScpiErrorRecord]: ...

    def check_errors(self) -> None: ...

    def _begin_digitize(self, channel: int) -> None: ...

    def _end_digitize(self, channel: int) -> None: ...

    def _query_binary_scpi(self, command: str, *, max_bytes: int) -> bytes: ...

    def _channel_supports_digitizing(self, channel: int) -> bool: ...


class BaseChannel:
    """Safe common operations shared by all module types."""

    def __init__(self, driver: _DriverProtocol, channel: int, capabilities: ChannelCapabilities) -> None:
        self._driver = driver
        self.channel = channel
        self.capabilities = capabilities

    @property
    def _chan(self) -> str:
        return format_channel_list(self.channel)

    def _invalidate_discovery(self) -> None:
        """Revoke stale channel handles on rediscovery/disconnect."""
        self._discovery_valid = False

    def _assert_discovery_valid(self) -> None:
        if not getattr(self, "_discovery_valid", True):
            raise DriverPreconditionError(
                "channel discovery is stale; reacquire the channel from the driver"
            )

    def _query(self, command: str) -> str:
        self._assert_discovery_valid()
        return self._driver.query_scpi(command)

    def _write(self, command: str) -> None:
        self._assert_discovery_valid()
        self._driver.write_scpi(command)

    def measure_voltage(self) -> float:
        return float(self._query(f"MEAS:VOLT? {self._chan}"))

    def measure_current(self) -> float:
        return float(self._query(f"MEAS:CURR? {self._chan}"))

    def measure_power(self) -> PowerMeasurement:
        return self._driver._measure_power(self.channel)

    def measure(self) -> Measurement:
        return self._driver._measure_channel(self.channel)

    def fetch_voltage(self) -> float:
        return float(self._query(f"FETC:VOLT? {self._chan}"))

    def fetch_current(self) -> float:
        return float(self._query(f"FETC:CURR? {self._chan}"))

    def fetch_power(self) -> PowerMeasurement:
        return self.measure_power()

    def fetch(self) -> Measurement:
        return self.measure()

    def _get_enable_state(self) -> bool | None:
        raise DriverUnsupportedOperationError("generic channel has no enable state")

    def _set_enable_state(self, enabled: bool) -> None:
        raise DriverUnsupportedOperationError("generic channel has no enable command")

    def get_protection_status(self) -> ProtectionStatus:
        """Return live channel protection state from the Questionable register.

        The N6700 has no generic ``OUTP:PROT?`` query.  Protection causes are
        reported in the per-channel Questionable Condition register.  Reading
        ``STAT:QUES:COND?`` is non-destructive and therefore suitable for
        status polling.
        """
        mask = int(self._query(f"STAT:QUES:COND? {self._chan}").strip())
        fault_mask = 1 | 2 | 4 | 8 | 16 | 32 | 64 | 512 | 2048 | 4096
        return ProtectionStatus(
            channel=self.channel,
            active=bool(mask & fault_mask),
            over_voltage=bool(mask & (1 | 64)),
            over_current=bool(mask & 2),
            over_temperature=bool(mask & 16),
            power_limit=bool(mask & (8 | 32)),
            power_fail=bool(mask & 4),
            inhibit=bool(mask & 512),
            oscillation=bool(mask & 4096),
            raw_status=mask,
        )

    def get_status_snapshot(self) -> ChannelStatusSnapshot:
        return ChannelStatusSnapshot(
            channel=self.channel,
            output_or_input_enabled=self._get_enable_state(),
            protection=self.get_protection_status(),
        )

    def clear_protection(
        self,
        *,
        restore_output: bool = False,
        force_output_off_first: bool = True,
        verify_cleared: bool = True,
    ) -> ProtectionClearResult:
        before_prot = self.get_protection_status()
        before_state = self._get_enable_state()
        if force_output_off_first and before_state is not None:
            self._set_enable_state(False)
        self._write(f"OUTP:PROT:CLE {self._chan}")
        if restore_output and before_state:
            self._set_enable_state(True)
        after_prot = self.get_protection_status() if verify_cleared else before_prot
        after_state = self._get_enable_state()
        return ProtectionClearResult(
            channel=self.channel,
            protection_before=before_prot,
            protection_after=after_prot,
            output_state_before=before_state,
            output_state_after=after_state,
            restored_output=bool(restore_output and before_state),
            errors=tuple(self._driver.drain_errors()),
        )


class PowerSupplyChannel(BaseChannel):
    """Power supply channel API."""

    _WTG_TRANSIENT = 16
    _TRANSIENT_ACTIVE = 64
    # Profile ceiling must stay this fraction below OVP so slew overshoot at a
    # step edge cannot trip protection mid-profile.
    _OVP_HEADROOM_FRACTION = 0.05
    # Completion-wait default: quantized profile duration plus this margin.
    _COMPLETE_TIMEOUT_MARGIN_S = 2.0
    _COMPLETE_TIMEOUT_MARGIN_FRACTION = 0.1
    # Undefined header / hardware missing: the module or mainframe lacks the feature.
    _UNSUPPORTED_SCPI_CODES = frozenset({-113, -241})
    _QUANTITY_SCPI: ClassVar[dict[str, str]] = {"voltage": "VOLT", "current": "CURR"}
    _UNITS: ClassVar[dict[str, str]] = {"voltage": "V", "current": "A"}
    _BOUND_NAMES: ClassVar[dict[str, tuple[str, str]]] = {
        "voltage": ("minimum_voltage_v", "maximum_voltage_v"),
        "current": ("minimum_current_a", "maximum_current_a"),
    }
    # Repeat-count queries report INFinity as 9.9E37.
    _INFINITE_REPEAT = 9.9e37
    # Single-value parameter queries reply in NR3 with 7 significant digits.
    _NR3_REL_TOL = 1e-6
    # [SOURce:]ARB:<VOLT|CURR>:CDWell:DWELl range, in 10.24 us steps.
    _CDW_MINIMUM_DWELL_S = 10.24e-6
    _CDW_MAXIMUM_DWELL_S = 0.30
    # The reviewed text codec accepts at most 64 KiB including its terminator.
    # CDW binary input is vendor-defined, but is not used until its transport
    # framing has separate protocol qualification; never fragment this list.
    _MAXIMUM_CDW_ASCII_COMMAND_BYTES = 65_536
    # [SOURce:]ARB:SEQuence:STEP:FUNCtion:SHAPe: at most 100 steps.
    _MAXIMUM_SEQUENCE_STEPS = 100

    def __init__(self, driver: _DriverProtocol, channel: int, capabilities: ChannelCapabilities) -> None:
        super().__init__(driver, channel, capabilities)
        self._transient_lock = threading.RLock()
        self._transient_generation = 0
        self._transient_state = "idle"
        self._transient_mode = "fixed"
        self._transient_trigger_source = "bus"
        self._transient_config: SourceTransientConfiguration | None = None
        self._transient_continuous = False
        self._transient_triggered_at: float | None = None
        self._sequence_step_base: int | None = None

    def _invalidate_discovery(self) -> None:
        super()._invalidate_discovery()
        with self._transient_lock:
            self._transient_generation += 1
            self._transient_config = None
            self._transient_state = "unknown"

    def output_on(self) -> None:
        self.set_output(True)

    def output_off(self) -> None:
        self.set_output(False)

    def set_output(self, enabled: bool) -> None:
        self._write(f"OUTP {format_bool(enabled)},{self._chan}")

    def get_output(self) -> bool:
        return self._query(f"OUTP? {self._chan}").strip() not in {"0", "+0"}

    def _get_enable_state(self) -> bool | None:
        return self.get_output()

    def _set_enable_state(self, enabled: bool) -> None:
        self.set_output(enabled)

    def set_voltage_setpoint(self, value: float, *, voltage_range: float | str | None = None) -> None:
        if voltage_range is None:
            self._write(f"VOLT {format_float(value)},{self._chan}")
        else:
            self._write(
                f"VOLT:RANG {voltage_range},{self._chan};VOLT {format_float(value)},{self._chan}"
            )

    def get_voltage_setpoint(self) -> float:
        return float(self._query(f"VOLT? {self._chan}"))

    def set_current_limit(self, value: float, *, current_range: float | str | None = None) -> None:
        if current_range is None:
            self._write(f"CURR {format_float(value)},{self._chan}")
        else:
            self._write(
                f"CURR {format_float(value)},{self._chan};CURR:RANG {current_range},{self._chan}"
            )

    def get_current_limit(self) -> float:
        return float(self._query(f"CURR? {self._chan}"))

    def set_voltage_range(self, value: float | str) -> None:
        self._write(f"VOLT:RANG {value},{self._chan}")

    def set_current_range(self, value: float | str) -> None:
        self._write(f"CURR:RANG {value},{self._chan}")

    def get_voltage_range(self) -> float:
        return float(self._query(f"VOLT:RANG? {self._chan}"))

    def get_current_range(self) -> float:
        return float(self._query(f"CURR:RANG? {self._chan}"))

    def set_ovp(self, value: float) -> None:
        self._write(f"VOLT:PROT {format_float(value)},{self._chan}")

    def get_ovp(self) -> float:
        return float(self._query(f"VOLT:PROT? {self._chan}"))

    def set_ocp(self, enabled: bool) -> None:
        self._write(f"CURR:PROT:STAT {format_bool(enabled)},{self._chan}")

    def get_ocp(self) -> bool:
        return self._query(f"CURR:PROT:STAT? {self._chan}").strip() not in {"0", "+0"}

    @staticmethod
    def _finite(value: float, name: str) -> float:
        if isinstance(value, bool):
            raise DriverArgumentValueError(f"{name} must be numeric, not boolean")
        result = float(value)
        if not math.isfinite(result):
            raise DriverArgumentValueError(f"{name} must be finite")
        return result

    @classmethod
    def _finite_values(cls, values: Sequence[float], name: str) -> tuple[float, ...]:
        if isinstance(values, (str, bytes)):
            raise DriverArgumentValueError(f"{name} must be a numeric sequence")
        result = tuple(cls._finite(value, f"{name}[{index}]") for index, value in enumerate(values))
        if not result:
            raise DriverArgumentValueError(f"{name} must not be empty")
        return result

    @classmethod
    def _normalize_dwells(
        cls, dwell_s: float | Sequence[float], points: int, *, maximum_s: float
    ) -> tuple[float, ...]:
        if isinstance(dwell_s, Sequence) and not isinstance(dwell_s, (str, bytes)):
            values = cls._finite_values(dwell_s, "dwell_s")
            if len(values) not in {1, points}:
                raise DriverArgumentValueError(
                    "dwell_s must contain one value or one value per point"
                )
            values = values * points if len(values) == 1 else values
        else:
            values = (cls._finite(dwell_s, "dwell_s"),) * points  # type: ignore[arg-type]
        if any(value < 0 or value > maximum_s for value in values):
            raise DriverArgumentValueError(f"dwell_s values must be within 0..{maximum_s:g} s")
        return values

    # -- source-transient validation helpers ---------------------------------

    def _require_transient_support(self, quantity: TransientQuantity) -> None:
        if not self.capabilities.supports_list_mode:
            raise DriverUnsupportedOperationError(
                f"{self.capabilities.model} does not advertise source LIST/ARB support"
            )
        if quantity == "current" and not self.capabilities.supports_current_transient:
            raise DriverUnsupportedOperationError(
                f"{self.capabilities.model} does not advertise current transient support"
            )

    def _require_arb_support(self, quantity: TransientQuantity) -> None:
        self._require_transient_support(quantity)
        supported = (
            self.capabilities.supports_voltage_arb
            if quantity == "voltage"
            else self.capabilities.supports_current_arb
        )
        if not supported:
            mainframe = self.capabilities.mainframe_model or "unknown mainframe"
            raise DriverUnsupportedOperationError(
                f"{quantity} ARB is unavailable for {self.capabilities.model} in {mainframe}; "
                "ARB waveforms require an N6705 mainframe"
            )

    def _bounds(
        self, quantity: TransientQuantity, minimum: float, maximum: float
    ) -> tuple[float, float]:
        low_name, high_name = self._BOUND_NAMES[quantity]
        low = self._finite(minimum, low_name)
        high = self._finite(maximum, high_name)
        negative_allowed = (
            quantity == "voltage" and self.capabilities.supports_bipolar_voltage
        ) or (
            quantity == "current" and self.capabilities.supports_negative_current_arb
        )
        if high <= low:
            raise DriverArgumentValueError(
                f"{quantity} safety bounds require {low_name} < {high_name}"
            )
        if low < 0 and not negative_allowed:
            detail = (
                "only N6784A voltage profiles support a bipolar voltage envelope"
                if quantity == "voltage"
                else "this module/current-programming role has no approved negative-current ARB"
            )
            raise DriverArgumentValueError(
                f"{quantity} safety bounds require 0 <= {low_name}; {detail}"
            )
        return low, high

    @classmethod
    def _require_within(
        cls, quantity: TransientQuantity, values: Sequence[float], low: float, high: float
    ) -> None:
        if any(value < low or value > high for value in values):
            raise DriverArgumentValueError(
                f"every {quantity} point must stay within the approved "
                f"{low:g}..{high:g} {cls._UNITS[quantity]} envelope"
            )

    @classmethod
    def _require_readback_within(
        cls,
        quantity: TransientQuantity,
        values: Sequence[float],
        low: float,
        high: float,
        name: str,
    ) -> None:
        if any(value < low or value > high for value in values):
            raise DriverOperationUncertainError(
                f"{name} {quantity} readback leaves the approved "
                f"{low:g}..{high:g} {cls._UNITS[quantity]} envelope"
            )

    def _check_instrument_envelope(
        self, quantity: TransientQuantity, minimum: float, maximum: float
    ) -> None:
        """Approved ceiling versus the live range, OVP and SMU priority mode."""
        if quantity == "voltage":
            voltage_range = self.get_voltage_range()
            if max(abs(minimum), abs(maximum)) > voltage_range:
                raise DriverArgumentValueError(
                    f"approved envelope {minimum:g}..{maximum:g} V exceeds instrument "
                    f"range +/-{voltage_range:g} V"
                )
            ovp = self.get_ovp()
            ovp_ceiling = ovp * (1.0 - self._OVP_HEADROOM_FRACTION)
            if maximum > ovp_ceiling:
                raise DriverArgumentValueError(
                    f"approved maximum {maximum:g} V leaves less than "
                    f"{self._OVP_HEADROOM_FRACTION:.0%} headroom below OVP {ovp:g} V "
                    f"(ceiling {ovp_ceiling:g} V)"
                )
        else:
            current_range = self.get_current_range()
            if max(abs(minimum), abs(maximum)) > current_range:
                raise DriverArgumentValueError(
                    f"approved envelope {minimum:g}..{maximum:g} A exceeds instrument "
                    f"range +/-{current_range:g} A"
                )
            role = self.capabilities.current_programming_role
            if role == "unsupported":
                raise DriverUnsupportedOperationError(
                    f"{self.capabilities.model} has no approved current-programming role"
                )
            if role == "current_source":
                if not self.capabilities.supports_smu_priority_mode:
                    raise DriverUnsupportedOperationError(
                        f"{self.capabilities.model} has no approved current-priority mode"
                    )
                function = self._query(f"FUNC? {self._chan}").strip().upper()
                if not function.startswith("CURR"):
                    raise DriverPreconditionError(
                        "SMU current LIST/ARB requires current priority mode (FUNC CURR)"
                    )
                voltage_limit = self._parse_float(
                    self._query(f"VOLT:LIM? {self._chan}"), "SMU positive voltage compliance"
                )
                voltage_range = self.get_voltage_range()
                if voltage_limit <= 0 or voltage_limit > voltage_range:
                    raise DriverPreconditionError(
                        f"SMU voltage compliance {voltage_limit:g} V must be within "
                        f"0..{voltage_range:g} V"
                    )
            else:
                # On an ordinary supply CURR is a limit while VOLT remains the
                # driven quantity.  Validate the fixed voltage operating point.
                voltage_setpoint = self.get_voltage_setpoint()
                voltage_range = self.get_voltage_range()
                ovp = self.get_ovp()
                if (
                    not math.isfinite(voltage_setpoint)
                    or voltage_setpoint < 0
                    or voltage_setpoint > voltage_range
                    or voltage_setpoint > ovp * (1.0 - self._OVP_HEADROOM_FRACTION)
                ):
                    raise DriverPreconditionError(
                        "current-limit LIST/ARB requires a non-negative fixed voltage setpoint "
                        "within range and at least 5% below OVP"
                    )
            protection = self.get_protection_status()
            if protection.active:
                raise DriverPreconditionError(
                    f"current LIST/ARB requires cleared protection; "
                    f"STAT:QUES:COND={protection.raw_status}"
                )
        if self.capabilities.module_type == "smu" and (
            quantity == "voltage" or self.capabilities.current_programming_role != "current_source"
        ):
            # A source profile only programs the output in its own priority mode.
            if not self.capabilities.supports_smu_priority_mode:
                return
            expected = self._QUANTITY_SCPI[quantity]
            function = self._query(f"FUNC? {self._chan}").strip().upper()
            if not function.startswith(expected):
                raise DriverPreconditionError(
                    f"SMU {quantity} LIST/ARB requires {quantity} priority mode (FUNC {expected})"
                )

    def _validate_profile(
        self,
        quantity: TransientQuantity,
        levels: Sequence[float],
        *,
        minimum: float,
        maximum: float,
        maximum_points: int,
    ) -> tuple[tuple[float, ...], float, float]:
        self._require_transient_support(quantity)
        low, high = self._bounds(quantity, minimum, maximum)
        name = f"levels_{self._UNITS[quantity].lower()}"
        values = self._finite_values(levels, name)
        if len(values) > maximum_points:
            raise DriverArgumentValueError(f"{name} exceeds {maximum_points} points")
        self._require_within(quantity, values, low, high)
        self._check_instrument_envelope(quantity, low, high)
        return values, low, high

    def _validate_waveform(
        self,
        quantity: TransientQuantity,
        waveform: ArbShape,
        low: float,
        high: float,
        name: str = "waveform",
    ) -> None:
        if not isinstance(waveform, (ArbUserDefined, *PARAMETRIC_SHAPES.values())):
            raise DriverArgumentValueError(f"{name} must be an ARB shape such as ArbRamp")
        shape_low, shape_high = waveform.level_bounds
        self._require_within(quantity, (shape_low, shape_high), low, high)
        maximum_s = (
            self._maximum_dwell_s
            if isinstance(waveform, ArbUserDefined)
            else PARAMETRIC_MAXIMUM_TIME_S
        )
        if any(value > maximum_s for value in waveform.times_s):
            raise DriverArgumentValueError(
                f"{name} time parameters must be within 0..{maximum_s:g} s"
            )

    @staticmethod
    def _require_duration(one_pass_s: float, repeat: RepeatCount, maximum_duration_s: float) -> float:
        """Bound a finite run, or one pass of an ``'infinite'`` profile."""
        limit = PowerSupplyChannel._finite(maximum_duration_s, "maximum_duration_s")
        duration = one_pass_s if repeat == "infinite" else one_pass_s * repeat
        if limit <= 0 or duration > limit:
            raise DriverArgumentValueError(
                f"profile duration {duration:g} s exceeds maximum_duration_s {limit:g} s"
            )
        return limit

    @staticmethod
    def _require_quantized_duration(
        one_pass_s: float, repeat: RepeatCount, limit: float, feature: str
    ) -> None:
        duration = one_pass_s if repeat == "infinite" else one_pass_s * repeat
        if duration > limit:
            raise DriverOperationUncertainError(
                f"quantized {feature} duration exceeds maximum_duration_s"
            )

    @property
    def _smu_dwell_timebase(self) -> bool:
        return self.capabilities.model.startswith("N678")

    @property
    def _maximum_dwell_s(self) -> float:
        # Programmer's Reference, [SOURce:]ARB/LIST dwell: 268.435 s on N678xA.
        return 268.435 if self._smu_dwell_timebase else 262.144

    def _check_transient_errors(self, feature: str) -> None:
        """Map "feature absent" SCPI errors to a clean unsupported-operation error."""
        try:
            self._driver.check_errors()
        except DriverCommandRejectedError as exc:
            codes = {code for code, _ in exc.scpi_errors}
            if codes & self._UNSUPPORTED_SCPI_CODES:
                raise DriverUnsupportedOperationError(
                    f"{self.capabilities.model} channel {self.channel} rejected {feature} "
                    f"commands as unsupported: {list(exc.scpi_errors)}"
                ) from exc
            raise

    # -- SCPI formatting and readback parsing --------------------------------

    @staticmethod
    def _csv(values: Sequence[float]) -> str:
        return ",".join(format_float(value) for value in values)

    @staticmethod
    def _flags_csv(flags: Sequence[bool]) -> str:
        return ",".join("1" if flag else "0" for flag in flags) if flags else "0"

    @staticmethod
    def _parse_csv_floats(raw: str, name: str) -> tuple[float, ...]:
        try:
            values = tuple(float(item.strip()) for item in raw.strip().split(",") if item.strip())
        except ValueError as exc:
            raise DriverMalformedResponseError(f"malformed {name} readback: {raw!r}") from exc
        if not values or any(not math.isfinite(value) for value in values):
            raise DriverMalformedResponseError(f"malformed {name} readback: {raw!r}")
        return values

    @classmethod
    def _parse_float(cls, raw: str, name: str) -> float:
        values = cls._parse_csv_floats(raw, name)
        if len(values) != 1:
            raise DriverMalformedResponseError(f"malformed {name} readback: {raw!r}")
        return values[0]

    @staticmethod
    def _parse_integer(raw: str, name: str) -> int:
        try:
            value = float(raw.strip())
        except ValueError as exc:
            raise DriverMalformedResponseError(f"malformed {name} readback: {raw!r}") from exc
        if not math.isfinite(value) or not value.is_integer():
            raise DriverMalformedResponseError(f"malformed {name} readback: {raw!r}")
        return int(value)

    @classmethod
    def _parse_repeat(cls, raw: str, name: str) -> RepeatCount:
        try:
            value = float(raw.strip())
        except ValueError as exc:
            raise DriverMalformedResponseError(f"malformed {name} readback: {raw!r}") from exc
        # The instrument reports INFinity as 9.9E37.
        if value >= cls._INFINITE_REPEAT:
            return "infinite"
        return cls._parse_integer(raw, name)

    @classmethod
    def _parse_boolean(cls, raw: str, name: str) -> bool:
        value = cls._parse_integer(raw, name)
        if value not in {0, 1}:
            raise DriverMalformedResponseError(f"malformed {name} readback: {raw!r}")
        return bool(value)

    @classmethod
    def _parse_flags(cls, raw: str, name: str) -> tuple[bool, ...]:
        return tuple(cls._parse_boolean(item, name) for item in raw.strip().split(",") if item.strip())

    @staticmethod
    def _assert_profile_readback(
        expected: Sequence[float], actual: Sequence[float], name: str
    ) -> None:
        if len(expected) != len(actual) or any(
            not math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-12)
            for left, right in zip(expected, actual, strict=True)
        ):
            raise DriverOperationUncertainError(
                f"{name} readback does not match the requested profile"
            )

    @classmethod
    def _assert_level_readback(cls, expected: float, actual: float, name: str) -> None:
        # Single-value parameter queries reply in NR3 (+n.nnnnnnE+nn).
        if not math.isclose(expected, actual, rel_tol=cls._NR3_REL_TOL, abs_tol=1e-9):
            raise DriverOperationUncertainError(
                f"{name} readback does not match the requested value"
            )

    def _assert_dwell_readback(
        self, expected: Sequence[float], actual: Sequence[float], name: str
    ) -> None:
        """Accept only the range-dependent dwell quantization documented by Keysight."""
        if len(expected) != len(actual):
            raise DriverOperationUncertainError(
                f"{name} readback does not match the requested profile"
            )
        limits = (0.268435, 2.68435, 26.8435)
        resolutions = (1.024e-6, 10.24e-6, 102.4e-6, 1.024e-3)
        if not self._smu_dwell_timebase:
            limits = (0.262144, 2.62144, 26.2144)
            resolutions = (1e-6, 10e-6, 100e-6, 1e-3)
        for requested, returned in zip(expected, actual, strict=True):
            resolution = next(
                (value for limit, value in zip(limits, resolutions, strict=False) if requested <= limit),
                resolutions[-1],
            )
            if abs(requested - returned) > resolution + 1e-12:
                raise DriverOperationUncertainError(
                    f"{name} readback differs by more than one documented dwell quantum"
                )

    @staticmethod
    def _assert_flags_readback(
        expected: Sequence[bool], actual: Sequence[bool], name: str
    ) -> None:
        # A singleton reply applies to every point; () expects no trigger at all.
        if not expected:
            matches = not any(actual)
        elif len(actual) == 1:
            matches = all(flag == actual[0] for flag in expected)
        else:
            matches = tuple(actual) == tuple(expected)
        if not matches:
            raise DriverOperationUncertainError(f"{name} readback mismatch")

    def _assert_source_modes(self, quantity: TransientQuantity, mode: str) -> None:
        """The driven quantity is in ``mode``; the other one stayed FIX."""
        if quantity == "voltage":
            self._assert_fixed_current_mode()
            if not self._query(f"VOLT:MODE? {self._chan}").strip().upper().startswith(mode):
                raise DriverOperationUncertainError(f"voltage mode did not enter {mode}")
            return
        if not self._query(f"VOLT:MODE? {self._chan}").strip().upper().startswith("FIX"):
            raise DriverOperationUncertainError("voltage mode did not stay FIX")
        if not self._query(f"CURR:MODE? {self._chan}").strip().upper().startswith(mode):
            raise DriverOperationUncertainError(f"current mode did not enter {mode}")
        protection = self.get_protection_status()
        if protection.active:
            raise DriverOperationUncertainError(
                f"protection became active while configuring current {mode}; "
                f"STAT:QUES:COND={protection.raw_status}"
            )

    def _assert_fixed_current_mode(self) -> None:
        if not self._query(f"CURR:MODE? {self._chan}").strip().upper().startswith("FIX"):
            raise DriverOperationUncertainError("current mode did not stay FIX")

    # -- slew -----------------------------------------------------------------

    def _set_slew_rate(self, quantity: TransientQuantity, rate: float | str, name: str) -> float:
        self._require_slew_support(quantity)
        if isinstance(rate, str):
            if rate.strip().lower() not in {"max", "infinity", "inf"}:
                raise DriverArgumentValueError(f"{name} must be finite and > 0, or 'max'")
            command_value = "INF"
        else:
            value = self._finite(rate, name)
            if value <= 0:
                raise DriverArgumentValueError(f"{name} must be > 0")
            command_value = format_float(value)
        self._write(f"{self._QUANTITY_SCPI[quantity]}:SLEW {command_value},{self._chan}")
        self._check_transient_errors(f"{quantity} slew")
        return self._get_slew_rate(quantity)

    def _get_slew_rate(self, quantity: TransientQuantity) -> float:
        self._require_slew_support(quantity)
        value = float(self._query(f"{self._QUANTITY_SCPI[quantity]}:SLEW? {self._chan}"))
        if not math.isfinite(value) or value <= 0:
            raise DriverMalformedResponseError(f"invalid {quantity} slew readback: {value!r}")
        return value

    def _require_slew_support(self, quantity: TransientQuantity) -> None:
        supported = (
            self.capabilities.supports_voltage_slew
            if quantity == "voltage"
            else self.capabilities.supports_current_slew
        )
        if not supported:
            raise DriverUnsupportedOperationError(
                f"{self.capabilities.model} does not advertise {quantity} slew control"
            )

    def set_voltage_slew_rate(self, rate_v_per_s: float | str) -> float:
        """Set and read back voltage slew in V/s; ``'max'`` selects fastest."""
        return self._set_slew_rate("voltage", rate_v_per_s, "rate_v_per_s")

    def get_voltage_slew_rate(self) -> float:
        return self._get_slew_rate("voltage")

    def set_current_slew_rate(self, rate_a_per_s: float | str) -> float:
        """Set and read back current slew in A/s (N678xA SMU only); ``'max'`` selects fastest."""
        return self._set_slew_rate("current", rate_a_per_s, "rate_a_per_s")

    def get_current_slew_rate(self) -> float:
        return self._get_slew_rate("current")

    # -- configuration transaction --------------------------------------------

    def _require_output_off_for_configuration(self) -> None:
        # Checked before any write: a refused call must not abort a profile
        # that is already armed or running on an energized output.
        if self.get_output():
            raise DriverPreconditionError(
                "source transient profiles must be configured while the output is OFF"
            )

    def _stop_transient_system(self) -> None:
        # "If INITiate:CONTinuous:TRANsient ON has been programmed, ABORt does
        # not turn off continuous triggers", so turn continuous initiation off
        # first, whatever the cached flag says, then return to Idle.
        self._write(f"INIT:CONT:TRAN OFF,{self._chan}")
        self._write(f"ABOR:TRAN {self._chan}")
        self._transient_continuous = False

    def _prepare_transient_configuration(self) -> None:
        self._stop_transient_system()
        self._write(f"VOLT:MODE FIX,{self._chan}")
        # The quantity a profile does not drive stays FIXed at its DC value,
        # so pin both before programming the one that is driven.
        self._write(f"CURR:MODE FIX,{self._chan}")

    def _invalidate_transient_tokens(self) -> None:
        # Every ABOR:TRAN path bumps the generation so a concurrent wait can
        # tell "aborted" apart from "ran to completion" (both clear the bits).
        self._transient_generation += 1

    def _configuration_failed(self, primary: BaseException) -> None:
        """Best-effort cleanup without ever replacing ``primary``."""
        failures: list[str] = []
        for command in (
            f"INIT:CONT:TRAN OFF,{self._chan}",
            f"ABOR:TRAN {self._chan}",
            f"VOLT:MODE FIX,{self._chan}",
            f"CURR:MODE FIX,{self._chan}",
        ):
            try:
                self._write(command)
            except BaseException as cleanup_error:
                failures.append(
                    f"{command}: {type(cleanup_error).__name__}: {cleanup_error}"
                )
        self._invalidate_transient_tokens()
        self._transient_state = "unknown"
        self._transient_mode = "fixed"
        self._transient_config = None
        self._transient_continuous = False
        outcome = (
            "cleanup completed"
            if not failures
            else "cleanup failures: " + "; ".join(failures)
        )
        if isinstance(primary, DriverError):
            primary.details["source_transient_configuration_cleanup"] = outcome
        if hasattr(primary, "add_note"):
            primary.add_note(f"source transient configuration {outcome}")

    def _run_configuration(
        self,
        mode: VoltageTransientMode,
        program: Callable[[], None],
        verify: Callable[[], _ConfigT],
    ) -> _ConfigT:
        """Output-OFF guarded program/verify transaction with cleanup on failure."""
        with self._transient_lock:
            with self._driver.operation_lock():
                self._require_output_off_for_configuration()
                try:
                    self._invalidate_transient_tokens()
                    self._prepare_transient_configuration()
                    program()
                    config = verify()
                except BaseException as primary:
                    self._configuration_failed(primary)
                    raise
            self._transient_config = config
            self._transient_mode = mode
            self._transient_state = "configured"
            return config

    def _cleanup_failed_voltage_transient(self, primary: BaseException) -> None:
        """Preserve a wait failure while recording best-effort abort outcome."""
        try:
            self.abort_voltage_transient()
        except Exception as cleanup_error:
            self._transient_state = "unknown"
            outcome = f"abort_failed: {type(cleanup_error).__name__}: {cleanup_error}"
        else:
            outcome = "aborted"
        if isinstance(primary, DriverError):
            primary.details["voltage_transient_cleanup"] = outcome
            primary.details["voltage_transient_generation"] = self._transient_generation
        if hasattr(primary, "add_note"):
            primary.add_note(f"voltage transient cleanup: {outcome}")

    # -- LIST -----------------------------------------------------------------

    def _configure_list(
        self,
        quantity: TransientQuantity,
        levels: Sequence[float],
        dwell_s: float | Sequence[float],
        *,
        minimum: float,
        maximum: float,
        repeat_count: RepeatCount,
        step_mode: Literal["auto", "once"],
        terminate_last: bool,
        maximum_duration_s: float,
        begin_step_triggers: bool | Sequence[bool],
        end_step_triggers: bool | Sequence[bool],
        build: Callable[[_ListReadback], _ConfigT],
    ) -> _ConfigT:
        values, low, high = self._validate_profile(
            quantity, levels, minimum=minimum, maximum=maximum, maximum_points=512
        )
        dwells = self._normalize_dwells(dwell_s, len(values), maximum_s=self._maximum_dwell_s)
        repeat = normalize_repeat_count(repeat_count, 256)
        if step_mode not in {"auto", "once"}:
            raise DriverArgumentValueError("step_mode must be 'auto' or 'once'")
        begin = normalize_trigger_flags(begin_step_triggers, len(values), "begin_step_triggers")
        end = normalize_trigger_flags(end_step_triggers, len(values), "end_step_triggers")
        duration_limit = self._require_duration(sum(dwells), repeat, maximum_duration_s)
        scpi = self._QUANTITY_SCPI[quantity]
        label = f"LIST {quantity}"

        def program() -> None:
            self._write(f"LIST:{scpi} {self._csv(values)},{self._chan}")
            self._write(f"LIST:DWEL {self._csv(dwells)},{self._chan}")
            self._write(f"LIST:TOUT:BOST {self._flags_csv(begin)},{self._chan}")
            self._write(f"LIST:TOUT:EOST {self._flags_csv(end)},{self._chan}")
            self._write(f"LIST:COUN {repeat_scpi(repeat)},{self._chan}")
            self._write(f"LIST:STEP {step_mode.upper()},{self._chan}")
            self._write(f"LIST:TERM:LAST {format_bool(terminate_last)},{self._chan}")
            self._write(f"{scpi}:MODE LIST,{self._chan}")
            self._check_transient_errors(f"{quantity} LIST")

        def verify() -> _ConfigT:
            level_readback = self._parse_csv_floats(
                self._query(f"LIST:{scpi}? {self._chan}"), label
            )
            dwell_readback = self._parse_csv_floats(
                self._query(f"LIST:DWEL? {self._chan}"), "LIST dwell"
            )
            self._assert_profile_readback(values, level_readback, label)
            self._assert_dwell_readback(dwells, dwell_readback, "LIST dwell")
            self._require_quantized_duration(sum(dwell_readback), repeat, duration_limit, "LIST")
            count_readback = self._parse_repeat(
                self._query(f"LIST:COUN? {self._chan}"), "LIST repeat count"
            )
            step_readback = self._query(f"LIST:STEP? {self._chan}").strip().upper()
            terminate_readback = self._parse_boolean(
                self._query(f"LIST:TERM:LAST? {self._chan}"), "LIST termination"
            )
            if count_readback != repeat:
                raise DriverOperationUncertainError("LIST repeat-count readback mismatch")
            if not step_readback.startswith(step_mode.upper()):
                raise DriverOperationUncertainError("LIST step-mode readback mismatch")
            if terminate_readback != bool(terminate_last):
                raise DriverOperationUncertainError("LIST termination readback mismatch")
            self._assert_flags_readback(
                begin,
                self._parse_flags(self._query(f"LIST:TOUT:BOST? {self._chan}"), "LIST BOST"),
                "LIST begin-of-step trigger",
            )
            self._assert_flags_readback(
                end,
                self._parse_flags(self._query(f"LIST:TOUT:EOST? {self._chan}"), "LIST EOST"),
                "LIST end-of-step trigger",
            )
            self._assert_source_modes(quantity, "LIST")
            return build(
                _ListReadback(level_readback, dwell_readback, repeat, begin, end, low, high)
            )

        return self._run_configuration("list", program, verify)

    def configure_voltage_list(
        self,
        levels_v: Sequence[float],
        dwell_s: float | Sequence[float],
        *,
        maximum_voltage_v: float,
        minimum_voltage_v: float = 0.0,
        repeat_count: RepeatCount = 1,
        step_mode: Literal["auto", "once"] = "auto",
        terminate_last: bool = False,
        maximum_duration_s: float = 60.0,
        begin_step_triggers: bool | Sequence[bool] = False,
        end_step_triggers: bool | Sequence[bool] = False,
    ) -> VoltageListConfiguration:
        """Configure a bounded, output-OFF voltage LIST and verify readback.

        ``step_mode='once'`` advances one step per trigger and therefore
        requires the ``'external'`` transient trigger source.
        ``repeat_count='infinite'`` runs until aborted; ``maximum_duration_s``
        then bounds one pass.  ``begin_step_triggers``/``end_step_triggers``
        program the ``LIST:TOUT:BOST``/``EOST`` trigger outputs (a bool
        applies to every step).
        """
        current_limit = self.get_current_limit()

        def build(readback: _ListReadback) -> VoltageListConfiguration:
            return VoltageListConfiguration(
                channel=self.channel,
                levels_v=readback.levels,
                dwell_s=readback.dwell_s,
                current_limit_a=current_limit,
                repeat_count=readback.repeat_count,
                step_mode=step_mode,
                terminate_last=bool(terminate_last),
                minimum_voltage_v=readback.minimum,
                maximum_voltage_v=readback.maximum,
                begin_step_triggers=readback.begin_step_triggers,
                end_step_triggers=readback.end_step_triggers,
            )

        return self._configure_list(
            "voltage",
            levels_v,
            dwell_s,
            minimum=minimum_voltage_v,
            maximum=maximum_voltage_v,
            repeat_count=repeat_count,
            step_mode=step_mode,
            terminate_last=terminate_last,
            maximum_duration_s=maximum_duration_s,
            begin_step_triggers=begin_step_triggers,
            end_step_triggers=end_step_triggers,
            build=build,
        )

    def configure_current_list(
        self,
        levels_a: Sequence[float],
        dwell_s: float | Sequence[float],
        *,
        maximum_current_a: float,
        minimum_current_a: float = 0.0,
        repeat_count: RepeatCount = 1,
        step_mode: Literal["auto", "once"] = "auto",
        terminate_last: bool = False,
        maximum_duration_s: float = 60.0,
        begin_step_triggers: bool | Sequence[bool] = False,
        end_step_triggers: bool | Sequence[bool] = False,
    ) -> CurrentListConfiguration:
        """Configure a bounded, output-OFF current LIST; voltage stays FIXed.

        On power-supply modules the list steps the current *limit*; on an
        SMU it requires current priority (``FUNC CURR``).
        """
        voltage_setpoint = self.get_voltage_setpoint()

        def build(readback: _ListReadback) -> CurrentListConfiguration:
            return CurrentListConfiguration(
                channel=self.channel,
                levels_a=readback.levels,
                dwell_s=readback.dwell_s,
                voltage_setpoint_v=voltage_setpoint,
                repeat_count=readback.repeat_count,
                step_mode=step_mode,
                terminate_last=bool(terminate_last),
                minimum_current_a=readback.minimum,
                maximum_current_a=readback.maximum,
                begin_step_triggers=readback.begin_step_triggers,
                end_step_triggers=readback.end_step_triggers,
            )

        return self._configure_list(
            "current",
            levels_a,
            dwell_s,
            minimum=minimum_current_a,
            maximum=maximum_current_a,
            repeat_count=repeat_count,
            step_mode=step_mode,
            terminate_last=terminate_last,
            maximum_duration_s=maximum_duration_s,
            begin_step_triggers=begin_step_triggers,
            end_step_triggers=end_step_triggers,
            build=build,
        )

    # -- ARB shapes -------------------------------------------------------------

    def _shape_command(
        self,
        quantity: TransientQuantity,
        suffix: str,
        value: str | None = None,
        *,
        step: int | None = None,
    ) -> str:
        """``ARB:<Q>:<suffix>`` or its ``ARB:SEQ:STEP:<Q>:<suffix> ..., <step#>`` form."""
        scpi = self._QUANTITY_SCPI[quantity]
        head = f"ARB:{scpi}:{suffix}" if step is None else f"ARB:SEQ:STEP:{scpi}:{suffix}"
        arguments = [] if value is None else [value]
        if step is not None:
            arguments.append(str(step))
        arguments.append(self._chan)
        if value is None:
            return f"{head}? {','.join(arguments)}"
        return f"{head} {','.join(arguments)}"

    def _write_shape(
        self, quantity: TransientQuantity, waveform: ArbShape, *, step: int | None = None
    ) -> None:
        if isinstance(waveform, ArbUserDefined):
            self._write(self._shape_command(quantity, "UDEF:LEV", self._csv(waveform.levels), step=step))
            self._write(self._shape_command(quantity, "UDEF:DWEL", self._csv(waveform.dwell_s), step=step))
            self._write(
                self._shape_command(
                    quantity, "UDEF:BOST", self._flags_csv(waveform.begin_step_triggers), step=step
                )
            )
            return
        for suffix, value, kind in waveform.scpi_parameters():
            text = str(int(value)) if kind == "count" else format_float(value)
            self._write(self._shape_command(quantity, suffix, text, step=step))

    def _read_shape(
        self, quantity: TransientQuantity, shape: str, *, step: int | None = None
    ) -> ArbShape:
        """Read every parameter of ``shape`` back into a validated shape object."""
        name = f"ARB {quantity} {shape}" if step is None else f"ARB sequence step {step} {shape}"
        if shape == "UDEF":
            levels = self._parse_csv_floats(
                self._query(self._shape_command(quantity, "UDEF:LEV", step=step)), f"{name} level"
            )
            dwells = self._parse_csv_floats(
                self._query(self._shape_command(quantity, "UDEF:DWEL", step=step)), f"{name} dwell"
            )
            flags = self._parse_flags(
                self._query(self._shape_command(quantity, "UDEF:BOST", step=step)), f"{name} BOST"
            )
            if len(flags) == 1:
                flags = flags * len(levels)
            try:
                return ArbUserDefined(levels, dwells, flags if any(flags) else ())
            except DriverArgumentValueError as exc:
                raise DriverMalformedResponseError(f"inconsistent {name} readback: {exc}") from exc
        cls = PARAMETRIC_SHAPES.get(shape)
        if cls is None:
            raise DriverMalformedResponseError(f"unsupported {name} shape readback {shape!r}")
        values = {
            suffix: self._parse_float(
                self._query(self._shape_command(quantity, suffix, step=step)), f"{name} {suffix}"
            )
            for _, suffix, _ in cls._parameters
        }
        try:
            return cls.from_scpi_parameters(values)  # type: ignore[return-value]
        except DriverArgumentValueError as exc:
            raise DriverMalformedResponseError(f"invalid {name} readback: {exc}") from exc

    def _assert_shape_readback(self, expected: ArbShape, actual: ArbShape, name: str) -> None:
        if type(expected) is not type(actual):
            raise DriverOperationUncertainError(f"{name} shape readback mismatch")
        if isinstance(expected, ArbUserDefined) and isinstance(actual, ArbUserDefined):
            self._assert_profile_readback(expected.levels, actual.levels, f"{name} level")
            self._assert_dwell_readback(expected.dwell_s, actual.dwell_s, f"{name} dwell")
            self._assert_flags_readback(
                expected.begin_step_triggers, actual.begin_step_triggers, f"{name} BOST"
            )
            return
        if isinstance(expected, ArbUserDefined) or isinstance(actual, ArbUserDefined):
            raise DriverOperationUncertainError(f"{name} shape readback mismatch")
        for (suffix, wanted, kind), (_, got, _) in zip(
            expected.scpi_parameters(), actual.scpi_parameters(), strict=True
        ):
            label = f"{name} {suffix}"
            if kind == "time":
                self._assert_dwell_readback((wanted,), (got,), label)
            elif kind == "count":
                if int(wanted) != int(got):
                    raise DriverOperationUncertainError(f"{label} readback mismatch")
            else:
                self._assert_level_readback(wanted, got, label)

    @staticmethod
    def _parse_shape_keyword(raw: str) -> str:
        value = raw.strip().strip('"').upper()
        for keyword in ("STEP", "RAMP", "STA", "SIN", "PULS", "TRAP", "EXP", "UDEF", "CDW", "SEQ", "NONE"):
            if value.startswith(keyword):
                return keyword
        raise DriverMalformedResponseError(f"malformed ARB shape readback: {raw!r}")

    def _assert_arb_function(self, quantity: TransientQuantity, shape: str) -> None:
        function_readback = self._query(f"ARB:FUNC:TYPE? {self._chan}").strip().upper()
        shape_readback = self._parse_shape_keyword(self._query(f"ARB:FUNC:SHAP? {self._chan}"))
        if not function_readback.startswith(self._QUANTITY_SCPI[quantity]) or shape_readback != shape:
            raise DriverOperationUncertainError("ARB function/shape readback mismatch")

    def _configure_single_arb(
        self,
        quantity: TransientQuantity,
        waveform: ArbShape,
        *,
        minimum: float,
        maximum: float,
        repeat_count: RepeatCount,
        terminate_last: bool,
        maximum_duration_s: float,
        convert: Callable[[ArbWaveformConfiguration], _ConfigT] | None = None,
    ) -> _ConfigT | ArbWaveformConfiguration:
        self._require_arb_support(quantity)
        low, high = self._bounds(quantity, minimum, maximum)
        self._validate_waveform(quantity, waveform, low, high)
        repeat = normalize_repeat_count(repeat_count, 16_777_216)
        duration_limit = self._require_duration(waveform.duration_s, repeat, maximum_duration_s)
        self._check_instrument_envelope(quantity, low, high)
        scpi = self._QUANTITY_SCPI[quantity]
        label = f"{quantity} ARB"

        def program() -> None:
            self._write(f"ARB:FUNC:TYPE {scpi},{self._chan}")
            self._write(f"ARB:FUNC:SHAP {waveform.shape},{self._chan}")
            self._write_shape(quantity, waveform)
            self._write(f"ARB:COUN {repeat_scpi(repeat)},{self._chan}")
            self._write(f"ARB:TERM:LAST {format_bool(terminate_last)},{self._chan}")
            self._write(f"{scpi}:MODE ARB,{self._chan}")
            self._check_transient_errors(label)

        def verify() -> _ConfigT | ArbWaveformConfiguration:
            readback = self._read_shape(quantity, waveform.shape)
            self._assert_shape_readback(waveform, readback, f"ARB {quantity}")
            self._require_readback_within(quantity, readback.level_bounds, low, high, "ARB")
            self._require_quantized_duration(readback.duration_s, repeat, duration_limit, "ARB")
            count_readback = self._parse_repeat(
                self._query(f"ARB:COUN? {self._chan}"), "ARB repeat count"
            )
            terminate_readback = self._parse_boolean(
                self._query(f"ARB:TERM:LAST? {self._chan}"), "ARB termination"
            )
            if count_readback != repeat:
                raise DriverOperationUncertainError("ARB repeat-count readback mismatch")
            if terminate_readback != bool(terminate_last):
                raise DriverOperationUncertainError("ARB termination readback mismatch")
            self._assert_arb_function(quantity, waveform.shape)
            self._assert_source_modes(quantity, "ARB")
            config = ArbWaveformConfiguration(
                channel=self.channel,
                quantity=quantity,
                waveform=readback,
                repeat_count=repeat,
                terminate_last=bool(terminate_last),
                minimum_level=low,
                maximum_level=high,
            )
            return config if convert is None else convert(config)

        return self._run_configuration("arb", program, verify)

    def _user_defined(
        self,
        quantity: TransientQuantity,
        levels: Sequence[float],
        dwell_s: float | Sequence[float],
        begin_step_triggers: bool | Sequence[bool],
        minimum: float,
        maximum: float,
    ) -> ArbUserDefined:
        # Offline checks only; the instrument envelope is checked once by
        # _configure_single_arb.
        self._require_transient_support(quantity)
        low, high = self._bounds(quantity, minimum, maximum)
        name = f"levels_{self._UNITS[quantity].lower()}"
        values = self._finite_values(levels, name)
        if len(values) > USER_DEFINED_MAXIMUM_POINTS:
            raise DriverArgumentValueError(f"{name} exceeds {USER_DEFINED_MAXIMUM_POINTS} points")
        self._require_within(quantity, values, low, high)
        dwells = self._normalize_dwells(dwell_s, len(values), maximum_s=self._maximum_dwell_s)
        flags = normalize_trigger_flags(begin_step_triggers, len(values), "begin_step_triggers")
        return ArbUserDefined(values, dwells, flags)

    def configure_voltage_arb(
        self,
        levels_v: Sequence[float],
        dwell_s: float | Sequence[float],
        *,
        maximum_voltage_v: float,
        minimum_voltage_v: float = 0.0,
        repeat_count: RepeatCount = 1,
        terminate_last: bool = False,
        maximum_duration_s: float = 60.0,
        begin_step_triggers: bool | Sequence[bool] = False,
    ) -> VoltageArbConfiguration:
        """Configure a bounded user-defined voltage ARB and verify readback."""
        waveform = self._user_defined(
            "voltage", levels_v, dwell_s, begin_step_triggers, minimum_voltage_v, maximum_voltage_v
        )

        def convert(config: ArbWaveformConfiguration) -> VoltageArbConfiguration:
            readback = config.waveform
            if not isinstance(readback, ArbUserDefined):
                raise DriverOperationUncertainError("ARB shape readback mismatch")
            return VoltageArbConfiguration(
                channel=self.channel,
                levels_v=readback.levels,
                dwell_s=readback.dwell_s,
                repeat_count=config.repeat_count,
                terminate_last=config.terminate_last,
                minimum_voltage_v=config.minimum_level,
                maximum_voltage_v=config.maximum_level,
                begin_step_triggers=readback.begin_step_triggers,
            )

        return self._configure_single_arb(  # type: ignore[return-value]
            "voltage",
            waveform,
            minimum=minimum_voltage_v,
            maximum=maximum_voltage_v,
            repeat_count=repeat_count,
            terminate_last=terminate_last,
            maximum_duration_s=maximum_duration_s,
            convert=convert,
        )

    def configure_current_arb(
        self,
        levels_a: Sequence[float],
        dwell_s: float | Sequence[float],
        *,
        maximum_current_a: float,
        minimum_current_a: float = 0.0,
        repeat_count: RepeatCount = 1,
        terminate_last: bool = False,
        maximum_duration_s: float = 60.0,
        begin_step_triggers: bool | Sequence[bool] = False,
    ) -> CurrentArbConfiguration:
        """Configure a bounded user-defined current ARB; voltage stays FIXed."""
        waveform = self._user_defined(
            "current", levels_a, dwell_s, begin_step_triggers, minimum_current_a, maximum_current_a
        )

        def convert(config: ArbWaveformConfiguration) -> CurrentArbConfiguration:
            readback = config.waveform
            if not isinstance(readback, ArbUserDefined):
                raise DriverOperationUncertainError("ARB shape readback mismatch")
            return CurrentArbConfiguration(
                channel=self.channel,
                levels_a=readback.levels,
                dwell_s=readback.dwell_s,
                repeat_count=config.repeat_count,
                terminate_last=config.terminate_last,
                minimum_current_a=config.minimum_level,
                maximum_current_a=config.maximum_level,
                begin_step_triggers=readback.begin_step_triggers,
            )

        return self._configure_single_arb(  # type: ignore[return-value]
            "current",
            waveform,
            minimum=minimum_current_a,
            maximum=maximum_current_a,
            repeat_count=repeat_count,
            terminate_last=terminate_last,
            maximum_duration_s=maximum_duration_s,
            convert=convert,
        )

    def configure_voltage_arb_waveform(
        self,
        waveform: ArbShape,
        *,
        maximum_voltage_v: float,
        minimum_voltage_v: float = 0.0,
        repeat_count: RepeatCount = 1,
        terminate_last: bool = False,
        maximum_duration_s: float = 60.0,
    ) -> ArbWaveformConfiguration:
        """Configure a built-in voltage ARB shape (``ArbStep``, ``ArbRamp``, ...).

        Every level the shape can reach must stay inside the approved
        envelope; the shape is read back parameter by parameter.
        """
        return self._configure_single_arb(
            "voltage",
            waveform,
            minimum=minimum_voltage_v,
            maximum=maximum_voltage_v,
            repeat_count=repeat_count,
            terminate_last=terminate_last,
            maximum_duration_s=maximum_duration_s,
        )

    def configure_current_arb_waveform(
        self,
        waveform: ArbShape,
        *,
        maximum_current_a: float,
        minimum_current_a: float = 0.0,
        repeat_count: RepeatCount = 1,
        terminate_last: bool = False,
        maximum_duration_s: float = 60.0,
    ) -> ArbWaveformConfiguration:
        """Configure a built-in current ARB shape; levels are amperes."""
        return self._configure_single_arb(
            "current",
            waveform,
            minimum=minimum_current_a,
            maximum=maximum_current_a,
            repeat_count=repeat_count,
            terminate_last=terminate_last,
            maximum_duration_s=maximum_duration_s,
        )

    # -- constant-dwell ARB ------------------------------------------------------

    def _configure_constant_dwell(
        self,
        quantity: TransientQuantity,
        levels: Sequence[float],
        dwell_s: float,
        *,
        minimum: float,
        maximum: float,
        repeat_count: RepeatCount,
        terminate_last: bool,
        maximum_duration_s: float,
    ) -> ConstantDwellArbConfiguration:
        self._require_arb_support(quantity)
        values, low, high = self._validate_profile(
            quantity, levels, minimum=minimum, maximum=maximum, maximum_points=65_535
        )
        dwell = self._finite(dwell_s, "dwell_s")
        if not self._CDW_MINIMUM_DWELL_S <= dwell <= self._CDW_MAXIMUM_DWELL_S:
            raise DriverArgumentValueError(
                f"constant-dwell dwell_s must be within {self._CDW_MINIMUM_DWELL_S:g}.."
                f"{self._CDW_MAXIMUM_DWELL_S:g} s"
            )
        # "For constant-dwell Arbs only, the maximum count is limited to 256."
        repeat = normalize_repeat_count(repeat_count, 256)
        duration_limit = self._require_duration(dwell * len(values), repeat, maximum_duration_s)
        scpi = self._QUANTITY_SCPI[quantity]
        level_command = f"ARB:{scpi}:CDW {self._csv(values)},{self._chan}"
        encoded_bytes = len(level_command.encode("ascii")) + 1  # conservative LF terminator
        if encoded_bytes > self._MAXIMUM_CDW_ASCII_COMMAND_BYTES:
            raise DriverArgumentValueError(
                f"constant-dwell ASCII command is {encoded_bytes} bytes; maximum is "
                f"{self._MAXIMUM_CDW_ASCII_COMMAND_BYTES} bytes. The driver does not "
                "fragment CDW vectors or use unqualified binary input."
            )

        def program() -> None:
            self._write(f"ARB:FUNC:TYPE {scpi},{self._chan}")
            self._write(f"ARB:FUNC:SHAP CDW,{self._chan}")
            self._write(f"ARB:{scpi}:CDW:DWEL {format_float(dwell)},{self._chan}")
            self._write(level_command)
            self._write(f"ARB:COUN {repeat_scpi(repeat)},{self._chan}")
            self._write(f"ARB:TERM:LAST {format_bool(terminate_last)},{self._chan}")
            self._write(f"{scpi}:MODE ARB,{self._chan}")
            self._check_transient_errors(f"{quantity} constant-dwell ARB")

        def verify() -> ConstantDwellArbConfiguration:
            level_readback = self._parse_csv_floats(
                self._query(f"ARB:{scpi}:CDW? {self._chan}"), "constant-dwell level"
            )
            point_readback = self._parse_integer(
                self._query(f"ARB:{scpi}:CDW:POIN? {self._chan}"),
                "constant-dwell point count",
            )
            if point_readback != len(values):
                raise DriverOperationUncertainError(
                    "constant-dwell point-count readback mismatch"
                )
            dwell_readback = self._parse_float(
                self._query(f"ARB:{scpi}:CDW:DWEL? {self._chan}"), "constant-dwell dwell"
            )
            self._assert_profile_readback(values, level_readback, "constant-dwell level")
            # Rounded to the nearest 10.24 microseconds.
            if abs(dwell_readback - dwell) > self._CDW_MINIMUM_DWELL_S / 2 + 1e-12:
                raise DriverOperationUncertainError(
                    "constant-dwell dwell readback differs by more than half a 10.24 us quantum"
                )
            self._require_quantized_duration(
                dwell_readback * len(level_readback), repeat, duration_limit, "constant-dwell ARB"
            )
            count_readback = self._parse_repeat(
                self._query(f"ARB:COUN? {self._chan}"), "ARB repeat count"
            )
            terminate_readback = self._parse_boolean(
                self._query(f"ARB:TERM:LAST? {self._chan}"), "ARB termination"
            )
            if count_readback != repeat:
                raise DriverOperationUncertainError("ARB repeat-count readback mismatch")
            if terminate_readback != bool(terminate_last):
                raise DriverOperationUncertainError("ARB termination readback mismatch")
            self._assert_arb_function(quantity, "CDW")
            self._assert_source_modes(quantity, "ARB")
            return ConstantDwellArbConfiguration(
                channel=self.channel,
                quantity=quantity,
                levels=level_readback,
                dwell_s=dwell_readback,
                repeat_count=repeat,
                terminate_last=bool(terminate_last),
                minimum_level=low,
                maximum_level=high,
            )

        return self._run_configuration("arb", program, verify)

    def configure_voltage_constant_dwell_arb(
        self,
        levels_v: Sequence[float],
        dwell_s: float,
        *,
        maximum_voltage_v: float,
        minimum_voltage_v: float = 0.0,
        repeat_count: RepeatCount = 1,
        terminate_last: bool = False,
        maximum_duration_s: float = 60.0,
    ) -> ConstantDwellArbConfiguration:
        """Configure a constant-dwell voltage ARB (up to 65,535 points, one shared dwell)."""
        return self._configure_constant_dwell(
            "voltage",
            levels_v,
            dwell_s,
            minimum=minimum_voltage_v,
            maximum=maximum_voltage_v,
            repeat_count=repeat_count,
            terminate_last=terminate_last,
            maximum_duration_s=maximum_duration_s,
        )

    def configure_current_constant_dwell_arb(
        self,
        levels_a: Sequence[float],
        dwell_s: float,
        *,
        maximum_current_a: float,
        minimum_current_a: float = 0.0,
        repeat_count: RepeatCount = 1,
        terminate_last: bool = False,
        maximum_duration_s: float = 60.0,
    ) -> ConstantDwellArbConfiguration:
        """Configure a constant-dwell current ARB (up to 65,535 points, one shared dwell)."""
        return self._configure_constant_dwell(
            "current",
            levels_a,
            dwell_s,
            minimum=minimum_current_a,
            maximum=maximum_current_a,
            repeat_count=repeat_count,
            terminate_last=terminate_last,
            maximum_duration_s=maximum_duration_s,
        )

    # -- ARB sequences -------------------------------------------------------------

    def _detect_sequence_step_base(self, *, reset: bool) -> int:
        """Resolve the manual's conflicting 0-based/1-based step documentation.

        The user's-guide example and append rule use step 0, while the
        programmer's-reference parameter table says 1..100.  Probe the
        reset-created first step while the output is OFF, cache the result for
        this session, and fail closed on anything other than the expected
        ``-222`` out-of-range response from the unsupported candidate.
        """
        if reset:
            self._write(f"ARB:SEQ:RES {self._chan}")
            self._check_transient_errors("ARB sequence reset")
        if self._sequence_step_base is not None:
            return self._sequence_step_base

        def probe(index: int) -> tuple[str, tuple[ScpiErrorRecord, ...]]:
            response = self._query(f"ARB:SEQ:STEP:FUNC:SHAP? {index},{self._chan}")
            return response, tuple(self._driver.drain_errors())

        zero_response, zero_errors = probe(0)
        if not zero_errors:
            self._parse_shape_keyword(zero_response)
            self._sequence_step_base = 0
            return 0
        if any(error.code != -222 for error in zero_errors):
            raise DriverOperationUncertainError(
                f"could not determine ARB sequence step numbering: {zero_errors!r}"
            )

        one_response, one_errors = probe(1)
        if one_errors:
            raise DriverOperationUncertainError(
                "neither zero-based nor one-based ARB sequence step numbering was accepted: "
                f"step 0={zero_errors!r}, step 1={one_errors!r}"
            )
        self._parse_shape_keyword(one_response)
        self._sequence_step_base = 1
        return 1

    def _read_sequence_steps(
        self, quantity: TransientQuantity, *, step_base: int
    ) -> tuple[ArbSequenceStep, ...]:
        length = self._parse_integer(
            self._query(f"ARB:SEQ:LEN? {self._chan}"), "ARB sequence length"
        )
        if not 1 <= length <= self._MAXIMUM_SEQUENCE_STEPS:
            raise DriverMalformedResponseError(f"invalid ARB sequence length readback: {length}")
        steps = []
        for logical_index in range(length):
            index = logical_index + step_base
            shape = self._parse_shape_keyword(
                self._query(f"ARB:SEQ:STEP:FUNC:SHAP? {index},{self._chan}")
            )
            pacing_raw = self._query(f"ARB:SEQ:STEP:PAC? {index},{self._chan}").strip().upper()
            if pacing_raw.startswith("DWEL"):
                pacing: Literal["dwell", "trigger"] = "dwell"
            elif pacing_raw.startswith("TRIG"):
                pacing = "trigger"
            else:
                raise DriverMalformedResponseError(f"malformed sequence pacing readback: {pacing_raw!r}")
            repeat = self._parse_repeat(
                self._query(f"ARB:SEQ:STEP:COUN? {index},{self._chan}"),
                f"ARB sequence step {logical_index} repeat count",
            )
            waveform = self._read_shape(quantity, shape, step=index)
            try:
                steps.append(ArbSequenceStep(waveform, repeat, pacing))
            except DriverArgumentValueError as exc:
                raise DriverMalformedResponseError(
                    f"invalid ARB sequence step {logical_index} readback: {exc}"
                ) from exc
        return tuple(steps)

    def _sequence_one_pass_s(self, steps: Sequence[ArbSequenceStep]) -> float:
        # An 'infinite' trigger-paced step is counted once for the bound.
        return sum(
            step.waveform.duration_s * (1 if step.repeat_count == "infinite" else step.repeat_count)
            for step in steps
        )

    def _validate_sequence(
        self,
        quantity: TransientQuantity,
        steps: Sequence[ArbSequenceStep],
        low: float,
        high: float,
    ) -> tuple[ArbSequenceStep, ...]:
        if isinstance(steps, (str, bytes)) or not isinstance(steps, Sequence):
            raise DriverArgumentValueError("steps must be a sequence of ArbSequenceStep")
        result = tuple(steps)
        if not 1 <= len(result) <= self._MAXIMUM_SEQUENCE_STEPS:
            raise DriverArgumentValueError(
                f"an ARB sequence has 1..{self._MAXIMUM_SEQUENCE_STEPS} steps"
            )
        for index, step in enumerate(result):
            if not isinstance(step, ArbSequenceStep):
                raise DriverArgumentValueError(f"steps[{index}] must be an ArbSequenceStep")
            self._validate_waveform(quantity, step.waveform, low, high, f"steps[{index}]")
            if step.pacing == "trigger":
                raise DriverArgumentValueError(
                    "trigger-paced ARB sequences require an external finite trigger schedule; "
                    "the normal completion API accepts dwell pacing only"
                )
        minimum_points = sum(
            sequence_waveform_points(step.waveform, SEQUENCE_MINIMUM_QUALITY)
            for step in result
        )
        if minimum_points > SEQUENCE_MAXIMUM_POINTS:
            raise DriverArgumentValueError(
                f"ARB sequence requires at least {minimum_points} generated points at quality "
                f"{SEQUENCE_MINIMUM_QUALITY}; maximum is {SEQUENCE_MAXIMUM_POINTS}"
            )
        return result

    def _assert_sequence_readback(
        self,
        quantity: TransientQuantity,
        expected: Sequence[ArbSequenceStep],
        actual: Sequence[ArbSequenceStep],
    ) -> None:
        if len(expected) != len(actual):
            raise DriverOperationUncertainError("ARB sequence length readback mismatch")
        for index, (wanted, got) in enumerate(zip(expected, actual, strict=True)):
            name = f"ARB sequence step {index}"
            if wanted.pacing != got.pacing:
                raise DriverOperationUncertainError(f"{name} pacing readback mismatch")
            if wanted.repeat_count != got.repeat_count:
                raise DriverOperationUncertainError(f"{name} repeat-count readback mismatch")
            self._assert_shape_readback(wanted.waveform, got.waveform, name)

    def _sequence_configuration(
        self,
        quantity: TransientQuantity,
        steps: tuple[ArbSequenceStep, ...],
        low: float,
        high: float,
        duration_limit: float,
    ) -> ArbSequenceConfiguration:
        """Read back sequence-wide settings and check the envelope of ``steps``."""
        for index, step in enumerate(steps):
            self._require_readback_within(
                quantity, step.waveform.level_bounds, low, high, f"ARB sequence step {index}"
            )
            maximum_time = (
                self._maximum_dwell_s
                if isinstance(step.waveform, ArbUserDefined)
                else PARAMETRIC_MAXIMUM_TIME_S
            )
            if any(value > maximum_time for value in step.waveform.times_s):
                raise DriverOperationUncertainError(
                    f"ARB sequence step {index} time parameters exceed {maximum_time:g} s"
                )
        quality = self._parse_integer(
            self._query(f"ARB:SEQ:QUAL? {self._chan}"), "ARB sequence quality"
        )
        if not SEQUENCE_MINIMUM_QUALITY <= quality <= SEQUENCE_MAXIMUM_QUALITY:
            raise DriverOperationUncertainError(
                f"ARB sequence quality {quality} is outside "
                f"{SEQUENCE_MINIMUM_QUALITY}..{SEQUENCE_MAXIMUM_QUALITY}"
            )
        generated_points = sum(
            sequence_waveform_points(step.waveform, quality) for step in steps
        )
        if generated_points > SEQUENCE_MAXIMUM_POINTS:
            raise DriverOperationUncertainError(
                f"ARB sequence readback requires {generated_points} generated points; "
                f"maximum is {SEQUENCE_MAXIMUM_POINTS}"
            )
        count = self._parse_repeat(
            self._query(f"ARB:SEQ:COUN? {self._chan}"), "ARB sequence count"
        )
        terminate = self._parse_boolean(
            self._query(f"ARB:SEQ:TERM:LAST? {self._chan}"), "ARB sequence termination"
        )
        self._require_quantized_duration(
            self._sequence_one_pass_s(steps), count, duration_limit, "ARB sequence"
        )
        return ArbSequenceConfiguration(
            channel=self.channel,
            quantity=quantity,
            steps=steps,
            sequence_count=count,
            terminate_last=terminate,
            minimum_level=low,
            maximum_level=high,
            quality=quality,
            generated_points=generated_points,
        )

    def _configure_sequence(
        self,
        quantity: TransientQuantity,
        steps: Sequence[ArbSequenceStep],
        *,
        minimum: float,
        maximum: float,
        sequence_count: RepeatCount,
        terminate_last: bool,
        maximum_duration_s: float,
    ) -> ArbSequenceConfiguration:
        self._require_arb_support(quantity)
        low, high = self._bounds(quantity, minimum, maximum)
        requested = self._validate_sequence(quantity, steps, low, high)
        count = normalize_repeat_count(sequence_count, 4096, "sequence_count")
        if count == "infinite":
            raise DriverArgumentValueError(
                "sequence_count must be finite for the normal completion API"
            )
        duration_limit = self._require_duration(
            self._sequence_one_pass_s(requested), count, maximum_duration_s
        )
        self._check_instrument_envelope(quantity, low, high)
        scpi = self._QUANTITY_SCPI[quantity]
        step_base: int | None = None

        def program() -> None:
            nonlocal step_base
            self._write(f"ARB:FUNC:TYPE {scpi},{self._chan}")
            self._write(f"ARB:FUNC:SHAP SEQ,{self._chan}")
            step_base = self._detect_sequence_step_base(reset=True)
            for logical_index, step in enumerate(requested):
                index = logical_index + step_base
                # Steps are created sequentially.  ARB:SEQ:RESet creates the
                # first step; subsequent step numbers append to the sequence.
                self._write(f"ARB:SEQ:STEP:FUNC:SHAP {step.waveform.shape},{index},{self._chan}")
                pacing = "TRIG" if step.pacing == "trigger" else "DWEL"
                self._write(f"ARB:SEQ:STEP:PAC {pacing},{index},{self._chan}")
                self._write(
                    f"ARB:SEQ:STEP:COUN {repeat_scpi(step.repeat_count)},{index},{self._chan}"
                )
                self._write_shape(quantity, step.waveform, step=index)
            self._write(f"ARB:SEQ:COUN {repeat_scpi(count)},{self._chan}")
            self._write(f"ARB:SEQ:TERM:LAST {format_bool(terminate_last)},{self._chan}")
            self._write(f"{scpi}:MODE ARB,{self._chan}")
            self._check_transient_errors(f"{quantity} ARB sequence")

        def verify() -> ArbSequenceConfiguration:
            if step_base is None:
                raise DriverOperationUncertainError("ARB sequence step numbering was not resolved")
            readback = self._read_sequence_steps(quantity, step_base=step_base)
            self._assert_sequence_readback(quantity, requested, readback)
            config = self._sequence_configuration(quantity, readback, low, high, duration_limit)
            if config.sequence_count != count:
                raise DriverOperationUncertainError("ARB sequence count readback mismatch")
            if config.terminate_last != bool(terminate_last):
                raise DriverOperationUncertainError("ARB sequence termination readback mismatch")
            self._assert_arb_function(quantity, "SEQ")
            self._assert_source_modes(quantity, "ARB")
            return config

        return self._run_configuration("arb", program, verify)

    def configure_voltage_arb_sequence(
        self,
        steps: Sequence[ArbSequenceStep],
        *,
        maximum_voltage_v: float,
        minimum_voltage_v: float = 0.0,
        sequence_count: RepeatCount = 1,
        terminate_last: bool = False,
        maximum_duration_s: float = 60.0,
    ) -> ArbSequenceConfiguration:
        """Configure a finite, dwell-paced voltage ARB sequence of up to 100 shapes."""
        return self._configure_sequence(
            "voltage",
            steps,
            minimum=minimum_voltage_v,
            maximum=maximum_voltage_v,
            sequence_count=sequence_count,
            terminate_last=terminate_last,
            maximum_duration_s=maximum_duration_s,
        )

    def configure_current_arb_sequence(
        self,
        steps: Sequence[ArbSequenceStep],
        *,
        maximum_current_a: float,
        minimum_current_a: float = 0.0,
        sequence_count: RepeatCount = 1,
        terminate_last: bool = False,
        maximum_duration_s: float = 60.0,
    ) -> ArbSequenceConfiguration:
        """Configure a current ARB sequence of up to 100 shapes; levels are amperes."""
        return self._configure_sequence(
            "current",
            steps,
            minimum=minimum_current_a,
            maximum=maximum_current_a,
            sequence_count=sequence_count,
            terminate_last=terminate_last,
            maximum_duration_s=maximum_duration_s,
        )

    # -- ARB sequence files (MMEMory) -----------------------------------------------

    @staticmethod
    def _sequence_filename(filename: str) -> str:
        if not isinstance(filename, str):
            raise DriverArgumentValueError("filename must be a string")
        value = filename.strip()
        if not value or any(char in value for char in "\"'\r\n;"):
            raise DriverArgumentValueError("filename must be non-empty without quotes, ';' or newlines")
        lowered = value.lower()
        if not lowered.startswith(("internal:", "external:", "int:", "ext:")):
            raise DriverArgumentValueError(
                r"filename must be a full instrument path such as 'internal:\seq\dip.seq'"
            )
        if not lowered.endswith(".seq"):
            raise DriverArgumentValueError("ARB sequence files use the .seq extension")
        return value

    def store_arb_sequence(self, filename: str) -> None:
        """Save this channel's ARB sequence to an instrument ``.seq`` file."""
        self._require_arb_support("voltage")
        path = self._sequence_filename(filename)
        with self._transient_lock:
            shape = self._parse_shape_keyword(self._query(f"ARB:FUNC:SHAP? {self._chan}"))
            if shape != "SEQ":
                raise DriverPreconditionError(
                    "the channel ARB function is not a sequence; configure one first"
                )
            self._write(f'MMEM:STOR:ARB:SEQ "{path}",{self._chan}')
            self._check_transient_errors("ARB sequence store")

    def _load_sequence(
        self,
        quantity: TransientQuantity,
        filename: str,
        *,
        minimum: float,
        maximum: float,
        maximum_duration_s: float,
        allow_infinite: bool,
    ) -> ArbSequenceConfiguration:
        self._require_arb_support(quantity)
        path = self._sequence_filename(filename)
        low, high = self._bounds(quantity, minimum, maximum)
        duration_limit = self._finite(maximum_duration_s, "maximum_duration_s")
        if duration_limit <= 0:
            raise DriverArgumentValueError("maximum_duration_s must be > 0")
        self._check_instrument_envelope(quantity, low, high)
        scpi = self._QUANTITY_SCPI[quantity]

        def program() -> None:
            self._write(f'MMEM:LOAD:ARB:SEQ "{path}",{self._chan}')
            self._check_transient_errors("ARB sequence load")

        def verify() -> ArbSequenceConfiguration:
            # The file's content is unknown until read back: check its type
            # and every step against the approved envelope before ARB mode.
            function_readback = self._query(f"ARB:FUNC:TYPE? {self._chan}").strip().upper()
            if not function_readback.startswith(scpi):
                raise DriverPreconditionError(
                    f"{path} holds a {function_readback or 'unknown'} sequence, not {quantity}"
                )
            self._write(f"ARB:FUNC:SHAP SEQ,{self._chan}")
            step_base = self._detect_sequence_step_base(reset=False)
            readback = self._read_sequence_steps(quantity, step_base=step_base)
            try:
                config = self._sequence_configuration(quantity, readback, low, high, duration_limit)
            except DriverOperationUncertainError as exc:
                raise DriverPreconditionError(f"loaded sequence {path} rejected: {exc}") from exc
            if not allow_infinite and (
                config.sequence_count == "infinite"
                or any(step.repeat_count == "infinite" for step in config.steps)
            ):
                raise DriverPreconditionError(
                    f"loaded sequence {path} repeats infinitely; pass allow_infinite=True to accept it"
                )
            self._write(f"{scpi}:MODE ARB,{self._chan}")
            self._check_transient_errors(f"{quantity} ARB sequence")
            self._assert_arb_function(quantity, "SEQ")
            self._assert_source_modes(quantity, "ARB")
            return config

        return self._run_configuration("arb", program, verify)

    def load_voltage_arb_sequence(
        self,
        filename: str,
        *,
        maximum_voltage_v: float,
        minimum_voltage_v: float = 0.0,
        maximum_duration_s: float = 60.0,
        allow_infinite: bool = False,
    ) -> ArbSequenceConfiguration:
        """Load a ``.seq`` file and accept it only if it fits the voltage envelope."""
        return self._load_sequence(
            "voltage",
            filename,
            minimum=minimum_voltage_v,
            maximum=maximum_voltage_v,
            maximum_duration_s=maximum_duration_s,
            allow_infinite=allow_infinite,
        )

    def load_current_arb_sequence(
        self,
        filename: str,
        *,
        maximum_current_a: float,
        minimum_current_a: float = 0.0,
        maximum_duration_s: float = 60.0,
        allow_infinite: bool = False,
    ) -> ArbSequenceConfiguration:
        """Load a ``.seq`` file and accept it only if it fits the current envelope."""
        return self._load_sequence(
            "current",
            filename,
            minimum=minimum_current_a,
            maximum=maximum_current_a,
            maximum_duration_s=maximum_duration_s,
            allow_infinite=allow_infinite,
        )

    # -- triggered step (VOLT:MODE STEP / CURR:MODE STEP) ---------------------------

    def _configure_step(
        self,
        quantity: TransientQuantity,
        triggered_level: float,
        *,
        minimum: float,
        maximum: float,
        trigger_output: bool,
    ) -> StepTransientConfiguration:
        self._require_transient_support(quantity)
        low, high = self._bounds(quantity, minimum, maximum)
        level = self._finite(triggered_level, f"triggered_{quantity}")
        self._require_within(quantity, (level,), low, high)
        self._check_instrument_envelope(quantity, low, high)
        scpi = self._QUANTITY_SCPI[quantity]

        def program() -> None:
            self._write(f"{scpi}:TRIG {format_float(level)},{self._chan}")
            self._write(f"STEP:TOUT {format_bool(trigger_output)},{self._chan}")
            self._write(f"{scpi}:MODE STEP,{self._chan}")
            self._check_transient_errors(f"{quantity} step")

        def verify() -> StepTransientConfiguration:
            level_readback = self._parse_float(
                self._query(f"{scpi}:TRIG? {self._chan}"), f"triggered {quantity}"
            )
            self._assert_level_readback(level, level_readback, f"triggered {quantity}")
            self._require_readback_within(quantity, (level_readback,), low, high, "triggered")
            output_readback = self._parse_boolean(
                self._query(f"STEP:TOUT? {self._chan}"), "step trigger output"
            )
            if output_readback != bool(trigger_output):
                raise DriverOperationUncertainError("step trigger-output readback mismatch")
            self._assert_source_modes(quantity, "STEP")
            return StepTransientConfiguration(
                channel=self.channel,
                quantity=quantity,
                triggered_level=level_readback,
                trigger_output=bool(trigger_output),
                minimum_level=low,
                maximum_level=high,
            )

        return self._run_configuration("step", program, verify)

    def configure_voltage_step(
        self,
        triggered_voltage_v: float,
        *,
        maximum_voltage_v: float,
        minimum_voltage_v: float = 0.0,
        trigger_output: bool = False,
    ) -> StepTransientConfiguration:
        """Program a triggered voltage step; the output stays at the new level.

        ``trigger_output`` emits a trigger on the step (``STEP:TOUT``).
        """
        return self._configure_step(
            "voltage",
            triggered_voltage_v,
            minimum=minimum_voltage_v,
            maximum=maximum_voltage_v,
            trigger_output=trigger_output,
        )

    def configure_current_step(
        self,
        triggered_current_a: float,
        *,
        maximum_current_a: float,
        minimum_current_a: float = 0.0,
        trigger_output: bool = False,
    ) -> StepTransientConfiguration:
        """Program a triggered current step; the output stays at the new level."""
        return self._configure_step(
            "current",
            triggered_current_a,
            minimum=minimum_current_a,
            maximum=maximum_current_a,
            trigger_output=trigger_output,
        )

    # -- transient trigger lifecycle -----------------------------------------------

    def _trigger_source_commands(self, source: str | None = None) -> tuple[str, str]:
        """Return the mode-specific setter/query without accepting raw SCPI."""
        if self._transient_mode == "arb":
            if self.capabilities.arb_trigger_dialect != "channel_scoped":
                raise DriverUnsupportedOperationError(
                    "the installed mainframe/module/firmware has no approved ARB trigger dialect"
                )
            prefix = "TRIG:ARB:SOUR"
        else:
            prefix = "TRIG:TRAN:SOUR"
        setter = "" if source is None else f"{prefix} {source},{self._chan}"
        return setter, f"{prefix}? {self._chan}"

    def configure_voltage_transient_trigger(
        self, source: Literal["bus", "external"] = "bus"
    ) -> None:
        if source not in {"bus", "external"}:
            raise DriverArgumentValueError("source must be 'bus' or 'external'")
        with self._transient_lock:
            if self._transient_state in {"armed", "ready", "triggered"}:
                raise DriverPreconditionError(
                    "transient trigger source cannot change while a profile is armed"
                )
            expected = "BUS" if source == "bus" else "EXT"
            setter, query = self._trigger_source_commands(expected)
            self._write(setter)
            self._driver.check_errors()
            readback = self._query(query).strip().upper()
            if not readback.startswith(expected):
                raise DriverOperationUncertainError("transient trigger-source readback mismatch")
            self._transient_trigger_source = source

    def _read_transient_trigger_source(self) -> Literal["bus", "external"]:
        # The instrument, not the cached default, decides what INIT:TRAN waits for.
        _, query = self._trigger_source_commands()
        readback = self._query(query).strip().upper()
        source: Literal["bus", "external"]
        if readback.startswith("BUS"):
            source = "bus"
        elif readback.startswith("EXT"):
            source = "external"
        else:
            raise DriverPreconditionError(
                f"unsupported transient trigger source {readback!r}; "
                "call configure_voltage_transient_trigger() first"
            )
        self._transient_trigger_source = source
        return source

    @staticmethod
    def _needs_external_triggers(config: SourceTransientConfiguration) -> str | None:
        """Why one software trigger cannot run ``config``, or ``None``."""
        # Each step needs its own trigger and triggers during a dwell are
        # ignored, so one software trigger cannot run the profile.
        if (
            isinstance(config, (VoltageListConfiguration, CurrentListConfiguration))
            and config.step_mode == "once"
        ):
            return "LIST step_mode 'once' requires the 'external' transient trigger source"
        if isinstance(config, ArbSequenceConfiguration) and config.trigger_paced:
            return "trigger-paced ARB sequence steps require the 'external' transient trigger source"
        return None

    def arm_voltage_transient(self, *, continuous: bool = False) -> VoltageTransientToken:
        """Arm the configured source profile (voltage or current).

        ``continuous=True`` uses ``INIT:CONT:TRAN ON``: the trigger system
        re-arms after every run, so one token can be triggered repeatedly
        until :meth:`abort_voltage_transient`.
        """
        with self._transient_lock:
            config = self._transient_config
            if config is None or self._transient_state not in {
                "configured", "complete", "aborted"
            }:
                raise DriverPreconditionError("a verified source LIST/ARB/STEP must be configured first")
            if not self.get_output():
                raise DriverPreconditionError("source output must be ON before arming a voltage transient")
            source = self._read_transient_trigger_source()
            reason = self._needs_external_triggers(config)
            if reason is not None and source == "bus":
                raise DriverPreconditionError(reason)
            self._invalidate_transient_tokens()
            token = VoltageTransientToken(self.channel, self._transient_generation)
            self._transient_state = "armed"
            self._transient_triggered_at = None
            try:
                if source == "external":
                    self._clear_transient_event_latch()
                if continuous:
                    self._transient_continuous = True
                    self._write(f"INIT:CONT:TRAN ON,{self._chan}")
                else:
                    self._write(f"INIT:TRAN {self._chan}")
                self._driver.check_errors()
            except BaseException as primary:
                self._cleanup_failed_voltage_transient(primary)
                raise
            return token

    def _read_operation_event(self) -> int:
        # Reading the Operation Event register clears it.
        return self._parse_integer(
            self._query(f"STAT:OPER:EVEN? {self._chan}"), "operation event"
        )

    def _clear_transient_event_latch(self) -> None:
        """Latch TRAN_active rising edges from a clean event register.

        An external edge can start and finish the profile between two
        condition polls; the latched edge then tells "already ran" apart
        from "not initiated yet", which both read as idle.
        """
        ptr = self._parse_integer(
            self._query(f"STAT:OPER:PTR? {self._chan}"), "operation PTR filter"
        )
        if not ptr & self._TRANSIENT_ACTIVE:
            self._write(f"STAT:OPER:PTR {ptr | self._TRANSIENT_ACTIVE},{self._chan}")
        self._read_operation_event()

    def _require_transient_token(self, token: VoltageTransientToken) -> None:
        if token.channel != self.channel or token.generation != self._transient_generation:
            raise DriverPreconditionError("stale or foreign voltage-transient token")

    def _require_not_superseded(self, token: VoltageTransientToken) -> None:
        if token.generation != self._transient_generation:
            raise DriverOperationUncertainError(
                "voltage transient was aborted or reconfigured while waiting",
                details={
                    "voltage_transient_generation": token.generation,
                    "current_generation": self._transient_generation,
                },
            )

    def _token_status(self, token: VoltageTransientToken) -> VoltageTransientStatus:
        self._require_not_superseded(token)
        return self.get_voltage_transient_status()

    def get_voltage_transient_status(self) -> VoltageTransientStatus:
        raw = self._parse_integer(
            self._query(f"STAT:OPER:COND? {self._chan}"), "operation status"
        )
        config = self._transient_config
        return VoltageTransientStatus(
            channel=self.channel,
            mode=self._transient_mode,  # type: ignore[arg-type]
            state=self._transient_state,  # type: ignore[arg-type]
            generation=self._transient_generation,
            trigger_source=self._transient_trigger_source,  # type: ignore[arg-type]
            operation_status=raw,
            waiting_for_trigger=bool(raw & self._WTG_TRANSIENT),
            transient_active=bool(raw & self._TRANSIENT_ACTIVE),
            quantity=config.quantity if config is not None else "voltage",
            continuous=self._transient_continuous,
        )

    def _poll_transient(
        self,
        token: VoltageTransientToken,
        predicate: Callable[[], bool],
        *,
        operation: str,
        description: str,
        timeout_s: float,
        poll_interval_s: float,
    ) -> None:
        try:
            with translated(operation=operation):
                poll_until(
                    predicate,
                    timeout_s=timeout_s,
                    interval_s=poll_interval_s,
                    description=description,
                )
        except BaseException as primary:
            # A superseded generation was already aborted by someone else;
            # aborting again could cancel a newer arm.
            if token.generation == self._transient_generation:
                self._cleanup_failed_voltage_transient(primary)
            raise

    def wait_voltage_transient_ready(
        self,
        token: VoltageTransientToken,
        *,
        timeout_s: float = 5.0,
        poll_interval_s: float = 0.01,
    ) -> VoltageTransientStatus:
        """Wait until the armed transient accepts a trigger.

        With the ``'external'`` source the trigger may land between polls, so
        TRAN_active without WTG (running) also ends the wait, as does an idle
        system whose TRAN_active edge was latched after ``INIT:TRAN``
        (already finished); the state then advances to ``'triggered'``.  An
        idle system without that latched edge has not initiated yet and is
        never mistaken for a finished run.
        """
        with self._transient_lock:
            self._require_transient_token(token)
            if self._transient_state != "armed":
                raise DriverPreconditionError("voltage transient is not armed")
            external = self._transient_trigger_source == "external"
        last: VoltageTransientStatus | None = None

        def predicate() -> bool:
            nonlocal last
            last = self._token_status(token)
            if last.waiting_for_trigger or not external:
                return last.waiting_for_trigger
            if last.transient_active:
                return True
            return bool(self._read_operation_event() & self._TRANSIENT_ACTIVE)

        self._poll_transient(
            token,
            predicate,
            operation="wait_voltage_transient_ready",
            description=f"channel {self.channel} transient trigger ready",
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
        )
        with self._transient_lock:
            self._require_not_superseded(token)
            waiting = last is not None and last.waiting_for_trigger
            self._transient_state = "ready" if waiting else "triggered"
        return self.get_voltage_transient_status()

    def trigger_voltage_transient(self, token: VoltageTransientToken) -> None:
        with self._transient_lock:
            self._require_transient_token(token)
            if self._transient_state != "ready":
                raise DriverPreconditionError(
                    "wait_voltage_transient_ready() must succeed before triggering"
                )
            if self._transient_trigger_source != "bus":
                raise DriverPreconditionError("software trigger requires trigger source 'bus'")
            self._write(f"TRIG:TRAN {self._chan}")
            self._driver.check_errors()
            self._transient_triggered_at = monotonic()
            self._transient_state = "triggered"

    def _default_complete_timeout_s(self) -> float:
        config = self._transient_config
        duration = config.duration_s if config is not None else 0.0
        return (
            duration * (1.0 + self._COMPLETE_TIMEOUT_MARGIN_FRACTION)
            + self._COMPLETE_TIMEOUT_MARGIN_S
        )

    def _verify_transient_outcome(self) -> None:
        """Idle bits also follow a protection trip or output OFF; reject those."""
        protection = self.get_protection_status()
        output_on = self.get_output()
        if protection.active or not output_on:
            reason = (
                f"protection fault active (STAT:QUES:COND {protection.raw_status})"
                if protection.active
                else "output is OFF"
            )
            raise DriverOperationUncertainError(
                f"voltage transient ended without a clean completion: {reason}",
                details={
                    "protection_raw_status": protection.raw_status,
                    "output_on": output_on,
                },
            )

    def wait_voltage_transient_complete(
        self,
        token: VoltageTransientToken,
        *,
        timeout_s: float | None = None,
        poll_interval_s: float = 0.01,
    ) -> VoltageTransientStatus:
        """Wait for a clean completion of the armed generation.

        ``timeout_s=None`` derives the bound from the configured profile
        duration; pass an explicit value for externally paced step-once lists
        and trigger-paced sequences.  A continuously armed BUS profile
        completes when it is waiting for the next trigger again and its
        duration has elapsed; its state then returns to ``'ready'``.
        """
        with self._transient_lock:
            self._require_transient_token(token)
            config = self._transient_config
            continuous = self._transient_continuous
            if self._transient_state not in {"ready", "triggered"}:
                raise DriverPreconditionError("voltage transient is not armed/triggered")
            if config is not None and math.isinf(config.duration_s):
                raise DriverPreconditionError(
                    "an infinitely repeating profile never completes; "
                    "stop it with abort_voltage_transient()"
                )
            if continuous and (
                self._transient_trigger_source != "bus" or self._transient_state != "triggered"
            ):
                raise DriverPreconditionError(
                    "a continuous profile only reports completion of a software-triggered run"
                )
            if timeout_s is None:
                timeout_s = self._default_complete_timeout_s()
            duration = config.duration_s if config is not None else 0.0
            triggered_at = self._transient_triggered_at

        def predicate() -> bool:
            status = self._token_status(token)
            if continuous:
                # The system re-arms after each run, so "complete" is "waiting
                # again" once the run could have finished.
                return (
                    status.waiting_for_trigger
                    and triggered_at is not None
                    and monotonic() - triggered_at >= duration
                )
            return not status.waiting_for_trigger and not status.transient_active

        self._poll_transient(
            token,
            predicate,
            operation="wait_voltage_transient_complete",
            description=f"channel {self.channel} voltage transient complete",
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
        )
        with self._transient_lock:
            self._require_not_superseded(token)
            try:
                self._driver.check_errors()
                self._verify_transient_outcome()
            except BaseException:
                self._transient_state = "unknown"
                raise
            self._transient_state = "ready" if continuous else "complete"
        return self.get_voltage_transient_status()

    def abort_voltage_transient(self) -> None:
        with self._transient_lock:
            self._invalidate_transient_tokens()
            try:
                self._stop_transient_system()
                self._driver.check_errors()
            except BaseException:
                self._transient_state = "unknown"
                raise
            self._transient_state = "aborted"

    def _emergency_abort_voltage_transient(self) -> None:
        """Send only ``INIT:CONT:TRAN OFF`` and ``ABOR:TRAN`` for shutdown.

        They are the only transient commands a digitizer lease held by
        another thread lets through, and this skips the lock so a long
        configure call cannot delay shutdown.
        """
        self._invalidate_transient_tokens()
        try:
            try:
                # ABOR:TRAN alone re-initiates a continuous trigger system.
                self._write(f"INIT:CONT:TRAN OFF,{self._chan}")
            finally:
                self._write(f"ABOR:TRAN {self._chan}")
        except BaseException:
            self._transient_state = "unknown"
            raise
        self._transient_continuous = False
        self._transient_state = "aborted"


class SMUChannel(PowerSupplyChannel):
    """SMU channel API for N678xA-style modules."""

    def _require_smu(self) -> None:
        if self.capabilities.module_type != "smu":
            raise DriverUnsupportedOperationError(f"channel {self.channel} is not an SMU")

    def set_smu_mode(self, mode: Literal["voltage", "current"]) -> None:
        self._require_smu()
        if not self.capabilities.supports_smu_priority_mode:
            raise DriverUnsupportedOperationError(
                f"{self.capabilities.model} does not support selectable voltage/current priority"
            )
        scpi_mode = "VOLT" if mode == "voltage" else "CURR"
        self._write(f"FUNC {scpi_mode},{self._chan}")

    def get_smu_mode(self) -> Literal["voltage", "current"]:
        self._require_smu()
        resp = self._query(f"FUNC? {self._chan}").upper()
        return "current" if "CURR" in resp else "voltage"

    def set_current_setpoint(self, value: float, *, current_range: float | str | None = None) -> None:
        self.set_current_limit(value, current_range=current_range)

    def get_current_setpoint(self) -> float:
        return self.get_current_limit()

    def set_voltage_limit(self, value: float) -> None:
        self._write(f"VOLT:LIM {format_float(value)},{self._chan}")

    def get_voltage_limit(self) -> float:
        return float(self._query(f"VOLT:LIM? {self._chan}"))

    def configure_voltage_priority(
        self,
        voltage: float,
        current_limit: float,
        *,
        voltage_limit: float | None = None,
        output: bool = False,
        verify: bool = True,
    ) -> None:
        self.set_smu_mode("voltage")
        self.set_voltage_setpoint(voltage)
        self.set_current_limit(current_limit)
        if voltage_limit is not None:
            self.set_voltage_limit(voltage_limit)
        if output:
            self.output_on()
        if verify:
            self._driver.check_errors()

    def configure_current_priority(
        self,
        current: float,
        voltage_limit: float,
        *,
        output: bool = False,
        verify: bool = True,
    ) -> None:
        self.set_smu_mode("current")
        self.set_current_setpoint(current)
        self.set_voltage_limit(voltage_limit)
        if output:
            self.output_on()
        if verify:
            self._driver.check_errors()

    def set_smu_output_off_mode(self, mode: Literal["high_z", "low_z"]) -> None:
        if not self.capabilities.supports_smu_output_off_mode:
            raise DriverUnsupportedOperationError("SMU output-off mode is not supported by this module")
        sim_mode = "LOWZ" if mode == "low_z" else "HIGHZ"
        self._write(f"SIM:SMU:OFFMODE {sim_mode},{self._chan}")

    def get_smu_output_off_mode(self) -> Literal["high_z", "low_z"]:
        if not self.capabilities.supports_smu_output_off_mode:
            raise DriverUnsupportedOperationError("SMU output-off mode is not supported by this module")
        resp = self._query(f"SIM:SMU:OFFMODE? {self._chan}").upper()
        return "low_z" if "LOW" in resp else "high_z"


class ElectronicLoadChannel(BaseChannel):
    """Electronic-load channel API for Keysight N679xA Electronic Load Modules.

    Real commands follow the official Keysight N6705C User's Guide /
    Programmer's Reference (see ``Keysight_documents/`` in this repository):
    priority mode is ``[SOURce:]FUNCtion CURRent|VOLTage|RESistance|POWer``
    (four modes — the N678xA SMU shares the same command but only accepts
    CURRent|VOLTage), the corresponding level is set with the matching
    ``VOLTage``/``CURRent``/``RESistance``/``POWer`` command, and the load's
    input terminals are switched with the ordinary ``OUTP`` command — the
    manual explicitly notes the load's input is referred to as "Output"
    throughout ("Note 1"). There is no separate load-specific input command.

    ``SIM_LOAD``, the simulator-only placeholder used by tests, has no real
    hardware behind it and keeps its own ``SIM:LOAD:*`` command namespace,
    untouched by this real-command path.
    """

    _MODE_TO_SCPI: ClassVar[dict[str, str]] = {"cc": "CURR", "cv": "VOLT", "cr": "RES", "cp": "POW"}
    _SCPI_TO_MODE: ClassVar[dict[str, Literal["cc", "cv", "cr", "cp"]]] = {
        "CURR": "cc",
        "VOLT": "cv",
        "RES": "cr",
        "POW": "cp",
    }
    _TRIGGER_SOURCE_TO_SCPI: ClassVar[dict[str, str]] = {
        "bus": "BUS",
        "voltage": "VOLT",
        "current": "CURR",
        "external": "EXT",
        "transient": "TRAN",
    }

    def __init__(self, driver: _DriverProtocol, channel: int, capabilities: ChannelCapabilities) -> None:
        super().__init__(driver, channel, capabilities)
        self._digitize_function: Literal["voltage", "current"] | None = None
        self._digitize_interval_s: float | None = None
        self._digitize_offset_points: int | None = None
        self._digitize_timing: DigitizeTiming | None = None
        self._digitize_previous_format: str | None = None
        self._acquisition = AcquisitionTracker()

    @property
    def digitize_status(self) -> AcquisitionSnapshot:
        """Conservative driver-observed state of this channel's latest capture."""
        return self._acquisition.snapshot()

    @property
    def digitize_timing(self) -> DigitizeTiming | None:
        """Last instrument-verified sweep configuration, or None."""
        return self._digitize_timing

    def _require_verified_or_sim(self) -> None:
        if self.capabilities.module_type != "electronic_load":
            raise DriverUnsupportedOperationError(f"channel {self.channel} is not an electronic load")
        if self.capabilities.model != "SIM_LOAD" and not self.capabilities.verified_real_load_commands:
            raise DriverUnsupportedOperationError(
                "electronic-load SCPI commands are not verified for this exact module"
            )

    @property
    def _is_sim(self) -> bool:
        return self.capabilities.model == "SIM_LOAD"

    def input_on(self) -> None:
        self.set_input(True)

    def input_off(self) -> None:
        self.set_input(False)

    def set_input(self, enabled: bool) -> None:
        self._require_verified_or_sim()
        if self._is_sim:
            self._write(f"SIM:LOAD:INP {format_bool(enabled)},{self._chan}")
            return
        self._write(f"OUTP {format_bool(enabled)},{self._chan}")

    def get_input(self) -> bool:
        self._require_verified_or_sim()
        if self._is_sim:
            return self._query(f"SIM:LOAD:INP? {self._chan}").strip() not in {"0", "+0"}
        return self._query(f"OUTP? {self._chan}").strip() not in {"0", "+0"}

    def _get_enable_state(self) -> bool | None:
        return self.get_input()

    def _set_enable_state(self, enabled: bool) -> None:
        self.set_input(enabled)

    def set_load_mode(self, mode: Literal["cc", "cv", "cr", "cp"]) -> None:
        self._require_verified_or_sim()
        if self._is_sim:
            self._write(f"SIM:LOAD:MODE {mode.upper()},{self._chan}")
            return
        self._write(f"FUNC {self._MODE_TO_SCPI[mode]},{self._chan}")

    def get_load_mode(self) -> Literal["cc", "cv", "cr", "cp"]:
        self._require_verified_or_sim()
        if self._is_sim:
            return self._query(f"SIM:LOAD:MODE? {self._chan}").strip().lower()  # type: ignore[return-value]
        resp = self._query(f"FUNC? {self._chan}").strip().upper()
        for scpi_mode, mode in self._SCPI_TO_MODE.items():
            if scpi_mode in resp:
                return mode
        raise DriverUnsupportedOperationError(f"unrecognized load priority mode reply: {resp!r}")

    def set_load_current(self, value: float) -> None:
        self._require_verified_or_sim()
        if self._is_sim:
            self._write(f"SIM:LOAD:CURR {format_float(value)},{self._chan}")
            return
        self._write(f"CURR {format_float(value)},{self._chan}")

    def get_load_current(self) -> float:
        self._require_verified_or_sim()
        if self._is_sim:
            return float(self._query(f"SIM:LOAD:CURR? {self._chan}"))
        return float(self._query(f"CURR? {self._chan}"))

    def set_load_voltage(self, value: float) -> None:
        self._require_verified_or_sim()
        if self._is_sim:
            self._write(f"SIM:LOAD:VOLT {format_float(value)},{self._chan}")
            return
        self._write(f"VOLT {format_float(value)},{self._chan}")

    def get_load_voltage(self) -> float:
        self._require_verified_or_sim()
        if self._is_sim:
            return float(self._query(f"SIM:LOAD:VOLT? {self._chan}"))
        return float(self._query(f"VOLT? {self._chan}"))

    def set_load_resistance(self, value: float) -> None:
        self._require_verified_or_sim()
        if self._is_sim:
            self._write(f"SIM:LOAD:RES {format_float(value)},{self._chan}")
            return
        self._write(f"RES {format_float(value)},{self._chan}")

    def get_load_resistance(self) -> float:
        self._require_verified_or_sim()
        if self._is_sim:
            return float(self._query(f"SIM:LOAD:RES? {self._chan}"))
        return float(self._query(f"RES? {self._chan}"))

    def set_load_power(self, value: float) -> None:
        self._require_verified_or_sim()
        if self._is_sim:
            self._write(f"SIM:LOAD:POW {format_float(value)},{self._chan}")
            return
        self._write(f"POW {format_float(value)},{self._chan}")

    def get_load_power(self) -> float:
        self._require_verified_or_sim()
        if self._is_sim:
            return float(self._query(f"SIM:LOAD:POW? {self._chan}"))
        return float(self._query(f"POW? {self._chan}"))

    def set_load_current_limit(self, value: float) -> None:
        """Limit the input current while operating in a non-current-priority mode.

        Real hardware only: ``CURR:LIM`` applies in voltage/resistance/power
        priority mode (manual example: "Optionally, set a current limit value
        of 5A while in voltage priority mode"). Not modeled by SIM_LOAD.
        """
        self._require_verified_or_sim()
        if self._is_sim:
            raise DriverUnsupportedOperationError("current limit is not modeled by the simulator")
        self._write(f"CURR:LIM {format_float(value)},{self._chan}")

    def get_load_current_limit(self) -> float:
        self._require_verified_or_sim()
        if self._is_sim:
            raise DriverUnsupportedOperationError("current limit is not modeled by the simulator")
        return float(self._query(f"CURR:LIM? {self._chan}"))

    def configure_cc(
        self,
        current: float,
        *,
        input_on: bool = False,
        verify: bool = True,
    ) -> None:
        self.set_load_mode("cc")
        self.set_load_current(current)
        if input_on:
            self.input_on()
        if verify:
            self._driver.check_errors()

    def configure_cv(
        self,
        voltage: float,
        *,
        current_limit: float | None = None,
        input_on: bool = False,
        verify: bool = True,
    ) -> None:
        self.set_load_mode("cv")
        self.set_load_voltage(voltage)
        if current_limit is not None:
            self.set_load_current_limit(current_limit)
        if input_on:
            self.input_on()
        if verify:
            self._driver.check_errors()

    def configure_cr(
        self,
        resistance: float,
        *,
        input_on: bool = False,
        verify: bool = True,
    ) -> None:
        self.set_load_mode("cr")
        self.set_load_resistance(resistance)
        if input_on:
            self.input_on()
        if verify:
            self._driver.check_errors()

    def configure_cp(
        self,
        power: float,
        *,
        input_on: bool = False,
        verify: bool = True,
    ) -> None:
        self.set_load_mode("cp")
        self.set_load_power(power)
        if input_on:
            self.input_on()
        if verify:
            self._driver.check_errors()

    # -- Digitized/buffered measurement (SENS:SWEep + TRIG:ACQuire + FETC:ARRay) --
    #
    # Per the N6705C manual's "Digitizing Measurements" chapter (see
    # Keysight_documents/N6791A_manual.pdf.md): the same remote-only command
    # surface the N679xA "Load Model Differences" table marks available
    # ("SCPI command array readback", "SCPI command programmable sample
    # rate"). Unlike the plain load setpoints above, there is no simulated-
    # waveform twin for this — SIM_LOAD raises, the same precedent
    # set_load_current_limit uses for real-hardware-only behavior the
    # simulator cannot meaningfully model.

    def _require_digitizing(self) -> None:
        self._require_verified_or_sim()
        if self._is_sim:
            raise DriverUnsupportedOperationError("digitized measurement is not modeled by the simulator")
        if not self.capabilities.supports_digitizing:
            raise DriverUnsupportedOperationError(
                f"{self.capabilities.model} does not support digitized measurement"
            )

    def configure_digitize(
        self,
        function: Literal["voltage", "current"],
        *,
        points: int,
        interval_s: float,
        offset_points: int = 0,
    ) -> None:
        """Select the measured quantity, sample interval, point count, and trigger offset.

        ``offset_points`` moves the acquisition window relative to the
        trigger: negative values capture pre-trigger samples, positive values
        delay the first sample after the trigger by ``offset_points *
        interval_s``.
        """
        self._require_digitizing()
        if function not in {"voltage", "current"}:
            raise DriverArgumentValueError(
                f"function must be 'voltage' or 'current', got {function!r}"
            )
        if isinstance(points, bool) or not isinstance(points, int):
            raise DriverArgumentValueError(f"points must be an integer, got {points!r}")
        if isinstance(offset_points, bool) or not isinstance(offset_points, int):
            raise DriverArgumentValueError(
                f"offset_points must be an integer, got {offset_points!r}"
            )
        if not math.isfinite(interval_s):
            raise DriverArgumentValueError(f"interval_s must be finite, got {interval_s!r}")
        min_interval = self.capabilities.digitize_min_interval_s
        if min_interval is not None and interval_s < min_interval:
            raise DriverArgumentValueError(
                f"interval_s {interval_s:.12g} is below the {min_interval:.12g} s "
                f"minimum for {self.capabilities.model}"
            )
        if points < 1:
            raise DriverArgumentValueError(f"points must be >= 1, got {points}")
        if points > 512 * 1024:
            raise DriverArgumentValueError(f"points must be <= 524288, got {points}")
        if offset_points < -(points - 1) or offset_points > 2_000_000_000:
            raise DriverArgumentValueError(
                f"offset_points must be within {-points + 1}..2000000000 for {points} points, "
                f"got {offset_points}"
            )
        self._acquisition.require(
            AcquisitionState.IDLE,
            AcquisitionState.CONFIGURED,
            AcquisitionState.COMPLETE,
            AcquisitionState.FETCHED,
            AcquisitionState.ABORTED,
        )
        self._driver._begin_digitize(self.channel)
        scpi_function = "VOLT" if function == "voltage" else "CURR"
        self._digitize_timing = None
        try:
            previous_format = self._query("FORM?").strip().upper()
            if not (previous_format.startswith("ASC") or previous_format.startswith("REAL")):
                raise DriverMalformedResponseError(
                    f"unsupported instrument response format {previous_format!r}"
                )
            self._digitize_previous_format = previous_format
            self._write("FORM ASC")
            if not self._query("FORM?").strip().upper().startswith("ASC"):
                raise DriverOperationUncertainError("instrument did not accept ASCII array format")
            other = "CURR" if function == "voltage" else "VOLT"
            self._write(f"SENS:FUNC:{other} OFF,{self._chan}")
            self._write(f"SENS:FUNC:{scpi_function} ON,{self._chan}")
            self._write(f"SENS:SWE:TINT {format_float(interval_s)},{self._chan}")
            self._write(f"SENS:SWE:POIN {points},{self._chan}")
            # Always write zero too: don't inherit a prior pre-trigger offset.
            self._write(f"SENS:SWE:OFFS:POIN {offset_points},{self._chan}")
            effective = self._read_digitize_interval()
            actual_points = self._read_digitize_integral("SENS:SWE:POIN?")
            actual_offset = self._read_digitize_integral("SENS:SWE:OFFS:POIN?")
            if not math.isfinite(effective) or effective <= 0:
                raise DriverMalformedResponseError(
                    f"invalid instrument-reported digitizing interval {effective!r}"
                )
            if actual_points != points or actual_offset != offset_points:
                raise DriverOperationUncertainError(
                    f"channel {self.channel} digitizing readback mismatch: "
                    f"requested points/offset={points}/{offset_points}, "
                    f"reported={actual_points}/{actual_offset}"
                )
            timing = DigitizeTiming(
                requested_interval_s=interval_s,
                actual_interval_s=effective,
                points=actual_points,
                offset_points=actual_offset,
            )
            self._verify_digitize_shared_memory()
            self._driver.check_errors()
        except Exception:
            # A readback/protocol failure can leave partially modified hardware.
            # Full SCPI error-queue verification/rollback remains PR05.
            self._acquisition.unknown()
            # Retain lease: partially configured instrument must be explicitly
            # aborted before another channel can start a capture.
            raise
        self._acquisition.configure()
        self._digitize_function = function
        self._digitize_interval_s = timing.actual_interval_s
        self._digitize_offset_points = timing.offset_points
        self._digitize_timing = timing

    def _read_digitize_interval(self) -> float:
        """Reject failed/unparseable effective timebases as protocol errors."""
        raw = self._query(f"SENS:SWE:TINT? {self._chan}").strip()
        try:
            return float(raw)
        except ValueError as exc:
            raise DriverMalformedResponseError(
                f"invalid instrument digitizing interval readback {raw!r}"
            ) from exc

    def _read_digitize_integral(self, command: str) -> int:
        """Read an exact integer from NR3/numeric SCPI format; reject truncation."""
        raw = self._query(f"{command} {self._chan}").strip()
        try:
            value = float(raw)
        except ValueError as exc:
            raise DriverMalformedResponseError(f"invalid {command} response {raw!r}") from exc
        if not math.isfinite(value) or not value.is_integer():
            raise DriverMalformedResponseError(f"non-integral {command} response {raw!r}")
        return int(value)

    def _verify_digitize_shared_memory(self) -> None:
        """Read back the mainframe's shared digitizer point/parameter budget."""
        try:
            count = int(self._query("SYST:CHAN:COUN?").strip())
        except (ValueError, OverflowError) as exc:
            raise DriverMalformedResponseError("invalid instrument channel count") from exc
        if not 1 <= count <= 4:
            raise DriverMalformedResponseError("unexpected digitizer mainframe channel count")
        total = 0
        active = 0
        for channel in range(1, count + 1):
            # A non-digitizing channel (e.g. a plain power-supply module) can
            # answer SENS:FUNC:VOLT?/CURR? (ordinary measurement enables) but
            # never replies at all to SENS:SWE:POIN? -- no SCPI error, just a
            # silent hang, the same failure mode already known for FUNC:MODE?
            # on N679xA. Only digitizing-capable channels have a SWEep
            # subsystem or any claim on the shared digitizer memory budget.
            # self.channel is always digitizing-capable here -- _require_digitizing
            # already enforced that -- so it's trusted directly rather than via
            # discovery, which a caller may not have run against this exact object.
            if channel != self.channel and not self._driver._channel_supports_digitizing(channel):
                continue
            selectors = format_channel_list(channel)
            enabled = 0
            for kind in ("VOLT", "CURR"):
                reply = self._query(f"SENS:FUNC:{kind}? {selectors}").strip().upper()
                if reply not in {"0", "1", "+0", "+1", "ON", "OFF"}:
                    raise DriverMalformedResponseError(
                        f"invalid {kind} enable readback for channel {channel}"
                    )
                enabled += int(reply in {"1", "+1", "ON"})
            if enabled:
                samples = self._query(f"SENS:SWE:POIN? {selectors}").strip()
                try:
                    numeric = float(samples)
                except ValueError as exc:
                    raise DriverMalformedResponseError("invalid sweep points readback") from exc
                if not math.isfinite(numeric) or not numeric.is_integer():
                    raise DriverMalformedResponseError("non-integral sweep points readback")
                count_samples = int(numeric)
                if count_samples < 1:
                    raise DriverMalformedResponseError("invalid sweep points <= 0")
                active += enabled
                total += enabled * count_samples
        if total > 512 * 1024:
            raise DriverArgumentValueError(
                f"digitizer requests {total} shared points; mainframe limit is 524288"
            )
        resolution = self._query("SENS:SWE:TINT:RES?").strip().upper()
        if resolution not in {"RES20", "RES40"}:
            raise DriverMalformedResponseError("invalid sweep resolution readback")
        if (resolution == "RES20" and active > 4) or active > 8:
            raise DriverArgumentValueError(
                f"{active} active measurement parameters exceed {resolution} limits"
            )
        if self._digitize_timing is None:
            # Called before committing metadata; verify actual interval directly.
            actual = self._read_digitize_interval()
        else:
            actual = self._digitize_timing.actual_interval_s
        minimum = 40.96e-6 if active > 4 else 20.48e-6 if active > 2 else 10.24e-6
        if actual < minimum * (1 - 1e-7):
            raise DriverOperationUncertainError(
                f"instrument effective interval {actual:.9g} below {minimum:.9g} "
                f"required for {active} parameters"
            )

    def _restore_digitize_format(self) -> None:
        previous = self._digitize_previous_format
        if previous is not None and not previous.startswith("ASC"):
            self._write(f"FORM {previous}")
            confirmed = self._query("FORM?").strip().upper()
            if not confirmed.startswith(previous):
                raise DriverOperationUncertainError(
                    f"could not restore array response format {previous!r}"
                )
        self._digitize_previous_format = None

    def configure_digitize_trigger(
        self,
        source: Literal["bus", "voltage", "current", "external", "transient"],
        *,
        level: float | None = None,
        slope: Literal["pos", "neg"] | None = None,
        source_channel: int | None = None,
    ) -> None:
        """Select what arms the acquisition.

        ``source_channel`` defaults to this channel, i.e. self-triggering —
        the common case of arming on this load's own measured voltage or
        current crossing ``level`` — but ``voltage``/``current``/``transient``
        can reference any other channel's level or transient system instead,
        per the manual's "Select another output's level as the trigger"
        example.
        """
        self._require_digitizing()
        if source not in self._TRIGGER_SOURCE_TO_SCPI:
            raise DriverArgumentValueError(f"unsupported digitize trigger source: {source!r}")
        if slope is not None and slope not in {"pos", "neg"}:
            raise DriverArgumentValueError(f"slope must be 'pos' or 'neg', got {slope!r}")
        if level is not None and not math.isfinite(level):
            raise DriverArgumentValueError(f"trigger level must be finite, got {level!r}")
        if source in {"voltage", "current"} and (level is None or slope is None):
            raise DriverArgumentValueError(
                f"{source} trigger requires both level and slope to avoid stale instrument settings"
            )
        src_chan = source_channel if source_channel is not None else self.channel
        format_channel_list(src_chan)  # validate even when no level/slope command follows
        scpi_source = self._TRIGGER_SOURCE_TO_SCPI[source]
        if source in {"voltage", "current", "transient"}:
            scpi_source = f"{scpi_source}{src_chan}"
        self._acquisition.require(
            AcquisitionState.CONFIGURED, AcquisitionState.COMPLETE, AcquisitionState.FETCHED
        )
        try:
            self._write(f"TRIG:ACQ:SOUR {scpi_source},{self._chan}")
            if source in {"voltage", "current"}:
                level_function = "VOLT" if source == "voltage" else "CURR"
                src_chanlist = format_channel_list(src_chan)
                if level is not None:
                    self._write(f"TRIG:ACQ:{level_function} {format_float(level)},{src_chanlist}")
                if slope is not None:
                    scpi_slope = "POS" if slope == "pos" else "NEG"
                    self._write(f"TRIG:ACQ:{level_function}:SLOP {scpi_slope},{src_chanlist}")
        except Exception:
            self._acquisition.unknown()
            raise
        self._acquisition.configure()

    def arm_digitize(self) -> None:
        """Enable the trigger system to receive the trigger configured by ``configure_digitize_trigger``."""
        self._require_digitizing()
        self._acquisition.require(
            AcquisitionState.CONFIGURED, AcquisitionState.COMPLETE, AcquisitionState.FETCHED
        )
        self._driver._begin_digitize(self.channel)
        try:
            self._write(f"INIT:ACQ {self._chan}")
        except Exception:
            self._acquisition.unknown()
            raise
        self._acquisition.arm()

    @staticmethod
    def _validate_digitize_timeout(timeout_s: float) -> None:
        if (
            isinstance(timeout_s, bool)
            or not isinstance(timeout_s, (int, float))
            or not math.isfinite(timeout_s)
            or timeout_s <= 0
        ):
            raise DriverArgumentValueError(
                f"digitize timeout must be a positive finite number of seconds, got {timeout_s!r}"
            )

    def _cleanup_failed_digitize(self, primary: BaseException) -> None:
        """Preserve the original failure and attach bounded cleanup evidence.

        A failure to communicate with the instrument remains UNKNOWN; it is
        never misreported as a successful abort. Per-command transport timeouts
        remain governed by the connected session's configured I/O timeout.
        """
        if self._acquisition.state is AcquisitionState.ABORTED:
            outcome = "already_aborted"
        else:
            try:
                self.abort_digitize(timeout_s=2.0)
            except Exception as cleanup_error:
                self._acquisition.unknown()
                outcome = f"abort_failed: {type(cleanup_error).__name__}: {cleanup_error}"
            else:
                outcome = "aborted_and_idle_verified"
        if isinstance(primary, DriverError):
            primary.details["acquisition_cleanup"] = outcome
            primary.details["acquisition_generation"] = self._acquisition.generation
        # Python 3.11+ adds exception notes; keep exception class and cause
        # unchanged for Python 3.10 compatibility.
        if hasattr(primary, "add_note"):
            primary.add_note(f"digitizing cleanup: {outcome}")

    def wait_digitize_armed(
        self,
        timeout_s: float = 5.0,
        *,
        pretrigger_guard_s: float = 0.005,
        abort_requested: Callable[[], bool] | None = None,
    ) -> None:
        """Wait for WTG_meas AND pre-trigger history before reporting readiness.

        The Keysight N6705C User's Guide (p. 213) warns that a trigger
        occurring before pre-trigger samples fill is ignored. The conservative
        fill clock starts only after WTG_meas is first *observed*, not when
        INIT was sent, because INIT-to-ready latency is firmware-dependent.

        The guard is a configurable engineering margin, not a qualified
        guarantee for spontaneous external events. For negative offsets, total
        timeout includes WTG polling plus pre-trigger fill and guard.
        """
        self._require_digitizing()
        self._validate_digitize_timeout(timeout_s)
        if (
            isinstance(pretrigger_guard_s, bool)
            or not isinstance(pretrigger_guard_s, (int, float))
            or not math.isfinite(pretrigger_guard_s)
            or pretrigger_guard_s < 0
        ):
            raise DriverArgumentValueError(
                f"pretrigger_guard_s must be a nonnegative finite number, got {pretrigger_guard_s!r}"
            )
        self._acquisition.require(AcquisitionState.ARMED, AcquisitionState.WAITING)
        timing = self._digitize_timing
        if timing is None:
            raise DriverPreconditionError("digitizing timing not verified; reconfigure sweep")
        deadline = monotonic() + timeout_s

        def check_abort() -> None:
            if abort_requested is not None and abort_requested():
                raise DriverOperationUncertainError(
                    f"channel {self.channel} pretrigger fill canceled by operator"
                )

        def predicate() -> bool:
            check_abort()
            mask = int(self._query(f"STAT:OPER:COND? {self._chan}").strip())
            return self._acquisition.observe(mask) is AcquisitionState.WAITING

        try:
            with translated(operation="wait_digitize_armed"):
                poll_until(
                    predicate,
                    timeout_s=timeout_s,
                    interval_s=0.05,
                    description=f"channel {self.channel} armed for digitizing",
                )

            # No negative offset: there is no pre-trigger buffer to fill.
            fill_s = timing.pretrigger_fill_s
            if fill_s > 0:
                # Start after first WTG observation; avoid assuming readiness
                # existed at INIT submission or completion.
                barrier = monotonic() + fill_s + pretrigger_guard_s
                if barrier > deadline:
                    raise DriverTimeoutError(
                        f"channel {self.channel} pretrigger fill requires "
                        f"{fill_s + pretrigger_guard_s:.6g} s after WTG_meas, "
                        f"but only {max(0.0, deadline - monotonic()):.6g} s remain"
                    )
                while True:
                    check_abort()
                    now = monotonic()
                    if now >= barrier:
                        break
                    if now >= deadline:
                        raise DriverTimeoutError(
                            f"channel {self.channel} pretrigger buffer did not fill "
                            f"within {timeout_s} s (requires {fill_s + pretrigger_guard_s:.6g} s "
                            "after WTG_meas observation)"
                        )
                    sleep(min(0.01, barrier - now, deadline - now))
                # The level/external trigger may already have fired; this
                # API must not report that an event can still be generated.
                check_abort()
                mask = int(self._query(f"STAT:OPER:COND? {self._chan}").strip())
                if self._acquisition.observe(mask) is not AcquisitionState.WAITING:
                    raise DriverOperationUncertainError(
                        f"channel {self.channel} is no longer waiting for trigger "
                        "after pretrigger buffer fill"
                    )
        except BaseException as primary:
            self._cleanup_failed_digitize(primary)
            raise

    def trigger_digitize(self) -> None:
        """Force an immediate trigger after waiting status was observed."""
        self._require_digitizing()
        self._acquisition.require(AcquisitionState.WAITING)
        try:
            self._write(f"TRIG:ACQ {self._chan}")
        except Exception:
            self._acquisition.unknown()
            raise
        self._acquisition.triggered()

    def wait_digitize_complete(
        self,
        timeout_s: float,
        *,
        abort_requested: Callable[[], bool] | None = None,
    ) -> None:
        """Wait for proven completion; abort if canceled, timed out, or failed.

        The current-generation status must have been observed waiting or
        active before cleared status can prove completion. A missing trigger
        cannot be mistaken for a successful waveform. Timeout/transport fault
        cleanup is best-effort, and may be UNKNOWN if communication is lost.
        """
        self._require_digitizing()
        self._validate_digitize_timeout(timeout_s)
        self._acquisition.require(
            AcquisitionState.ARMED,
            AcquisitionState.WAITING,
            AcquisitionState.ACQUIRING,
            AcquisitionState.COMPLETE,
        )

        def predicate() -> bool:
            if abort_requested is not None and abort_requested():
                self.abort_digitize()
                raise DriverOperationUncertainError(
                    f"channel {self.channel} digitizing aborted by operator request"
                )
            mask = int(self._query(f"STAT:OPER:COND? {self._chan}").strip())
            return self._acquisition.observe(mask) is AcquisitionState.COMPLETE

        try:
            with translated(operation="wait_digitize_complete"):
                poll_until(
                    predicate,
                    timeout_s=timeout_s,
                    interval_s=0.05,
                    description=f"channel {self.channel} digitizing complete",
                )
        except BaseException as primary:
            self._cleanup_failed_digitize(primary)
            raise

    def abort_digitize(self, *, timeout_s: float = 2.0) -> None:
        """Cancel a capture and confirm that WTG_meas/MEAS_active are clear.

        If the device is unreachable or does not acknowledge idle status,
        the acquisition remains UNKNOWN rather than being labeled aborted.
        """
        self._require_digitizing()
        self._validate_digitize_timeout(timeout_s)
        try:
            self._write(f"ABOR:ACQ {self._chan}")

            def idle() -> bool:
                mask = int(self._query(f"STAT:OPER:COND? {self._chan}").strip())
                return not bool(mask & (8 | 32))

            with translated(operation="abort_digitize"):
                poll_until(
                    idle,
                    timeout_s=timeout_s,
                    interval_s=0.05,
                    description=f"channel {self.channel} acquisition idle after abort",
                )
        except BaseException:
            self._acquisition.unknown()
            raise
        try:
            self._restore_digitize_format()
        except Exception:
            self._acquisition.unknown()
            raise
        self._acquisition.aborted()
        self._driver._end_digitize(self.channel)

    def fetch_digitized_array(
        self,
        function: Literal["voltage", "current"] | None = None,
        *,
        transfer_format: Literal["auto", "ascii", "real32"] = "auto",
    ) -> ArrayMeasurement:
        """Retrieve the full acquisition buffer. Blocks if the acquisition has not completed yet.

        ``function`` defaults to whatever ``configure_digitize`` last
        selected. Fetch immediately after the acquisition completes: any
        subsequent ``MEASure`` command starts a new acquisition and overwrites
        this triggered buffer on real N6700 firmware.
        """
        self._require_digitizing()
        resolved = function or self._digitize_function
        if resolved is None:
            raise DriverArgumentValueError(
                "fetch_digitized_array requires configure_digitize to run first, or an explicit function"
            )
        self._acquisition.require(AcquisitionState.COMPLETE, AcquisitionState.FETCHED)
        if resolved != self._digitize_function:
            raise DriverArgumentValueError(
                f"cannot fetch {resolved!r}: only {self._digitize_function!r} was configured"
            )
        scpi_function = "VOLT" if resolved == "voltage" else "CURR"
        timing = self._digitize_timing
        if timing is None:
            raise DriverPreconditionError("cannot fetch digitizer without verified timing")
        if transfer_format not in {"auto", "ascii", "real32"}:
            raise DriverArgumentValueError(f"unknown waveform transfer format {transfer_format!r}")
        real32 = transfer_format == "real32" or (
            transfer_format == "auto" and timing.points >= 4096
        )
        payload_hash: str | None = None
        data_format: Literal["ascii", "real"] = "ascii"
        try:
            if not self._query("FORM?").strip().upper().startswith("ASC"):
                raise DriverOperationUncertainError("digitizer transfer format changed unexpectedly")
            if real32:
                previous_border = self._query("FORM:BORD?").strip().upper()
                if previous_border not in {"SWAP", "NORM"}:
                    raise DriverMalformedResponseError(
                        f"unsupported instrument byte order {previous_border!r}"
                    )
                self._write("FORM:BORD SWAP")
                self._write("FORM REAL")
                try:
                    if not self._query("FORM?").strip().upper().startswith("REAL"):
                        raise DriverOperationUncertainError("instrument rejected REAL32 array format")
                    raw_bytes = self._driver._query_binary_scpi(
                        f"FETC:ARR:{scpi_function}? {self._chan}",
                        max_bytes=4 * timing.points,
                    )
                finally:
                    # Always restore ASCII before ordinary text queries.
                    self._write("FORM ASC")
                    self._write(f"FORM:BORD {previous_border}")
                values = parse_digitize_real32(raw_bytes, expected_points=timing.points)
                payload_hash = hashlib.sha256(raw_bytes).hexdigest()
                data_format = "real"
            else:
                raw = self._query(f"FETC:ARR:{scpi_function}? {self._chan}")
                values = parse_digitize_ascii(raw, expected_points=timing.points)
            self._restore_digitize_format()
        except Exception:
            self._acquisition.unknown()
            raise
        self._acquisition.fetched()
        self._driver._end_digitize(self.channel)
        return ArrayMeasurement(
            channel=self.channel,
            values=values,
            unit="V" if resolved == "voltage" else "A",
            format=data_format,
            interval_s=self._digitize_interval_s,
            offset_points=self._digitize_offset_points,
            raw_sha256=payload_hash,
        )
