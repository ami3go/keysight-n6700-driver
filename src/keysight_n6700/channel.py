"""Type-specific channel APIs."""

from __future__ import annotations

import hashlib
import math
import threading
from collections.abc import Callable, Sequence
from time import monotonic, sleep
from typing import ClassVar, Literal, Protocol

from scpi_driver_core.execution.polling import poll_until

from ._translate import translated
from .acquisition import AcquisitionSnapshot, AcquisitionState, AcquisitionTracker, DigitizeTiming
from .exceptions import (
    DriverArgumentValueError,
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
    ArrayMeasurement,
    ChannelStatusSnapshot,
    Measurement,
    PowerMeasurement,
    ProtectionClearResult,
    ProtectionStatus,
    ScpiErrorRecord,
    VoltageArbConfiguration,
    VoltageListConfiguration,
    VoltageTransientStatus,
    VoltageTransientToken,
)


class _DriverProtocol(Protocol):
    def query_scpi(self, command: str) -> str: ...

    def write_scpi(self, command: str) -> None: ...

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
        transient_lock = getattr(self, "_transient_lock", None)
        if transient_lock is not None:
            with transient_lock:
                self._transient_generation += 1
                self._transient_config = None
                self._transient_state = "unknown"

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

    def __init__(self, driver: _DriverProtocol, channel: int, capabilities: ChannelCapabilities) -> None:
        super().__init__(driver, channel, capabilities)
        self._transient_lock = threading.RLock()
        self._transient_generation = 0
        self._transient_state = "idle"
        self._transient_mode = "fixed"
        self._transient_trigger_source = "bus"
        self._transient_config: VoltageListConfiguration | VoltageArbConfiguration | None = None

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
                    "dwell_s must contain one value or one value per voltage point"
                )
            values = values * points if len(values) == 1 else values
        else:
            values = (cls._finite(dwell_s, "dwell_s"),) * points  # type: ignore[arg-type]
        if any(value < 0 or value > maximum_s for value in values):
            raise DriverArgumentValueError(f"dwell_s values must be within 0..{maximum_s:g} s")
        return values

    def _validate_voltage_profile(
        self,
        levels_v: Sequence[float],
        *,
        minimum_voltage_v: float,
        maximum_voltage_v: float,
        maximum_points: int,
    ) -> tuple[float, ...]:
        if not self.capabilities.supports_list_mode:
            raise DriverUnsupportedOperationError(
                f"{self.capabilities.model} does not advertise source LIST/ARB support"
            )
        minimum = self._finite(minimum_voltage_v, "minimum_voltage_v")
        maximum = self._finite(maximum_voltage_v, "maximum_voltage_v")
        if minimum < 0 or maximum <= minimum:
            raise DriverArgumentValueError(
                "voltage safety bounds require 0 <= minimum_voltage_v < maximum_voltage_v"
            )
        levels = self._finite_values(levels_v, "levels_v")
        if len(levels) > maximum_points:
            raise DriverArgumentValueError(f"levels_v exceeds {maximum_points} points")
        if any(value < minimum or value > maximum for value in levels):
            raise DriverArgumentValueError(
                f"every voltage point must stay within the approved {minimum:g}..{maximum:g} V envelope"
            )
        instrument_max = min(self.get_voltage_range(), self.get_ovp())
        if maximum > instrument_max:
            raise DriverArgumentValueError(
                f"approved maximum {maximum:g} V exceeds instrument range/OVP {instrument_max:g} V"
            )
        return levels

    @staticmethod
    def _csv(values: Sequence[float]) -> str:
        return ",".join(format_float(value) for value in values)

    @staticmethod
    def _parse_csv_floats(raw: str, name: str) -> tuple[float, ...]:
        try:
            values = tuple(float(item.strip()) for item in raw.strip().split(",") if item.strip())
        except ValueError as exc:
            raise DriverMalformedResponseError(f"malformed {name} readback: {raw!r}") from exc
        if not values or any(not math.isfinite(value) for value in values):
            raise DriverMalformedResponseError(f"malformed {name} readback: {raw!r}")
        return values

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
    def _parse_boolean(cls, raw: str, name: str) -> bool:
        value = cls._parse_integer(raw, name)
        if value not in {0, 1}:
            raise DriverMalformedResponseError(f"malformed {name} readback: {raw!r}")
        return bool(value)

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

    def _assert_dwell_readback(
        self, expected: Sequence[float], actual: Sequence[float], name: str
    ) -> None:
        """Accept only the range-dependent dwell quantization documented by Keysight."""
        if len(expected) != len(actual):
            raise DriverOperationUncertainError(
                f"{name} readback does not match the requested profile"
            )
        smu = self.capabilities.model.startswith("N678")
        limits = (0.268435, 2.68435, 26.8435)
        resolutions = (1.024e-6, 10.24e-6, 102.4e-6, 1.024e-3)
        if not smu:
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

    def set_voltage_slew_rate(self, rate_v_per_s: float | str) -> float:
        """Set and read back voltage slew in V/s; ``'max'`` selects fastest."""
        if not self.capabilities.supports_voltage_slew:
            raise DriverUnsupportedOperationError(
                f"{self.capabilities.model} does not advertise voltage slew control"
            )
        if isinstance(rate_v_per_s, str):
            if rate_v_per_s.strip().lower() not in {"max", "infinity", "inf"}:
                raise DriverArgumentValueError("rate_v_per_s must be finite and > 0, or 'max'")
            command_value = "INF"
        else:
            rate = self._finite(rate_v_per_s, "rate_v_per_s")
            if rate <= 0:
                raise DriverArgumentValueError("rate_v_per_s must be > 0")
            command_value = format_float(rate)
        self._write(f"VOLT:SLEW {command_value},{self._chan}")
        self._driver.check_errors()
        return self.get_voltage_slew_rate()

    def get_voltage_slew_rate(self) -> float:
        if not self.capabilities.supports_voltage_slew:
            raise DriverUnsupportedOperationError(
                f"{self.capabilities.model} does not advertise voltage slew control"
            )
        value = float(self._query(f"VOLT:SLEW? {self._chan}"))
        if not math.isfinite(value) or value <= 0:
            raise DriverMalformedResponseError(f"invalid voltage slew readback: {value!r}")
        return value

    def _require_output_off_for_configuration(self) -> None:
        # Checked before any write: a refused call must not abort a profile
        # that is already armed or running on an energized output.
        if self.get_output():
            raise DriverPreconditionError(
                "source transient profiles must be configured while the output is OFF"
            )

    def _prepare_transient_configuration(self) -> None:
        self._write(f"ABOR:TRAN {self._chan}")
        self._write(f"INIT:CONT:TRAN OFF,{self._chan}")
        self._write(f"VOLT:MODE FIX,{self._chan}")

    def _configuration_failed(self) -> None:
        try:
            self._write(f"ABOR:TRAN {self._chan}")
            self._write(f"INIT:CONT:TRAN OFF,{self._chan}")
            self._write(f"VOLT:MODE FIX,{self._chan}")
        finally:
            self._transient_state = "unknown"
            self._transient_mode = "fixed"
            self._transient_config = None

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

    def configure_voltage_list(
        self,
        levels_v: Sequence[float],
        dwell_s: float | Sequence[float],
        *,
        maximum_voltage_v: float,
        minimum_voltage_v: float = 0.0,
        repeat_count: int = 1,
        step_mode: Literal["auto", "once"] = "auto",
        terminate_last: bool = False,
        maximum_duration_s: float = 60.0,
    ) -> VoltageListConfiguration:
        """Configure a bounded, output-OFF voltage LIST and verify readback."""
        if not self.capabilities.supports_list_mode:
            raise DriverUnsupportedOperationError(
                f"{self.capabilities.model} on {self.capabilities.mainframe_model or 'unknown mainframe'} "
                "does not advertise source LIST support"
            )
        levels = self._validate_voltage_profile(
            levels_v,
            minimum_voltage_v=minimum_voltage_v,
            maximum_voltage_v=maximum_voltage_v,
            maximum_points=512,
        )
        dwells = self._normalize_dwells(dwell_s, len(levels), maximum_s=262.144)
        if isinstance(repeat_count, bool) or not 1 <= int(repeat_count) <= 256:
            raise DriverArgumentValueError("repeat_count must be an integer within 1..256")
        if int(repeat_count) != repeat_count:
            raise DriverArgumentValueError("repeat_count must be an integer within 1..256")
        if step_mode not in {"auto", "once"}:
            raise DriverArgumentValueError("step_mode must be 'auto' or 'once'")
        duration = sum(dwells) * int(repeat_count)
        duration_limit = self._finite(maximum_duration_s, "maximum_duration_s")
        if duration_limit <= 0 or duration > duration_limit:
            raise DriverArgumentValueError(
                f"profile duration {duration:g} s exceeds maximum_duration_s {duration_limit:g} s"
            )
        current_limit = self.get_current_limit()
        with self._transient_lock:
            self._require_output_off_for_configuration()
            try:
                self._prepare_transient_configuration()
                self._write(f"LIST:VOLT {self._csv(levels)},{self._chan}")
                self._write(f"LIST:CURR {format_float(current_limit)},{self._chan}")
                self._write(f"LIST:DWEL {self._csv(dwells)},{self._chan}")
                self._write(f"LIST:TOUT:BOST 0,{self._chan}")
                self._write(f"LIST:TOUT:EOST 0,{self._chan}")
                self._write(f"LIST:COUN {int(repeat_count)},{self._chan}")
                self._write(f"LIST:STEP {step_mode.upper()},{self._chan}")
                self._write(f"LIST:TERM:LAST {format_bool(terminate_last)},{self._chan}")
                self._write(f"VOLT:MODE LIST,{self._chan}")
                self._driver.check_errors()
                level_readback = self._parse_csv_floats(
                    self._query(f"LIST:VOLT? {self._chan}"), "LIST voltage"
                )
                dwell_readback = self._parse_csv_floats(
                    self._query(f"LIST:DWEL? {self._chan}"), "LIST dwell"
                )
                current_readback = self._parse_csv_floats(
                    self._query(f"LIST:CURR? {self._chan}"), "LIST current"
                )
                self._assert_profile_readback(levels, level_readback, "LIST voltage")
                self._assert_dwell_readback(dwells, dwell_readback, "LIST dwell")
                self._assert_profile_readback((current_limit,), current_readback, "LIST current")
                if sum(dwell_readback) * int(repeat_count) > duration_limit:
                    raise DriverOperationUncertainError(
                        "quantized LIST duration exceeds maximum_duration_s"
                    )
                count_readback = self._parse_integer(
                    self._query(f"LIST:COUN? {self._chan}"), "LIST repeat count"
                )
                step_readback = self._query(f"LIST:STEP? {self._chan}").strip().upper()
                terminate_readback = self._parse_boolean(
                    self._query(f"LIST:TERM:LAST? {self._chan}"), "LIST termination"
                )
                if count_readback != int(repeat_count):
                    raise DriverOperationUncertainError("LIST repeat-count readback mismatch")
                if not step_readback.startswith(step_mode.upper()):
                    raise DriverOperationUncertainError("LIST step-mode readback mismatch")
                if terminate_readback != bool(terminate_last):
                    raise DriverOperationUncertainError("LIST termination readback mismatch")
                if not self._query(f"VOLT:MODE? {self._chan}").strip().upper().startswith("LIST"):
                    raise DriverOperationUncertainError("voltage mode did not enter LIST")
            except BaseException:
                self._configuration_failed()
                raise
            config = VoltageListConfiguration(
                channel=self.channel,
                levels_v=level_readback,
                dwell_s=dwell_readback,
                current_limit_a=current_limit,
                repeat_count=int(repeat_count),
                step_mode=step_mode,
                terminate_last=bool(terminate_last),
                minimum_voltage_v=float(minimum_voltage_v),
                maximum_voltage_v=float(maximum_voltage_v),
            )
            self._transient_config = config
            self._transient_mode = "list"
            self._transient_state = "configured"
            return config

    def configure_voltage_arb(
        self,
        levels_v: Sequence[float],
        dwell_s: float | Sequence[float],
        *,
        maximum_voltage_v: float,
        minimum_voltage_v: float = 0.0,
        repeat_count: int = 1,
        terminate_last: bool = False,
        maximum_duration_s: float = 60.0,
    ) -> VoltageArbConfiguration:
        """Configure a bounded user-defined voltage ARB and verify readback."""
        if not self.capabilities.supports_voltage_arb:
            raise DriverUnsupportedOperationError(
                f"{self.capabilities.model} does not advertise voltage ARB support"
            )
        levels = self._validate_voltage_profile(
            levels_v,
            minimum_voltage_v=minimum_voltage_v,
            maximum_voltage_v=maximum_voltage_v,
            maximum_points=511,
        )
        dwells = self._normalize_dwells(dwell_s, len(levels), maximum_s=262.144)
        if isinstance(repeat_count, bool) or int(repeat_count) != repeat_count:
            raise DriverArgumentValueError("repeat_count must be an integer within 1..16777216")
        if not 1 <= int(repeat_count) <= 16_777_216:
            raise DriverArgumentValueError("repeat_count must be an integer within 1..16777216")
        duration = sum(dwells) * int(repeat_count)
        duration_limit = self._finite(maximum_duration_s, "maximum_duration_s")
        if duration_limit <= 0 or duration > duration_limit:
            raise DriverArgumentValueError(
                f"profile duration {duration:g} s exceeds maximum_duration_s {duration_limit:g} s"
            )
        with self._transient_lock:
            self._require_output_off_for_configuration()
            try:
                self._prepare_transient_configuration()
                self._write(f"ARB:FUNC:TYPE VOLT,{self._chan}")
                self._write(f"ARB:FUNC:SHAP UDEF,{self._chan}")
                self._write(f"ARB:VOLT:UDEF:LEV {self._csv(levels)},{self._chan}")
                self._write(f"ARB:VOLT:UDEF:DWEL {self._csv(dwells)},{self._chan}")
                self._write(f"ARB:VOLT:UDEF:BOST 0,{self._chan}")
                self._write(f"ARB:COUN {int(repeat_count)},{self._chan}")
                self._write(f"ARB:TERM:LAST {format_bool(terminate_last)},{self._chan}")
                self._write(f"VOLT:MODE ARB,{self._chan}")
                self._driver.check_errors()
                level_readback = self._parse_csv_floats(
                    self._query(f"ARB:VOLT:UDEF:LEV? {self._chan}"), "ARB voltage"
                )
                dwell_readback = self._parse_csv_floats(
                    self._query(f"ARB:VOLT:UDEF:DWEL? {self._chan}"), "ARB dwell"
                )
                self._assert_profile_readback(levels, level_readback, "ARB voltage")
                self._assert_dwell_readback(dwells, dwell_readback, "ARB dwell")
                if sum(dwell_readback) * int(repeat_count) > duration_limit:
                    raise DriverOperationUncertainError(
                        "quantized ARB duration exceeds maximum_duration_s"
                    )
                count_readback = self._parse_integer(
                    self._query(f"ARB:COUN? {self._chan}"), "ARB repeat count"
                )
                terminate_readback = self._parse_boolean(
                    self._query(f"ARB:TERM:LAST? {self._chan}"), "ARB termination"
                )
                function_readback = self._query(f"ARB:FUNC:TYPE? {self._chan}").strip().upper()
                shape_readback = self._query(f"ARB:FUNC:SHAP? {self._chan}").strip().upper()
                if count_readback != int(repeat_count):
                    raise DriverOperationUncertainError("ARB repeat-count readback mismatch")
                if terminate_readback != bool(terminate_last):
                    raise DriverOperationUncertainError("ARB termination readback mismatch")
                if not function_readback.startswith("VOLT") or not shape_readback.startswith(
                    "UDEF"
                ):
                    raise DriverOperationUncertainError("ARB function/shape readback mismatch")
                if not self._query(f"VOLT:MODE? {self._chan}").strip().upper().startswith("ARB"):
                    raise DriverOperationUncertainError("voltage mode did not enter ARB")
            except BaseException:
                self._configuration_failed()
                raise
            config = VoltageArbConfiguration(
                channel=self.channel,
                levels_v=level_readback,
                dwell_s=dwell_readback,
                repeat_count=int(repeat_count),
                terminate_last=bool(terminate_last),
                minimum_voltage_v=float(minimum_voltage_v),
                maximum_voltage_v=float(maximum_voltage_v),
            )
            self._transient_config = config
            self._transient_mode = "arb"
            self._transient_state = "configured"
            return config

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
            self._write(f"TRIG:TRAN:SOUR {'BUS' if source == 'bus' else 'EXT'},{self._chan}")
            self._driver.check_errors()
            readback = self._query(f"TRIG:TRAN:SOUR? {self._chan}").strip().upper()
            expected = "BUS" if source == "bus" else "EXT"
            if not readback.startswith(expected):
                raise DriverOperationUncertainError("transient trigger-source readback mismatch")
            self._transient_trigger_source = source

    def arm_voltage_transient(self) -> VoltageTransientToken:
        with self._transient_lock:
            if self._transient_config is None or self._transient_state not in {
                "configured", "complete", "aborted"
            }:
                raise DriverPreconditionError("a verified voltage LIST/ARB must be configured first")
            if not self.get_output():
                raise DriverPreconditionError("source output must be ON before arming a voltage transient")
            self._transient_generation += 1
            self._write(f"INIT:TRAN {self._chan}")
            self._driver.check_errors()
            self._transient_state = "armed"
            return VoltageTransientToken(self.channel, self._transient_generation)

    def _require_transient_token(self, token: VoltageTransientToken) -> None:
        if token.channel != self.channel or token.generation != self._transient_generation:
            raise DriverPreconditionError("stale or foreign voltage-transient token")

    def get_voltage_transient_status(self) -> VoltageTransientStatus:
        raw = int(self._query(f"STAT:OPER:COND? {self._chan}").strip())
        return VoltageTransientStatus(
            channel=self.channel,
            mode=self._transient_mode,  # type: ignore[arg-type]
            state=self._transient_state,  # type: ignore[arg-type]
            generation=self._transient_generation,
            trigger_source=self._transient_trigger_source,  # type: ignore[arg-type]
            operation_status=raw,
            waiting_for_trigger=bool(raw & self._WTG_TRANSIENT),
            transient_active=bool(raw & self._TRANSIENT_ACTIVE),
        )

    def wait_voltage_transient_ready(
        self,
        token: VoltageTransientToken,
        *,
        timeout_s: float = 5.0,
        poll_interval_s: float = 0.01,
    ) -> VoltageTransientStatus:
        self._require_transient_token(token)
        if self._transient_state != "armed":
            raise DriverPreconditionError("voltage transient is not armed")
        last = self.get_voltage_transient_status()

        def predicate() -> bool:
            nonlocal last
            last = self.get_voltage_transient_status()
            return last.waiting_for_trigger

        try:
            with translated(operation="wait_voltage_transient_ready"):
                poll_until(
                    predicate,
                    timeout_s=timeout_s,
                    interval_s=poll_interval_s,
                    description=f"channel {self.channel} transient trigger ready",
                )
        except BaseException as primary:
            self._cleanup_failed_voltage_transient(primary)
            raise
        self._transient_state = "ready"
        return self.get_voltage_transient_status()

    def trigger_voltage_transient(self, token: VoltageTransientToken) -> None:
        self._require_transient_token(token)
        if self._transient_state != "ready":
            raise DriverPreconditionError("wait_voltage_transient_ready() must succeed before triggering")
        if self._transient_trigger_source != "bus":
            raise DriverPreconditionError("software trigger requires trigger source 'bus'")
        self._write(f"TRIG:TRAN {self._chan}")
        self._driver.check_errors()
        self._transient_state = "triggered"

    def wait_voltage_transient_complete(
        self,
        token: VoltageTransientToken,
        *,
        timeout_s: float = 10.0,
        poll_interval_s: float = 0.01,
    ) -> VoltageTransientStatus:
        self._require_transient_token(token)
        if self._transient_state not in {"ready", "triggered"}:
            raise DriverPreconditionError("voltage transient is not armed/triggered")
        last = self.get_voltage_transient_status()

        def predicate() -> bool:
            nonlocal last
            last = self.get_voltage_transient_status()
            return not last.waiting_for_trigger and not last.transient_active

        try:
            with translated(operation="wait_voltage_transient_complete"):
                poll_until(
                    predicate,
                    timeout_s=timeout_s,
                    interval_s=poll_interval_s,
                    description=f"channel {self.channel} voltage transient complete",
                )
        except BaseException as primary:
            self._cleanup_failed_voltage_transient(primary)
            raise
        self._driver.check_errors()
        self._transient_state = "complete"
        return self.get_voltage_transient_status()

    def abort_voltage_transient(self) -> None:
        self._write(f"ABOR:TRAN {self._chan}")
        self._write(f"INIT:CONT:TRAN OFF,{self._chan}")
        self._driver.check_errors()
        self._transient_state = "aborted"


class SMUChannel(PowerSupplyChannel):
    """SMU channel API for N678xA-style modules."""

    def _require_smu(self) -> None:
        if self.capabilities.module_type != "smu":
            raise DriverUnsupportedOperationError(f"channel {self.channel} is not an SMU")

    def set_smu_mode(self, mode: Literal["voltage", "current"]) -> None:
        self._require_smu()
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
        # Capability-gated wrapper; maps to OVP-style voltage protection for supported simulator/PSU behavior.
        self.set_ovp(value)

    def get_voltage_limit(self) -> float:
        return self.get_ovp()

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
