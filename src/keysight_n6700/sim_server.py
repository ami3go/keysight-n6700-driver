"""Isolated N6700C-compatible SCPI TCP simulator (no hardware access).

Default listener is *literal loopback* 127.0.0.1. Remote development uses
an independently authenticated SSH local forward. This server does not expose
an unauthenticated LAN bind, physical VISA resource, or host-command console.

Fidelity is DRIVER_CONTRACT_VERIFIED only until a hardware trace is compared.
"""
from __future__ import annotations

import argparse
import json
import socket
import socketserver
import threading
from dataclasses import dataclass
from typing import cast

from .simulator import SimN6700Instrument


class SimulatorServerError(ValueError):
    """Invalid service configuration or an unsafe network exposure request."""


@dataclass(frozen=True)
class SimulatorServerConfig:
    host: str = "127.0.0.1"
    port: int = 5025
    max_message_bytes: int = 65536
    socket_timeout_s: float = 0.25

    def __post_init__(self) -> None:
        # DNS names, wildcard and direct LAN binds are prohibited until an
        # independently authenticated control plane is implemented.
        if self.host != "127.0.0.1":
            raise SimulatorServerError(
                "simulator TCP listener is loopback-only; use a pinned SSH tunnel"
            )
        if type(self.port) is not int or not 0 <= self.port <= 65535:
            raise SimulatorServerError("port must be an integer from 0 to 65535")
        if type(self.max_message_bytes) is not int or not 1 <= self.max_message_bytes <= 1048576:
            raise SimulatorServerError("max_message_bytes must be 1..1048576")
        if not isinstance(self.socket_timeout_s, (float, int)) or not 0 < self.socket_timeout_s <= 10:
            raise SimulatorServerError("socket timeout must be finite and within (0,10]")


class _SimulatorTCPHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server = cast("SimulatorTCPServer", self.server)
        # One writer has sole custody of the virtual instrument; a competing
        # client receives a finite denial rather than racing controller state.
        if not server.owner_lock.acquire(blocking=False):
            self.request.sendall(b'-380,"Simulator busy"\n')
            return
        try:
            self.request.settimeout(server.settings.socket_timeout_s)
            pending = bytearray()
            while not server.stopping.is_set():
                try:
                    chunk = self.request.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                pending.extend(chunk)
                if len(pending) > server.settings.max_message_bytes:
                    server.instrument._push_error(-223, "SCPI program message too long")
                    self.request.sendall(b'-223,"SCPI program message too long"\n')
                    break
                while b"\n" in pending:
                    raw, _, remaining = pending.partition(b"\n")
                    pending = bytearray(remaining)
                    raw = raw.rstrip(b"\r")
                    if not raw:
                        continue
                    if len(raw) > server.settings.max_message_bytes or b"\x00" in raw:
                        server.instrument._push_error(-102, "Invalid SCPI program message")
                        self.request.sendall(b'-102,"Invalid SCPI program message"\n')
                        return
                    try:
                        command = raw.decode("ascii")
                    except UnicodeDecodeError:
                        server.instrument._push_error(-102, "Non-ASCII SCPI program message")
                        self.request.sendall(b'-102,"Non-ASCII SCPI program message"\n')
                        return
                    result = server.instrument.execute(command)
                    if result is None:
                        continue
                    payload = result.encode("ascii") if isinstance(result, str) else result
                    # Definite-length binary waveform blocks are never decoded.
                    self.request.sendall(payload if payload.endswith(b"\n") else payload + b"\n")
        finally:
            # Virtual safe-off on disconnect/timeout/STOP. These are simulated
            # channel states, not electrical safe-off or physical proof.
            for state in server.instrument.channels.values():
                state.enabled = False
                state.acquisition_state = "aborted"
                state.completed_generation = None
            server.owner_lock.release()


class SimulatorTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """Share one simulator state; serialize all mutating network sessions."""

    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False

    def __init__(
        self, settings: SimulatorServerConfig | None = None,
        instrument: SimN6700Instrument | None = None,
    ) -> None:
        self.settings = settings or SimulatorServerConfig()
        if type(self.settings) is not SimulatorServerConfig:
            raise SimulatorServerError("typed simulator server configuration required")
        self.instrument = instrument or SimN6700Instrument(
            models={1: "N6775A", 2: "N6791A"}, mainframe_model="N6700C",
        )
        self.owner_lock = threading.Lock()
        self.stopping = threading.Event()
        super().__init__((self.settings.host, self.settings.port), _SimulatorTCPHandler)

    def server_close(self) -> None:
        self.stopping.set()
        super().server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Loopback-only N6700C TCP simulator")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5025)
    parser.add_argument("--self-test", action="store_true")
    options = parser.parse_args()
    try:
        settings = SimulatorServerConfig(host=options.host, port=options.port)
    except SimulatorServerError as exc:
        parser.error(str(exc))
    if options.self_test:
        instrument = SimN6700Instrument(
            models={1: "N6775A", 2: "N6791A"}, mainframe_model="N6700C",
        )
        assert instrument.execute("SYST:CHAN:MOD? (@1:2)") == "N6775A,N6791A"
        print(json.dumps({"mode": "SIMULATED", "identity": instrument.execute("*IDN?")}))
        return 0
    with SimulatorTCPServer(settings) as server:
        print(json.dumps({
            "mode": "SIMULATED", "host": settings.host, "port": server.server_address[1],
            "identity": server.instrument.execute("*IDN?"),
            "fidelity": "DRIVER_CONTRACT_VERIFIED",
        }), flush=True)
        try:
            server.serve_forever(poll_interval=0.1)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
