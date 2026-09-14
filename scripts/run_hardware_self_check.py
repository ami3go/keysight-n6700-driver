#!/usr/bin/env python3
"""Exercise the driver's full read-only API against a real N6700, and
optionally one guarded output test, producing a machine- and human-readable
report.

This is the driver's only script that talks to real hardware. Nothing here
runs unless a resource is given explicitly — there is no discovery, no
default resource, and no silent fallback to the simulator (LPDS-009 §18).

Read-only checks (always run, once ``--resource`` is given): identity,
connection state, capability/configuration introspection, self-test, the
error queue, module discovery, and per-channel model/serial/options,
measurement, setpoint-readback, and status queries. None of these write a
setpoint or change output/load-input state.

The guarded output test (opt-in, ``--output-test`` plus every one of
``--output-channel``/``--output-voltage``/``--output-current-limit``) sets a
setpoint, enables exactly one channel's output, waits for the voltage to
settle, measures it, and disables the output again in a ``finally`` block
regardless of outcome — then runs ``safe_shutdown()`` as a second net. It
requires typed confirmation (interactively, or ``--yes`` once you have
reviewed the parameters) before it energizes anything.

Examples:
    # Read-only checks only.
    python scripts/run_hardware_self_check.py --resource "USB0::0x0957::0x0907::MY43014421::INSTR"

    # Read-only checks plus one guarded output test on channel 1.
    python scripts/run_hardware_self_check.py --resource 192.168.1.50 --connection-type ethernet \\
        --output-test --output-channel 1 --output-voltage 5.0 --output-current-limit 0.5
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from keysight_n6700 import N6700, DriverError
from keysight_n6700.module_capabilities import ChannelCapabilities

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class CheckResult:
    name: str
    status: str  # "pass" | "fail" | "skip"
    detail: str = ""
    value: Any = None
    duration_s: float = 0.0


@dataclass
class Report:
    schema_version: str = "1.0"
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    resource: str = ""
    connection_type: str = ""
    identity: dict[str, Any] | None = None
    results: list[CheckResult] = field(default_factory=list)
    output_test: dict[str, Any] | None = None

    def summary(self) -> dict[str, int]:
        summary = {"pass": 0, "fail": 0, "skip": 0}
        for result in self.results:
            summary[result.status] = summary.get(result.status, 0) + 1
        return summary


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def run_check(results: list[CheckResult], name: str, func: Callable[[], Any]) -> Any | None:
    """Call ``func``, record a pass/fail :class:`CheckResult`, and print one line live."""
    started = time.monotonic()
    try:
        value = func()
    except Exception as exc:
        elapsed = time.monotonic() - started
        results.append(CheckResult(name, "fail", f"{type(exc).__name__}: {exc}", duration_s=elapsed))
        print(f"  [FAIL] {name}: {type(exc).__name__}: {exc}")
        return None
    elapsed = time.monotonic() - started
    results.append(CheckResult(name, "pass", value=_jsonable(value), duration_s=elapsed))
    print(f"  [PASS] {name} ({elapsed * 1000:.0f} ms)")
    return value


def skip_check(results: list[CheckResult], name: str, reason: str) -> None:
    results.append(CheckResult(name, "skip", reason))
    print(f"  [SKIP] {name}: {reason}")


def run_read_only_checks(drv: N6700, results: list[CheckResult]) -> dict[int, ChannelCapabilities] | None:
    print("\n== Identity, connection, and introspection ==")
    run_check(results, "get_identity", drv.get_identity)
    run_check(results, "get_connection_state", drv.get_connection_state)
    run_check(results, "check_communication", drv.check_communication)
    run_check(results, "get_driver_information", drv.get_driver_information)
    run_check(results, "get_capability_model", drv.get_capability_model)
    run_check(results, "validate_driver_capabilities", drv.validate_driver_capabilities)
    run_check(results, "get_driver_configuration_schema", drv.get_driver_configuration_schema)
    run_check(results, "get_driver_default_configuration", drv.get_driver_default_configuration)

    print("\n== Self-test and error queue ==")
    run_check(results, "self_test", drv.self_test)
    run_check(results, "get_all_device_errors", drv.get_all_device_errors)

    print("\n== Module discovery ==")
    channel_count = run_check(results, "channel_count", drv.channel_count)
    modules: dict[int, ChannelCapabilities] | None = run_check(
        results, "discover_modules", drv.discover_modules
    )
    if not modules:
        skip_check(results, "per-channel checks", "discover_modules did not return any channel")
        return None
    run_check(results, "list_channels", drv.list_channels)
    if channel_count:
        run_check(results, "validate_channel", lambda: drv.validate_channel(channel_count))

    print("\n== Per-channel checks ==")
    for channel, caps in sorted(modules.items()):
        _run_channel_checks(drv, results, channel, caps)

    run_check(results, "measure_all", drv.measure_all)
    return modules


def _run_channel_checks(
    drv: N6700, results: list[CheckResult], channel: int, caps: ChannelCapabilities
) -> None:
    prefix = f"channel[{channel}]"
    run_check(results, f"{prefix}.channel_model", lambda: drv.channel_model(channel))
    run_check(results, f"{prefix}.channel_serial", lambda: drv.channel_serial(channel))
    run_check(results, f"{prefix}.channel_options", lambda: drv.channel_options(channel))
    run_check(results, f"{prefix}.get_protection_status", lambda: drv.channel(channel).get_protection_status())
    run_check(results, f"{prefix}.get_operation_status", lambda: drv.get_operation_status(channel))
    run_check(results, f"{prefix}.get_questionable_status", lambda: drv.get_questionable_status(channel))

    if caps.module_type in {"power_supply", "smu"}:
        run_check(results, f"{prefix}.measure_dc_voltage", lambda: drv.measure_dc_voltage(channel))
        run_check(results, f"{prefix}.measure_dc_current", lambda: drv.measure_dc_current(channel))
        run_check(results, f"{prefix}.measure_dc_power", lambda: drv.measure_dc_power(channel))
        run_check(results, f"{prefix}.get_output_state", lambda: drv.get_output_state(channel))
        run_check(results, f"{prefix}.get_dc_voltage_setpoint", lambda: drv.get_dc_voltage_setpoint(channel))
        run_check(results, f"{prefix}.get_dc_current_setpoint", lambda: drv.get_dc_current_setpoint(channel))
        run_check(results, f"{prefix}.get_dc_voltage_limits", lambda: drv.get_dc_voltage_limits(channel))
        run_check(results, f"{prefix}.get_dc_current_limits", lambda: drv.get_dc_current_limits(channel))
        if caps.module_type == "smu":
            run_check(results, f"{prefix}.get_smu_mode", lambda: drv.get_smu_mode(channel))
    elif caps.module_type == "electronic_load":
        if caps.model == "SIM_LOAD" or caps.verified_real_load_commands:
            run_check(results, f"{prefix}.get_input", lambda: drv.load(channel).get_input())
            run_check(results, f"{prefix}.get_load_mode", lambda: drv.get_load_mode(channel))
        else:
            skip_check(
                results,
                f"{prefix}.load_queries",
                f"model {caps.model!r} is not in module_capabilities.VERIFIED_LOAD_MODELS",
            )
    else:
        skip_check(results, f"{prefix}.typed_queries", f"unclassified module type {caps.model!r}")


def run_guarded_output_test(
    drv: N6700,
    modules: dict[int, ChannelCapabilities],
    *,
    channel: int,
    voltage: float,
    current_limit: float,
    settle_timeout_s: float,
    confirm: Callable[[str], bool],
) -> dict[str, Any]:
    """Enable exactly one channel's output, measure it, and always disable it again."""
    outcome: dict[str, Any] = {
        "channel": channel,
        "requested_voltage_v": voltage,
        "requested_current_limit_a": current_limit,
        "status": "not_run",
    }

    caps = modules.get(channel)
    if caps is None:
        outcome["status"] = "skip"
        outcome["detail"] = f"channel {channel} was not discovered"
        return outcome
    if caps.module_type not in {"power_supply", "smu"}:
        outcome["status"] = "skip"
        outcome["detail"] = f"channel {channel} is a {caps.module_type}, not a power-supply/SMU output"
        return outcome

    if drv.get_output_state(channel):
        outcome["status"] = "skip"
        outcome["detail"] = (
            f"channel {channel} output is already ON; refusing to touch a live output "
            "with unknown provenance"
        )
        return outcome

    prompt = (
        f"About to set channel {channel} to {voltage:.6g} V / {current_limit:.6g} A limit "
        f"and enable its output on {caps.model}. Proceed?"
    )
    if not confirm(prompt):
        outcome["status"] = "skip"
        outcome["detail"] = "not confirmed"
        return outcome

    try:
        drv.set_dc_voltage(voltage, channel)
        drv.set_dc_current(current_limit, channel)
        readback = {
            "voltage_setpoint_v": drv.get_dc_voltage_setpoint(channel),
            "current_limit_a": drv.get_dc_current_setpoint(channel),
        }
        drv.enable_output(channel)
        try:
            measured_voltage = drv.wait_for_voltage_in_range(
                channel,
                voltage * 0.95 if voltage >= 0 else voltage * 1.05,
                voltage * 1.05 if voltage >= 0 else voltage * 0.95,
                timeout_s=settle_timeout_s,
            )
        except DriverError as exc:
            measured_voltage = drv.measure_dc_voltage(channel)
            outcome["settle_error"] = str(exc)
        measured_current = drv.measure_dc_current(channel)
        outcome["status"] = "pass"
        outcome["readback"] = readback
        outcome["measured_voltage_v"] = measured_voltage
        outcome["measured_current_a"] = measured_current
    except DriverError as exc:
        outcome["status"] = "fail"
        outcome["detail"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            drv.disable_output(channel)
        except DriverError as exc:
            outcome["disable_error"] = f"{type(exc).__name__}: {exc}"
        with suppress(DriverError):
            drv.safe_shutdown()

    return outcome


def _interactive_confirm(prompt: str) -> bool:
    response = input(f"{prompt} [type 'yes' to proceed] ")
    return response.strip().lower() == "yes"


def write_report(report: Report, results_dir: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = results_dir / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    document = {
        "schema_version": report.schema_version,
        "generated_at": report.generated_at,
        "resource": report.resource,
        "connection_type": report.connection_type,
        "identity": report.identity,
        "summary": report.summary(),
        "results": [asdict(r) for r in report.results],
        "output_test": report.output_test,
    }
    (run_dir / "report.json").write_text(json.dumps(document, indent=2, default=str) + "\n")

    summary = report.summary()
    lines = [
        f"# Hardware self-check — {report.generated_at}",
        "",
        f"Resource: `{report.resource}` ({report.connection_type})",
        f"Identity: `{report.identity}`" if report.identity else "Identity: unavailable",
        "",
        f"**{summary.get('pass', 0)} passed, {summary.get('fail', 0)} failed, "
        f"{summary.get('skip', 0)} skipped**",
        "",
        "| Check | Status | Duration (ms) | Detail |",
        "|---|---|---|---|",
    ]
    for result in report.results:
        detail = result.detail.replace("|", "\\|")
        lines.append(f"| {result.name} | {result.status} | {result.duration_s * 1000:.0f} | {detail} |")
    if report.output_test is not None:
        lines += ["", "## Guarded output test", "", f"```json\n{json.dumps(report.output_test, indent=2, default=str)}\n```"]
    (run_dir / "summary.md").write_text("\n".join(lines) + "\n")
    return run_dir


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--resource", required=True, help="VISA resource string, or host/IP for --connection-type ethernet")
    parser.add_argument("--connection-type", default="visa", choices=["visa", "usb", "ethernet", "socket"])
    parser.add_argument("--port", type=int, default=5025, help="TCP port for ethernet/socket (default 5025)")
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=15.0,
        help=(
            "communication timeout (default 15s). The transport's own default "
            "is 5s, which is marginal for *TST? on real hardware -- confirmed "
            "against a real N6700C, self-test took ~5.4s"
        ),
    )
    parser.add_argument("--results-dir", type=Path, default=REPO_ROOT / "results" / "hardware_self_check")

    output = parser.add_argument_group("guarded output test (all four required together)")
    output.add_argument("--output-test", action="store_true", help="enable the guarded output test")
    output.add_argument("--output-channel", type=int, default=None)
    output.add_argument("--output-voltage", type=float, default=None)
    output.add_argument("--output-current-limit", type=float, default=None)
    output.add_argument("--output-settle-timeout-s", type=float, default=5.0)
    output.add_argument("--yes", action="store_true", help="skip the interactive confirmation prompt")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)

    if args.output_test and (
        args.output_channel is None or args.output_voltage is None or args.output_current_limit is None
    ):
        print(
            "--output-test requires --output-channel, --output-voltage, and "
            "--output-current-limit to all be given explicitly.",
            file=sys.stderr,
        )
        return 2

    report = Report(resource=args.resource, connection_type=args.connection_type)
    drv = N6700()
    print(f"Connecting to {args.resource!r} ({args.connection_type})...")
    try:
        drv.connect(
            args.resource,
            connection_type=args.connection_type,
            port=args.port,
            timeout_s=args.timeout_s,
            discover=False,
        )
    except DriverError as exc:
        print(f"Connection failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    try:
        report.identity = drv.get_connection_state().get("identity")
        modules = run_read_only_checks(drv, report.results)

        if args.output_test and modules is not None:
            print("\n== Guarded output test ==")
            confirm = (lambda _prompt: True) if args.yes else _interactive_confirm
            report.output_test = run_guarded_output_test(
                drv,
                modules,
                channel=args.output_channel,
                voltage=args.output_voltage,
                current_limit=args.output_current_limit,
                settle_timeout_s=args.output_settle_timeout_s,
                confirm=confirm,
            )
            print(f"  output test: {report.output_test['status']}")
    finally:
        drv.disconnect()

    run_dir = write_report(report, args.results_dir)
    summary = report.summary()
    print(
        f"\n{summary.get('pass', 0)} passed, {summary.get('fail', 0)} failed, "
        f"{summary.get('skip', 0)} skipped. Report: {run_dir / 'summary.md'}"
    )
    return 1 if summary.get("fail", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
