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
    voltage_range: float = 20.0
    current_range: float = 1.0
    ovp: float = 20.0
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


class SimN6700Instrument:
    """Small SCPI simulator for the public driver APIs.

    The model ``SIM_LOAD`` is a simulator-only electronic load. Real
    electronic-load SCPI commands remain source-gated by the driver.
    """

    def __init__(self, models: dict[int, str] | None = None) -> None:
        models = models or {1: "N6751A", 2: "N6781A", 3: "SIM_LOAD", 4: "N6731B"}
        self.channels: dict[int, _ChannelState] = {
            ch: _ChannelState(model=model.upper(), serial=f"SIM{ch:04d}")
            for ch, model in models.items()
        }
        self.errors: list[tuple[int, str]] = []
        self.remote_state = "local"
        self.idn = "KEYSIGHT TECHNOLOGIES,N6700B,SIM000001,B.00.00"

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
            return ",".join(str(self.channels[ch].operation_status) for ch in channels)
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
        if "MEAS" in upper or "FETCH" in upper or "FETC" in upper:
            channels = self._channels_from_command(norm)
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


def build_simulated_transport(models: dict[int, str] | None = None) -> ScriptedScpiTransport:
    """Build a scripted transport whose replies come from :class:`SimN6700Instrument`."""
    instrument = SimN6700Instrument(models)
    transport = ScriptedScpiTransport(
        descriptor=TransportDescriptor(kind="simulated", address="sim://n6700"),
        error_query=_DISABLE_BUILTIN_ERROR_QUEUE,
        unknown_command_error=None,
    )
    transport.on_predicate(lambda _command: True, lambda command: instrument.execute(command))
    transport.instrument = instrument  # type: ignore[attr-defined]
    return transport
