"""Real loopback fault-injection socket tests; NEVER physical N6700/HIL."""
from __future__ import annotations

import json
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from keysight_n6700.sim_fault_plan import (
    SimulatorFaultPlanError,
    TCPFault,
    TCPFaultMode,
    TCPFaultPlan,
)
from keysight_n6700.sim_server import SimulatorServerConfig, SimulatorTCPServer


@contextmanager
def running_server(plan: TCPFaultPlan) -> Iterator[SimulatorTCPServer]:
    server = SimulatorTCPServer(
        SimulatorServerConfig(port=0, socket_timeout_s=0.1), fault_plan=plan,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def plan(*events: TCPFault) -> TCPFaultPlan:
    return TCPFaultPlan(tuple(events))


def connect(port: int) -> socket.socket:
    sock = socket.create_connection(("127.0.0.1", port), timeout=2)
    sock.settimeout(1)
    return sock


def test_fault_plan_does_not_change_default_server_behavior() -> None:
    with running_server(plan()) as server:
        with connect(server.server_address[1]) as client, client.makefile("rb") as incoming:
            client.sendall(b"*IDN?\n")
            assert b"N6700C" in incoming.readline()
        assert server.received_commands == 1


def test_virtual_tcp_timeout_suppresses_reply_and_recovers_next_command() -> None:
    faults = plan(TCPFault(1, 1, TCPFaultMode.TIMEOUT))
    with running_server(faults) as server:
        with connect(server.server_address[1]) as client:
            client.settimeout(0.15)
            client.sendall(b"*IDN?\n")
            with pytest.raises(socket.timeout):
                client.recv(4096)
            client.sendall(b"*IDN?\n")
            assert b"N6700C" in client.recv(4096)
        assert server.received_commands == 2


def test_simulated_tcp_disconnect_preserves_virtual_safe_off_and_can_reconnect() -> None:
    with running_server(plan(TCPFault(2, 1, TCPFaultMode.DISCONNECT))) as server:
        with connect(server.server_address[1]) as first:
            first.sendall(b"OUTP ON,(@1)\n")
            first.sendall(b"OUTP? (@1)\n")
            assert first.recv(32) == b""
        assert server.instrument.channels[1].enabled is False
        with connect(server.server_address[1]) as next_client:
            next_client.sendall(b"OUTP? (@1)\n")
            assert next_client.recv(32) == b"0\n"
        assert server.received_commands == 3


def test_malformed_reply_is_not_a_fabricated_scpi_measurement() -> None:
    with (
        running_server(plan(TCPFault(1, 1, TCPFaultMode.MALFORMED_REPLY))) as server,
        connect(server.server_address[1]) as client,
        client.makefile("rb") as incoming,
    ):
        client.sendall(b"*IDN?\n*IDN?\n")
        assert incoming.readline() == b"!SIMULATED-MALFORMED-REPLY!\n"
        assert b"N6700C" in incoming.readline()


def test_source_trip_prevents_output_enable_during_fault_but_allows_recovery() -> None:
    fault = TCPFault(1, 1, TCPFaultMode.SOURCE_TRIP, channel=1)
    with (
        running_server(plan(fault)) as server,
        connect(server.server_address[1]) as client,
        client.makefile("rb") as incoming,
    ):
        client.sendall(b"OUTP ON,(@1)\nOUTP? (@1)\n")
        assert incoming.readline() == b"0\n"
        assert server.instrument.channels[1].enabled is False
        client.sendall(b"OUTP ON,(@1)\nOUTP? (@1)\n")
        assert incoming.readline() == b"1\n"


def test_fault_script_does_not_touch_read_only_identity_service() -> None:
    faults = plan(TCPFault(1, 2, TCPFaultMode.TIMEOUT))
    with running_server(faults) as server:
        with connect(server.identity_server.server_address[1]) as sock:
            identity = json.loads(sock.recv(1024))
        assert identity["origin"] == "SIMULATED"
        assert identity["instance_id"] == "SIM-n6700c-virtual-v1"
        assert server.received_commands == 0


@pytest.mark.parametrize("invalid", [
    {"schema_version": 2, "events": []},
    {"schema_version": True, "events": []},
    {"schema_version": 1, "events": {}, "shell": "whoami"},
    {"schema_version": 1, "events": [{"first_command": 1, "count": 1, "mode": "SHELL", "channel": None}]},
    {"schema_version": 1, "events": [{"first_command": 1, "count": 1, "mode": "DISCONNECT", "channel": 1}]},
    {"schema_version": 1, "events": [{"first_command": 0, "count": 1, "mode": "TIMEOUT", "channel": None}]},
    {"schema_version": 1, "events": [{"first_command": 1, "count": 0, "mode": "TIMEOUT", "channel": None}]},
])
def test_reject_unknown_script_commands_or_unbounded_faults(invalid) -> None:
    with pytest.raises(SimulatorFaultPlanError):
        TCPFaultPlan.from_document(invalid)


def test_fault_script_can_repeat_bounded_window_and_still_recover() -> None:
    script = TCPFaultPlan.from_document({
        "schema_version": 1,
        "events": [{"first_command": 1, "count": 2, "mode": "TIMEOUT", "channel": None}],
    })
    assert script.at_command(1).mode is TCPFaultMode.TIMEOUT
    assert script.at_command(2).mode is TCPFaultMode.TIMEOUT
    assert script.at_command(3) is None
    with running_server(script) as server, connect(server.server_address[1]) as client:
        client.settimeout(0.15)
        client.sendall(b"*IDN?\n")
        with pytest.raises(socket.timeout):
            client.recv(1024)
        client.sendall(b"*IDN?\n")
        with pytest.raises(socket.timeout):
            client.recv(1024)
        client.sendall(b"*IDN?\n")
        assert b"N6700C" in client.recv(1024)


def test_overlapping_windows_are_rejected_before_service_starts() -> None:
    with pytest.raises(SimulatorFaultPlanError, match="nonoverlapping"):
        plan(
            TCPFault(1, 2, TCPFaultMode.TIMEOUT),
            TCPFault(2, 1, TCPFaultMode.MALFORMED_REPLY),
        )


def test_source_trip_cannot_target_load_card_or_missing_channel() -> None:
    for channel in (2, 3):
        with pytest.raises(SimulatorFaultPlanError, match="source channel"):
            SimulatorTCPServer(
                SimulatorServerConfig(port=0),
                fault_plan=plan(TCPFault(1, 1, TCPFaultMode.SOURCE_TRIP, channel)),
            )


def test_read_only_identity_and_unknown_host_commands_are_separate() -> None:
    with running_server(plan(TCPFault(2, 1, TCPFaultMode.MALFORMED_REPLY))) as server:
        with connect(server.identity_server.server_address[1]) as sock:
            assert b"PST-N6700-VIRTUAL/1" in sock.recv(1024)
        with connect(server.server_address[1]) as sock, sock.makefile("rb") as incoming:
            sock.sendall(b"*IDN?\n")
            assert b"N6700C" in incoming.readline()
            sock.sendall(b"whoami\n")
            assert incoming.readline() == b"!SIMULATED-MALFORMED-REPLY!\n"
        assert server.received_commands == 2
