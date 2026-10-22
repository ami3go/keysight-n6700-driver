"""Protocol-aware no-hardware simulator for unit and conformance tests."""

from __future__ import annotations

import copy
import re
import struct
from dataclasses import dataclass, field
from typing import Any, ClassVar

from scpi_driver_core.scpi.binary_block import encode_definite_length_block
from scpi_driver_core.simulation.scripted import ScriptedScpiTransport
from scpi_driver_core.transport.models import TransportDescriptor

from .scpi import parse_channel_list

#: Repeat counts and slew rates report INFinity as 9.9E37.
_INFINITE = 9.9e37
_UNHANDLED = object()
_SHAPE_KEYWORDS = {
    "STEP": "STEP", "RAMP": "RAMP", "STA": "STA", "SIN": "SIN", "PULS": "PULS",
    "TRAP": "TRAP", "EXP": "EXP", "UDEF": "UDEF", "CDW": "CDW", "SEQ": "SEQ", "NONE": "NONE",
}
# Power-on parameter defaults that differ from 0 (Programmer's Reference).
_ARB_DEFAULTS = {
    "SIN:FREQ": (1.0,), "STA:NST": (10.0,), "EXP:TCON": (1.0,), "EXP:TIM": (1.0,),
    "RAMP:RTIM": (1.0,), "PULS:TOP:TIM": (1.0,), "STA:TIM": (1.0,), "TRAP:RTIM": (1.0,),
    "TRAP:TOP:TIM": (1.0,), "TRAP:FTIM": (1.0,), "UDEF:DWEL": (0.001,), "CDW:DWEL": (0.001,),
}


def _new_sequence_step(shape: str = "PULS") -> dict[str, Any]:
    return {"shape": shape, "pacing": "DWEL", "count": 1.0, "params": {}}


def _count(raw: str) -> float:
    value = raw.strip().upper()
    return _INFINITE if value in {"INF", "INFINITY", "MAX"} else int(float(value))


def _shape_keyword(raw: str) -> str | None:
    value = raw.strip().upper()
    return next((short for prefix, short in _SHAPE_KEYWORDS.items() if value.startswith(prefix)), None)


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
    operation_condition: int = 0
    # STAT:OPER:PTR filter (preset 127) and the latched, read-to-clear event register.
    operation_ptr: int = 127
    operation_event: int = 0
    sweep_points: int = 1
    sweep_interval_s: float = 20.48e-6
    measure_voltage_enabled: bool = False
    measure_current_enabled: bool = False
    sweep_offset_points: int = 0
    sweep_resolution: str = "RES20"
    acquisition_state: str = "idle"
    acquisition_generation: int = 0
    completed_generation: int | None = None
    voltage_slew_v_per_s: float = 9.9e37
    current_slew_a_per_s: float = 9.9e37
    voltage_mode: str = "FIX"
    current_mode: str = "FIX"
    # N679xA loads also run resistance/power LIST and ARB profiles.
    resistance_mode: str = "FIX"
    power_mode: str = "FIX"
    voltage_triggered: float = 0.0
    current_triggered: float = 0.0
    resistance_triggered: float = 0.0
    power_triggered: float = 0.0
    resistance_range: float = 8000.0
    power_range: float = 102.0
    # N679xA VOLT:INH:VON: the load does not sink below this input voltage.
    voltage_on_level: float = 0.01
    step_trigger_output: bool = False
    voltage_list: tuple[float, ...] = (0.0,)
    current_list: tuple[float, ...] = (1.0,)
    resistance_list: tuple[float, ...] = (1.0,)
    power_list: tuple[float, ...] = (1.0,)
    dwell_list: tuple[float, ...] = (0.001,)
    list_bost: tuple[float, ...] = (0.0,)
    list_eost: tuple[float, ...] = (0.0,)
    list_count: float = 1
    list_step: str = "AUTO"
    list_terminate_last: bool = False
    arb_type: str = "VOLT"
    arb_shape: str = "NONE"
    # "<VOLT|CURR>:<suffix>" -> values, e.g. "VOLT:UDEF:LEV" or "CURR:RAMP:RTIM".
    arb_values: dict[str, tuple[float, ...]] = field(default_factory=dict)
    arb_count: float = 1
    arb_terminate_last: bool = False
    sequence_steps: list[dict[str, Any]] = field(default_factory=lambda: [_new_sequence_step()])
    sequence_count: float = 1
    sequence_terminate_last: bool = False
    transient_trigger_source: str = "BUS"
    arb_trigger_source: str = "BUS"
    transient_state: str = "idle"
    transient_generation: int = 0
    transient_initial_voltage: float = 0.0
    transient_initial_current: float = 1.0
    transient_active_quantity: str = "VOLT"
    transient_step_once: bool = False
    transient_continuous: bool = False
    transient_steps_left: float = 0
    transient_polls_left: int = 0
    abort_polls_left: int = 0

    @property
    def operation_status(self) -> int:
        return self.operation_condition

    @operation_status.setter
    def operation_status(self, value: int) -> None:
        self.operation_event |= value & ~self.operation_condition & self.operation_ptr
        self.operation_condition = value

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

    def __init__(
        self,
        models: dict[int, str] | None = None,
        *,
        mainframe_model: str = "N6705C",
        sequence_step_base: int = 0,
    ) -> None:
        if sequence_step_base not in {0, 1}:
            raise ValueError("sequence_step_base must be 0 or 1")
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
        self.array_format = "ASC"
        self.array_border = "NORM"
        self.idn = f"KEYSIGHT TECHNOLOGIES,{mainframe_model.upper()},SIM000001,B.00.00"
        self.sequence_step_base = sequence_step_base
        # On the N6700C one TRIG:ARB:SOUR without a channel list gates every
        # ARB start; the channel-scoped form returns -108, and the IMMediate
        # default starts an ARB at INIT:TRAN (confirmed on E.02.09 hardware).
        self.mainframe_global_arb_trigger = mainframe_model.upper() == "N6700C"
        self.global_arb_trigger_source = "IMM"
        # Status polls a triggered AUTO profile stays TRAN_active; 0 completes
        # immediately on the trigger.
        self.transient_active_polls = 0
        # Status polls an aborted, initiated transient stays TRAN_active; real
        # hardware takes ~0.2 s and refuses MODE changes meanwhile (+308).
        self.abort_settle_polls = 0
        # Instrument file system for MMEM:STOR/LOAD:ARB:SEQ.
        self.files: dict[str, tuple[str, list[dict[str, Any]], float, bool]] = {}

    _MODE_ATTRIBUTES: ClassVar[dict[str, str]] = {
        "VOLT": "voltage_mode",
        "CURR": "current_mode",
        "RES": "resistance_mode",
        "POW": "power_mode",
    }
    _LIST_ATTRIBUTES: ClassVar[dict[str, str]] = {
        "VOLT": "voltage_list",
        "CURR": "current_list",
        "RES": "resistance_list",
        "POW": "power_list",
    }

    @classmethod
    def _active_mode(cls, st: _ChannelState) -> str:
        return str(getattr(st, cls._MODE_ATTRIBUTES[st.transient_active_quantity]))

    def _initiate_transient(self, st: _ChannelState) -> None:
        for quantity, attribute in self._MODE_ATTRIBUTES.items():
            if getattr(st, attribute) != "FIX":
                st.transient_active_quantity = quantity
                break
        else:
            self._push_error(-230, "No source LIST/ARB/STEP selected")
            return
        mode = self._active_mode(st)
        st.transient_generation += 1
        st.transient_initial_voltage = st.voltage
        st.transient_initial_current = st.current_limit
        st.transient_state = "waiting"
        st.transient_step_once = mode == "LIST" and st.list_step == "ONCE"
        if st.transient_step_once:
            levels = getattr(st, self._LIST_ATTRIBUTES[st.transient_active_quantity])
            st.transient_steps_left = len(levels) * st.list_count
        # TRAN_active stays set from INIT until the trigger system is idle.
        st.operation_status |= 16 | 64
        if mode == "ARB" and self.mainframe_global_arb_trigger and self.global_arb_trigger_source == "IMM":
            # IMMediate needs no trigger: the ARB runs as soon as it is initiated.
            self._finish_transient(st)

    @staticmethod
    def _transient_is_infinite(st: _ChannelState) -> bool:
        mode = SimN6700Instrument._active_mode(st)
        if mode == "LIST":
            return st.list_count >= _INFINITE
        if mode == "ARB" and st.arb_shape == "SEQ":
            return st.sequence_count >= _INFINITE or any(
                step["count"] >= _INFINITE for step in st.sequence_steps
            )
        if mode == "ARB":
            return st.arb_count >= _INFINITE
        return False

    def _finish_transient(self, st: _ChannelState) -> None:
        quantity = st.transient_active_quantity
        mode = self._active_mode(st)
        final: float | None = None
        if mode == "STEP":
            final = st.voltage_triggered if quantity == "VOLT" else st.current_triggered
        elif mode == "LIST" and st.list_terminate_last:
            final = getattr(st, self._LIST_ATTRIBUTES[quantity])[-1]
        elif mode == "ARB" and st.arb_shape == "UDEF" and st.arb_terminate_last:
            final = st.arb_values.get(f"{quantity}:UDEF:LEV", (None,))[-1]
        if quantity == "VOLT":
            st.voltage = st.transient_initial_voltage if final is None else final
        elif quantity == "CURR":
            st.current_limit = st.transient_initial_current if final is None else final
        elif final is not None:
            setattr(st, "resistance" if quantity == "RES" else "power", final)
        st.transient_state = "complete"
        st.operation_status &= ~(16 | 64)
        if st.transient_continuous:
            self._initiate_transient(st)

    def _sequence_step(self, st: _ChannelState, raw_index: str) -> dict[str, Any] | None:
        index = int(float(raw_index)) - self.sequence_step_base
        if not 0 <= index < len(st.sequence_steps):
            self._push_error(-222, "ARB sequence step does not exist")
            return None
        step: dict[str, Any] = st.sequence_steps[index]
        return step

    def _execute_arb(self, norm: str, upper: str) -> object:
        """ARB subsystem; returns :data:`_UNHANDLED` for unknown headers."""
        channels = self._channels_from_command(norm)
        st = self.channels[channels[0]]
        query = upper.split(None, 1)[0].endswith("?")
        arguments = self._argument_values(norm) if " " in norm else ()
        settings: list[tuple[str, Any]] = [
            ("ARB:FUNC:TYPE", "arb_type"),
            ("ARB:FUNC:SHAP", "arb_shape"),
            ("ARB:COUN", "arb_count"),
            ("ARB:TERM:LAST", "arb_terminate_last"),
            ("ARB:SEQ:COUN", "sequence_count"),
            ("ARB:SEQ:TERM:LAST", "sequence_terminate_last"),
        ]
        header = upper.split(None, 1)[0].rstrip("?")
        for command, attribute in settings:
            if header != command:
                continue
            if query:
                value = getattr(st, attribute)
                return ("1" if value else "0") if isinstance(value, bool) else str(value)
            raw = arguments[0]
            parsed: Any
            if attribute == "arb_type":
                parsed = next(
                    (name for name in ("CURR", "RES", "POW") if raw.upper().startswith(name)), "VOLT"
                )
            elif attribute == "arb_shape":
                parsed = _shape_keyword(raw)
                if parsed is None:
                    self._push_error(-222, "Invalid ARB shape")
                    return None
            elif attribute.endswith("terminate_last"):
                parsed = raw.upper() in {"ON", "1"}
            else:
                parsed = _count(raw)
            for ch in channels:
                setattr(self.channels[ch], attribute, parsed)
            return None
        if header == "ARB:SEQ:RES":
            for ch in channels:
                self.channels[ch].sequence_steps = [_new_sequence_step()]
            return None
        if header == "ARB:SEQ:LEN":
            return str(len(st.sequence_steps))
        if header == "ARB:SEQ:QUAL":
            fixed_points = 0
            smooth_shapes = 0
            for sequence_step in st.sequence_steps:
                shape = sequence_step["shape"]
                if shape in {"RAMP", "TRAP", "EXP", "SIN"}:
                    smooth_shapes += 1
                elif shape == "PULS":
                    fixed_points += 3
                elif shape == "STEP":
                    fixed_points += 2
                elif shape == "STA":
                    values = sequence_step["params"].get(
                        f"{st.arb_type}:STA:NST", (10.0,)
                    )
                    fixed_points += int(values[0]) + 1
                elif shape == "UDEF":
                    values = sequence_step["params"].get(
                        f"{st.arb_type}:UDEF:LEV", (0.0,)
                    )
                    fixed_points += len(values)
            if not smooth_shapes:
                return "100"
            return str(min(100, max(16, (511 - fixed_points) // smooth_shapes)))
        if header == "ARB:SEQ:STEP:FUNC:SHAP":
            if query:
                step = self._sequence_step(st, arguments[0])
                return "" if step is None else step["shape"]
            shape = _shape_keyword(arguments[0])
            index = int(float(arguments[1])) - self.sequence_step_base
            if shape is None or shape in {"CDW", "SEQ", "NONE"}:
                self._push_error(-222, "Invalid ARB sequence step shape")
            elif index == len(st.sequence_steps):
                st.sequence_steps.append(_new_sequence_step(shape))
            elif 0 <= index < len(st.sequence_steps):
                st.sequence_steps[index] = _new_sequence_step(shape)
            else:
                self._push_error(-222, "Sequence steps must be specified sequentially")
            return None
        for command, key in (("ARB:SEQ:STEP:PAC", "pacing"), ("ARB:SEQ:STEP:COUN", "count")):
            if header != command:
                continue
            step = self._sequence_step(st, arguments[-1])
            if step is None:
                return "" if query else None
            if query:
                return str(step[key])
            step[key] = ("TRIG" if arguments[0].upper().startswith("TRIG") else "DWEL") if key == "pacing" else _count(arguments[0])
            return None
        match = re.match(r"^ARB:SEQ:STEP:(VOLT|CURR):([A-Z:]+)$", header)
        if match:
            step = self._sequence_step(st, arguments[-1])
            if step is None:
                return "" if query else None
            key = f"{match.group(1)}:{match.group(2)}"
            if query:
                values = step["params"].get(key, _ARB_DEFAULTS.get(match.group(2), (0.0,)))
                return ",".join(str(value) for value in values)
            step["params"][key] = tuple(float(value) for value in arguments[:-1])
            return None
        # The resistance node has no short form: ARB:RES:... is undefined.
        match = re.match(r"^ARB:(VOLT|CURR|RESISTANCE|POW):([A-Z:]+)$", header)
        if match:
            key = f"{match.group(1).replace('RESISTANCE', 'RES')}:{match.group(2)}"
            if key.endswith(":POIN"):
                return str(len(st.arb_values.get(key[: -len(":POIN")], (0.0,))))
            if query:
                values = st.arb_values.get(key, _ARB_DEFAULTS.get(match.group(2), (0.0,)))
                return ",".join(str(value) for value in values)
            for ch in channels:
                self.channels[ch].arb_values[key] = tuple(float(value) for value in arguments)
            return None
        return _UNHANDLED

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

    @staticmethod
    def _range_bounds(state: _ChannelState, attribute: str) -> tuple[float, float]:
        """Return (minimum, maximum) selectable range for a range attribute."""
        current = float(getattr(state, attribute))
        if attribute == "voltage_range":
            return state.max_voltage / 2.0, state.max_voltage
        if attribute == "current_range":
            return state.max_current / 10.0, state.max_current
        # Resistance and power ranges have no model-independent envelope; keep
        # the simulator's configured range as the default and bracket it.
        return current / 10.0, current * 10.0

    def _range_value(self, command: str, state: _ChannelState, attribute: str) -> float:
        argument = self._first_argument(command)
        minimum, maximum = self._range_bounds(state, attribute)
        if argument == "MAX":
            return maximum
        if argument == "MIN":
            return minimum
        if argument == "DEF":
            return float(getattr(state, attribute))
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

    @staticmethod
    def _argument_values(command: str) -> tuple[str, ...]:
        payload = command.split(None, 1)[1]
        payload = re.sub(r",?\s*\(@[^)]*\)\s*$", "", payload)
        return tuple(value.strip() for value in payload.split(",") if value.strip())

    @classmethod
    def _float_arguments(cls, command: str) -> tuple[float, ...]:
        return tuple(float(value) for value in cls._argument_values(command))

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
            for st in self.channels.values():
                st.enabled = False
                st.voltage = 0.0
                st.current = 0.0
                st.current_limit = st.max_current
                st.protection_active = False
                st.questionable_status = 0
                st.smu_mode = "VOLT"
                st.smu_off_mode = "HIGHZ"
                st.operation_status &= ~(8 | 32)
                st.acquisition_state = "idle"
                st.completed_generation = None
                st.voltage_mode = "FIX"
                st.current_mode = "FIX"
                st.transient_continuous = False
                st.transient_state = "idle"
                st.operation_status &= ~(16 | 64)
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
        if upper.startswith("STAT:QUES:COND?") or upper.startswith("STATUS:QUESTIONABLE:CONDITION?"):
            channels = self._channels_from_command(norm)
            return ",".join(str(self.channels[ch].questionable_status) for ch in channels)
        if upper.startswith("STAT:OPER:COND?") or upper.startswith("STATUS:OPERATION:CONDITION?"):
            channels = self._channels_from_command(norm)
            for ch in channels:
                st = self.channels[ch]
                if st.abort_polls_left > 0:
                    st.abort_polls_left -= 1
                    if st.abort_polls_left <= 0:
                        st.operation_status &= ~64
                if st.transient_state == "active" and st.transient_polls_left > 0:
                    st.transient_polls_left -= 1
                    if st.transient_polls_left <= 0:
                        self._finish_transient(st)
            return ",".join(str(self.channels[ch].operation_status) for ch in channels)
        if upper.startswith("STAT:OPER:PTR?") or upper.startswith("STATUS:OPERATION:PTRANSITION?"):
            channels = self._channels_from_command(norm)
            return ",".join(str(self.channels[ch].operation_ptr) for ch in channels)
        if upper.startswith("STAT:OPER:PTR") or upper.startswith("STATUS:OPERATION:PTRANSITION"):
            ptr = int(float(self._argument_values(norm)[0]))
            for ch in self._channels_from_command(norm):
                self.channels[ch].operation_ptr = ptr
            return None
        if upper.startswith("STAT:OPER?") or upper.startswith("STAT:OPER:EVEN?") or upper.startswith(
            "STATUS:OPERATION?"
        ) or upper.startswith("STATUS:OPERATION:EVENT?"):
            channels = self._channels_from_command(norm)
            events = []
            for ch in channels:
                events.append(str(self.channels[ch].operation_event))
                self.channels[ch].operation_event = 0
            return ",".join(events)
        if upper.startswith("OUTP:PROT?") or upper.startswith("OUTPUT:PROTECTION?"):
            self._push_error(-113, f"Undefined header: {command}")
            return ""

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
            if state.is_real_load or state.is_sim_load:
                return state.load_mode
            self._push_error(-113, f"Undefined header: {command}")
            return ""
        if header == "FUNC":
            argument = self._first_argument(norm)
            for channel in self._channels_from_command(norm):
                state = self.channels[channel]
                if state.is_smu and argument in {"VOLT", "VOLTAGE", "CURR", "CURRENT"}:
                    state.smu_mode = "CURR" if argument.startswith("CURR") else "VOLT"
                elif state.is_real_load or state.is_sim_load:
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
        for quantity, attribute in (
            ("VOLT", "voltage_range"),
            ("CURR", "current_range"),
            ("RES", "resistance_range"),
            ("POW", "power_range"),
        ):
            if header in {f"{quantity}:RANG?", f"{quantity}:RANGE?"}:
                argument = self._first_argument(norm)
                range_values: list[str] = []
                for channel in self._channels_from_command(norm):
                    state = self.channels[channel]
                    minimum, maximum = self._range_bounds(state, attribute)
                    if argument == "MIN":
                        value = minimum
                    elif argument == "MAX":
                        value = maximum
                    else:
                        value = getattr(state, attribute)
                    range_values.append(str(value))
                return ",".join(range_values)
            if header in {f"{quantity}:RANG", f"{quantity}:RANGE"}:
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

        for name, attr in (
            ("VOLT:INH:VON", "voltage_on_level"),
            ("VOLT:LIM", "voltage_limit"),
            ("VOLTAGE:LIMIT", "voltage_limit"),
        ):
            if upper.startswith(name + "?"):
                channels = self._channels_from_command(norm)
                return self._query_values(channels, attr)
            if upper.startswith(name):
                value = float(norm.split(None, 1)[1].split(",")[0])
                for ch in self._channels_from_command(norm):
                    setattr(self.channels[ch], attr, value)
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
        if header in {"CURR:LIM?", "CURRENT:LIMIT?"}:
            channel = self._channels_from_command(norm)[0]
            state = self.channels[channel]
            if state.is_smu:
                return str(state.current_limit)
            if state.is_real_load or state.is_sim_load:
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
            for ch in self._channels_from_command(norm):
                st = self.channels[ch]
                if st.is_smu:
                    if value < 0.0 or value > st.max_current:
                        self._push_error(-222, f"Data out of range: {value}")
                    else:
                        st.current_limit = value
                elif st.is_real_load or st.is_sim_load:
                    if st.model.startswith("N679") and st.load_mode != "VOLT":
                        # Confirmed on an N6791A: CURR:LIM applies in voltage priority only.
                        self._push_error(
                            315,
                            f"Settings conflict error; chan {ch} must be in voltage priority mode",
                        )
                        continue
                    if value < 0.0:
                        self._push_error(-222, f"Data out of range: {value}")
                    else:
                        st.load_current_limit = value
                else:
                    self._push_error(-113, f"Undefined header: {command}")
            return None
        # Source LIST/ARB/STEP/slew and the output transient trigger system.
        # These branches intentionally precede the generic VOLT/CURR handlers.
        for quantity, slew_attribute in (("VOLT", "voltage_slew_v_per_s"), ("CURR", "current_slew_a_per_s")):
            if upper.startswith(f"{quantity}:SLEW:MAX?"):
                st = self.channels[self._channels_from_command(norm)[0]]
                return "1" if getattr(st, slew_attribute) >= _INFINITE else "0"
            if upper.startswith(f"{quantity}:SLEW:MAX"):
                enabled = self._argument_values(norm)[0].upper() in {"ON", "1"}
                for ch in self._channels_from_command(norm):
                    if enabled:
                        setattr(self.channels[ch], slew_attribute, _INFINITE)
                return None
            if upper.startswith(f"{quantity}:SLEW?"):
                return self._query_values(self._channels_from_command(norm), slew_attribute)
            if upper.startswith(f"{quantity}:SLEW"):
                raw = self._argument_values(norm)[0].upper()
                value = _INFINITE if raw in {"INF", "INFINITY", "MAX"} else float(raw)
                for ch in self._channels_from_command(norm):
                    setattr(self.channels[ch], slew_attribute, value)
                return None
        for quantity, mode_attribute, triggered_attribute in (
            ("VOLT", "voltage_mode", "voltage_triggered"),
            ("CURR", "current_mode", "current_triggered"),
            ("RES", "resistance_mode", "resistance_triggered"),
            ("POW", "power_mode", "power_triggered"),
        ):
            if upper.startswith(f"{quantity}:MODE?"):
                return self._query_values(self._channels_from_command(norm), mode_attribute)
            if upper.startswith(f"{quantity}:MODE"):
                if any(self.channels[ch].abort_polls_left > 0 for ch in self._channels_from_command(norm)):
                    self._push_error(308, "Cannot change while transient trigger is initiated")
                    return None
                mode = self._argument_values(norm)[0].upper()
                if mode not in {"FIX", "FIXED", "LIST", "ARB", "STEP"}:
                    self._push_error(-222, "Invalid transient mode")
                else:
                    normalized_mode = "FIX" if mode == "FIXED" else mode
                    for ch in self._channels_from_command(norm):
                        setattr(self.channels[ch], mode_attribute, normalized_mode)
                return None
            if upper.startswith(f"{quantity}:TRIG?"):
                return self._query_values(self._channels_from_command(norm), triggered_attribute)
            if upper.startswith(f"{quantity}:TRIG"):
                value = float(self._argument_values(norm)[0])
                for ch in self._channels_from_command(norm):
                    setattr(self.channels[ch], triggered_attribute, value)
                return None
        if upper.startswith("STEP:TOUT?"):
            st = self.channels[self._channels_from_command(norm)[0]]
            return "1" if st.step_trigger_output else "0"
        if upper.startswith("STEP:TOUT"):
            step_output = self._argument_values(norm)[0].upper() in {"ON", "1"}
            for ch in self._channels_from_command(norm):
                self.channels[ch].step_trigger_output = step_output
            return None
        for list_header, attribute in (
            ("LIST:VOLT", "voltage_list"),
            ("LIST:CURR", "current_list"),
            ("LIST:RES", "resistance_list"),
            ("LIST:POW", "power_list"),
            ("LIST:DWEL", "dwell_list"),
            ("LIST:TOUT:BOST", "list_bost"),
            ("LIST:TOUT:EOST", "list_eost"),
        ):
            if upper.startswith(list_header + ":POIN?"):
                profile_values = getattr(
                    self.channels[self._channels_from_command(norm)[0]], attribute
                )
                return str(len(profile_values))
            if upper.startswith(list_header + "?"):
                profile_values = getattr(
                    self.channels[self._channels_from_command(norm)[0]], attribute
                )
                return ",".join(str(value) for value in profile_values)
            if upper.startswith(list_header):
                profile_values = self._float_arguments(norm)
                for ch in self._channels_from_command(norm):
                    setattr(self.channels[ch], attribute, profile_values)
                return None
        if upper.startswith("LIST:COUN?"):
            return self._query_values(self._channels_from_command(norm), "list_count")
        if upper.startswith("LIST:COUN"):
            list_count_value = _count(self._argument_values(norm)[0])
            for ch in self._channels_from_command(norm):
                self.channels[ch].list_count = list_count_value
            return None
        if upper.startswith("LIST:STEP?"):
            return self._query_values(self._channels_from_command(norm), "list_step")
        if upper.startswith("LIST:STEP"):
            list_step_value = self._argument_values(norm)[0].upper()
            for ch in self._channels_from_command(norm):
                self.channels[ch].list_step = list_step_value
            return None
        if upper.startswith("LIST:TERM:LAST?"):
            st = self.channels[self._channels_from_command(norm)[0]]
            return "1" if st.list_terminate_last else "0"
        if upper.startswith("LIST:TERM:LAST"):
            list_terminate_last = self._argument_values(norm)[0].upper() in {"ON", "1"}
            for ch in self._channels_from_command(norm):
                self.channels[ch].list_terminate_last = list_terminate_last
            return None
        if upper.startswith("ARB:"):
            arb_response = self._execute_arb(norm, upper)
            if arb_response is not _UNHANDLED:
                return arb_response  # type: ignore[return-value]
        if upper.startswith(("MMEM:STOR:ARB:SEQ", "MMEM:LOAD:ARB:SEQ")):
            match = re.search(r'"([^"]*)"', norm)
            filename = match.group(1) if match else ""
            for ch in self._channels_from_command(norm):
                st = self.channels[ch]
                if upper.startswith("MMEM:STOR"):
                    self.files[filename] = (
                        st.arb_type,
                        copy.deepcopy(st.sequence_steps),
                        st.sequence_count,
                        st.sequence_terminate_last,
                    )
                elif filename not in self.files:
                    self._push_error(-256, "File name not found")
                else:
                    arb_type, steps, count, terminate = self.files[filename]
                    st.arb_type = arb_type
                    st.sequence_steps = copy.deepcopy(steps)
                    st.sequence_count = count
                    st.sequence_terminate_last = terminate
            return None
        if upper.startswith("TRIG:ARB:SOUR") and self.mainframe_global_arb_trigger:
            if "(@" in norm:
                self._push_error(-108, "Parameter not allowed")
                return "" if upper.startswith("TRIG:ARB:SOUR?") else None
            if upper.startswith("TRIG:ARB:SOUR?"):
                return self.global_arb_trigger_source
            if any(st.transient_state in {"waiting", "active"} for st in self.channels.values()):
                self._push_error(308, "Cannot change while transient trigger is initiated")
                return None
            raw = self._argument_values(norm)[0].upper()
            self.global_arb_trigger_source = "EXT" if raw.startswith("EXT") else raw[:3]
            return None
        if upper.startswith("TRIG:ARB:SOUR?"):
            return self._query_values(self._channels_from_command(norm), "arb_trigger_source")
        if upper.startswith("TRIG:ARB:SOUR"):
            arb_source = self._argument_values(norm)[0].upper()
            for ch in self._channels_from_command(norm):
                self.channels[ch].arb_trigger_source = arb_source
            return None
        if upper.startswith("TRIG:TRAN:SOUR?"):
            return self._query_values(self._channels_from_command(norm), "transient_trigger_source")
        if upper.startswith("TRIG:TRAN:SOUR"):
            transient_source = self._argument_values(norm)[0].upper()
            for ch in self._channels_from_command(norm):
                self.channels[ch].transient_trigger_source = transient_source
            return None
        if upper.startswith("INIT:CONT:TRAN?"):
            st = self.channels[self._channels_from_command(norm)[0]]
            return "1" if st.transient_continuous else "0"
        if upper.startswith("INIT:CONT:TRAN"):
            continuous = self._argument_values(norm)[0].upper() in {"ON", "1"}
            for ch in self._channels_from_command(norm):
                st = self.channels[ch]
                st.transient_continuous = continuous
                if continuous and st.transient_state not in {"waiting", "active"}:
                    self._initiate_transient(st)
            return None
        if upper.startswith("INIT:TRAN"):
            for ch in self._channels_from_command(norm):
                self._initiate_transient(self.channels[ch])
            return None
        if upper.startswith("TRIG:TRAN"):
            for ch in self._channels_from_command(norm):
                st = self.channels[ch]
                if st.transient_state != "waiting":
                    self._push_error(-230, "Transient system is not armed")
                    continue
                if st.transient_step_once:
                    # One step per trigger; WTG returns for the next step.
                    st.transient_steps_left -= 1
                    if st.transient_steps_left <= 0:
                        self._finish_transient(st)
                    continue
                st.operation_status &= ~16
                st.operation_status |= 64
                if self._transient_is_infinite(st):
                    st.transient_state = "active"
                    st.transient_polls_left = -1
                elif self.transient_active_polls > 0:
                    st.transient_state = "active"
                    st.transient_polls_left = self.transient_active_polls
                else:
                    self._finish_transient(st)
            return None
        if upper.startswith("ABOR:TRAN"):
            for ch in self._channels_from_command(norm):
                st = self.channels[ch]
                settling = st.transient_state in {"waiting", "active"} and self.abort_settle_polls > 0
                if st.transient_state in {"waiting", "active"}:
                    st.voltage = st.transient_initial_voltage
                    st.current_limit = st.transient_initial_current
                st.transient_state = "aborted"
                st.operation_status &= ~(16 | 64)
                if settling:
                    st.abort_polls_left = self.abort_settle_polls
                    st.operation_status |= 64
                if st.transient_continuous:
                    # Continuous initiation re-arms the system right away.
                    self._initiate_transient(st)
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

        # Digitized/buffered measurement (SENS:SWEep + TRIG:ACQuire + FETC:ARRay):
        # kept to "accept the configuration, replay a flat buffer on fetch" —
        # enough to exercise the driver's configure/arm/poll/fetch sequence
        # without pretending to simulate real transient waveforms. Order matters:
        # the colon-qualified sub-commands must be checked before their shorter
        # prefixes (TRIG:ACQ:VOLT:SLOP before TRIG:ACQ:VOLT before bare TRIG:ACQ).
        if upper == "FORM:BORD?":
            return self.array_border
        if upper.startswith("FORM:BORD "):
            border_value = upper.split(None, 1)[1].split(",")[0]
            if border_value not in {"SWAP", "NORM"}:
                self._push_error(-222, "Invalid array byte order")
            else:
                self.array_border = border_value
            return None
        if upper == "FORM?":
            return self.array_format
        if upper.startswith("FORM "):
            fmt = upper.split(None, 1)[1].split(",")[0].strip()
            if fmt not in {"ASC", "REAL"}:
                self._push_error(-222, "Invalid measurement format")
            else:
                self.array_format = fmt
            return None
        if upper.startswith("SENS:FUNC:VOLT?"):
            ch = self._channels_from_command(norm)[0]
            return "1" if self.channels[ch].measure_voltage_enabled else "0"
        if upper.startswith("SENS:FUNC:CURR?"):
            ch = self._channels_from_command(norm)[0]
            return "1" if self.channels[ch].measure_current_enabled else "0"
        if upper.startswith("SENS:FUNC:VOLT") or upper.startswith("SENS:FUNC:CURR"):
            kind = "voltage" if upper.startswith("SENS:FUNC:VOLT") else "current"
            enabled = upper.split(None, 1)[1].split(",")[0] in {"1", "ON"}
            for ch in self._channels_from_command(norm):
                st = self.channels[ch]
                if kind == "voltage":
                    st.measure_voltage_enabled = enabled
                else:
                    st.measure_current_enabled = enabled
            return None
        if upper.startswith("SENS:SWE:OFFS:POIN?"):
            return self._query_values(self._channels_from_command(norm), "sweep_offset_points")
        if upper.startswith("SENS:SWE:OFFS:POIN"):
            value = int(float(norm.split(None, 1)[1].split(",")[0]))
            for ch in self._channels_from_command(norm):
                self.channels[ch].sweep_offset_points = value
            return None
        if upper.startswith("SENS:SWE:TINT:RES?"):
            return self.channels[self._channels_from_command(norm)[0]].sweep_resolution
        if upper.startswith("SENS:SWE:TINT:RES"):
            mode = norm.split(None, 1)[1].split(",")[0].upper()
            if mode not in {"RES20", "RES40"}:
                self._push_error(-222, f"Invalid time interval resolution: {mode}")
            else:
                for ch in self._channels_from_command(norm):
                    self.channels[ch].sweep_resolution = mode
            return None
        if upper.startswith("SENS:SWE:TINT?"):
            return self._query_values(self._channels_from_command(norm), "sweep_interval_s")
        if upper.startswith("SENS:SWE:TINT"):
            requested = float(norm.split(None, 1)[1].split(",")[0])
            for ch in self._channels_from_command(norm):
                st = self.channels[ch]
                # Approximate the manual's interval quantization, not the
                # analog response or multi-channel memory scheduling.
                step = (
                    10.24e-6
                    if requested <= 20.48e-6
                    else 40.96e-6 if st.sweep_resolution == "RES40" else 20.48e-6
                )
                st.sweep_interval_s = max(step, int(requested / step + 0.5) * step)
            return None
        if upper.startswith("SENS:SWE:POIN?"):
            return self._query_values(self._channels_from_command(norm), "sweep_points")
        if upper.startswith("SENS:SWE:POIN"):
            value = int(float(norm.split(None, 1)[1].split(",")[0]))
            for ch in self._channels_from_command(norm):
                self.channels[ch].sweep_points = value
            return None
        if upper.startswith("TRIG:ACQ:SOUR"):
            return None
        if upper.startswith("TRIG:ACQ:VOLT:SLOP") or upper.startswith("TRIG:ACQ:CURR:SLOP"):
            return None
        if upper.startswith("TRIG:ACQ:VOLT") or upper.startswith("TRIG:ACQ:CURR"):
            return None
        if upper.startswith("INIT:ACQ"):
            for ch in self._channels_from_command(norm):
                st = self.channels[ch]
                st.acquisition_generation += 1
                st.completed_generation = None
                st.acquisition_state = "waiting"
                st.operation_status |= 8 | 32  # WTG_meas and MEAS_active
            return None
        if upper.startswith("TRIG:ACQ"):
            for ch in self._channels_from_command(norm):
                st = self.channels[ch]
                if st.acquisition_state != "waiting":
                    self._push_error(-230, "Acquisition is not armed")
                    continue
                st.acquisition_state = "complete"
                st.completed_generation = st.acquisition_generation
                st.operation_status &= ~(8 | 32)
            return None
        if upper.startswith("ABOR:ACQ"):
            for ch in self._channels_from_command(norm):
                st = self.channels[ch]
                st.acquisition_state = "aborted"
                st.completed_generation = None
                st.operation_status &= ~(8 | 32)
            return None
        if upper.startswith("FETC:ARR:VOLT?") or upper.startswith("FETCH:ARRAY:VOLTAGE?"):
            st = self.channels[self._channels_from_command(norm)[0]]
            if st.acquisition_state != "complete" or st.completed_generation != st.acquisition_generation:
                self._push_error(-230, "No completed acquisition buffer")
                return ""
            if self.array_format == "REAL":
                fmt = "<" if self.array_border == "SWAP" else ">"
                payload = struct.pack(f"{fmt}{st.sweep_points}f", *([st.voltage] * st.sweep_points))
                return encode_definite_length_block(payload) + b"\n"
            return ",".join([str(st.voltage)] * st.sweep_points)
        if upper.startswith("FETC:ARR:CURR?") or upper.startswith("FETCH:ARRAY:CURRENT?"):
            st = self.channels[self._channels_from_command(norm)[0]]
            if st.acquisition_state != "complete" or st.completed_generation != st.acquisition_generation:
                self._push_error(-230, "No completed acquisition buffer")
                return ""
            if self.array_format == "REAL":
                fmt = "<" if self.array_border == "SWAP" else ">"
                payload = struct.pack(f"{fmt}{st.sweep_points}f", *([st.current_limit] * st.sweep_points))
                return encode_definite_length_block(payload) + b"\n"
            return ",".join([str(st.current_limit)] * st.sweep_points)

        if header.startswith("MEAS:") or header.startswith("FETC:") or header.startswith("FETCH:"):
            channels = self._channels_from_command(norm)
            if header.startswith("MEAS:"):
                for channel in channels:
                    state = self.channels[channel]
                    state.acquisition_state = "idle"
                    state.completed_generation = None
                    state.operation_status &= ~(8 | 32)
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
        return "" if norm.rstrip().endswith("?") else None


_DISABLE_BUILTIN_ERROR_QUEUE = "\x00__n6700_simulator_disabled_error_query__\x00"


def build_simulated_transport(
    models: dict[int, str] | None = None,
    *,
    mainframe_model: str = "N6705C",
    sequence_step_base: int = 0,
) -> ScriptedScpiTransport:
    """Build a scripted transport whose replies come from :class:`SimN6700Instrument`."""
    instrument = SimN6700Instrument(
        models,
        mainframe_model=mainframe_model,
        sequence_step_base=sequence_step_base,
    )
    transport = ScriptedScpiTransport(
        descriptor=TransportDescriptor(kind="simulated", address="sim://n6700"),
        error_query=_DISABLE_BUILTIN_ERROR_QUEUE,
        unknown_command_error=None,
    )
    transport.on_predicate(lambda _command: True, lambda command: instrument.execute(command))
    transport.instrument = instrument  # type: ignore[attr-defined]
    return transport
