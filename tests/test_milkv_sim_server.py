"""Real loopback SCPI socket tests; no physical instrument or HIL."""
from __future__ import annotations

import socket
import threading
from contextlib import contextmanager
from collections.abc import Iterator

import pytest

from keysight_n6700.driver import N6700
from keysight_n6700.sim_server import (
    SimulatorServerConfig,
    SimulatorServerError,
    SimulatorTCPServer,
)


@contextmanager
def running_server() -> Iterator[SimulatorTCPServer]:
    server = SimulatorTCPServer(SimulatorServerConfig(port=0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def connect(port: int) -> socket.socket:
    result = socket.create_connection(("127.0.0.1", port), timeout=2)
    result.settimeout(2)
    return result


def test_exact_model_inventory_over_real_tcp_connection() -> None:
    with running_server() as server, connect(server.server_address[1]) as sock, sock.makefile("rb") as response:
        sock.sendall(b"*IDN?\r\nSYST:CHAN:COUN?\nSYST:CHAN:MOD? (@1:2)\n")
        assert b",N6700C," in response.readline()
        assert response.readline() == b"2\n"
        assert response.readline() == b"N6775A,N6791A\n"


def test_tcp_fragmented_compound_query_and_stateful_disconnect() -> None:
    with running_server() as server:
        port = server.server_address[1]
        with connect(port) as sock, sock.makefile("rb") as response:
            sock.sendall(b"OUTP ON,(@1)\n")
            sock.sendall(b"VOLT 12,(@1)\n")
            sock.sendall(b"OUTP? (@1);:VOLT? (@1);:STAT:QUES:COND? (@1);")
            sock.sendall(b":STAT:QUES:COND? (@2);:SYST:ERR?\n")
            assert response.readline() == b'1;12.0;0;0;0,"No error"\n'
        with connect(port) as second, second.makefile("rb") as response:
            second.sendall(b"OUTP? (@1)\n")
            assert response.readline() == b"0\n"


def test_second_simultaneous_writer_is_refused() -> None:
    with running_server() as server:
        port = server.server_address[1]
        with connect(port) as owner, owner.makefile("rb") as primary:
            owner.sendall(b"*IDN?\n")
            assert b"N6700C" in primary.readline()
            with connect(port) as second, second.makefile("rb") as denied:
                assert b"Simulator busy" in denied.readline()
            owner.sendall(b"OUTP? (@1)\n")
            assert primary.readline() == b"0\n"


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.0.55", "localhost", "::1"])  # noqa: S104
def test_service_refuses_nonliteral_ipv4_loopback(host: str) -> None:
    with pytest.raises(SimulatorServerError, match="loopback-only"):
        SimulatorServerConfig(host=host)


def test_invalid_messages_fail_closed_and_cannot_exceed_buffer() -> None:
    with running_server() as server:
        port = server.server_address[1]
        with connect(port) as sock, sock.makefile("rb") as response:
            sock.sendall(b"BOGUS?\nSYST:ERR?\n")
            assert response.readline() == b"\n"
            assert b"-113" in response.readline()
        with connect(port) as sock, sock.makefile("rb") as response:
            sock.sendall(b"X" * 65537)
            assert b"too long" in response.readline()


def test_definite_length_binary_reply_is_byte_preserving() -> None:
    with running_server() as server:
        st = server.instrument.channels[2]
        st.acquisition_state = "complete"
        st.acquisition_generation = 1
        st.completed_generation = 1
        st.sweep_points = 2
        st.voltage = 3.0
        server.instrument.array_format = "REAL"
        with connect(server.server_address[1]) as sock, sock.makefile("rb") as response:
            sock.sendall(b"FETC:ARR:VOLT? (@2)\n")
            payload = response.readline()
            assert payload.startswith(b"#18")
            assert payload.endswith(b"\n")
            assert len(payload) == 12  # 2 REAL32 samples + 3-block header + LF


def test_normal_driver_connects_and_discovers_over_tcp() -> None:
    with running_server() as server:
        driver = N6700.connect_ethernet("127.0.0.1", port=server.server_address[1])
        try:
            modules = driver.discover_modules()
            assert modules[1].model == "N6775A"
            assert modules[2].model == "N6791A"
            assert driver.get_output_state(1) is False
        finally:
            driver.disconnect_all()
