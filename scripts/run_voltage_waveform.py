#!/usr/bin/env python3
"""Preview or run a finite voltage waveform with an N6700 source channel.

Pulse, ramp and sine use the instrument's matching built-in ARB shape; CSV
uses a user-defined ARB. All run through the driver's bounded
configure/arm/trigger/wait lifecycle. Preview is the default; pass
``--execute`` to make any instrument connection or change any output.

Examples:
    # Preview a 12 V -> 6 V -> 12 V dip.
    python scripts/run_voltage_waveform.py --maximum-voltage 18 pulse --baseline 12 --level 6 --width 0.02

    # Run the same profile against the simulator.
    python scripts/run_voltage_waveform.py --maximum-voltage 18 --current-limit 1 --execute pulse --baseline 12 --level 6 --width 0.02

    # Run a 1 Hz, 5 V peak sine centered on 5 V over Ethernet.
    python scripts/run_voltage_waveform.py --connection-type ethernet --resource 192.168.0.6 --channel 1 --maximum-voltage 12 --current-limit 1 --execute sine --offset 5 --amplitude 5 --frequency 1

CSV profiles require a header with ``voltage_v,dwell_s`` columns and contain
one voltage/dwell pair per row.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from keysight_n6700 import N6700, ArbPulse, ArbRamp, ArbSine, DriverError
from keysight_n6700.waveforms import ArbShape

MAX_ARB_POINTS = 511
MIN_DWELL_S = 1.024e-6
MAX_DWELL_S = 262.144


@dataclass(frozen=True)
class WaveformProfile:
    """A finite user-defined voltage waveform and its normal DC baseline."""

    shape: str
    levels_v: tuple[float, ...]
    dwell_s: tuple[float, ...]
    baseline_v: float
    waveform: ArbShape | None = None

    @property
    def duration_s(self) -> float:
        return sum(self.dwell_s)


def _finite(value: float, name: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _nonnegative(value: float, name: str) -> float:
    result = _finite(value, name)
    if result < 0:
        raise ValueError(f"{name} must be >= 0")
    return result


def _positive(value: float, name: str) -> float:
    result = _finite(value, name)
    if result <= 0:
        raise ValueError(f"{name} must be > 0")
    return result


def build_pulse_profile(
    *, baseline_v: float, level_v: float, delay_s: float, width_s: float, recovery_s: float
) -> WaveformProfile:
    baseline = _finite(baseline_v, "baseline")
    level = _finite(level_v, "level")
    delay = _nonnegative(delay_s, "delay")
    width = _positive(width_s, "width")
    recovery = _nonnegative(recovery_s, "recovery")
    return WaveformProfile(
        shape="pulse",
        levels_v=(baseline, level, baseline),
        dwell_s=(delay, width, recovery),
        baseline_v=baseline,
        waveform=ArbPulse(
            baseline, level, width, start_time_s=delay, end_time_s=recovery
        ),
    )


def build_ramp_profile(
    *,
    start_v: float,
    end_v: float,
    delay_s: float,
    ramp_time_s: float,
    hold_s: float,
    points: int,
) -> WaveformProfile:
    start = _finite(start_v, "start")
    end = _finite(end_v, "end")
    delay = _nonnegative(delay_s, "delay")
    ramp_time = _positive(ramp_time_s, "ramp-time")
    hold = _nonnegative(hold_s, "hold")
    if isinstance(points, bool) or not isinstance(points, int) or not 2 <= points <= MAX_ARB_POINTS - 2:
        raise ValueError(f"points must be an integer within 2..{MAX_ARB_POINTS - 2}")

    levels: list[float] = []
    dwells: list[float] = []
    if delay > 0:
        levels.append(start)
        dwells.append(delay)

    dwell = ramp_time / points
    if dwell < MIN_DWELL_S:
        raise ValueError(
            f"ramp-time/points must be at least {MIN_DWELL_S:g} s for all supported modules"
        )
    # A UDEF ARB is step-based. These equally spaced points approximate a
    # linear ramp while preserving the requested total ramp duration.
    for index in range(1, points + 1):
        levels.append(start + (end - start) * index / points)
        dwells.append(dwell)
    if hold > 0:
        levels.append(end)
        dwells.append(hold)

    return WaveformProfile(
        "ramp",
        tuple(levels),
        tuple(dwells),
        start,
        ArbRamp(start, end, ramp_time, start_time_s=delay, end_time_s=hold),
    )


def build_sine_profile(
    *, offset_v: float, amplitude_v: float, frequency_hz: float, points_per_cycle: int
) -> WaveformProfile:
    offset = _finite(offset_v, "offset")
    amplitude = _positive(amplitude_v, "amplitude")
    frequency = _positive(frequency_hz, "frequency")
    if (
        isinstance(points_per_cycle, bool)
        or not isinstance(points_per_cycle, int)
        or not 4 <= points_per_cycle <= MAX_ARB_POINTS
    ):
        raise ValueError(f"points-per-cycle must be an integer within 4..{MAX_ARB_POINTS}")
    dwell = 1.0 / (frequency * points_per_cycle)
    if dwell < MIN_DWELL_S:
        maximum_frequency = 1.0 / (MIN_DWELL_S * points_per_cycle)
        raise ValueError(
            "frequency is too high for the selected points-per-cycle; "
            f"maximum is {maximum_frequency:g} Hz"
        )
    levels = tuple(
        offset + amplitude * math.sin(2.0 * math.pi * index / points_per_cycle)
        for index in range(points_per_cycle)
    )
    return WaveformProfile(
        "sine",
        levels,
        (dwell,) * points_per_cycle,
        offset,
        ArbSine(amplitude, offset, frequency),
    )


def load_csv_profile(path: Path) -> WaveformProfile:
    levels: list[float] = []
    dwells: list[float] = []
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        required = {"voltage_v", "dwell_s"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError("CSV must have voltage_v,dwell_s header columns")
        for row_number, row in enumerate(reader, start=2):
            levels.append(_finite(row["voltage_v"], f"CSV row {row_number} voltage_v"))
            dwell = _nonnegative(row["dwell_s"], f"CSV row {row_number} dwell_s")
            if 0 < dwell < MIN_DWELL_S:
                raise ValueError(
                    f"CSV row {row_number} dwell_s must be 0 or at least {MIN_DWELL_S:g} s"
                )
            dwells.append(dwell)
    if not levels:
        raise ValueError("CSV profile must contain at least one data row")
    return WaveformProfile("csv", tuple(levels), tuple(dwells), levels[0])


def build_profile(args: argparse.Namespace) -> WaveformProfile:
    if args.waveform == "pulse":
        return build_pulse_profile(
            baseline_v=args.baseline,
            level_v=args.level,
            delay_s=args.delay,
            width_s=args.width,
            recovery_s=args.recovery,
        )
    if args.waveform == "ramp":
        return build_ramp_profile(
            start_v=args.start,
            end_v=args.end,
            delay_s=args.delay,
            ramp_time_s=args.ramp_time,
            hold_s=args.hold,
            points=args.points,
        )
    if args.waveform == "sine":
        return build_sine_profile(
            offset_v=args.offset,
            amplitude_v=args.amplitude,
            frequency_hz=args.frequency,
            points_per_cycle=args.points_per_cycle,
        )
    if args.waveform == "csv":
        return load_csv_profile(args.file)
    raise ValueError(f"unsupported waveform {args.waveform!r}")


def validate_profile(
    profile: WaveformProfile,
    *,
    minimum_voltage_v: float,
    maximum_voltage_v: float,
    repeat_count: int,
    maximum_duration_s: float,
) -> None:
    minimum = _finite(minimum_voltage_v, "minimum-voltage")
    maximum = _finite(maximum_voltage_v, "maximum-voltage")
    duration_limit = _positive(maximum_duration_s, "maximum-duration")
    if minimum < 0 or maximum <= minimum:
        raise ValueError("require 0 <= minimum-voltage < maximum-voltage")
    if (
        isinstance(repeat_count, bool)
        or not isinstance(repeat_count, int)
        or not 1 <= repeat_count <= 16_777_216
    ):
        raise ValueError("repeat-count must be an integer within 1..16777216")
    if len(profile.levels_v) != len(profile.dwell_s):
        raise ValueError("profile voltage and dwell arrays must have the same length")
    if not 1 <= len(profile.levels_v) <= MAX_ARB_POINTS:
        raise ValueError(f"profile must contain 1..{MAX_ARB_POINTS} points")
    level_bounds = (
        profile.waveform.level_bounds
        if profile.waveform is not None
        else (min(profile.levels_v), max(profile.levels_v))
    )
    if level_bounds[0] < minimum or level_bounds[1] > maximum:
        raise ValueError(
            f"all voltage points must be within the approved {minimum:g}..{maximum:g} V envelope"
        )
    if any(dwell < 0 for dwell in profile.dwell_s):
        raise ValueError("all dwell values must be >= 0")
    if any(0 < dwell < MIN_DWELL_S or dwell > MAX_DWELL_S for dwell in profile.dwell_s):
        raise ValueError(
            f"nonzero dwell values must be within {MIN_DWELL_S:g}..{MAX_DWELL_S:g} s"
        )
    total_duration = profile.duration_s * repeat_count
    if total_duration > duration_limit:
        raise ValueError(
            f"repeated profile duration {total_duration:g} s exceeds maximum-duration "
            f"{duration_limit:g} s"
        )


def profile_summary(profile: WaveformProfile, repeat_count: int) -> dict[str, Any]:
    return {
        "shape": profile.shape,
        "points": len(profile.levels_v),
        "baseline_v": profile.baseline_v,
        "minimum_level_v": min(profile.levels_v),
        "maximum_level_v": max(profile.levels_v),
        "single_run_duration_s": profile.duration_s,
        "repeat_count": repeat_count,
        "total_duration_s": profile.duration_s * repeat_count,
    }


def _connect(args: argparse.Namespace) -> N6700:
    if args.connection_type == "simulated":
        return N6700.connect_simulated(timeout_s=args.timeout)
    if args.connection_type == "ethernet":
        if not args.resource:
            raise ValueError("--resource HOST is required for an Ethernet connection")
        return N6700.connect_ethernet(args.resource, args.port, timeout_s=args.timeout)
    if not args.resource:
        raise ValueError("--resource VISA_RESOURCE is required for a VISA connection")
    return N6700.connect_visa(args.resource, timeout_s=args.timeout)


def _enabled_channels(driver: N6700) -> list[int]:
    enabled: list[int] = []
    for channel, capabilities in driver.discover_modules().items():
        if capabilities.module_type in {"power_supply", "smu"}:
            active = driver.get_output_state(channel)
        elif capabilities.module_type == "electronic_load":
            active = driver.load(channel).get_input()
        else:
            continue
        if active:
            enabled.append(channel)
    return enabled


def _interactive_confirm(prompt: str) -> bool:
    print(prompt)
    return input("Type GENERATE to continue: ").strip() == "GENERATE"


def run_profile(
    driver: N6700,
    profile: WaveformProfile,
    args: argparse.Namespace,
    *,
    confirm: Callable[[str], bool],
) -> dict[str, Any]:
    modules = driver.discover_modules()
    capabilities = modules.get(args.channel)
    if capabilities is None:
        raise ValueError(f"channel {args.channel} was not discovered")
    if capabilities.module_type not in {"power_supply", "smu"}:
        raise ValueError(f"channel {args.channel} is {capabilities.module_type}, not a source output")
    if not capabilities.supports_voltage_arb:
        raise ValueError(f"module {capabilities.model} does not advertise voltage ARB support")

    active = _enabled_channels(driver)
    if active:
        raise ValueError(
            f"channels {active} are already enabled; refusing to change a live mainframe"
        )

    prompt = (
        f"About to energize {capabilities.model} channel {args.channel} at "
        f"{profile.baseline_v:g} V with a {args.current_limit:g} A current limit and run "
        f"the {profile.shape} profile ({len(profile.levels_v)} points, "
        f"{profile.duration_s * args.repeat_count:g} s)."
    )
    if not confirm(prompt):
        return {"status": "cancelled", **profile_summary(profile, args.repeat_count)}

    try:
        driver.set_dc_current(args.current_limit, args.channel)
        driver.set_dc_voltage(profile.baseline_v, args.channel)
        actual_slew = driver.set_voltage_slew_rate(args.channel, args.slew)
        if profile.waveform is None:
            configuration = driver.configure_voltage_arb(
                args.channel,
                profile.levels_v,
                profile.dwell_s,
                minimum_voltage_v=args.minimum_voltage,
                maximum_voltage_v=args.maximum_voltage,
                repeat_count=args.repeat_count,
                maximum_duration_s=args.maximum_duration,
            )
        else:
            configuration = driver.configure_voltage_arb_waveform(
                args.channel,
                profile.waveform,
                minimum_voltage_v=args.minimum_voltage,
                maximum_voltage_v=args.maximum_voltage,
                repeat_count=args.repeat_count,
                maximum_duration_s=args.maximum_duration,
            )
        driver.configure_voltage_transient_trigger(args.channel, "bus")
        driver.enable_output(args.channel)
        tolerance = _positive(args.settle_tolerance, "settle-tolerance")
        driver.wait_for_voltage_in_range(
            args.channel,
            profile.baseline_v - tolerance,
            profile.baseline_v + tolerance,
            timeout_s=args.ready_timeout,
        )
        token = driver.arm_voltage_transient(args.channel)
        driver.wait_voltage_transient_ready(
            args.channel, token, timeout_s=args.ready_timeout
        )
        driver.trigger_voltage_transient(args.channel, token)
        status = driver.wait_voltage_transient_complete(
            args.channel, token, timeout_s=args.complete_timeout
        )
        driver.check_errors()
        return {
            "status": status.state,
            "module": capabilities.model,
            "channel": args.channel,
            "actual_slew_v_per_s": actual_slew,
            "configuration": asdict(configuration),
            **profile_summary(profile, args.repeat_count),
        }
    finally:
        try:
            driver.abort_voltage_transient(args.channel)
        finally:
            try:
                driver.disable_output(args.channel)
            finally:
                driver.safe_shutdown()


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--connection-type",
        choices=["simulated", "ethernet", "visa"],
        default="simulated",
        help="instrument transport (default: simulated)",
    )
    parser.add_argument("--resource", help="Ethernet host/IP or VISA resource")
    parser.add_argument("--port", type=int, default=5025, help="Ethernet SCPI port")
    parser.add_argument("--timeout", type=float, default=10.0, help="transport timeout in seconds")
    parser.add_argument("--channel", type=int, default=1)
    parser.add_argument(
        "--minimum-voltage", type=float, default=0.0, help="approved DUT/fixture lower bound"
    )
    parser.add_argument(
        "--maximum-voltage",
        type=float,
        required=True,
        help="approved DUT/fixture upper bound; not merely the module rating",
    )
    parser.add_argument(
        "--current-limit", type=float, help="required with --execute; source current limit in A"
    )
    parser.add_argument("--repeat-count", type=int, default=1)
    parser.add_argument("--maximum-duration", type=float, default=60.0)
    parser.add_argument(
        "--slew",
        default="max",
        help="voltage slew in V/s or 'max' (affects all voltage transitions)",
    )
    parser.add_argument("--settle-tolerance", type=float, default=0.1, help="baseline tolerance in V")
    parser.add_argument("--ready-timeout", type=float, default=5.0)
    parser.add_argument(
        "--complete-timeout",
        type=float,
        default=None,
        help="completion timeout; default derives it from profile duration",
    )
    parser.add_argument(
        "--execute", action="store_true", help="connect and energize; without this, only preview"
    )
    parser.add_argument("--yes", action="store_true", help="skip typed confirmation")
    parser.add_argument("--show-points", action="store_true", help="include all points in preview")

    subparsers = parser.add_subparsers(dest="waveform", required=True)

    pulse = subparsers.add_parser("pulse", help="baseline -> event level -> baseline")
    pulse.add_argument("--baseline", type=float, required=True)
    pulse.add_argument("--level", type=float, required=True, help="dip or peak voltage")
    pulse.add_argument("--delay", type=float, default=0.01)
    pulse.add_argument("--width", type=float, required=True)
    pulse.add_argument("--recovery", type=float, default=0.01)

    ramp = subparsers.add_parser("ramp", help="built-in linear voltage ramp")
    ramp.add_argument("--start", type=float, required=True)
    ramp.add_argument("--end", type=float, required=True)
    ramp.add_argument("--delay", type=float, default=0.01)
    ramp.add_argument("--ramp-time", type=float, required=True)
    ramp.add_argument("--hold", type=float, default=0.01)
    ramp.add_argument("--points", type=int, default=100)

    sine = subparsers.add_parser("sine", help="built-in sine; repeat-count adds cycles")
    sine.add_argument("--offset", type=float, required=True)
    sine.add_argument("--amplitude", type=float, required=True, help="positive peak amplitude")
    sine.add_argument("--frequency", type=float, required=True, help="frequency in Hz")
    sine.add_argument("--points-per-cycle", type=int, default=100)

    csv_parser = subparsers.add_parser("csv", help="load voltage_v,dwell_s rows from a CSV file")
    csv_parser.add_argument("--file", type=Path, required=True)
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        if args.execute and args.current_limit is None:
            raise ValueError("--current-limit is required with --execute")
        if args.current_limit is not None:
            args.current_limit = _positive(args.current_limit, "current-limit")
        if args.slew.strip().lower() != "max":
            args.slew = _positive(float(args.slew), "slew")
        profile = build_profile(args)
        validate_profile(
            profile,
            minimum_voltage_v=args.minimum_voltage,
            maximum_voltage_v=args.maximum_voltage,
            repeat_count=args.repeat_count,
            maximum_duration_s=args.maximum_duration,
        )
        preview = profile_summary(profile, args.repeat_count)
        if args.show_points:
            preview["levels_v"] = profile.levels_v
            preview["dwell_s"] = profile.dwell_s
        print(json.dumps(preview, indent=2))
        if not args.execute:
            print("Preview only. Pass --execute and --current-limit to run it.")
            return 0

        driver = _connect(args)
        try:
            confirm = (lambda _prompt: True) if args.yes else _interactive_confirm
            result = run_profile(driver, profile, args, confirm=confirm)
            print(json.dumps(result, indent=2))
            return 0 if result["status"] in {"complete", "cancelled"} else 1
        finally:
            driver.disconnect()
    except (DriverError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
