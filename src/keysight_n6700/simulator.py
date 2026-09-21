"""Protocol-aware no-hardware simulator for unit and conformance tests."""

from __future__ import annotations

import re
from dataclasses import dataclass

from scpi_driver_core.simulation.scripted import ScriptedScpiTransport
from scpi_driver_core.transport.models import TransportDescriptor

from .scpi import parse_channel_list


@dataclass
class _ChannelState:
    model: str
    serial: str
    option: str = ""
    enabled: bool = False
    voltage: float = 0.0
    current: float = 0.0
    current_limit: float = 1.0
    voltage_limit: float = 20.0
    voltage_range: float = 20.0
    current_range: float = 1.0
    min_voltage: float = 0.0
    max_voltage: float = 20.0
    min_current: float = 0.0
    max_current: float = 5.0
    ovp: float = 20.0
    remote_ovp: float = 20.0
    ocp: bool = False
    smu_mode: str = "VOLT"
    smu_off_mode: str = "HIGHZ"
    load_mode: str = "CC"
    load_current: float = 0.0
    load_voltage: float = 0.0
    load_resistance: float = 1.0
    load_power: float = 0.0
    resistance: float = 100.0
    power: float = 10.0
    load_current_limit: float = 1.0
    protection_active: bool = False
    questionable_status: int = 0
    operation_status: int = 0

    @property
    def is_smu(self) -> bool:
        return self.model in {"N6781A", "N6782A", "N6784A", "N6785A", "N6786A"}

    @property
    def is_real_load(self) -> bool:
        return self.model in {"N6791A", "N6792A"}


class SimN6700Instrument:
    """Small simulator with SCPI header-path and model-applicability checks."""

    def __init__(self, models: dict[int, str] | None = None) -> None:
        models = models or {1: "N6751A", 2: "N6781A", 3: "SIM_LOAD", 4: "N6731B"}
        self.channels: dict[int, _ChannelState] = {}
        for channel, model in models.items():
            state = _ChannelState(model=model.upper(), serial=f"SIM{channel:04d}")
            if state.is_smu:
                state.min_voltage = -20.0
                state.max_voltage = 20.0
                state.min_current = -3.0
                state.max_current = 3.0
                state.current_limit = 3.0
                state.current_range = 3.0
            elif state.model.startswith("N67"):
                state.min_voltage = 0.0
                state.max_voltage = 50.0
                state.min_current = 0.0
                state.max_current = 5.0
                state.voltage_range = 50.0
                state.current_range = 5.0
                state.ovp = 55.0
            self.channels[channel] = state
        self.errors: list[tuple[int, str]] = []
        self.remote_state = "LOC"
        self.watchdog_enabled = False
        self.watchdog_delay_s = 60.0
        self.idn = "KEYSIGHT TECHNOLOGIES,N6700B,SIM000001,B.00.00"

    def clear(self) -> None:
        self.errors.clear()

    def _push_error(self, code: int, message: str) -> None:
        self.errors.append((code, message))

    def _channels_from_command(self, command: str) -> list[int]:
        channels = parse_channel_list(command)
        return channels or [1]

    def _query_values(self, channels: list[int], getter: str) -> str:
        return ",".join(str(getattr(self.channels[channel], getter)) for channel in channels)

    @staticmethod
    def _expand_message(message: str) -> list[str]:
        """Apply the SCPI program-message header-path rule."""
        units: list[str] = []
        path = ""
        for raw in message.strip().split(";"):
            unit = raw.strip()
            if not unit:
                continue
            if unit.startswith("*"):
                units.append(unit)
                continue
            full = unit[1:] if unit.startswith(":") else path + unit
            header = full.split(None, 1)[0].rstrip("?")
            path = header[: header.rfind(":") + 1] if ":" in header else ""
            units.append(full)
        return units

    @staticmethod
    def _header(command: str) -> str:
        return command.split(None, 1)[0].upper()

    @staticmethod
    def _arguments(command: str) -> str:
        parts = command.split(None, 1)
        return parts[1].strip() if len(parts) == 2 else ""

    @staticmethod
    def _first_argument(command: str) -> str:
        return SimN6700Instrument._arguments(command).split(",", 1)[0].strip().upper()

    def _number_or_keyword(self, command: str, state: _ChannelState, attr: str) -> float:
        token = self._first_argument(command)
        if token == "MAX":
            if attr == "voltage_range":
                return state.max_voltage
            if attr == "current_range":
                return state.max_current
        if token == "MIN":
            if attr == "voltage_range":
                return state.max_voltage / 2.0
            if attr == "current_range":
                return state.max_current / 10.0
        if token == "DEF":
            return state.max_voltage if attr == "voltage_range" else state.max_current
        try:
            return float(token)
        except ValueError:
            self._push_error(-222, f"Data out of range: {token}")
            return getattr(state, attr)

    def _set_numeric(
        self,
        command: str,
        attr: str,
        *,
        minimum_attr: str | None = None,
        maximum_attr: str | None = None,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> None:
        token = self._first_argument(command)
        try:
            value = float(token)
        except ValueError:
            self._push_error(-222, f"Data out of range: {token}")
            return
        for channel in self._channels_from_command(command):
            state = self.channels[channel]
            low = getattr(state, minimum_attr) if minimum_attr else minimum
            high = getattr(state, maximum_attr) if maximum_attr else maximum
            if low is not None and value < low or high is not None and value > high:
                self._push_error(-222, f"Data out of range: {value}")
                continue
            setattr(state, attr, value)

    def execute(self, message: str) -> str | bytes | None:
        response: str | bytes | None = None
        for unit in self._expand_message(message):
            current = self._execute_one(unit)
            if current is not None:
                response = current
        return response

    def _execute_one(self, command: str) -> str | bytes | None:
        norm = " ".join(command.strip().split())
        upper = norm.upper()
        header = self._header(norm)

        if upper == "*IDN?":
            return self.idn
        if upper == "*OPT?":
            return "+0"
        if upper == "*CLS":
            self.errors.clear()
            return None
        if upper == "*RST":
            for state in self.channels.values():
                state.enabled = False
                state.voltage = 0.0
                state.current = 0.0
                state.current_limit = state.max_current
                state.protection_active = False
                state.questionable_status = 0
                state.smu_mode = "VOLT"
                state.smu_off_mode = "HIGHZ"
            return None
        if upper == "*OPC?":
            return "1"
        if upper == "*WAI":
            return None
        if upper == "*STB?":
            return "0"
        if upper == "*ESR?":
            return "0"
        if upper == "*TST?":
            return '0,"No error"'
        if upper == "*RDT?":
            return ";".join(f"CHAN{channel}:{state.model}" for channel, state in self.channels.items())

        if header in {"SYST:ERR?", "SYSTEM:ERROR?"}:
            if self.errors:
                code, message = self.errors.pop(0)
                return f'{code},"{message}"'
            return '0,"No error"'
        if header in {"SYST:CHAN:COUN?", "SYSTEM:CHANNEL:COUNT?"}:
            return str(len(self.channels))
        if header in {"SYST:CHAN:MOD?", "SYSTEM:CHANNEL:MODEL?"}:
            channel = self._channels_from_command(norm)[0]
            return self.channels[channel].model
        if header in {"SYST:CHAN:OPT?", "SYSTEM:CHANNEL:OPTION?"}:
            channel = self._channels_from_command(norm)[0]
            return self.channels[channel].option or "0"
        if header in {"SYST:CHAN:SER?", "SYSTEM:CHANNEL:SERIAL?"}:
            channel = self._channels_from_command(norm)[0]
            return self.channels[channel].serial

        if header == "SYST:COMM:RLST?":
            return self.remote_state
        if header == "SYST:COMM:RLST":
            token = self._first_argument(norm)
            if token.startswith("LOC"):
                self.remote_state = "LOC"
            elif token.startswith("REM"):
                self.remote_state = "REM"
            elif token.startswith("RWL"):
                self.remote_state = "RWL"
            else:
                self._push_error(-222, f"Data out of range: {token}")
            return None

        if header == "OUTP:PROT:WDOG?":
            return "1" if self.watchdog_enabled else "0"
        if header == "OUTP:PROT:WDOG":
            token = self._first_argument(norm)
            self.watchdog_enabled = token in {"1", "ON"}
            return None
        if header == "OUTP:PROT:WDOG:DEL?":
            return str(self.watchdog_delay_s)
        if header == "OUTP:PROT:WDOG:DEL":
            try:
                value = float(self._first_argument(norm))
            except ValueError:
                self._push_error(-222, "Data out of range")
                return None
            if not 1.0 <= value <= 3600.0:
                self._push_error(-222, "Data out of range")
            else:
                self.watchdog_delay_s = value
            return None

        if header in {"OUTP:PROT:CLE", "OUTPUT:PROTECTION:CLEAR"}:
            for channel in self._channels_from_command(norm):
                state = self.channels[channel]
                state.protection_active = False
                state.questionable_status = 0
            return None
        if header in {"STAT:QUES:COND?", "STATUS:QUESTIONABLE:CONDITION?"}:
            return self._query_values(self._channels_from_command(norm), "questionable_status")
        if header in {"STAT:OPER:COND?", "STATUS:OPERATION:CONDITION?"}:
            return self._query_values(self._channels_from_command(norm), "operation_status")

        if header in {"OUTP:TMOD?", "OUTPUT:TMODE?"}:
            channel = self._channels_from_command(norm)[0]
            state = self.channels[channel]
            if not state.is_smu:
                self._push_error(-113, f"Undefined header: {command}")
                return ""
            return state.smu_off_mode
        if header in {"OUTP:TMOD", "OUTPUT:TMODE"}:
            token = self._first_argument(norm)
            for channel in self._channels_from_command(norm):
                state = self.channels[channel]
                if not state.is_smu:
                    self._push_error(-113, f"Undefined header: {command}")
                    continue
                if token not in {"HIGHZ", "LOWZ"}:
                    self._push_error(-222, f"Data out of range: {token}")
                    continue
                state.smu_off_mode = token
            return None

        if header in {"OUTP?", "OUTPUT?"}:
            return ",".join(
                "1" if self.channels[channel].enabled else "0"
                for channel in self._channels_from_command(norm)
            )
        if header in {"OUTP", "OUTPUT"}:
            token = self._first_argument(norm)
            if token not in {"ON", "OFF", "1", "0"}:
                self._push_error(-222, f"Data out of range: {token}")
                return None
            enabled = token in {"ON", "1"}
            for channel in self._channels_from_command(norm):
                self.channels[channel].enabled = enabled
            return None

        if header == "FUNC?":
            channel = self._channels_from_command(norm)[0]
            state = self.channels[channel]
            if state.is_smu:
                return state.smu_mode
            if state.is_real_load:
                return state.load_mode
            self._push_error(-113, f"Undefined header: {command}")
            return ""
        if header == "FUNC":
            token = self._first_argument(norm)
            for channel in self._channels_from_command(norm):
                state = self.channels[channel]
                if state.is_smu and token in {"VOLT", "VOLTAGE", "CURR", "CURRENT"}:
                    state.smu_mode = "CURR" if token.startswith("CURR") else "VOLT"
                elif state.is_real_load and token[:3] in {"VOL", "CUR", "RES", "POW"}:
                    state.load_mode = token[:3]
                else:
                    self._push_error(-222 if state.is_smu or state.is_real_load else -113, f"Invalid FUNC: {token}")
            return None

        if header == "SIM:LOAD:INP?":
            return ",".join(
                "1" if self.channels[channel].enabled else "0"
                for channel in self._channels_from_command(norm)
            )
        if header == "SIM:LOAD:INP":
            token = self._first_argument(norm)
            enabled = token in {"ON", "1"}
            for channel in self._channels_from_command(norm):
                self.channels[channel].enabled = enabled
            return None
        if header == "SIM:LOAD:MODE?":
            channel = self._channels_from_command(norm)[0]
            return self.channels[channel].load_mode
        if header == "SIM:LOAD:MODE":
            token = self._first_argument(norm)
            if token not in {"CC", "CV", "CR", "CP"}:
                self._push_error(-222, f"Data out of range: {token}")
            else:
                self.channels[self._channels_from_command(norm)[0]].load_mode = token
            return None
        for name, attr in (
            ("SIM:LOAD:CURR", "load_current"),
            ("SIM:LOAD:VOLT", "load_voltage"),
            ("SIM:LOAD:RES", "load_resistance"),
            ("SIM:LOAD:POW", "load_power"),
        ):
            if header == name + "?":
                return self._query_values(self._channels_from_command(norm), attr)
            if header == name:
                self._set_numeric(norm, attr, minimum=0.0)
                return None

        if header in {"VOLT:RANG?", "VOLTAGE:RANGE?", "CURR:RANG?", "CURRENT:RANGE?"}:
            is_voltage = header.startswith("VOLT")
            attr = "voltage_range" if is_voltage else "current_range"
            token = self._first_argument(norm)
            values: list[str] = []
            for channel in self._channels_from_command(norm):
                state = self.channels[channel]
                if token == "MIN":
                    value = state.max_voltage / 2.0 if is_voltage else state.max_current / 10.0
                elif token == "MAX":
                    value = state.max_voltage if is_voltage else state.max_current
                else:
                    value = getattr(state, attr)
                values.append(str(value))
            return ",".join(values)
        if header in {"VOLT:RANG", "VOLTAGE:RANGE", "CURR:RANG", "CURRENT:RANGE"}:
            attr = "voltage_range" if header.startswith("VOLT") else "current_range"
            for channel in self._channels_from_command(norm):
                state = self.channels[channel]
                setattr(state, attr, self._number_or_keyword(norm, state, attr))
            return None

        if header in {"VOLT:PROT:REM?", "VOLTAGE:PROTECTION:REMOTE?"}:
            channel = self._channels_from_command(norm)[0]
            state = self.channels[channel]
            if not state.is_smu:
                self._push_error(-113, f"Undefined header: {command}")
                return ""
            return str(state.remote_ovp)
        if header in {"VOLT:PROT:REM", "VOLTAGE:PROTECTION:REMOTE"}:
            for channel in self._channels_from_command(norm):
                state = self.channels[channel]
                if not state.is_smu:
                    self._push_error(-113, f"Undefined header: {command}")
                    continue
                token = self._first_argument(norm)
                try:
                    state.remote_ovp = float(token)
                except ValueError:
                    self._push_error(-222, f"Data out of range: {token}")
            return None

        if header in {"VOLT:PROT?", "VOLTAGE:PROTECTION?"}:
            channel = self._channels_from_command(norm)[0]
            state = self.channels[channel]
            if state.is_smu:
                self._push_error(-113, f"Undefined header: {command}")
                return ""
            return str(state.ovp)
        if header in {"VOLT:PROT", "VOLTAGE:PROTECTION"}:
            for channel in self._channels_from_command(norm):
                state = self.channels[channel]
                if state.is_smu:
                    self._push_error(-113, f"Undefined header: {command}")
                    continue
                token = self._first_argument(norm)
                try:
                    state.ovp = float(token)
                except ValueError:
                    self._push_error(-222, f"Data out of range: {token}")
            return None

        if header == "VOLT:LIM?":
            channel = self._channels_from_command(norm)[0]
            state = self.channels[channel]
            if not state.is_smu:
                self._push_error(-113, f"Undefined header: {command}")
                return ""
            return str(state.voltage_limit)
        if header == "VOLT:LIM":
            for channel in self._channels_from_command(norm):
                state = self.channels[channel]
                if not state.is_smu:
                    self._push_error(-113, f"Undefined header: {command}")
                    continue
                self._set_numeric(norm, "voltage_limit", minimum=0.0, maximum_attr="max_voltage")
            return None

        if header in {"CURR:PROT:STAT?", "CURRENT:PROTECTION:STATE?"}:
            channel = self._channels_from_command(norm)[0]
            return "1" if self.channels[channel].ocp else "0"
        if header in {"CURR:PROT:STAT", "CURRENT:PROTECTION:STATE"}:
            token = self._first_argument(norm)
            enabled = token in {"ON", "1"}
            for channel in self._channels_from_command(norm):
                self.channels[channel].ocp = enabled
            return None
        if header.startswith("CURR:PROT") or header.startswith("CURRENT:PROTECTION"):
            self._push_error(-113, f"Undefined header: {command}")
            return None

        if header in {"CURR:LIM?", "CURRENT:LIMIT?"}:
            channel = self._channels_from_command(norm)[0]
            state = self.channels[channel]
            attr = "current_limit" if state.is_smu else "load_current_limit"
            return self._query_values(self._channels_from_command(norm), attr)
        if header in {"CURR:LIM", "CURRENT:LIMIT"}:
            for channel in self._channels_from_command(norm):
                state = self.channels[channel]
                attr = "current_limit" if state.is_smu else "load_current_limit"
                maximum = state.max_current if state.is_smu else None
                self._set_numeric(norm, attr, minimum=0.0, maximum=maximum)
            return None

        if header in {"VOLT?", "VOLTAGE?", "CURR?", "CURRENT?"}:
            is_voltage = header.startswith("VOLT")
            token = self._first_argument(norm)
            values: list[str] = []
            for channel in self._channels_from_command(norm):
                state = self.channels[channel]
                if token == "MIN":
                    value = state.min_voltage if is_voltage else state.min_current
                elif token == "MAX":
                    value = state.max_voltage if is_voltage else state.max_current
                else:
                    value = state.voltage if is_voltage else (state.current if state.is_smu else state.current_limit)
                values.append(str(value))
            return ",".join(values)

        if header in {"VOLT", "VOLTAGE"}:
            self._set_numeric(norm, "voltage", minimum_attr="min_voltage", maximum_attr="max_voltage")
            return None
        if header in {"CURR", "CURRENT"}:
            for channel in self._channels_from_command(norm):
                state = self.channels[channel]
                attr = "current" if state.is_smu else "current_limit"
                low = state.min_current if state.is_smu else 0.0
                high = state.max_current
                self._set_numeric(norm, attr, minimum=low, maximum=high)
            return None
        if header in {"RES?", "RESISTANCE?"}:
            return self._query_values(self._channels_from_command(norm), "resistance")
        if header in {"POW?", "POWER?"}:
            return self._query_values(self._channels_from_command(norm), "power")
        if header in {"RES", "RESISTANCE"}:
            self._set_numeric(norm, "resistance", minimum=0.0)
            return None
        if header in {"POW", "POWER"}:
            self._set_numeric(norm, "power", minimum=0.0)
            return None

        if header.startswith("MEAS:") or header.startswith("FETC:") or header.startswith("FETCH:"):
            channels = self._channels_from_command(norm)
            values: list[str] = []
            for channel in channels:
                state = self.channels[channel]
                active = state.enabled and not state.protection_active
                if "VOLT" in header:
                    if state.model == "SIM_LOAD":
                        value = state.load_voltage if active else 0.0
                    else:
                        value = state.voltage if active else 0.0
                elif "CURR" in header:
                    if state.model == "SIM_LOAD":
                        value = state.load_current if active else 0.0
                    elif state.is_smu and state.smu_mode == "CURR":
                        value = state.current if active else 0.0
                    else:
                        value = min(state.current_limit, abs(state.voltage) / 10.0) if active else 0.0
                elif "POW" in header:
                    if state.model == "SIM_LOAD":
                        value = (state.load_power or state.load_voltage * state.load_current) if active else 0.0
                    elif state.model.startswith(("N676", "N678", "N679")):
                        current = min(state.current_limit, abs(state.voltage) / 10.0) if active else 0.0
                        value = state.voltage * current if active else 0.0
                    else:
                        self._push_error(310, "The command is not supported by this model")
                        return ""
                else:
                    self._push_error(-113, f"Undefined header: {command}")
                    return ""
                values.append(str(value))
            return ",".join(values)

        self._push_error(-113, f"Undefined header: {command}")
        # Unknown queries deliberately queue no reply. Scripted transport then
        # times out, matching real SCPI behavior instead of returning "".
        return None


_DISABLE_BUILTIN_ERROR_QUEUE = "\x00__n6700_simulator_disabled_error_query__\x00"


def build_simulated_transport(models: dict[int, str] | None = None) -> ScriptedScpiTransport:
    instrument = SimN6700Instrument(models)
    transport = ScriptedScpiTransport(
        descriptor=TransportDescriptor(kind="simulated", address="sim://n6700"),
        error_query=_DISABLE_BUILTIN_ERROR_QUEUE,
        unknown_command_error=None,
    )
    transport.on_predicate(lambda _command: True, lambda command: instrument.execute(command))
    transport.instrument = instrument  # type: ignore[attr-defined]
    return transport
