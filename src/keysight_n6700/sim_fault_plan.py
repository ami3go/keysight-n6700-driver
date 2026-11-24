"""Opt-in, deterministic TCP fault script for the *virtual-only* N6700 server.

Faults are indexed by completed SCPI command number, not wall time: repeatable
on Python/Linux/Windows without sleeps or a hardware clock. No physical I/O,
subprocess, authentication, user roles, shell commands or instrument discovery.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class SimulatorFaultPlanError(ValueError):
    """Malformed, unsafe or unsupported virtual SCPI fault script."""


class TCPFaultMode(str, Enum):
    TIMEOUT = "TIMEOUT"
    DISCONNECT = "DISCONNECT"
    MALFORMED_REPLY = "MALFORMED_REPLY"
    SOURCE_TRIP = "SOURCE_TRIP"


@dataclass(frozen=True)
class TCPFault:
    """First 1-based request index, count, failure mode and optional source channel."""

    first_command: int
    count: int
    mode: TCPFaultMode
    channel: int | None = None

    def __post_init__(self) -> None:
        if type(self.first_command) is not int or not 1 <= self.first_command <= 1_000_000:
            raise SimulatorFaultPlanError("first_command must be 1..1000000")
        if type(self.count) is not int or not 1 <= self.count <= 1024:
            raise SimulatorFaultPlanError("fault count must be 1..1024")
        if self.first_command + self.count > 1_000_001:
            raise SimulatorFaultPlanError("fault window exceeds simulated command budget")
        if type(self.mode) is not TCPFaultMode:
            raise SimulatorFaultPlanError("typed fault mode required")
        if self.mode is TCPFaultMode.SOURCE_TRIP:
            if type(self.channel) is not int or not 1 <= self.channel <= 4:
                raise SimulatorFaultPlanError("source-trip channel must be 1..4")
        elif self.channel is not None:
            raise SimulatorFaultPlanError("only SOURCE_TRIP may name a source channel")

    @property
    def last_command_exclusive(self) -> int:
        return self.first_command + self.count


@dataclass(frozen=True)
class TCPFaultPlan:
    events: tuple[TCPFault, ...]

    def __post_init__(self) -> None:
        if type(self.events) is not tuple or len(self.events) > 256:
            raise SimulatorFaultPlanError("fault script supports no more than 256 events")
        previous_end = 1
        for event in self.events:
            if type(event) is not TCPFault:
                raise SimulatorFaultPlanError("typed TCP fault event required")
            if event.first_command < previous_end:
                raise SimulatorFaultPlanError("TCP fault windows must be ordered/nonoverlapping")
            previous_end = event.last_command_exclusive

    def at_command(self, command_index: int) -> TCPFault | None:
        if type(command_index) is not int or command_index < 1:
            raise SimulatorFaultPlanError("command index must be positive integer")
        for event in self.events:
            if event.first_command > command_index:
                break
            if command_index < event.last_command_exclusive:
                return event
        return None

    @classmethod
    def from_document(cls, document: Any) -> TCPFaultPlan:
        if (
            type(document) is not dict
            or set(document) != {"schema_version", "events"}
            or type(document["schema_version"]) is not int
            or document["schema_version"] != 1
            or type(document["events"]) is not list
            or len(document["events"]) > 256
        ):
            raise SimulatorFaultPlanError("unsupported TCP fault script schema")
        events = []
        for item in document["events"]:
            if type(item) is not dict or set(item) != {
                "first_command", "count", "mode", "channel"
            }:
                raise SimulatorFaultPlanError("invalid TCP fault event fields")
            try:
                event = TCPFault(
                    first_command=item["first_command"],
                    count=item["count"],
                    mode=TCPFaultMode(item["mode"]),
                    channel=item["channel"],
                )
            except (ValueError, TypeError) as exc:
                raise SimulatorFaultPlanError("invalid TCP fault event") from exc
            events.append(event)
        return cls(tuple(events))

    @classmethod
    def from_file(cls, path: Path) -> TCPFaultPlan:
        path = Path(path)
        if (
            path.is_symlink() or not path.is_file()
            or not 0 < path.stat().st_size <= 65536
        ):
            raise SimulatorFaultPlanError("fault script must be bounded regular JSON file")
        try:
            return cls.from_document(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeError, ValueError) as exc:
            raise SimulatorFaultPlanError("invalid TCP fault script JSON") from exc
