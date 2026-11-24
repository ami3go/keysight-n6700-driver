# Optional Milk-V N6700 TCP simulator fault injection

This document covers **only** the Keysight driver's pure software N6700C
SCPI TCP simulator. It never contacts a physical instrument or authorizes
HIL. It does not claim exact physical N6700 firmware/SCPI failure fidelity.

## No-authorization use

Any user or AI agent can launch the simulator and run the configured fault
script without PIN, login, SSH, bench reservation, profile approval or HIL
permit. The default server only binds loopback and never connects to real
VISA/USB/LAN instruments. Private-LAN bind requires explicit RFC1918 address
configuration. Never port-forward this unauthenticated test server publicly.

```bash
n6700-simserver --port 5025 --fault-plan examples/simulator_fault_plan.json
```

Optional trusted private-LAN deployment uses the usual
`--host <actual-private-IP> --allow-private-lan` flags.

## Script contract (schema version 1)

A `TCPFaultPlan` has an `events` array, at most 256 events. Every event has
**exactly** `first_command`, `count`, `mode`, `channel`. The command
counter is 1-based, global to this server instance, and increments after a
complete **ASCII SCPI command line** is received. It persists across client
reconnects. Windows are ordered, non-overlapping and bounded to at most
1,024 consecutive commands. No wall-clock dependence, random timing, host
subprocess, file transfer or network proxy is involved.

| Mode | Result when scheduled | Recovery behavior |
|---|---|---|
| `TIMEOUT` | Command deliberately **not executed**; no bytes sent back to client; client experiences its configured receive timeout | Subsequent command outside the fault window executes normally |
| `DISCONNECT` | TCP server handler closes this virtual SCPI connection before executing the triggering command | A new client can reconnect; virtual outputs are forced OFF by the existing session cleanup |
| `MALFORMED_REPLY` | Command not executed; response is `!SIMULATED-MALFORMED-REPLY!` plus newline, which is intentionally not valid measurement data | Subsequent unaffected commands execute normally |
| `SOURCE_TRIP` | Specified **virtual power-source channel** output state is forced OFF, including when the triggering command attempts `OUTP ON`; capture state aborted | State stays OFF until a later unaffected SCPI command re-enables that channel |

`channel` must be `null` except for `SOURCE_TRIP`, where it must select
one installed virtual source module (not an N679x electronic load). The
service refuses unsupported combinations, missing channels and malformed
scripts **before starting the SCPI listener**.

```json
{
  "schema_version": 1,
  "events": [
    {"first_command": 2, "count": 1, "mode": "SOURCE_TRIP", "channel": 1},
    {"first_command": 4, "count": 1, "mode": "TIMEOUT", "channel": null},
    {"first_command": 6, "count": 1, "mode": "DISCONNECT", "channel": null},
    {"first_command": 8, "count": 1, "mode": "MALFORMED_REPLY", "channel": null}
  ]
}
```

No fault script is loaded unless `--fault-plan` is explicitly specified.
Normal simulator replies are unchanged when no fault is scheduled.
The **server-first, read-only identity banner** remains available on the
separate identity port even if SCPI command faults are active. The banner
does not cryptographically authenticate a later SCPI connection; remain on
the trusted host/private LAN and never auto-route to real equipment.

## Verification (no hardware)

```bash
python -m pytest -q tests/test_milkv_sim_server.py tests/test_milkv_sim_tcp_fault_injection.py
ruff check src/keysight_n6700/sim_server.py src/keysight_n6700/sim_fault_plan.py tests/test_milkv_sim_tcp_fault_injection.py
mypy src/keysight_n6700
```

These are verification commands; they are **not evidence of execution** until
the exact PR head CI has run and succeeded. Socket tests use only
`127.0.0.1` and ephemeral ports.

## Application-side simulation integration and limitations

The application `ami3go/power_supply_test` draft PR #777 independently
implements offline **schema v3** fault windows for power-supply voltage
sag/overvoltage/output loss/OCP/thermal shutdown, DUT test output deviation,
assertion/evaluation failures, and missing samples from simulated
communication failures. Its evidence and optional report bundle are always
marked `SIMULATED` and never confer real DUT qualification.

**Not yet implemented:** a single state/time clock shared between this SCPI
TCP server and the application's electrical model, DUT console,
fault-injection schedule, or original generic test bodies. This TCP
command-index script **does not** inject analog measurements or genuine
socket transport errors into physical Keysight hardware. It is not a
substitute for HIL or failure-mode hardware validation.
