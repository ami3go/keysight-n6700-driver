#!/usr/bin/env python3
"""Guarded real-hardware load-transient scope test.

Energizes a power-supply channel to feed a DUT power supply, steps an
electronic-load channel's current through a trigger level, and uses the
load's level-triggered digitizer to capture the resulting voltage transient
on the DUT's output. This switches real outputs on — every value is a
required CLI argument with no defaults, and ``--confirm`` must be passed
explicitly. Both the source output and load input are disabled in a
``finally`` block regardless of outcome, then independently re-verified.
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from keysight_n6700 import N6700


@dataclass
class EdgeEstimate:
    index: int | None
    baseline_v: float | None
    settled_v: float | None


def estimate_edge(values: tuple[float, ...], *, expected_index: int, baseline_points: int = 64) -> EdgeEstimate:
    """Locate the largest-slope sample near the expected trigger index.

    A plumbing sanity check, not a DUT-specific pass/fail gate: it reports
    where the captured waveform actually moved, so the caller can compare it
    against where the trigger was configured to fire (``expected_index``).
    """
    if len(values) < baseline_points + 2:
        return EdgeEstimate(None, None, None)
    baseline_v = sum(values[:baseline_points]) / baseline_points
    settled_v = sum(values[-baseline_points:]) / baseline_points
    window_lo = max(0, expected_index - 64)
    window_hi = min(len(values) - 1, expected_index + 64)
    best_index = window_lo
    best_slope = -1.0
    for i in range(window_lo, window_hi):
        slope = abs(values[i + 1] - values[i])
        if slope > best_slope:
            best_slope = slope
            best_index = i
    return EdgeEstimate(best_index, baseline_v, settled_v)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--connection-type", default="ethernet")
    parser.add_argument("--source-channel", type=int, required=True)
    parser.add_argument("--source-voltage", type=float, required=True)
    parser.add_argument("--source-current-limit", type=float, required=True)
    parser.add_argument("--load-channel", type=int, required=True)
    parser.add_argument("--load-initial-current", type=float, required=True)
    parser.add_argument("--load-final-current", type=float, required=True)
    parser.add_argument("--trigger-current-level", type=float, required=True)
    parser.add_argument("--trigger-slope", choices=("pos", "neg"), default="pos")
    parser.add_argument("--points", type=int, default=1024)
    parser.add_argument("--interval-s", type=float, default=20.48e-6)
    parser.add_argument("--offset-points", type=int, default=-512)
    parser.add_argument("--timeout-s", type=float, default=20.0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--protocol-trace", type=Path, default=None)
    parser.add_argument("--confirm", action="store_true", required=True)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    outcome: dict[str, Any] = {
        "schema_version": "1.0",
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "host": args.host,
        "source_channel": args.source_channel,
        "load_channel": args.load_channel,
        "state": "FAILED",
    }

    driver: N6700 | None = None
    try:
        driver = N6700()
        driver.connect(
            args.host,
            connection_type=args.connection_type,
            timeout_s=args.timeout_s,
            discover=True,
            protocol_trace=str(args.protocol_trace) if args.protocol_trace else None,
        )
        outcome["idn"] = driver.get_identity()

        modules = driver.discover_modules()
        source_caps = modules[args.source_channel]
        load_caps = modules[args.load_channel]
        outcome["source_model"] = source_caps.model
        outcome["load_model"] = load_caps.model
        if source_caps.module_type not in ("power_supply", "smu"):
            raise RuntimeError(f"channel {args.source_channel} is {source_caps.module_type!r}, not a source")
        if load_caps.module_type != "electronic_load":
            raise RuntimeError(f"channel {args.load_channel} is {load_caps.module_type!r}, not an electronic load")
        if not load_caps.supports_digitizing:
            raise RuntimeError(f"{load_caps.model} does not support digitizing")

        if driver.get_output_state(args.source_channel):
            raise RuntimeError(f"channel {args.source_channel} output is already on; refusing to proceed")
        load = driver.load(args.load_channel)
        if load.get_input():
            raise RuntimeError(f"channel {args.load_channel} input is already on; refusing to proceed")

        pre_existing_errors = [{"code": e.code, "message": e.message} for e in driver.drain_errors()]
        outcome["pre_existing_errors"] = pre_existing_errors

        source = driver.power_supply(args.source_channel)
        source.set_voltage_setpoint(args.source_voltage)
        source.set_current_limit(args.source_current_limit)
        source.output_on()
        driver.wait_for_voltage_in_range(
            args.source_channel,
            args.source_voltage * 0.95,
            args.source_voltage * 1.05,
            timeout_s=5.0,
        )

        load.configure_cc(args.load_initial_current, input_on=True, verify=True)
        before = {
            "source_v": driver.measure_dc_voltage(args.source_channel),
            "source_a": driver.measure_dc_current(args.source_channel),
            "load_v": driver.measure_dc_voltage(args.load_channel),
            "load_a": driver.measure_dc_current(args.load_channel),
        }
        outcome["before"] = before

        load.configure_digitize(
            "voltage", points=args.points, interval_s=args.interval_s, offset_points=args.offset_points
        )
        load.configure_digitize_trigger(
            "current",
            level=args.trigger_current_level,
            slope=args.trigger_slope,
            source_channel=args.load_channel,
        )
        driver.check_errors()
        load.arm_digitize()
        load.wait_digitize_armed(timeout_s=5.0)

        # CC mode was already selected before arming; only the setpoint
        # changes now -- re-sending FUNC CURR while armed is rejected by
        # real firmware with SCPI error 315.
        load.set_load_current(args.load_final_current)
        driver.check_errors()
        load.wait_digitize_complete(timeout_s=max(args.timeout_s, 5.0))

        # Fetch immediately: a later MEASure query starts a new acquisition
        # and overwrites this triggered buffer on real firmware.
        capture = load.fetch_digitized_array("voltage", transfer_format="real32")
        if len(capture.values) != args.points:
            raise RuntimeError(f"expected {args.points} points, got {len(capture.values)}")

        after = {
            "source_v": driver.measure_dc_voltage(args.source_channel),
            "source_a": driver.measure_dc_current(args.source_channel),
            "load_v": driver.measure_dc_voltage(args.load_channel),
            "load_a": driver.measure_dc_current(args.load_channel),
        }
        outcome["after"] = after

        expected_index = -args.offset_points
        edge = estimate_edge(capture.values, expected_index=expected_index)
        outcome["edge_estimate"] = {
            "expected_index": expected_index,
            "detected_index": edge.index,
            "detected_offset_samples": None if edge.index is None else edge.index - expected_index,
            "baseline_v": edge.baseline_v,
            "settled_v": edge.settled_v,
        }

        times = [(i + (capture.offset_points or 0)) * (capture.interval_s or 0) for i in range(len(capture.values))]
        waveform_path = args.output / "load_transient_waveform.csv"
        with waveform_path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(("time_s", "voltage_v"))
            writer.writerows(zip(times, capture.values, strict=True))
        outcome["waveform_csv"] = str(waveform_path)
        outcome["sample_interval_s"] = capture.interval_s
        outcome["offset_points"] = capture.offset_points
        outcome["raw_payload_sha256"] = capture.raw_sha256

        load.set_load_current(args.load_initial_current)
        outcome["state"] = "CAPTURED"
    except Exception as exc:
        outcome["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if driver is not None:
            try:
                load = driver.load(args.load_channel)
                if load.digitize_status.state.value not in {"aborted", "fetched", "idle"}:
                    load.abort_digitize()
                load.input_off()
                driver.disable_output(args.source_channel)
                outcome["load_input_off_confirmed"] = not driver.load(args.load_channel).get_input()
                outcome["source_output_off_confirmed"] = not driver.get_output_state(args.source_channel)
            except Exception as exc:
                outcome["cleanup_error"] = f"{type(exc).__name__}: {exc}"
            try:
                outcome["final_errors"] = [
                    {"code": e.code, "message": e.message} for e in driver.drain_errors()
                ]
            except Exception as exc:
                outcome["error_queue_unavailable"] = str(exc)
            try:
                driver.disconnect()
            except Exception as exc:
                outcome["disconnect_error"] = str(exc)
        if outcome.get("load_input_off_confirmed") is not True or outcome.get("source_output_off_confirmed") is not True:
            outcome["state"] = "FAILED_UNVERIFIED_SAFE_STATE"
        summary_path = args.output / "load_transient_summary.json"
        summary_path.write_text(json.dumps(outcome, indent=2) + "\n", encoding="utf-8")

    print(outcome["state"])
    print(json.dumps(outcome, indent=2))
    return 0 if outcome["state"] == "CAPTURED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
