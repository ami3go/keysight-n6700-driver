"""Robot Framework adapter for :mod:`keysight_n6700`.

Per LPDS-015, an adapter contains no device logic of its own — it only
translates calls and results between its framework and the driver's public
API. Session/alias management, module discovery, channel typing, safety
shutdown, and SCPI semantics all live in :class:`keysight_n6700.driver.N6700`
and are exercised through one shared instance; this module's job is Robot
Framework argument conversion, keyword naming, and result serialization.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, cast

from robot.api import logger
from robot.api.deco import keyword, library, not_keyword

from keysight_n6700 import N6700, DriverTimeoutError

from .version import __version__

_NUMBER_RE = re.compile(
    r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*"
    r"([pnumkMGTµμ]?)\s*(?:V|A|W|OHM|Ω|S)?\s*$",
    re.IGNORECASE,
)
_TIME_RE = re.compile(
    r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(ms|s|sec|secs|second|seconds|min|minute|minutes)?\s*$",
    re.IGNORECASE,
)
_PREFIX = {
    "": 1.0,
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "µ": 1e-6,
    "μ": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "K": 1e3,
    "M": 1e6,
    "g": 1e9,
    "G": 1e9,
    "t": 1e12,
    "T": 1e12,
}
_TRUE = {"1", "true", "yes", "on", "enable", "enabled"}
_FALSE = {"0", "false", "no", "off", "disable", "disabled", "none", ""}


@not_keyword
def _as_bool(value: Any, *, name: str = "value") -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError(f"{name} must be a boolean value, got {value!r}")


@not_keyword
def _as_float(value: Any, *, name: str = "value") -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric, got boolean {value!r}")
    if isinstance(value, (int, float)):
        result = float(value)
    else:
        text = str(value).strip().replace(" ", "")
        match = _NUMBER_RE.match(text)
        if not match:
            raise ValueError(
                f"{name} must be numeric; engineering forms such as 5V, 250mA and 2.2k are supported, got {value!r}"
            )
        number, prefix = match.groups()
        source_prefix = text[len(number) : len(number) + 1]
        multiplier = _PREFIX.get(source_prefix, _PREFIX.get(prefix, 1.0))
        result = float(number) * multiplier
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return result


@not_keyword
def _as_seconds(value: Any, *, name: str = "timeout") -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        result = float(value)
    else:
        match = _TIME_RE.match(str(value))
        if not match:
            raise ValueError(f"{name} must be seconds or a value such as 500ms, 5s, or 2min")
        number, unit = match.groups()
        result = float(number)
        unit = (unit or "s").lower()
        if unit == "ms":
            result /= 1000.0
        elif unit.startswith("min"):
            result *= 60.0
    if result < 0 or not math.isfinite(result):
        raise ValueError(f"{name} must be a finite non-negative duration")
    return result


@not_keyword
def _as_channel(value: Any) -> int:
    channel = int(value)
    if channel < 1 or channel > 4:
        raise ValueError(f"channel must be between 1 and 4, got {channel}")
    return channel


@not_keyword
def _as_channels(value: Any) -> list[int]:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("channels cannot be empty")
        if text.lower() == "all":
            return [1, 2, 3, 4]
        raw = [part for part in re.split(r"[,;\s]+", text) if part]
    elif isinstance(value, Sequence):
        raw = list(value)
    else:
        raw = [value]
    channels = [_as_channel(item) for item in raw]
    if len(set(channels)) != len(channels):
        raise ValueError(f"channels contain duplicates: {channels}")
    return channels


@not_keyword
def _serialize(value: Any) -> Any:
    """Convert typed driver values into Robot-friendly Python primitives."""
    if is_dataclass(value):
        return {key: _serialize(item) for key, item in asdict(cast(Any, value)).items()}
    if isinstance(value, Mapping):
        return {(str(key) if isinstance(key, int) else key): _serialize(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_serialize(item) for item in value]
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value


@library(scope="SUITE", version=__version__, auto_keywords=False)
class KeysightN6700Library:
    """Robot Framework library for Keysight/Agilent N6700-series mainframes.

    A thin translation over :class:`keysight_n6700.N6700`, which owns every
    session, module, and safety decision. ``auto_shutdown`` defaults to true:
    at disconnect and suite end, the driver attempts a best-effort shutdown
    of every channel before closing. Set it to false only when another safety
    controller owns the output state.

    ``strict_errors`` defaults to true. Mutating keywords drain the SCPI
    error queue after the command and fail the Robot keyword if the
    instrument reports an error.
    """

    ROBOT_LIBRARY_DOC_FORMAT = "ROBOT"
    ROBOT_LISTENER_API_VERSION = 2

    def __init__(self, auto_shutdown: Any = True, strict_errors: Any = True) -> None:
        self.auto_shutdown = _as_bool(auto_shutdown, name="auto_shutdown")
        self.strict_errors = _as_bool(strict_errors, name="strict_errors")
        self._driver = N6700()
        self._driver.set_auto_shutdown_on_disconnect(self.auto_shutdown)
        self.ROBOT_LIBRARY_LISTENER = self

    def _end_suite(self, name: str, attrs: Mapping[str, Any]) -> None:
        self._driver.disconnect_all()

    @not_keyword
    def _post_write(self, alias: str | None) -> None:
        if self.strict_errors:
            self._driver.check_errors(alias=alias)

    # -- connection -----------------------------------------------------------

    @keyword("Connect To N6700")
    def connect_to_n6700(
        self,
        resource: str = "",
        alias: str = "default",
        connection_type: str = "visa",
        port: Any = 5025,
        discover: Any = True,
        reset_on_connect: Any = False,
        clear_errors_on_connect: Any = False,
        audit_log_path: str | None = None,
        replace: Any = False,
    ) -> str:
        """Connect using ``visa``, ``usb``, ``ethernet``/``socket``, or ``simulated``."""
        info = self._driver.connect(
            resource or None,
            alias=str(alias),
            connection_type=str(connection_type),
            port=port,
            discover=_as_bool(discover, name="discover"),
            reset_on_connect=_as_bool(reset_on_connect, name="reset_on_connect"),
            clear_errors_on_connect=_as_bool(clear_errors_on_connect, name="clear_errors_on_connect"),
            replace=_as_bool(replace, name="replace"),
        )
        if audit_log_path:
            self._driver.audit_log_path = Path(audit_log_path)
        identity = info["identity"] or {}
        logger.info(
            f"Connected N6700 alias={alias!r}: {identity.get('manufacturer')}, "
            f"{identity.get('model')}, serial={identity.get('serial_number')}"
        )
        return str(alias)

    @keyword("Connect To Simulated N6700")
    def connect_to_simulated_n6700(self, alias: str = "default", replace: Any = False) -> str:
        """Connect to the bundled four-channel simulator."""
        return self.connect_to_n6700(alias=alias, connection_type="simulated", replace=replace)

    @keyword("Connect To N6700 Via VISA")
    def connect_to_n6700_via_visa(self, resource: str, alias: str = "default", replace: Any = False) -> str:
        """Connect to a VISA/USB/LAN VISA resource."""
        return self.connect_to_n6700(resource, alias, "visa", replace=replace)

    @keyword("Connect To N6700 Via USB")
    def connect_to_n6700_via_usb(
        self,
        resource: str,
        alias: str = "default",
        discover: Any = True,
        reset_on_connect: Any = False,
        clear_errors_on_connect: Any = False,
        audit_log_path: str | None = None,
        replace: Any = False,
    ) -> str:
        """Connect to an N6700 mainframe through a USBTMC VISA resource."""
        return self.connect_to_n6700(
            resource=resource,
            alias=alias,
            connection_type="usb",
            discover=discover,
            reset_on_connect=reset_on_connect,
            clear_errors_on_connect=clear_errors_on_connect,
            audit_log_path=audit_log_path,
            replace=replace,
        )

    @keyword("Connect To N6700 Via Ethernet")
    def connect_to_n6700_via_ethernet(
        self, host: str, port: Any = 5025, alias: str = "default", replace: Any = False
    ) -> str:
        """Connect using a raw TCP socket, normally port 5025."""
        return self.connect_to_n6700(host, alias, "ethernet", port, replace=replace)

    @keyword("Select N6700")
    def select_n6700(self, alias: str) -> str:
        """Select the named connection used by keywords that omit ``alias``."""
        return self._driver.select(alias)

    @keyword("Get Current N6700 Alias")
    def get_current_n6700_alias(self) -> str | None:
        """Return the current connection alias, or ``None``."""
        return self._driver.get_current_alias()

    @keyword("Get Connected N6700 Aliases")
    def get_connected_n6700_aliases(self) -> list[str]:
        """Return all open aliases."""
        return self._driver.list_connected_aliases()

    @keyword("Disconnect N6700")
    def disconnect_n6700(self, alias: str | None = None) -> None:
        """Safely disconnect one connection."""
        self._driver.disconnect(alias)

    @keyword("Disconnect All N6700")
    def disconnect_all_n6700(self) -> None:
        """Safely disconnect every connection."""
        self._driver.disconnect_all()

    @keyword("Get N6700 Identity")
    def get_n6700_identity(self, alias: str | None = None) -> dict[str, Any]:
        """Return manufacturer, model, serial and firmware as a dictionary."""
        return _serialize(self._driver.idn(alias=alias))

    @keyword("Get N6700 Channel Count")
    def get_n6700_channel_count(self, alias: str | None = None) -> int:
        """Return the installed channel count."""
        return self._driver.channel_count(alias=alias)

    @keyword("Discover N6700 Modules")
    def discover_n6700_modules(self, alias: str | None = None) -> dict[str, Any]:
        """Discover modules and return capability dictionaries keyed by channel."""
        return _serialize(self._driver.discover_modules(alias=alias))

    @keyword("Get N6700 Channel Information")
    def get_n6700_channel_information(self, channel: Any, alias: str | None = None) -> dict[str, Any]:
        """Return model, serial, options and capabilities for one channel."""
        ch = _as_channel(channel)
        return {
            "channel": ch,
            "model": self._driver.channel_model(ch, alias=alias),
            "serial": self._driver.channel_serial(ch, alias=alias),
            "options": self._driver.channel_options(ch, alias=alias),
            "capabilities": _serialize(self._driver.channel(ch, alias=alias).capabilities),
        }

    @keyword("Write N6700 SCPI")
    def write_n6700_scpi(self, command: str, check_errors: Any | None = None, alias: str | None = None) -> None:
        """Write a raw SCPI command. Prefer typed keywords for normal tests."""
        self._driver.write_scpi(str(command), alias=alias)
        if self.strict_errors if check_errors is None else _as_bool(check_errors):
            self._driver.check_errors(alias=alias)

    @keyword("Query N6700 SCPI")
    def query_n6700_scpi(self, command: str, alias: str | None = None) -> str:
        """Send a raw SCPI query and return its response string."""
        return self._driver.query_scpi(str(command), alias=alias)

    @keyword("Set N6700 Raw SCPI Guard")
    def set_n6700_raw_scpi_guard(self, phrase: str) -> None:
        """Require ``phrase`` before ``Write N6700 SCPI``/``Query N6700 SCPI`` will run."""
        self._driver.set_raw_scpi_guard(str(phrase))

    @keyword("Enable N6700 Raw SCPI")
    def enable_n6700_raw_scpi(self, phrase: str) -> None:
        """Unlock a previously set raw-SCPI guard for subsequent keyword calls."""
        self._driver.enable_raw_scpi(str(phrase))

    @keyword("Disable N6700 Raw SCPI")
    def disable_n6700_raw_scpi(self) -> None:
        """Re-lock a previously set raw-SCPI guard."""
        self._driver.disable_raw_scpi()

    @keyword("Clear N6700 Status")
    def clear_n6700_status(self, alias: str | None = None) -> None:
        """Send ``*CLS``."""
        self._driver.clear_status(alias=alias)

    @keyword("Reset N6700")
    def reset_n6700(self, alias: str | None = None) -> None:
        """Reset the instrument. This can change output configuration."""
        self._driver.reset(alias=alias)
        self._post_write(alias)

    @keyword("Run N6700 Self Test")
    def run_n6700_self_test(self, alias: str | None = None) -> dict[str, Any]:
        """Run ``*TST?`` and return ``code``, ``message`` and ``passed``."""
        result = self._driver.self_test(alias=alias)
        data = _serialize(result)
        data["passed"] = result.passed
        return data

    @keyword("Check N6700 Errors")
    def check_n6700_errors(self, alias: str | None = None) -> None:
        """Fail if the instrument SCPI error queue is not empty."""
        self._driver.check_errors(alias=alias)

    @keyword("Drain N6700 Errors")
    def drain_n6700_errors(self, alias: str | None = None) -> list[dict[str, Any]]:
        """Drain and return all SCPI errors without failing."""
        return _serialize(self._driver.drain_errors(alias=alias))

    @keyword("Set N6700 Voltage")
    def set_n6700_voltage(self, channel: Any, voltage: Any, alias: str | None = None) -> float:
        """Set a power-supply/SMU voltage setpoint and return volts."""
        value = _as_float(voltage, name="voltage")
        self._driver.set_dc_voltage(value, _as_channel(channel), alias=alias)
        self._post_write(alias)
        return value

    @keyword("Get N6700 Voltage Setpoint")
    def get_n6700_voltage_setpoint(self, channel: Any, alias: str | None = None) -> float:
        """Return the programmed voltage setpoint."""
        return self._driver.get_dc_voltage_setpoint(_as_channel(channel), alias=alias)

    @keyword("Set N6700 Current Limit")
    def set_n6700_current_limit(self, channel: Any, current: Any, alias: str | None = None) -> float:
        """Set a power-supply/SMU current limit and return amperes."""
        value = _as_float(current, name="current")
        self._driver.set_dc_current(value, _as_channel(channel), alias=alias)
        self._post_write(alias)
        return value

    @keyword("Get N6700 Current Limit")
    def get_n6700_current_limit(self, channel: Any, alias: str | None = None) -> float:
        """Return the programmed current limit."""
        return self._driver.get_dc_current_setpoint(_as_channel(channel), alias=alias)

    @keyword("Configure N6700 Power Supply Channel")
    def configure_n6700_power_supply_channel(
        self,
        channel: Any,
        voltage: Any,
        current_limit: Any,
        output: Any = False,
        ovp: Any | None = None,
        ocp: Any | None = None,
        alias: str | None = None,
    ) -> dict[str, Any]:
        """Configure voltage/current/protection and optionally enable output."""
        ch_num = _as_channel(channel)
        ch = self._driver.power_supply(ch_num, alias=alias)
        voltage_v = _as_float(voltage, name="voltage")
        current_a = _as_float(current_limit, name="current_limit")
        ch.set_voltage_setpoint(voltage_v)
        ch.set_current_limit(current_a)
        ovp_v = None
        if ovp is not None and str(ovp).strip() != "":
            ovp_v = _as_float(ovp, name="ovp")
            ch.set_ovp(ovp_v)
        ocp_enabled = None
        if ocp is not None and str(ocp).strip() != "":
            ocp_enabled = _as_bool(ocp, name="ocp")
            ch.set_ocp(ocp_enabled)
        output_enabled = _as_bool(output, name="output")
        ch.set_output(output_enabled)
        self._post_write(alias)
        return {
            "channel": ch_num,
            "voltage_V": voltage_v,
            "current_limit_A": current_a,
            "ovp_V": ovp_v,
            "ocp_enabled": ocp_enabled,
            "output_enabled": output_enabled,
        }

    @keyword("Set N6700 Output")
    def set_n6700_output(self, channel: Any, enabled: Any, alias: str | None = None) -> bool:
        """Enable or disable one power-supply/SMU output."""
        state = _as_bool(enabled, name="enabled")
        if state:
            self._driver.enable_output(_as_channel(channel), alias=alias)
        else:
            self._driver.disable_output(_as_channel(channel), alias=alias)
        self._post_write(alias)
        return state

    @keyword("Turn On N6700 Output")
    def turn_on_n6700_output(self, channel: Any, alias: str | None = None) -> None:
        """Enable one power-supply/SMU output."""
        self.set_n6700_output(channel, True, alias)

    @keyword("Turn Off N6700 Output")
    def turn_off_n6700_output(self, channel: Any, alias: str | None = None) -> None:
        """Disable one power-supply/SMU output."""
        self.set_n6700_output(channel, False, alias)

    @keyword("Get N6700 Output State")
    def get_n6700_output_state(self, channel: Any, alias: str | None = None) -> bool:
        """Return one power-supply/SMU output state."""
        return self._driver.get_output_state(_as_channel(channel), alias=alias)

    @keyword("Set N6700 Outputs")
    def set_n6700_outputs(self, channels: Any, enabled: Any, alias: str | None = None) -> list[int]:
        """Set several power-supply/SMU outputs; ``channels`` may be ``1,2,4``."""
        parsed = _as_channels(channels)
        self._driver.set_power_outputs(parsed, _as_bool(enabled, name="enabled"), alias=alias)
        self._post_write(alias)
        return parsed

    @keyword("Set N6700 Over Voltage Protection")
    def set_n6700_over_voltage_protection(self, channel: Any, voltage: Any, alias: str | None = None) -> float:
        """Set OVP threshold in volts."""
        value = _as_float(voltage, name="voltage")
        self._driver.power_supply(_as_channel(channel), alias=alias).set_ovp(value)
        self._post_write(alias)
        return value

    @keyword("Get N6700 Over Voltage Protection")
    def get_n6700_over_voltage_protection(self, channel: Any, alias: str | None = None) -> float:
        """Return OVP threshold in volts."""
        return self._driver.power_supply(_as_channel(channel), alias=alias).get_ovp()

    @keyword("Set N6700 Over Current Protection")
    def set_n6700_over_current_protection(self, channel: Any, enabled: Any, alias: str | None = None) -> bool:
        """Enable or disable OCP."""
        state = _as_bool(enabled, name="enabled")
        self._driver.power_supply(_as_channel(channel), alias=alias).set_ocp(state)
        self._post_write(alias)
        return state

    @keyword("Get N6700 Over Current Protection")
    def get_n6700_over_current_protection(self, channel: Any, alias: str | None = None) -> bool:
        """Return OCP state."""
        return self._driver.power_supply(_as_channel(channel), alias=alias).get_ocp()

    @keyword("Measure N6700 Voltage")
    def measure_n6700_voltage(self, channel: Any, alias: str | None = None) -> float:
        """Measure channel voltage in volts."""
        return self._driver.measure_dc_voltage(_as_channel(channel), alias=alias)

    @keyword("Measure N6700 Current")
    def measure_n6700_current(self, channel: Any, alias: str | None = None) -> float:
        """Measure channel current in amperes."""
        return self._driver.measure_dc_current(_as_channel(channel), alias=alias)

    @keyword("Measure N6700 Power")
    def measure_n6700_power(self, channel: Any, alias: str | None = None) -> dict[str, Any]:
        """Measure channel power and return value, source and timestamp."""
        return _serialize(self._driver.measure_power(_as_channel(channel), alias=alias))

    @keyword("Measure N6700 Channel")
    def measure_n6700_channel(self, channel: Any, alias: str | None = None) -> dict[str, Any]:
        """Return voltage, current, power, source and timestamp for one channel."""
        return _serialize(self._driver.channel(_as_channel(channel), alias=alias).measure())

    @keyword("Measure All N6700 Channels")
    def measure_all_n6700_channels(self, alias: str | None = None) -> dict[str, Any]:
        """Return measurement dictionaries keyed by channel number."""
        return _serialize(self._driver.measure_all(alias=alias))

    @not_keyword
    def _assert_near(self, actual: float, expected: Any, tolerance: Any, quantity: str) -> None:
        target = _as_float(expected, name="expected")
        tol = _as_float(tolerance, name="tolerance")
        if tol < 0:
            raise ValueError("tolerance cannot be negative")
        error = abs(actual - target)
        if error > tol:
            raise AssertionError(
                f"{quantity} {actual:.12g} differs from expected {target:.12g} by {error:.12g}, exceeding tolerance {tol:.12g}"
            )

    @keyword("N6700 Voltage Should Be")
    def n6700_voltage_should_be(self, channel: Any, expected: Any, tolerance: Any = "0.1V", alias: str | None = None) -> float:
        """Measure voltage and fail unless it is within absolute tolerance."""
        actual = self.measure_n6700_voltage(channel, alias)
        self._assert_near(actual, expected, tolerance, "Voltage")
        return actual

    @keyword("N6700 Current Should Be")
    def n6700_current_should_be(self, channel: Any, expected: Any, tolerance: Any = "10mA", alias: str | None = None) -> float:
        """Measure current and fail unless it is within absolute tolerance."""
        actual = self.measure_n6700_current(channel, alias)
        self._assert_near(actual, expected, tolerance, "Current")
        return actual

    @keyword("N6700 Power Should Be")
    def n6700_power_should_be(self, channel: Any, expected: Any, tolerance: Any = "0.1W", alias: str | None = None) -> float:
        """Measure power and fail unless it is within absolute tolerance."""
        result = self._driver.measure_power(_as_channel(channel), alias=alias)
        if result.power_W is None:
            raise AssertionError("Power measurement is unavailable")
        self._assert_near(result.power_W, expected, tolerance, "Power")
        return result.power_W

    @keyword("N6700 Output Should Be")
    def n6700_output_should_be(self, channel: Any, expected: Any, alias: str | None = None) -> bool:
        """Fail unless output state matches the expected boolean."""
        actual = self.get_n6700_output_state(channel, alias)
        wanted = _as_bool(expected, name="expected")
        if actual != wanted:
            raise AssertionError(f"Output state was {actual}, expected {wanted}")
        return actual

    @keyword("Wait Until N6700 Voltage Is In Range")
    def wait_until_n6700_voltage_is_in_range(
        self,
        channel: Any,
        minimum: Any,
        maximum: Any,
        timeout: Any = "10s",
        poll_interval: Any = "200ms",
        alias: str | None = None,
    ) -> float:
        """Poll voltage until ``minimum <= value <= maximum`` or fail on timeout.

        Delegates to :meth:`keysight_n6700.N6700.wait_for_voltage_in_range`,
        which does the actual bounded polling; this keyword only converts
        Robot argument strings and the timeout exception.
        """
        low = _as_float(minimum, name="minimum")
        high = _as_float(maximum, name="maximum")
        if low > high:
            raise ValueError("minimum cannot be greater than maximum")
        timeout_s = _as_seconds(timeout)
        poll_s = _as_seconds(poll_interval, name="poll_interval")
        try:
            return self._driver.wait_for_voltage_in_range(
                _as_channel(channel), low, high, timeout_s=timeout_s, poll_interval_s=poll_s, alias=alias
            )
        except DriverTimeoutError as exc:
            raise AssertionError(str(exc)) from exc

    @keyword("Get N6700 Protection Status")
    def get_n6700_protection_status(self, channel: Any, alias: str | None = None) -> dict[str, Any]:
        """Return protection status for one channel."""
        return _serialize(self._driver.channel(_as_channel(channel), alias=alias).get_protection_status())

    @keyword("Clear N6700 Protection")
    def clear_n6700_protection(
        self,
        channel: Any,
        restore_output: Any = False,
        force_output_off_first: Any = True,
        verify_cleared: Any = True,
        alias: str | None = None,
    ) -> dict[str, Any]:
        """Safely clear channel protection and return before/after states."""
        result = self._driver.clear_protection(
            _as_channel(channel),
            restore_output=_as_bool(restore_output, name="restore_output"),
            force_output_off_first=_as_bool(force_output_off_first, name="force_output_off_first"),
            verify_cleared=_as_bool(verify_cleared, name="verify_cleared"),
            alias=alias,
        )
        return _serialize(result)

    @keyword("Shutdown All N6700 Channels")
    def shutdown_all_n6700_channels(self, alias: str | None = None) -> dict[str, Any]:
        """Attempt to disable every output/input and return per-channel results."""
        result = self._driver.shutdown_all(alias=alias)
        data = _serialize(result)
        data["success"] = result.success
        return data

    @keyword("Set N6700 SMU Mode")
    def set_n6700_smu_mode(self, channel: Any, mode: str, alias: str | None = None) -> str:
        """Set an SMU channel to ``voltage`` or ``current`` priority."""
        selected = str(mode).strip().lower()
        if selected not in {"voltage", "current"}:
            raise ValueError("mode must be voltage or current")
        self._driver.set_smu_mode(_as_channel(channel), selected, alias=alias)  # type: ignore[arg-type]
        self._post_write(alias)
        return selected

    @keyword("Get N6700 SMU Mode")
    def get_n6700_smu_mode(self, channel: Any, alias: str | None = None) -> str:
        """Return SMU priority mode."""
        return self._driver.get_smu_mode(_as_channel(channel), alias=alias)

    @keyword("Configure N6700 SMU Voltage Priority")
    def configure_n6700_smu_voltage_priority(
        self,
        channel: Any,
        voltage: Any,
        current_limit: Any,
        voltage_limit: Any | None = None,
        output: Any = False,
        alias: str | None = None,
    ) -> dict[str, Any]:
        """Configure an SMU in voltage-priority mode; output defaults off."""
        ch_num = _as_channel(channel)
        voltage_v = _as_float(voltage, name="voltage")
        current_a = _as_float(current_limit, name="current_limit")
        limit_v = None if voltage_limit is None or str(voltage_limit).strip() == "" else _as_float(voltage_limit, name="voltage_limit")
        enabled = _as_bool(output, name="output")
        self._driver.configure_smu_voltage_priority(
            ch_num, voltage_v, current_a, voltage_limit=limit_v, output=enabled, verify=self.strict_errors, alias=alias
        )
        return {
            "channel": ch_num,
            "mode": "voltage",
            "voltage_V": voltage_v,
            "current_limit_A": current_a,
            "voltage_limit_V": limit_v,
            "output_enabled": enabled,
        }

    @keyword("Configure N6700 SMU Current Priority")
    def configure_n6700_smu_current_priority(
        self, channel: Any, current: Any, voltage_limit: Any, output: Any = False, alias: str | None = None
    ) -> dict[str, Any]:
        """Configure an SMU in current-priority mode; output defaults off."""
        ch_num = _as_channel(channel)
        current_a = _as_float(current, name="current")
        limit_v = _as_float(voltage_limit, name="voltage_limit")
        enabled = _as_bool(output, name="output")
        self._driver.configure_smu_current_priority(
            ch_num, current_a, limit_v, output=enabled, verify=self.strict_errors, alias=alias
        )
        return {
            "channel": ch_num,
            "mode": "current",
            "current_A": current_a,
            "voltage_limit_V": limit_v,
            "output_enabled": enabled,
        }

    @keyword("Set N6700 Load Mode")
    def set_n6700_load_mode(self, channel: Any, mode: str, alias: str | None = None) -> str:
        """Set load mode to ``cc``, ``cv``, ``cr`` or ``cp``."""
        selected = str(mode).strip().lower()
        if selected not in {"cc", "cv", "cr", "cp"}:
            raise ValueError("mode must be cc, cv, cr, or cp")
        self._driver.set_load_mode(_as_channel(channel), selected, alias=alias)  # type: ignore[arg-type]
        self._post_write(alias)
        return selected

    @keyword("Get N6700 Load Mode")
    def get_n6700_load_mode(self, channel: Any, alias: str | None = None) -> str:
        """Return electronic-load mode."""
        return self._driver.get_load_mode(_as_channel(channel), alias=alias)

    @keyword("Set N6700 Load Input")
    def set_n6700_load_input(self, channel: Any, enabled: Any, alias: str | None = None) -> bool:
        """Enable or disable an electronic-load input."""
        state = _as_bool(enabled, name="enabled")
        self._driver.load(_as_channel(channel), alias=alias).set_input(state)
        self._post_write(alias)
        return state

    @keyword("Turn On N6700 Load Input")
    def turn_on_n6700_load_input(self, channel: Any, alias: str | None = None) -> None:
        """Enable an electronic-load input."""
        self.set_n6700_load_input(channel, True, alias)

    @keyword("Turn Off N6700 Load Input")
    def turn_off_n6700_load_input(self, channel: Any, alias: str | None = None) -> None:
        """Disable an electronic-load input."""
        self.set_n6700_load_input(channel, False, alias)

    @keyword("Get N6700 Load Input State")
    def get_n6700_load_input_state(self, channel: Any, alias: str | None = None) -> bool:
        """Return electronic-load input state."""
        return self._driver.load(_as_channel(channel), alias=alias).get_input()

    @keyword("Configure N6700 Load CC")
    def configure_n6700_load_cc(self, channel: Any, current: Any, input_on: Any = False, alias: str | None = None) -> dict[str, Any]:
        """Configure constant-current load mode; input defaults off."""
        ch_num = _as_channel(channel)
        current_a = _as_float(current, name="current")
        enabled = _as_bool(input_on, name="input_on")
        self._driver.configure_load_cc(ch_num, current_a, input_on=enabled, verify=self.strict_errors, alias=alias)
        return {"channel": ch_num, "mode": "cc", "current_A": current_a, "input_enabled": enabled}
