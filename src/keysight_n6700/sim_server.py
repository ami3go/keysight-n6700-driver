"""Isolated N6700C-compatible SCPI TCP simulator (no hardware access).

Default listener is *literal loopback* 127.0.0.1. For direct Milk-V access,
explicitly select --host with a literal RFC1918 IP and --allow-private-lan. No simulator user/agent login, PIN or approval is
required. Never bind the simulator to a public network. The simulator owns no
physical VISA resource, privileged host-command console or HIL permits.

Fidelity is DRIVER_CONTRACT_VERIFIED only until a hardware trace is compared.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import socketserver
import threading
from contextlib import suppress
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import cast

from .sim_fault_plan import (
    SimulatorFaultPlanError, TCPFaultMode, TCPFaultPlan,
)
from .simulator import SimN6700Instrument

_PRIVATE_LAN_NETWORKS = tuple(IPv4Network(cidr) for cidr in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
))


class SimulatorServerError(ValueError):
    """Invalid service configuration or an unsafe network exposure request."""


@dataclass(frozen=True)
class SimulatorServerConfig:
    host: str = "127.0.0.1"
    port: int = 5025
    max_message_bytes: int = 65536
    socket_timeout_s: float = 0.25
    allow_private_lan: bool = False
    identity_port: int | None = None

    def __post_init__(self) -> None:
        # Explicit RFC1918-only bind is a deployment choice, not an access
        # approval. Any user/agent on the private LAN can use the virtual service.
        # The service must never be exposed to public, wildcard or DNS binds.
        if type(self.allow_private_lan) is not bool:
            raise SimulatorServerError("allow_private_lan must be a boolean")
        if self.host != "127.0.0.1":
            try:
                addr = IPv4Address(self.host)
            except ValueError as exc:
                raise SimulatorServerError(
                    "simulator bind must be literal IPv4 loopback or RFC1918 private LAN"
                ) from exc
            if not (self.allow_private_lan and any(
                addr in network for network in _PRIVATE_LAN_NETWORKS
            )):
                raise SimulatorServerError(
                    "private-LAN bind requires --allow-private-lan and an RFC1918 address"
                )
        if type(self.port) is not int or not 0 <= self.port <= 65535:
            raise SimulatorServerError("port must be an integer from 0 to 65535")
        if self.identity_port is not None and (
            type(self.identity_port) is not int or not 0 <= self.identity_port <= 65535
        ):
            raise SimulatorServerError("identity_port must be 0..65535 or omitted")
        if self.port == 65535 and self.identity_port is None:
            raise SimulatorServerError("port 65535 needs an explicit identity_port")
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
                except TimeoutError:
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
                    # Entirely simulator-local command-index faults. The
                    # identity listener is unaffected, and no physical device
                    # is reachable from this handler.
                    server.received_commands += 1
                    fault = server.fault_plan.at_command(server.received_commands)
                    if fault is not None:
                        if fault.mode is TCPFaultMode.TIMEOUT:
                            # No reply, and no command side effect; the client
                            # experiences its own configured read timeout.
                            continue
                        if fault.mode is TCPFaultMode.DISCONNECT:
                            # Finally block enforces virtual safe-off.
                            return
                        if fault.mode is TCPFaultMode.MALFORMED_REPLY:
                            self.request.sendall(b"!SIMULATED-MALFORMED-REPLY!\n")
                            continue
                        if fault.mode is TCPFaultMode.SOURCE_TRIP:
                            target = server.instrument.channels[cast(int, fault.channel)]
                            target.enabled = False
                            target.acquisition_state = "aborted"
                            target.completed_generation = None
                    result = server.instrument.execute(command)
                    if fault is not None and fault.mode is TCPFaultMode.SOURCE_TRIP:
                        # Even if the injected command attempted OUTP ON, it
                        # must not override the simulated protection trip.
                        server.instrument.channels[cast(int, fault.channel)].enabled = False
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


# The identity socket SENDS a banner and NEVER accepts incoming SCPI bytes.
# A simulation client can probe it without transmitting even an IDN query to a
# real instrument accidentally configured at the same address.
SIMULATOR_SERVICE_MAGIC = "PST-N6700-VIRTUAL/1"
SIMULATOR_INSTANCE_ID = "SIM-n6700c-virtual-v1"


class _IdentityHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server = cast("_IdentityTCPServer", self.server)
        self.request.settimeout(1.0)
        self.request.sendall(server.identity_line)


class _IdentityTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False

    def __init__(self, address: tuple[str, int], identity_line: bytes) -> None:
        self.identity_line = identity_line
        super().__init__(address, _IdentityHandler)


class SimulatorTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """Share one simulator state; serialize all mutating network sessions."""

    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False

    def __init__(
        self, settings: SimulatorServerConfig | None = None,
        instrument: SimN6700Instrument | None = None,
        fault_plan: TCPFaultPlan | None = None,
    ) -> None:
        self.settings = settings or SimulatorServerConfig()
        if type(self.settings) is not SimulatorServerConfig:
            raise SimulatorServerError("typed simulator server configuration required")
        self.instrument = instrument or SimN6700Instrument(
            models={1: "N6775A", 2: "N6791A"}, mainframe_model="N6700C",
        )
        if fault_plan is not None and type(fault_plan) is not TCPFaultPlan:
            raise SimulatorFaultPlanError("typed fault plan required")
        self.fault_plan = fault_plan or TCPFaultPlan(())
        for event in self.fault_plan.events:
            if event.mode is TCPFaultMode.SOURCE_TRIP:
                channel = event.channel
                if channel is None:
                    raise SimulatorFaultPlanError("source trip requires a channel")
                state = self.instrument.channels.get(channel)
                if state is None or state.model.startswith("N679"):
                    raise SimulatorFaultPlanError(
                        "source trip requires an installed virtual source channel"
                    )
        self.received_commands = 0
        self.owner_lock = threading.Lock()
        self.stopping = threading.Event()
        super().__init__((self.settings.host, self.settings.port), _SimulatorTCPHandler)
        modules = {
            str(ch): state.model
            for ch, state in sorted(self.instrument.channels.items())
        }
        topology = {"mainframe": self.instrument.idn.split(",")[1], "modules": modules}
        topology_hash = hashlib.sha256(
            json.dumps(topology, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        identity_payload = {
            "service": SIMULATOR_SERVICE_MAGIC,
            "instance_id": SIMULATOR_INSTANCE_ID,
            "origin": "SIMULATED",
            "topology_sha256": topology_hash,
            "scpi_port": self.server_address[1],
        }
        banner = (json.dumps(identity_payload, sort_keys=True) + "\n").encode("ascii")
        identity_port = (
            self.settings.identity_port if self.settings.identity_port is not None
            else self.settings.port + 1 if self.settings.port else 0
        )
        if identity_port == self.server_address[1]:
            super().server_close()
            raise SimulatorServerError("identity port must differ from SCPI port")
        try:
            self.identity_server = _IdentityTCPServer(
                (self.settings.host, identity_port), banner,
            )
        except OSError:
            super().server_close()
            raise
        self._identity_thread: threading.Thread | None = None
        self.topology_sha256 = topology_hash

    def serve_forever(self, poll_interval: float = 0.5) -> None:
        if self._identity_thread is not None:
            raise SimulatorServerError("simulator service already running")
        self._identity_thread = threading.Thread(
            target=self.identity_server.serve_forever, daemon=True,
        )
        self._identity_thread.start()
        try:
            super().serve_forever(poll_interval=poll_interval)
        finally:
            self.identity_server.shutdown()
            self._identity_thread.join(timeout=2.0)

    def server_close(self) -> None:
        self.stopping.set()
        self.identity_server.server_close()
        super().server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description="No-auth virtual-only N6700C TCP simulator")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5025)
    parser.add_argument("--identity-port", type=int, default=None, help="Read-only no-auth virtual-service banner port; defaults to SCPI port + 1")
    parser.add_argument("--allow-private-lan", action="store_true", help="Opt into binding a literal RFC1918 address; no user/client credentials")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument(
        "--fault-plan", type=Path, default=None,
        help="Optional bounded JSON command-index fault script; no client PIN",
    )
    options = parser.parse_args()
    try:
        settings = SimulatorServerConfig(host=options.host, port=options.port, allow_private_lan=options.allow_private_lan, identity_port=options.identity_port)
    except SimulatorServerError as exc:
        parser.error(str(exc))
    try:
        fault_plan = (
            TCPFaultPlan.from_file(options.fault_plan)
            if options.fault_plan is not None else None
        )
    except SimulatorFaultPlanError as exc:
        parser.error(str(exc))
    if options.self_test:
        instrument = SimN6700Instrument(
            models={1: "N6775A", 2: "N6791A"}, mainframe_model="N6700C",
        )
        if instrument.execute("SYST:CHAN:MOD? (@1:2)") != "N6775A,N6791A":
            raise SimulatorServerError("simulated channel inventory mismatch")
        print(json.dumps({"mode": "SIMULATED", "identity": instrument.execute("*IDN?")}))
        return 0
    with SimulatorTCPServer(settings, fault_plan=fault_plan) as server:
        print(json.dumps({
            "mode": "SIMULATED", "host": settings.host, "port": server.server_address[1],
            "identity": server.instrument.execute("*IDN?"),
            "identity_port": server.identity_server.server_address[1],
            "instance_id": SIMULATOR_INSTANCE_ID,
            "topology_sha256": server.topology_sha256,
            "fidelity": "DRIVER_CONTRACT_VERIFIED",
            "fault_script_events": len(server.fault_plan.events),
        }), flush=True)
        with suppress(KeyboardInterrupt):
            server.serve_forever(poll_interval=0.1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
