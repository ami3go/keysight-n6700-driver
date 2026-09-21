"""Protocol-aware no-hardware simulator for unit and conformance tests."""

from __future__ import annotations

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
    ocp_delay_s: float = 0.02
    output_rise_delay_s: float = 0.0
    output_fall_delay_s: float = 0.0
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

    @property
    def is_sim_load(self) -> bool:
        return self.model == "SIM_LOAD"


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
            elif state.is_real_load:
                state.load_mode = "CURR"
                state.max_voltage = 60.0
                state.max_current = 20.0
                state.load_current_limit = 20.0
            elif state.model.startswith("N67"):
                state.min_voltage = 0.0
                state.max_voltage = 50.0
                state.min_current = 0.0
                state.max_current = 5.0
                state.voltage_range = 50.0
                state.current_range = 5.0
                state.current_limit = 5.0
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

    def _query_values(self, channels: list[int], attribute: str) -> str:
        return ",".join(str(getattr(self.channels[channel], attribute)) for channel in channels)

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

    def _range_value(self, command: str, state: _ChannelState, attribute: str) -> float:
        argument = self._first_argument(command)
        if argument == "MAX":
            return state.max_voltage if attribute == "voltage_range" else state.max_current
        if argument == "MIN":
            return state.max_voltage / 2.0 if attribute == "voltage_range" else state.max_current / 10.0
        if argument == "DEF":
            return state.max_voltage if attribute == "voltage_range" else state.max_current
        try:
            return float(argument)
        except ValueError:
            self._push_error(-222, f"Data out of range: {argument}")
            return float(getattr(state, attribute))

    def _set_numeric(
        self,
        command: str,
        attribute: str,
        *,
        minimum_attribute: str | None = None,
        maximum_attribute: str | None = None,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> None:
        argument = self._first_argument(command)
        try:
            value = float(argument)
        except ValueError:
            self._push_error(-222, f"Data out of range: {argument}")
            return
        for channel in self._channels_from_command(command):
            state = self.channels[channel]
            low = getattr(state, minimum_attribute) if minimum_attribute else minimum
            high = getattr(state, maximum_attribute) if maximum_attribute else maximum
            if (low is not None and value < low) or (high is not None and value > high):
                self._push_error(-222, f"Data out of range: {value}")
                continue
            setattr(state, attribute, value)

    def _level_attribute(self, state: _ChannelState, subsystem: str) -> str:
        if state.is_real_load:
            return {
                "VOLT": "load_voltage",
                "CURR": "load_current",
                "RES": "load_resistance",
                "POW": "load_power",
            }[subsystem]
        if subsystem == "CURR" and not state.is_smu:
            return "current_limit"
        return {
            "VOLT": "voltage",
            "CURR": "current",
            "RES": "resistance",
            "POW": "power",
        }[subsystem]

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
            return ";".join(
                f"CHAN{channel}:{state.model}" for channel, state in self.channels.items()
            )

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
            argument = self._first_argument(norm)
            if argument.startswith("LOC"):
                self.remote_state = "LOC"
            elif argument.startswith("REM"):
                self.remote_state = "REM"
            elif argument.startswith("RWL"):
                self.remote_state = "RWL"
            else:
                self._push_error(-222, f"Data out of range: {argument}")
            return None

        if header == "OUTP:PROT:WDOG?":
            return "1" if self.watchdog_enabled else "0"
        if header == "OUTP:PROT:WDOG":
            argument = self._first_argument(norm)
            if argument not in {"0", "1", "OFF", "ON"}:
                self._push_error(-222, f"Data out of range: {argument}")
            else:
                self.watchdog_enabled = argument in {"1", "ON"}
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

        if header in {"OUTP:DEL:RISE?", "OUTPUT:DELAY:RISE?"}:
            return self._query_values(self._channels_from_command(norm), "output_rise_delay_s")
        if header in {"OUTP:DEL:FALL?", "OUTPUT:DELAY:FALL?"}:
            return self._query_values(self._channels_from_command(norm), "output_fall_delay_s")
        if header in {"OUTP:DEL:RISE", "OUTPUT:DELAY:RISE"}:
            self._set_numeric(norm, "output_rise_delay_s", minimum=0.0)
            return None
        if header in {"OUTP:DEL:FALL", "OUTPUT:DELAY:FALL"}:
            self._set_numeric(norm, "output_fall_delay_s", minimum=0.0)
            return None

        if header in {"OUTP:TMOD?", "OUTPUT:TMODE?"}:
            channel = self._channels_from_command(norm)[0]
            state = self.channels[channel]
            if not state.is_smu:
                self._push_error(-113, f"Undefined header: {command}")
                return ""
            return state.smu_off_mode
        if header in {"OUTP:TMOD", "OUTPUT:TMODE"}:
            argument = self._first_argument(norm)
            for channel in self._channels_from_command(norm):
                state = self.channels[channel]
                if not state.is_smu:
                    self._push_error(-113, f"Undefined header: {command}")
                    continue
                if argument not in {"HIGHZ", "LOWZ"}:
                    self._push_error(-222, f"Data out of range: {argument}")
                    continue
                state.smu_off_mode = argument
            return None

        if header in {"OUTP?", "OUTPUT?"}:
            return ",".join(
                "1" if self.channels[channel].enabled else "0"
                for channel in self._channels_from_command(norm)
            )
        if header in {"OUTP", "OUTPUT"}:
            argument = self._first_argument(norm)
            if argument not in {"ON", "OFF", "1", "0"}:
                self._push_error(-222, f"Data out of range: {argument}")
                return None
            enabled = argument in {"ON", "1"}
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
            argument = self._first_argument(norm)
            for channel in self._channels_from_command(norm):
                state = self.channels[channel]
                if state.is_smu and argument in {"VOLT", "VOLTAGE", "CURR", "CURRENT"}:
                    state.smu_mode = "CURR" if argument.startswith("CURR") else "VOLT"
                elif state.is_real_load:
                    if argument.startswith("CURR"):
                        state.load_mode = "CURR"
                    elif argument.startswith("VOLT"):
                        state.load_mode = "VOLT"
                    elif argument.startswith("RES"):
                        state.load_mode = "RES"
                    elif argument.startswith("POW"):
                        state.load_mode = "POW"
                    else:
                        self._push_error(-222, f"Invalid FUNC: {argument}")
                else:
                    self._push_error(-113, f"Undefined header: {command}")
            return None

        if header == "SIM:LOAD:INP?":
            return ",".join(
                "1" if self.channels[channel].enabled else "0"
                for channel in self._channels_from_command(norm)
            )
        if header == "SIM:LOAD:INP":
            argument = self._first_argument(norm)
            if argument not in {"ON", "OFF", "1", "0"}:
                self._push_error(-222, f"Data out of range: {argument}")
            else:
                enabled = argument in {"ON", "1"}
                for channel in self._channels_from_command(norm):
                    self.channels[channel].enabled = enabled
            return None
        if header == "SIM:LOAD:MODE?":
            channel = self._channels_from_command(norm)[0]
            return self.channels[channel].load_mode
        if header == "SIM:LOAD:MODE":
            argument = self._first_argument(norm)
            if argument not in {"CC", "CV", "CR", "CP"}:
                self._push_error(-222, f"Data out of range: {argument}")
            else:
                self.channels[self._channels_from_command(norm)[0]].load_mode = argument
            return None
        for name, attribute in (
            ("SIM:LOAD:CURR", "load_current"),
            ("SIM:LOAD:VOLT", "load_voltage"),
            ("SIM:LOAD:RES", "load_resistance"),
            ("SIM:LOAD:POW", "load_power"),
        ):
            if header == name + "?":
                return self._query_values(self._channels_from_command(norm), attribute)
            if header == name:
                self._set_numeric(norm, attribute, minimum=0.0)
                return None

        if header in {"VOLT:RANG?", "VOLTAGE:RANGE?", "CURR:RANG?", "CURRENT:RANGE?"}:
            is_voltage = header.startswith("VOLT")
            attribute = "voltage_range" if is_voltage else "current_range"
            argument = self._first_argument(norm)
            range_values: list[str] = []
            for channel in self._channels_from_command(norm):
                state = self.channels[channel]
                if argument == "MIN":
                    value = state.max_voltage / 2.0 if is_voltage else state.max_current / 10.0
                elif argument == "MAX":
                    value = state.max_voltage if is_voltage else state.max_current
                else:
                    value = getattr(state, attribute)
                range_values.append(str(value))
            return ",".join(range_values)
        if header in {"VOLT:RANG", "VOLTAGE:RANGE", "CURR:RANG", "CURRENT:RANGE"}:
            attribute = "voltage_range" if header.startswith("VOLT") else "current_range"
            for channel in self._channels_from_command(norm):
                state = self.channels[channel]
                setattr(state, attribute, self._range_value(norm, state, attribute))
            return None

        if header in {"VOLT:PROT:REM?", "VOLTAGE:PROTECTION:REMOTE?"}:
            channel = self._channels_from_command(norm)[0]
            state = self.channels[channel]
            if not state.is_smu:
                self._push_error(-113, f"Undefined header: {command}")
                return ""
            return str(state.remote_ovp)
        if header in {"VOLT:PROT:REM", "VOLTAGE:PROTECTION:REMOTE"}:
            argument = self._first_argument(norm)
            try:
                value = float(argument)
            except ValueError:
                self._push_error(-222, f"Data out of range: {argument}")
                return None
            for channel in self._channels_from_command(norm):
                state = self.channels[channel]
                if not state.is_smu:
                    self._push_error(-113, f"Undefined header: {command}")
                else:
                    state.remote_ovp = value
            return None

        if header in {"VOLT:PROT?", "VOLTAGE:PROTECTION?"}:
            channel = self._channels_from_command(norm)[0]
            state = self.channels[channel]
            if state.is_smu:
                self._push_error(-113, f"Undefined header: {command}")
                return ""
            return str(state.ovp)
        if header in {"VOLT:PROT", "VOLTAGE:PROTECTION"}:
            argument = self._first_argument(norm)
            try:
                value = float(argument)
            except ValueError:
                self._push_error(-222, f"Data out of range: {argument}")
                return None
            for channel in self._channels_from_command(norm):
                state = self.channels[channel]
                if state.is_smu:
                    self._push_error(-113, f"Undefined header: {command}")
                else:
                    state.ovp = value
            return None

        if header == "VOLT:LIM?":
            channel = self._channels_from_command(norm)[0]
            state = self.channels[channel]
            if not state.is_smu:
                self._push_error(-113, f"Undefined header: {command}")
                return ""
            return str(state.voltage_limit)
        if header == "VOLT:LIM":
            argument = self._first_argument(norm)
            try:
                value = float(argument)
            except ValueError:
                self._push_error(-222, f"Data out of range: {argument}")
                return None
            for channel in self._channels_from_command(norm):
                state = self.channels[channel]
                if not state.is_smu:
                    self._push_error(-113, f"Undefined header: {command}")
                elif value < 0.0 or value > state.max_voltage:
                    self._push_error(-222, f"Data out of range: {value}")
                else:
                    state.voltage_limit = value
            return None

        if header in {"CURR:PROT:STAT?", "CURRENT:PROTECTION:STATE?"}:
            channel = self._channels_from_command(norm)[0]
            return "1" if self.channels[channel].ocp else "0"
        if header in {"CURR:PROT:STAT", "CURRENT:PROTECTION:STATE"}:
            argument = self._first_argument(norm)
            if argument not in {"ON", "OFF", "1", "0"}:
                self._push_error(-222, f"Data out of range: {argument}")
            else:
                enabled = argument in {"ON", "1"}
                for channel in self._channels_from_command(norm):
                    self.channels[channel].ocp = enabled
            return None
        if header in {"CURR:PROT:DEL?", "CURRENT:PROTECTION:DELAY?"}:
            return self._query_values(self._channels_from_command(norm), "ocp_delay_s")
        if header in {"CURR:PROT:DEL", "CURRENT:PROTECTION:DELAY"}:
            self._set_numeric(norm, "ocp_delay_s", minimum=0.0, maximum=0.255)
            return None
        if header.startswith("CURR:PROT") or header.startswith("CURRENT:PROTECTION"):
            self._push_error(-113, f"Undefined header: {command}")
            return None

        if header in {"CURR:LIM?", "CURRENT:LIMIT?"}:
            channel = self._channels_from_command(norm)[0]
            state = self.channels[channel]
            if state.is_smu:
                return str(state.current_limit)
            if state.is_real_load:
                return str(state.load_current_limit)
            self._push_error(-113, f"Undefined header: {command}")
            return ""
        if header in {"CURR:LIM", "CURRENT:LIMIT"}:
            argument = self._first_argument(norm)
            try:
                value = float(argument)
            except ValueError:
                self._push_error(-222, f"Data out of range: {argument}")
                return None
            for channel in self._channels_from_command(norm):
                state = self.channels[channel]
                if state.is_smu:
                    if value < 0.0 or value > state.max_current:
                        self._push_error(-222, f"Data out of range: {value}")
                    else:
                        state.current_limit = value
                elif state.is_real_load:
                    if value < 0.0:
                        self._push_error(-222, f"Data out of range: {value}")
                    else:
                        state.load_current_limit = value
                else:
                    self._push_error(-113, f"Undefined header: {command}")
            return None

        if header in {"VOLT?", "VOLTAGE?", "CURR?", "CURRENT?"}:
            is_voltage = header.startswith("VOLT")
            subsystem = "VOLT" if is_voltage else "CURR"
            argument = self._first_argument(norm)
            level_values: list[str] = []
            for channel in self._channels_from_command(norm):
                state = self.channels[channel]
                if argument == "MIN":
                    value = state.min_voltage if is_voltage else state.min_current
                elif argument == "MAX":
                    value = state.max_voltage if is_voltage else state.max_current
                else:
                    value = getattr(state, self._level_attribute(state, subsystem))
                level_values.append(str(value))
            return ",".join(level_values)

        if header in {"VOLT", "VOLTAGE", "CURR", "CURRENT", "RES", "RESISTANCE", "POW", "POWER"}:
            if header.startswith("VOLT"):
                subsystem = "VOLT"
            elif header.startswith("CURR"):
                subsystem = "CURR"
            elif header.startswith("RES"):
                subsystem = "RES"
            else:
                subsystem = "POW"
            argument = self._first_argument(norm)
            try:
                value = float(argument)
            except ValueError:
                self._push_error(-222, f"Data out of range: {argument}")
                return None
            for channel in self._channels_from_command(norm):
                state = self.channels[channel]
                attribute = self._level_attribute(state, subsystem)
                if subsystem == "VOLT" and not state.is_real_load:
                    low, high = state.min_voltage, state.max_voltage
                elif subsystem == "CURR" and state.is_smu:
                    low, high = state.min_current, state.max_current
                elif subsystem == "CURR" and not state.is_real_load:
                    low, high = 0.0, state.max_current
                else:
                    low, high = 0.0, None
                if value < low or (high is not None and value > high):
                    self._push_error(-222, f"Data out of range: {value}")
                else:
                    setattr(state, attribute, value)
            return None

        if header in {"RES?", "RESISTANCE?", "POW?", "POWER?"}:
            subsystem = "RES" if header.startswith("RES") else "POW"
            return ",".join(
                str(
                    getattr(
                        self.channels[channel],
                        self._level_attribute(self.channels[channel], subsystem),
                    )
                )
                for channel in self._channels_from_command(norm)
            )

        if header.startswith("MEAS:") or header.startswith("FETC:") or header.startswith("FETCH:"):
            channels = self._channels_from_command(norm)
            measurement_values: list[str] = []
            for channel in channels:
                state = self.channels[channel]
                active = state.enabled and not state.protection_active
                if state.is_sim_load or state.is_real_load:
                    measured_voltage = state.load_voltage if active else 0.0
                    measured_current = state.load_current if active else 0.0
                    measured_power = (
                        state.load_power or measured_voltage * measured_current
                    ) if active else 0.0
                else:
                    measured_voltage = state.voltage if active else 0.0
                    if state.is_smu and state.smu_mode == "CURR":
                        measured_current = state.current if active else 0.0
                    else:
                        measured_current = (
                            min(state.current_limit, abs(state.voltage) / 10.0)
                            if active
                            else 0.0
                        )
                    measured_power = measured_voltage * measured_current

                if "VOLT" in header:
                    value = measured_voltage
                elif "CURR" in header:
                    value = measured_current
                elif "POW" in header:
                    if (
                        not state.model.startswith(("N676", "N678", "N679"))
                        and not state.is_sim_load
                    ):
                        self._push_error(310, "The command is not supported by this model")
                        return ""
                    value = measured_power
                else:
                    self._push_error(-113, f"Undefined header: {command}")
                    return ""
                measurement_values.append(str(value))
            return ",".join(measurement_values)

        self._push_error(-113, f"Undefined header: {command}")
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
