"""Type-specific channel APIs with validated and checked SCPI operations."""

from __future__ import annotations

import math
from typing import ClassVar, Literal, Protocol

from scpi_driver_core.exceptions import ResponseParseError
from scpi_driver_core.execution.polling import poll_until
from scpi_driver_core.scpi.parsers import parse_bool, parse_float, parse_int

from ._translate import translated
from .exceptions import (
    DriverMalformedResponseError,
    DriverOperationUncertainError,
    DriverUnsafeOperationError,
    DriverUnsupportedOperationError,
    DriverUnsupportedValueError,
)
from .module_capabilities import ChannelCapabilities
from .scpi import format_bool, format_channel_list, format_numeric_param, require_number
from .types import (
    ChannelStatusSnapshot,
    Measurement,
    PowerMeasurement,
    ProtectionClearResult,
    ProtectionStatus,
    ScpiErrorRecord,
)

_SCPI_SENTINEL = 9.9e37


class _DriverProtocol(Protocol):
    def query_scpi(self, command: str) -> str: ...

    def write_scpi(self, command: str) -> None: ...

    def write_checked(self, command: str) -> None: ...

    def _measure_power(self, channel: int) -> PowerMeasurement: ...

    def _measure_channel(self, channel: int) -> Measurement: ...

    def _fetch_power(self, channel: int) -> PowerMeasurement: ...

    def _fetch_channel(self, channel: int) -> Measurement: ...

    def drain_errors(self) -> list[ScpiErrorRecord]: ...

    def check_errors(self) -> None: ...

    def set_channel_enabled(self, channels: list[int], enabled: bool) -> None: ...


class BaseChannel:
    """Safe common operations shared by all module types."""

    def __init__(self, driver: _DriverProtocol, channel: int, capabilities: ChannelCapabilities) -> None:
        self._driver = driver
        self.channel = channel
        self.capabilities = capabilities

    @property
    def _chan(self) -> str:
        return format_channel_list(self.channel)

    def _query(self, command: str) -> str:
        return self._driver.query_scpi(command)

    def _write(self, command: str) -> None:
        self._driver.write_checked(command)

    def _query_float(self, command: str, *, allow_sentinel: bool = False) -> float:
        raw = self._query(command)
        try:
            value = parse_float(raw)
        except ResponseParseError as exc:
            raise DriverMalformedResponseError(
                f"invalid floating-point reply {raw!r} for {command!r}", code="LPDS-PRT-010"
            ) from exc
        if abs(value) >= _SCPI_SENTINEL:
            if allow_sentinel:
                return math.nan
            raise DriverMalformedResponseError(
                f"instrument returned SCPI sentinel {value!r} for {command!r}",
                code="LPDS-PRT-011",
                details={"raw": raw},
            )
        return value

    def _query_int(self, command: str) -> int:
        raw = self._query(command)
        try:
            return parse_int(raw)
        except ResponseParseError as exc:
            raise DriverMalformedResponseError(
                f"invalid integer reply {raw!r} for {command!r}", code="LPDS-PRT-010"
            ) from exc

    def _query_bool(self, command: str) -> bool:
        raw = self._query(command)
        try:
            return parse_bool(raw)
        except ResponseParseError as exc:
            raise DriverMalformedResponseError(
                f"invalid boolean reply {raw!r} for {command!r}", code="LPDS-PRT-010"
            ) from exc

    def measure_voltage(self) -> float:
        return self._query_float(f"MEAS:VOLT? {self._chan}")

    def measure_current(self) -> float:
        return self._query_float(f"MEAS:CURR? {self._chan}")

    def measure_power(self) -> PowerMeasurement:
        return self._driver._measure_power(self.channel)

    def measure(self) -> Measurement:
        return self._driver._measure_channel(self.channel)

    def fetch_voltage(self) -> float:
        return self._query_float(f"FETC:VOLT? {self._chan}")

    def fetch_current(self) -> float:
        return self._query_float(f"FETC:CURR? {self._chan}")

    def fetch_power(self) -> PowerMeasurement:
        return self._driver._fetch_power(self.channel)

    def fetch(self) -> Measurement:
        return self._driver._fetch_channel(self.channel)

    def _get_enable_state(self) -> bool | None:
        raise DriverUnsupportedOperationError("generic channel has no enable state")

    def _set_enable_state(self, enabled: bool) -> None:
        raise DriverUnsupportedOperationError("generic channel has no enable command")

    def get_protection_status(self) -> ProtectionStatus:
        return ProtectionStatus(
            channel=self.channel,
            raw_status=self._query_int(f"STAT:QUES:COND? {self._chan}"),
        )

    def get_status_snapshot(self) -> ChannelStatusSnapshot:
        return ChannelStatusSnapshot(
            channel=self.channel,
            output_or_input_enabled=self._get_enable_state(),
            protection=self.get_protection_status(),
        )

    def is_output_active(self) -> bool:
        """Return true only when the programmed output/input is on and no trip disables it."""
        state = self._get_enable_state()
        return state is True and not self.get_protection_status().tripped

    def clear_protection(
        self,
        *,
        restore_output: bool = False,
        force_output_off_first: bool = True,
        verify_cleared: bool = True,
        timeout_s: float = 1.0,
    ) -> ProtectionClearResult:
        """Clear protection without allowing the clear operation to re-energize unexpectedly.

        ``force_output_off_first`` is retained for API compatibility. For safety,
        version 0.6+ always commands OFF before clearing a latched protection.
        """
        del force_output_off_first
        before_prot = self.get_protection_status()
        before_state = self._get_enable_state()
        if before_state is not None:
            self._set_enable_state(False)
        self._write(f"OUTP:PROT:CLE {self._chan}")

        if verify_cleared:
            with translated(operation="clear_protection"):
                poll_until(
                    lambda: not self.get_protection_status().tripped,
                    timeout_s=timeout_s,
                    interval_s=0.05,
                    description=f"channel {self.channel} protection cleared",
                )

        restored = False
        if restore_output and before_state:
            self._driver.set_channel_enabled([self.channel], True)
            restored = True

        after_prot = self.get_protection_status() if verify_cleared else before_prot
        after_state = self._get_enable_state()
        if restored and (after_state is not True or after_prot.tripped):
            raise DriverOperationUncertainError(
                "output could not be confirmed active after protection clear",
                code="LPDS-STA-021",
            )
        return ProtectionClearResult(
            channel=self.channel,
            protection_before=before_prot,
            protection_after=after_prot,
            output_state_before=before_state,
            output_state_after=after_state,
            restored_output=restored,
            errors=tuple(self._driver.drain_errors()),
        )


class PowerSupplyChannel(BaseChannel):
    """Power-supply channel API."""

    def output_on(self) -> None:
        self.set_output(True)

    def output_off(self) -> None:
        self.set_output(False)

    def set_output(self, enabled: bool) -> None:
        self._write(f"OUTP {format_bool(enabled)},{self._chan}")

    def get_output(self) -> bool:
        return self._query_bool(f"OUTP? {self._chan}")

    def _get_enable_state(self) -> bool | None:
        return self.get_output()

    def _set_enable_state(self, enabled: bool) -> None:
        self.set_output(enabled)

    def _voltage_bounds(self) -> tuple[float | None, float | None]:
        return self.capabilities.min_voltage, self.capabilities.max_voltage

    def _current_bounds(self) -> tuple[float | None, float | None]:
        return self.capabilities.min_current, self.capabilities.max_current

    def set_voltage_setpoint(
        self, value: float, *, voltage_range: float | str | None = None
    ) -> None:
        minimum, maximum = self._voltage_bounds()
        voltage = require_number("voltage", value, minimum=minimum, maximum=maximum)
        if voltage_range is None:
            self._write(f"VOLT {voltage:.12g},{self._chan}")
            return
        range_value = format_numeric_param("voltage_range", voltage_range, minimum=0.0)
        self._write(
            f"VOLT:RANG {range_value},{self._chan};:VOLT {voltage:.12g},{self._chan}"
        )

    def get_voltage_setpoint(self) -> float:
        return self._query_float(f"VOLT? {self._chan}")

    def set_current_limit(
        self, value: float, *, current_range: float | str | None = None
    ) -> None:
        _minimum, maximum = self._current_bounds()
        current = require_number("current_limit", value, minimum=0.0, maximum=maximum)
        if current_range is None:
            self._write(f"CURR {current:.12g},{self._chan}")
            return
        range_value = format_numeric_param("current_range", current_range, minimum=0.0)
        self._write(
            f"CURR:RANG {range_value},{self._chan};:CURR {current:.12g},{self._chan}"
        )

    def get_current_limit(self) -> float:
        return self._query_float(f"CURR? {self._chan}")

    def _guard_range_change(
        self, subsystem: Literal["VOLT", "CURR"], value: float | str, allow_output_glitch: bool
    ) -> None:
        if allow_output_glitch or not self.get_output():
            return
        present = self._query_float(f"{subsystem}:RANG? {self._chan}")
        range_value = format_numeric_param(
            f"{subsystem.lower()}_range", value, minimum=0.0
        )
        if range_value == "DEF":
            raise DriverUnsafeOperationError(
                "range change on an energized output may cause a temporary output dropout; "
                "pass allow_output_glitch=True after reviewing the DUT impact",
                code="LPDS-SAF-020",
            )
        if range_value in {"MIN", "MAX"}:
            target = self._query_float(f"{subsystem}:RANG? {range_value},{self._chan}")
        else:
            target = float(range_value)
        if not math.isclose(present, target, rel_tol=1e-9, abs_tol=1e-12):
            raise DriverUnsafeOperationError(
                "range change on an energized output may cause a temporary output dropout; "
                "pass allow_output_glitch=True after reviewing the DUT impact",
                code="LPDS-SAF-020",
            )

    def set_voltage_range(
        self, value: float | str, *, allow_output_glitch: bool = False
    ) -> None:
        self._guard_range_change("VOLT", value, allow_output_glitch)
        range_value = format_numeric_param("voltage_range", value, minimum=0.0)
        self._write(f"VOLT:RANG {range_value},{self._chan}")

    def set_current_range(
        self, value: float | str, *, allow_output_glitch: bool = False
    ) -> None:
        self._guard_range_change("CURR", value, allow_output_glitch)
        range_value = format_numeric_param("current_range", value, minimum=0.0)
        self._write(f"CURR:RANG {range_value},{self._chan}")

    def get_voltage_range(self) -> float:
        return self._query_float(f"VOLT:RANG? {self._chan}")

    def get_current_range(self) -> float:
        return self._query_float(f"CURR:RANG? {self._chan}")

    def set_ovp(self, value: float) -> None:
        voltage = require_number("ovp", value, minimum=0.0)
        self._write(f"VOLT:PROT {voltage:.12g},{self._chan}")

    def get_ovp(self) -> float:
        return self._query_float(f"VOLT:PROT? {self._chan}")

    def set_ocp(self, enabled: bool) -> None:
        self._write(f"CURR:PROT:STAT {format_bool(enabled)},{self._chan}")

    def get_ocp(self) -> bool:
        return self._query_bool(f"CURR:PROT:STAT? {self._chan}")


class SMUChannel(PowerSupplyChannel):
    """N678xA SMU channel API with priority-aware source and limit commands."""

    def _require_smu(self) -> None:
        if self.capabilities.module_type != "smu":
            raise DriverUnsupportedOperationError(f"channel {self.channel} is not an SMU")

    def set_smu_mode(self, mode: Literal["voltage", "current"]) -> None:
        self._require_smu()
        if mode not in {"voltage", "current"}:
            raise DriverUnsupportedValueError(f"invalid SMU mode {mode!r}", code="LPDS-ARG-015")
        self._write(f"FUNC {'VOLT' if mode == 'voltage' else 'CURR'},{self._chan}")

    def get_smu_mode(self) -> Literal["voltage", "current"]:
        self._require_smu()
        reply = self._query(f"FUNC? {self._chan}").strip().upper()
        if "CURR" in reply:
            return "current"
        if "VOLT" in reply:
            return "voltage"
        raise DriverMalformedResponseError(
            f"unexpected FUNC? reply {reply!r}", code="LPDS-PRT-012"
        )

    def set_current_limit(
        self, value: float, *, current_range: float | str | None = None
    ) -> None:
        self._require_smu()
        current = require_number(
            "current_limit", value, minimum=0.0, maximum=self.capabilities.max_current
        )
        if current_range is not None:
            range_value = format_numeric_param("current_range", current_range, minimum=0.0)
            self._write(
                f"CURR:RANG {range_value},{self._chan};:CURR:LIM {current:.12g},{self._chan}"
            )
        else:
            self._write(f"CURR:LIM {current:.12g},{self._chan}")

    def get_current_limit(self) -> float:
        self._require_smu()
        return self._query_float(f"CURR:LIM? {self._chan}")

    def set_current_setpoint(
        self, value: float, *, current_range: float | str | None = None
    ) -> None:
        self._require_smu()
        current = require_number(
            "current",
            value,
            minimum=self.capabilities.min_current,
            maximum=self.capabilities.max_current,
        )
        if current_range is not None:
            range_value = format_numeric_param("current_range", current_range, minimum=0.0)
            self._write(
                f"CURR:RANG {range_value},{self._chan};:CURR {current:.12g},{self._chan}"
            )
        else:
            self._write(f"CURR {current:.12g},{self._chan}")

    def get_current_setpoint(self) -> float:
        self._require_smu()
        return self._query_float(f"CURR? {self._chan}")

    def set_voltage_limit(self, value: float) -> None:
        self._require_smu()
        voltage = require_number(
            "voltage_limit",
            value,
            minimum=0.0,
            maximum=self.capabilities.max_voltage,
        )
        self._write(f"VOLT:LIM {voltage:.12g},{self._chan}")

    def get_voltage_limit(self) -> float:
        self._require_smu()
        return self._query_float(f"VOLT:LIM? {self._chan}")

    def set_ovp(self, value: float) -> None:
        self._require_smu()
        voltage = require_number("ovp", value, minimum=0.0)
        self._write(f"VOLT:PROT:REM {voltage:.12g},{self._chan}")

    def get_ovp(self) -> float:
        self._require_smu()
        return self._query_float(f"VOLT:PROT:REM? {self._chan}")

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
            self.set_ovp(voltage_limit)
        if verify:
            self._driver.check_errors()
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
        if verify:
            self._driver.check_errors()
        if output:
            self.output_on()
            if verify:
                self._driver.check_errors()

    def set_smu_output_off_mode(self, mode: Literal["high_z", "low_z"]) -> None:
        self._require_smu()
        if not self.capabilities.supports_smu_output_off_mode:
            raise DriverUnsupportedOperationError(
                "SMU output-off mode is not supported by this module"
            )
        if mode not in {"high_z", "low_z"}:
            raise DriverUnsupportedValueError(
                f"invalid output-off mode {mode!r}", code="LPDS-ARG-016"
            )
        self._write(f"OUTP:TMOD {'LOWZ' if mode == 'low_z' else 'HIGHZ'},{self._chan}")

    def get_smu_output_off_mode(self) -> Literal["high_z", "low_z"]:
        self._require_smu()
        reply = self._query(f"OUTP:TMOD? {self._chan}").strip().upper()
        if reply.startswith("LOW"):
            return "low_z"
        if reply.startswith("HIGH"):
            return "high_z"
        raise DriverMalformedResponseError(
            f"unexpected OUTP:TMOD? reply {reply!r}", code="LPDS-PRT-013"
        )


class ElectronicLoadChannel(BaseChannel):
    """Electronic-load channel API for verified N679xA models and SIM_LOAD."""

    _MODE_TO_SCPI: ClassVar[dict[str, str]] = {
        "cc": "CURR",
        "cv": "VOLT",
        "cr": "RES",
        "cp": "POW",
    }
    _SCPI_TO_MODE: ClassVar[dict[str, Literal["cc", "cv", "cr", "cp"]]] = {
        "CURR": "cc",
        "VOLT": "cv",
        "RES": "cr",
        "POW": "cp",
    }

    def _require_verified_or_sim(self) -> None:
        if self.capabilities.module_type != "electronic_load":
            raise DriverUnsupportedOperationError(
                f"channel {self.channel} is not an electronic load"
            )
        if (
            self.capabilities.model != "SIM_LOAD"
            and not self.capabilities.verified_real_load_commands
        ):
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
        else:
            self._write(f"OUTP {format_bool(enabled)},{self._chan}")

    def get_input(self) -> bool:
        self._require_verified_or_sim()
        if self._is_sim:
            return self._query_bool(f"SIM:LOAD:INP? {self._chan}")
        return self._query_bool(f"OUTP? {self._chan}")

    def _get_enable_state(self) -> bool | None:
        return self.get_input()

    def _set_enable_state(self, enabled: bool) -> None:
        self.set_input(enabled)

    def set_load_mode(self, mode: Literal["cc", "cv", "cr", "cp"]) -> None:
        self._require_verified_or_sim()
        if mode not in self._MODE_TO_SCPI:
            raise DriverUnsupportedValueError(
                f"invalid load mode {mode!r}", code="LPDS-ARG-017"
            )
        if self._is_sim:
            self._write(f"SIM:LOAD:MODE {mode.upper()},{self._chan}")
        else:
            self._write(f"FUNC {self._MODE_TO_SCPI[mode]},{self._chan}")

    def get_load_mode(self) -> Literal["cc", "cv", "cr", "cp"]:
        self._require_verified_or_sim()
        if self._is_sim:
            reply = self._query(f"SIM:LOAD:MODE? {self._chan}").strip().lower()
            if reply in self._MODE_TO_SCPI:
                return reply  # type: ignore[return-value]
            raise DriverMalformedResponseError(
                f"invalid SIM load mode {reply!r}", code="LPDS-PRT-014"
            )
        reply = self._query(f"FUNC? {self._chan}").strip().upper()
        for scpi_mode, mode in self._SCPI_TO_MODE.items():
            if scpi_mode in reply:
                return mode
        raise DriverMalformedResponseError(
            f"unrecognized load priority mode reply: {reply!r}", code="LPDS-PRT-014"
        )

    def _set_level(self, name: str, command: str, value: float) -> None:
        self._require_verified_or_sim()
        level = require_number(name, value, minimum=0.0)
        self._write(f"{command} {level:.12g},{self._chan}")

    def set_load_current(self, value: float) -> None:
        self._set_level(
            "load_current", "SIM:LOAD:CURR" if self._is_sim else "CURR", value
        )

    def get_load_current(self) -> float:
        self._require_verified_or_sim()
        return self._query_float(
            f"{'SIM:LOAD:CURR' if self._is_sim else 'CURR'}? {self._chan}"
        )

    def set_load_voltage(self, value: float) -> None:
        self._set_level(
            "load_voltage", "SIM:LOAD:VOLT" if self._is_sim else "VOLT", value
        )

    def get_load_voltage(self) -> float:
        self._require_verified_or_sim()
        return self._query_float(
            f"{'SIM:LOAD:VOLT' if self._is_sim else 'VOLT'}? {self._chan}"
        )

    def set_load_resistance(self, value: float) -> None:
        self._set_level(
            "load_resistance", "SIM:LOAD:RES" if self._is_sim else "RES", value
        )

    def get_load_resistance(self) -> float:
        self._require_verified_or_sim()
        return self._query_float(
            f"{'SIM:LOAD:RES' if self._is_sim else 'RES'}? {self._chan}"
        )

    def set_load_power(self, value: float) -> None:
        self._set_level(
            "load_power", "SIM:LOAD:POW" if self._is_sim else "POW", value
        )

    def get_load_power(self) -> float:
        self._require_verified_or_sim()
        return self._query_float(
            f"{'SIM:LOAD:POW' if self._is_sim else 'POW'}? {self._chan}"
        )

    def set_load_current_limit(self, value: float) -> None:
        self._require_verified_or_sim()
        if self._is_sim:
            raise DriverUnsupportedOperationError("current limit is not modeled by SIM_LOAD")
        current = require_number("load_current_limit", value, minimum=0.0)
        self._write(f"CURR:LIM {current:.12g},{self._chan}")

    def get_load_current_limit(self) -> float:
        self._require_verified_or_sim()
        if self._is_sim:
            raise DriverUnsupportedOperationError("current limit is not modeled by SIM_LOAD")
        return self._query_float(f"CURR:LIM? {self._chan}")

    def _enable_after_verified_configuration(self, input_on: bool, verify: bool) -> None:
        if verify:
            self._driver.check_errors()
        if input_on:
            self.input_on()
            if verify:
                self._driver.check_errors()

    def configure_cc(self, current: float, *, input_on: bool = False, verify: bool = True) -> None:
        self.set_load_mode("cc")
        self.set_load_current(current)
        self._enable_after_verified_configuration(input_on, verify)

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
        self._enable_after_verified_configuration(input_on, verify)

    def configure_cr(
        self, resistance: float, *, input_on: bool = False, verify: bool = True
    ) -> None:
        self.set_load_mode("cr")
        self.set_load_resistance(resistance)
        self._enable_after_verified_configuration(input_on, verify)

    def configure_cp(self, power: float, *, input_on: bool = False, verify: bool = True) -> None:
        self.set_load_mode("cp")
        self.set_load_power(power)
        self._enable_after_verified_configuration(input_on, verify)
