#!/usr/bin/env python3
"""Measure the N679xA post-capture SENSe leak: DC MEASure latency before/after.

Non-energizing. Refuses to run unless every power-supply output and every load
input is verified OFF, and never turns anything on. Uses only API present in
both v0.8.0 and the fix, so the same script yields "before fix" and "after fix"
metrics when run with each driver version.

Sequence on the chosen load channel:

1. ``reset``       raw SCPI: 1024 points x 20.48 us, offset 0, VOLT+CURR enabled
2. ``after_fetch`` large BUS-triggered voltage capture, fetched, not yet aborted
3. ``after_abort`` ``abort_digitize()`` (the release step)
4. ``final_reset`` raw SCPI defaults again, so the bench is left clean

At each phase it records the sweep readback and the median/max latency of
``MEAS:VOLT?``, ``MEAS:CURR?`` and ``MEAS:POW?`` on that channel.

    python scripts/measure_digitize_sense_leak.py --resource 192.168.0.6 \\
        --connection-type ethernet --channel 2 --out results/sense_leak/before.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import keysight_n6700
from keysight_n6700 import N6700

CAPTURE_POINTS = 48830
CAPTURE_INTERVAL_S = 10.24e-6
SAMPLES = 5


def _sense(drv: N6700, channel: int) -> dict[str, Any]:
    sel = f"(@{channel})"

    def flag(kind: str) -> bool:
        return drv.query_scpi(f"SENS:FUNC:{kind}? {sel}").strip().upper() in {"1", "+1", "ON"}

    return {
        "points": int(float(drv.query_scpi(f"SENS:SWE:POIN? {sel}"))),
        "interval_s": float(drv.query_scpi(f"SENS:SWE:TINT? {sel}")),
        "offset_points": int(float(drv.query_scpi(f"SENS:SWE:OFFS:POIN? {sel}"))),
        "voltage_enabled": flag("VOLT"),
        "current_enabled": flag("CURR"),
    }


def _latency(call: Any) -> dict[str, float]:
    times = []
    for _ in range(SAMPLES):
        began = time.monotonic()
        call()
        times.append((time.monotonic() - began) * 1000.0)
    return {"median_ms": round(statistics.median(times), 2), "max_ms": round(max(times), 2)}


def _phase(drv: N6700, channel: int, name: str) -> dict[str, Any]:
    load = drv.load(channel)
    return {
        "phase": name,
        "sense": _sense(drv, channel),
        "meas_volt": _latency(load.measure_voltage),
        "meas_curr": _latency(load.measure_current),
        "meas_pow": _latency(lambda: drv.measure_dc_power(channel)),
    }


def _reset_defaults(drv: N6700, channel: int) -> None:
    sel = f"(@{channel})"
    for command in (
        f"SENS:SWE:OFFS:POIN 0,{sel}",
        f"SENS:SWE:POIN 1024,{sel}",
        f"SENS:SWE:TINT 2.048e-05,{sel}",
        f"SENS:FUNC:VOLT ON,{sel}",
        f"SENS:FUNC:CURR ON,{sel}",
    ):
        drv.write_scpi(command)
    drv.check_errors()


def _require_all_off(drv: N6700, modules: dict[int, Any]) -> dict[str, bool]:
    states: dict[str, bool] = {}
    for channel, caps in sorted(modules.items()):
        if caps.module_type == "electronic_load":
            states[f"load_input_ch{channel}"] = bool(drv.load(channel).get_input())
        elif caps.module_type in {"power_supply", "smu"}:
            states[f"output_ch{channel}"] = bool(drv.power_supply(channel).get_output())
    if any(states.values()):
        raise SystemExit(f"refusing to run: an output or load input is ON: {states}")
    return states


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--resource", required=True)
    parser.add_argument("--connection-type", default="ethernet")
    parser.add_argument("--port", type=int, default=5025)
    parser.add_argument("--channel", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    drv = N6700()
    drv.connect(args.resource, connection_type=args.connection_type, port=args.port,
                timeout_s=15.0, discover=True)
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "driver_version": keysight_n6700.__version__,
        "driver_path": str(Path(keysight_n6700.__file__).resolve().parent),
        "capture": {"points": CAPTURE_POINTS, "interval_s": CAPTURE_INTERVAL_S, "trigger": "bus"},
        "samples_per_metric": SAMPLES,
        "phases": [],
    }
    try:
        if hasattr(drv, "set_auto_shutdown_on_disconnect"):
            drv.set_auto_shutdown_on_disconnect(False)
        modules = drv.discover_modules()
        caps = modules.get(args.channel)
        if caps is None or caps.module_type != "electronic_load" or not caps.supports_digitizing:
            raise SystemExit(f"channel {args.channel} is not a digitizing electronic load: {caps}")
        report["identity"] = str(drv.idn())
        report["preflight_off"] = _require_all_off(drv, modules)

        _reset_defaults(drv, args.channel)
        report["phases"].append(_phase(drv, args.channel, "reset"))

        load = drv.load(args.channel)
        load.configure_digitize("voltage", points=CAPTURE_POINTS, interval_s=CAPTURE_INTERVAL_S)
        load.configure_digitize_trigger("bus")
        load.arm_digitize()
        load.wait_digitize_armed(timeout_s=5.0)
        load.trigger_digitize()
        load.wait_digitize_complete(timeout_s=10.0)
        fetched = load.fetch_digitized_array()
        report["capture"]["fetched_points"] = len(fetched.values)
        report["phases"].append(_phase(drv, args.channel, "after_fetch"))

        load.abort_digitize()
        report["restore_status"] = getattr(load, "measurement_restore_status", "not_available")
        report["phases"].append(_phase(drv, args.channel, "after_abort"))

        _reset_defaults(drv, args.channel)
        report["phases"].append(_phase(drv, args.channel, "final_reset"))
        report["postflight_off"] = _require_all_off(drv, modules)
    finally:
        drv.disconnect()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for phase in report["phases"]:
        s = phase["sense"]
        print(f"{phase['phase']:12} POIN={s['points']:6} TINT={s['interval_s']:.3e} "
              f"VOLT={phase['meas_volt']['median_ms']:7.1f} ms CURR={phase['meas_curr']['median_ms']:7.1f} ms "
              f"POW={phase['meas_pow']['median_ms']:7.1f} ms")
    print("restore_status:", report.get("restore_status"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
