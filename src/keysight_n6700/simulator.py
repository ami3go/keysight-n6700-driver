"""No-hardware simulator for unit tests, conformance tests, and examples.

Built on :class:`scpi_driver_core.simulation.scripted.ScriptedScpiTransport`,
which supplies the byte-level transport state machine (open/close/write/read,
bounded reads, fault injection) so this module only has to supply N6700
command *semantics*: one predicate rule dispatches every incoming command to
:meth:`SimN6700Instrument.execute`, exactly as the previous hand-rolled
``Transport`` implementation did.

A command handler must return ``None`` for a write-only command (nothing is
queued for the next read) and a response string for a query. Returning ``""``
would queue an empty-but-present reply, which is only correct for a query
that legitimately answers with nothing.
"""

from __future__ import annotations

import copy
import re
import struct
from dataclasses import dataclass, field
from typing import Any

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
    voltage_range: float = 20.0
    current_range: float = 1.0
    ovp: float = 20.0
    voltage_limit: float = 20.0
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
    voltage_triggered: float = 0.0
    current_triggered: float = 0.0
    step_trigger_output: bool = False
    voltage_list: tuple[float, ...] = (0.0,)
    current_list: tuple[float, ...] = (1.0,)
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

    @property
    def operation_status(self) -> int:
        return self.operation_condition

    @operation_status.setter
    def operation_status(self, value: int) -> None:
        self.operation_event |= value & ~self.operation_condition & self.operation_ptr
        self.operation_condition = value


class SimN6700Instrument:
    """Small SCPI simulator for the public driver APIs.

    The model ``SIM_LOAD`` is a simulator-only electronic load. Real
    electronic-load SCPI commands remain source-gated by the driver.
    """

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
        self.channels: dict[int, _ChannelState] = {
            ch: _ChannelState(model=model.upper(), serial=f"SIM{ch:04d}")
            for ch, model in models.items()
        }
        self.errors: list[tuple[int, str]] = []
        self.remote_state = "local"
        self.array_format = "ASC"
        self.array_border = "NORM"
        self.idn = f"KEYSIGHT TECHNOLOGIES,{mainframe_model.upper()},SIM000001,B.00.00"
        self.sequence_step_base = sequence_step_base
        # Status polls a triggered AUTO profile stays TRAN_active; 0 completes
        # immediately on the trigger.
        self.transient_active_polls = 0
        # Instrument file system for MMEM:STOR/LOAD:ARB:SEQ.
        self.files: dict[str, tuple[str, list[dict[str, Any]], float, bool]] = {}

    @staticmethod
    def _active_mode(st: _ChannelState) -> str:
        return st.voltage_mode if st.transient_active_quantity == "VOLT" else st.current_mode

    def _initiate_transient(self, st: _ChannelState) -> None:
        if st.voltage_mode != "FIX":
            st.transient_active_quantity = "VOLT"
        elif st.current_mode != "FIX":
            st.transient_active_quantity = "CURR"
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
            levels = st.voltage_list if st.transient_active_quantity == "VOLT" else st.current_list
            st.transient_steps_left = len(levels) * st.list_count
        # TRAN_active stays set from INIT until the trigger system is idle.
        st.operation_status |= 16 | 64

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
            final = (st.voltage_list if quantity == "VOLT" else st.current_list)[-1]
        elif mode == "ARB" and st.arb_shape == "UDEF" and st.arb_terminate_last:
            final = st.arb_values.get(f"{quantity}:UDEF:LEV", (None,))[-1]
        if quantity == "VOLT":
            st.voltage = st.transient_initial_voltage if final is None else final
        else:
            st.current_limit = st.transient_initial_current if final is None else final
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
                parsed = "CURR" if raw.upper().startswith("CURR") else "VOLT"
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
        match = re.match(r"^ARB:(VOLT|CURR):([A-Z:]+)$", header)
        if match:
            key = f"{match.group(1)}:{match.group(2)}"
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
        if not channels:
            return [1]
        return channels

    def _query_values(self, channels: list[int], getter: str) -> str:
        vals = [str(getattr(self.channels[ch], getter)) for ch in channels]
        return ",".join(vals)

    @staticmethod
    def _argument_values(command: str) -> tuple[str, ...]:
        payload = command.split(None, 1)[1]
        payload = re.sub(r",?\s*\(@[^)]*\)\s*$", "", payload)
        return tuple(value.strip() for value in payload.split(",") if value.strip())

    @classmethod
    def _float_arguments(cls, command: str) -> tuple[float, ...]:
        return tuple(float(value) for value in cls._argument_values(command))

    def execute(self, message: str) -> str | bytes | None:
        """Run every ``;``-separated part of ``message``; return the last reply."""
        response: str | bytes | None = None
        parts = [part.strip() for part in message.strip().split(";") if part.strip()]
        for part in parts:
            response = self._execute_one(part)
        return response

    def _execute_one(self, cmd: str) -> str | bytes | None:
        norm = " ".join(cmd.strip().split())
        upper = norm.upper()
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
                st.current_limit = 1.0
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
            return ";".join(f"CHAN{ch}:{st.model}" for ch, st in self.channels.items())
        if upper.startswith("SYST:ERR") or upper.startswith("SYSTEM:ERROR"):
            if self.errors:
                code, msg = self.errors.pop(0)
                return f'{code},"{msg}"'
            return '0,"No error"'
        if upper.startswith("SYST:CHAN:COUN") or upper.startswith("SYSTEM:CHANNEL:COUNT"):
            return str(len(self.channels))
        if "SYST:CHAN:MOD" in upper or "SYSTEM:CHANNEL:MODEL" in upper:
            ch = self._channels_from_command(norm)[0]
            return self.channels[ch].model
        if "SYST:CHAN:OPT" in upper or "SYSTEM:CHANNEL:OPTION" in upper:
            ch = self._channels_from_command(norm)[0]
            return self.channels[ch].option or "0"
        if "SYST:CHAN:SER" in upper or "SYSTEM:CHANNEL:SERIAL" in upper:
            ch = self._channels_from_command(norm)[0]
            return self.channels[ch].serial
        if upper == "SYST:LOC":
            self.remote_state = "local"
            return None
        if upper == "SYST:REM":
            self.remote_state = "remote"
            return None
        if upper == "SYST:RWL":
            self.remote_state = "remote_lockout"
            return None
        if upper == "SYST:REM?":
            return self.remote_state
        if upper.startswith("OUTP:PROT:CLE") or upper.startswith("OUTPUT:PROTECTION:CLEAR"):
            for ch in self._channels_from_command(norm):
                self.channels[ch].protection_active = False
                self.channels[ch].questionable_status = 0
            return None
        if upper.startswith("STAT:QUES:COND?") or upper.startswith("STATUS:QUESTIONABLE:CONDITION?"):
            channels = self._channels_from_command(norm)
            return ",".join(str(self.channels[ch].questionable_status) for ch in channels)
        if upper.startswith("STAT:OPER:COND?") or upper.startswith("STATUS:OPERATION:CONDITION?"):
            channels = self._channels_from_command(norm)
            for ch in channels:
                st = self.channels[ch]
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
            self._push_error(-113, f"Undefined header: {cmd}")
            return ""
        if upper.startswith("OUTP?") or upper.startswith("OUTPUT?"):
            channels = self._channels_from_command(norm)
            return ",".join("1" if self.channels[ch].enabled else "0" for ch in channels)
        if upper.startswith("OUTP") or upper.startswith("OUTPUT"):
            enabled = " ON" in f" {upper}" or re.search(r"\b1\b", upper) is not None
            if "OFF" in upper or re.search(r"\b0\b", upper):
                enabled = False
            for ch in self._channels_from_command(norm):
                self.channels[ch].enabled = enabled
            return None
        if upper.startswith("FUNC?") or upper.startswith("FUNCTION?"):
            ch = self._channels_from_command(norm)[0]
            return self.channels[ch].smu_mode
        if upper.startswith("FUNC") or upper.startswith("FUNCTION"):
            ch = self._channels_from_command(norm)[0]
            for mode in ("CURR", "VOLT", "RES", "POW"):
                if mode in upper:
                    self.channels[ch].smu_mode = mode
                    break
            return None
        if upper.startswith("SIM:SMU:OFFMODE?"):
            ch = self._channels_from_command(norm)[0]
            return self.channels[ch].smu_off_mode
        if upper.startswith("SIM:SMU:OFFMODE"):
            ch = self._channels_from_command(norm)[0]
            self.channels[ch].smu_off_mode = "LOWZ" if "LOW" in upper else "HIGHZ"
            return None
        if upper.startswith("SIM:LOAD:INP?"):
            channels = self._channels_from_command(norm)
            return ",".join("1" if self.channels[ch].enabled else "0" for ch in channels)
        if upper.startswith("SIM:LOAD:INP"):
            enabled = " ON" in f" {upper}" or re.search(r"\b1\b", upper) is not None
            if "OFF" in upper or re.search(r"\b0\b", upper):
                enabled = False
            for ch in self._channels_from_command(norm):
                self.channels[ch].enabled = enabled
            return None
        if upper.startswith("SIM:LOAD:MODE?"):
            ch = self._channels_from_command(norm)[0]
            return self.channels[ch].load_mode
        if upper.startswith("SIM:LOAD:MODE"):
            ch = self._channels_from_command(norm)[0]
            for mode in ("CC", "CV", "CR", "CP"):
                if mode in upper:
                    self.channels[ch].load_mode = mode
            return None
        for name, attr in (
            ("SIM:LOAD:CURR", "load_current"),
            ("SIM:LOAD:VOLT", "load_voltage"),
            ("SIM:LOAD:RES", "load_resistance"),
            ("SIM:LOAD:POW", "load_power"),
        ):
            if upper.startswith(name + "?"):
                ch = self._channels_from_command(norm)[0]
                return str(getattr(self.channels[ch], attr))
            if upper.startswith(name):
                ch = self._channels_from_command(norm)[0]
                value = float(re.split(r"\s+|,", norm, maxsplit=1)[1].split(",")[0])
                setattr(self.channels[ch], attr, value)
                return None
        for name, attr in (
            ("VOLT:RANG", "voltage_range"),
            ("VOLTAGE:RANGE", "voltage_range"),
            ("CURR:RANG", "current_range"),
            ("CURRENT:RANGE", "current_range"),
            ("VOLT:PROT", "ovp"),
            ("VOLTAGE:PROTECTION", "ovp"),
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
        if upper.startswith("CURR:PROT:STAT?") or upper.startswith("CURRENT:PROTECTION:STATE?"):
            ch = self._channels_from_command(norm)[0]
            return "1" if self.channels[ch].ocp else "0"
        if upper.startswith("CURR:PROT:STAT") or upper.startswith("CURRENT:PROTECTION:STATE"):
            enabled = " ON" in f" {upper}" or re.search(r"\b1\b", upper) is not None
            if "OFF" in upper or re.search(r"\b0\b", upper):
                enabled = False
            for ch in self._channels_from_command(norm):
                self.channels[ch].ocp = enabled
            return None
        if upper.startswith("CURR:PROT") or upper.startswith("CURRENT:PROTECTION"):
            self._push_error(-113, f"Undefined header: {cmd}")
            return None
        if upper.startswith("CURR:LIM?") or upper.startswith("CURRENT:LIMIT?"):
            return self._query_values(self._channels_from_command(norm), "load_current_limit")
        if upper.startswith("CURR:LIM") or upper.startswith("CURRENT:LIMIT"):
            value = float(norm.split(None, 1)[1].split(",")[0])
            for ch in self._channels_from_command(norm):
                self.channels[ch].load_current_limit = value
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
        ):
            if upper.startswith(f"{quantity}:MODE?"):
                return self._query_values(self._channels_from_command(norm), mode_attribute)
            if upper.startswith(f"{quantity}:MODE"):
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
        for command, attribute in (
            ("LIST:VOLT", "voltage_list"),
            ("LIST:CURR", "current_list"),
            ("LIST:DWEL", "dwell_list"),
            ("LIST:TOUT:BOST", "list_bost"),
            ("LIST:TOUT:EOST", "list_eost"),
        ):
            if upper.startswith(command + ":POIN?"):
                profile_values = getattr(
                    self.channels[self._channels_from_command(norm)[0]], attribute
                )
                return str(len(profile_values))
            if upper.startswith(command + "?"):
                profile_values = getattr(
                    self.channels[self._channels_from_command(norm)[0]], attribute
                )
                return ",".join(str(value) for value in profile_values)
            if upper.startswith(command):
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
                if st.transient_state in {"waiting", "active"}:
                    st.voltage = st.transient_initial_voltage
                    st.current_limit = st.transient_initial_current
                st.transient_state = "aborted"
                st.operation_status &= ~(16 | 64)
                if st.transient_continuous:
                    # Continuous initiation re-arms the system right away.
                    self._initiate_transient(st)
            return None
        if upper.startswith("VOLT?") or upper.startswith("VOLTAGE?"):
            return self._query_values(self._channels_from_command(norm), "voltage")
        if upper.startswith("CURR?") or upper.startswith("CURRENT?"):
            return self._query_values(self._channels_from_command(norm), "current_limit")
        if upper.startswith("RES?") or upper.startswith("RESISTANCE?"):
            return self._query_values(self._channels_from_command(norm), "resistance")
        if upper.startswith("POW?") or upper.startswith("POWER?"):
            return self._query_values(self._channels_from_command(norm), "power")
        if upper.startswith("VOLT") or upper.startswith("VOLTAGE"):
            value = float(norm.split(None, 1)[1].split(",")[0])
            for ch in self._channels_from_command(norm):
                self.channels[ch].voltage = value
            return None
        if upper.startswith("CURR") or upper.startswith("CURRENT"):
            value = float(norm.split(None, 1)[1].split(",")[0])
            for ch in self._channels_from_command(norm):
                self.channels[ch].current_limit = value
            return None
        if upper.startswith("RES") or upper.startswith("RESISTANCE"):
            value = float(norm.split(None, 1)[1].split(",")[0])
            for ch in self._channels_from_command(norm):
                self.channels[ch].resistance = value
            return None
        if upper.startswith("POW") or upper.startswith("POWER"):
            value = float(norm.split(None, 1)[1].split(",")[0])
            for ch in self._channels_from_command(norm):
                self.channels[ch].power = value
            return None
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
        if "MEAS" in upper or "FETCH" in upper or "FETC" in upper:
            channels = self._channels_from_command(norm)
            if "MEAS" in upper:
                for ch in channels:
                    st = self.channels[ch]
                    st.acquisition_state = "idle"
                    st.completed_generation = None
                    st.operation_status &= ~(8 | 32)
            values: list[str] = []
            for ch in channels:
                st = self.channels[ch]
                if "VOLT" in upper:
                    values.append(str(st.load_voltage if st.model == "SIM_LOAD" else st.voltage))
                elif "CURR" in upper:
                    if st.model == "SIM_LOAD":
                        values.append(str(st.load_current if st.enabled else 0.0))
                    else:
                        values.append(str(min(st.current_limit, abs(st.voltage) / 10.0) if st.enabled else 0.0))
                elif "POW" in upper:
                    if st.model == "SIM_LOAD":
                        values.append(str(st.load_power or st.load_voltage * st.load_current))
                    elif st.model.startswith(("N676", "N678")):
                        curr = min(st.current_limit, abs(st.voltage) / 10.0) if st.enabled else 0.0
                        values.append(str(st.voltage * curr))
                    else:
                        self._push_error(310, "The command is not supported by this model")
                        return ""
            return ",".join(values)
        self._push_error(-113, f"Undefined header: {cmd}")
        # Only a query is expected to produce a reply; an unmatched write must
        # not queue one, or it corrupts the read a later query performs.
        return "" if norm.rstrip().endswith("?") else None


#: Never sent as a real command; keeps ScriptedScpiTransport's own error-queue
#: interception out of the way so SYST:ERR? is served by our own dispatch,
#: which already reproduces N6700 error-queue semantics.
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
