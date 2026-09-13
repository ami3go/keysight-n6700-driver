"""Type-specific channel APIs."""

from __future__ import annotations

from typing import Literal, Protocol

from .exceptions import DriverUnsupportedOperationError
from .module_capabilities import ChannelCapabilities
from .scpi import format_bool, format_channel_list, format_float
from .types import (
    ChannelStatusSnapshot,
    Measurement,
    PowerMeasurement,
    ProtectionClearResult,
    ProtectionStatus,
    ScpiErrorRecord,
)


class _DriverProtocol(Protocol):
    def query_scpi(self, command: str) -> str: ...

    def write_scpi(self, command: str) -> None: ...

    def _measure_power(self, channel: int) -> PowerMeasurement: ...

    def _measure_channel(self, channel: int) -> Measurement: ...

    def drain_errors(self) -> list[ScpiErrorRecord]: ...

    def check_errors(self) -> None: ...


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


class SMUChannel(PowerSupplyChannel):
    """SMU channel API for N678xA-style modules."""

    def _require_smu(self) -> None:
        if self.capabilities.module_type != "smu":
            raise DriverUnsupportedOperationError(f"channel {self.channel} is not an SMU")

    def set_smu_mode(self, mode: Literal["voltage", "current"]) -> None:
        self._require_smu()
        scpi_mode = "VOLT" if mode == "voltage" else "CURR"
        self._write(f"FUNC:MODE {scpi_mode},{self._chan}")

    def get_smu_mode(self) -> Literal["voltage", "current"]:
        self._require_smu()
        resp = self._query(f"FUNC:MODE? {self._chan}").upper()
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
    """Electronic load channel API.

    Real load commands are intentionally blocked unless verified. SIM_LOAD
    supports simulator-only commands for tests.
    """

    def _require_verified_or_sim(self) -> None:
        if self.capabilities.module_type != "electronic_load":
            raise DriverUnsupportedOperationError(f"channel {self.channel} is not an electronic load")
        if self.capabilities.model != "SIM_LOAD" and not self.capabilities.verified_real_load_commands:
            raise DriverUnsupportedOperationError(
                "electronic-load SCPI commands are not verified for this exact module"
            )

    def input_on(self) -> None:
        self.set_input(True)

    def input_off(self) -> None:
        self.set_input(False)

    def set_input(self, enabled: bool) -> None:
        self._require_verified_or_sim()
        if self.capabilities.model == "SIM_LOAD":
            self._write(f"SIM:LOAD:INP {format_bool(enabled)},{self._chan}")
            return
        raise DriverUnsupportedOperationError("real load input command is not implemented without source map")

    def get_input(self) -> bool:
        self._require_verified_or_sim()
        if self.capabilities.model == "SIM_LOAD":
            return self._query(f"SIM:LOAD:INP? {self._chan}").strip() not in {"0", "+0"}
        raise DriverUnsupportedOperationError("real load input query is not implemented without source map")

    def _get_enable_state(self) -> bool | None:
        return self.get_input()

    def _set_enable_state(self, enabled: bool) -> None:
        self.set_input(enabled)

    def set_load_mode(self, mode: Literal["cc", "cv", "cr", "cp"]) -> None:
        self._require_verified_or_sim()
        self._write(f"SIM:LOAD:MODE {mode.upper()},{self._chan}")

    def get_load_mode(self) -> Literal["cc", "cv", "cr", "cp"]:
        self._require_verified_or_sim()
        return self._query(f"SIM:LOAD:MODE? {self._chan}").strip().lower()  # type: ignore[return-value]

    def set_load_current(self, value: float) -> None:
        self._require_verified_or_sim()
        self._write(f"SIM:LOAD:CURR {format_float(value)},{self._chan}")

    def get_load_current(self) -> float:
        self._require_verified_or_sim()
        return float(self._query(f"SIM:LOAD:CURR? {self._chan}"))

    def set_load_voltage(self, value: float) -> None:
        self._require_verified_or_sim()
        self._write(f"SIM:LOAD:VOLT {format_float(value)},{self._chan}")

    def get_load_voltage(self) -> float:
        self._require_verified_or_sim()
        return float(self._query(f"SIM:LOAD:VOLT? {self._chan}"))

    def set_load_resistance(self, value: float) -> None:
        self._require_verified_or_sim()
        self._write(f"SIM:LOAD:RES {format_float(value)},{self._chan}")

    def get_load_resistance(self) -> float:
        self._require_verified_or_sim()
        return float(self._query(f"SIM:LOAD:RES? {self._chan}"))

    def set_load_power(self, value: float) -> None:
        self._require_verified_or_sim()
        self._write(f"SIM:LOAD:POW {format_float(value)},{self._chan}")

    def get_load_power(self) -> float:
        self._require_verified_or_sim()
        return float(self._query(f"SIM:LOAD:POW? {self._chan}"))

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
