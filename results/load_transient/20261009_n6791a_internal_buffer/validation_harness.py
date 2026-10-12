"""Run the reviewed load-transient matrix with N6791A voltage digitizing."""

from __future__ import annotations

import csv
import json
import sys
from dataclasses import asdict
from pathlib import Path


APP_ROOT = Path("/home/codepc/github/ami3go/power_supply_test-worktrees/slavs")
DRIVER_ROOT = Path("/home/codepc/github/ami3go/keysight-n6700-driver")
sys.path[:0] = [str(DRIVER_ROOT / "src"), str(APP_ROOT)]

from keysight_n6700 import N6700, __version__  # noqa: E402
from testbench_core.bench import BenchConfig, N6700BuckBench  # noqa: E402
from tests.hil.extended_support import analyze_load_transient  # noqa: E402


VIN_VALUES = (8.0, 13.5, 18.0)
LOW_A = 0.5
HIGH_A = 2.5
TRIGGER_A = (LOW_A + HIGH_A) / 2.0
POINTS = 1024
INTERVAL_S = 20.48e-6
OFFSET_POINTS = -(POINTS // 2)
OUTPUT_DIR = Path("/home/codepc/github/ami3go/power_supply_test/results/n6700_load_scope_matrix")


def write_waveform(path: Path, times: list[float], values: tuple[float, ...]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=("time_s", "vout_v"))
        writer.writeheader()
        writer.writerows(
            {"time_s": time_s, "vout_v": value}
            for time_s, value in zip(times, values, strict=True)
        )


def main() -> None:
    cfg = BenchConfig.load(APP_ROOT / "config" / "pytest_bench.json")
    if cfg.resource != "192.168.0.6":
        raise RuntimeError(f"unexpected reviewed N6700 resource: {cfg.resource!r}")
    if max(VIN_VALUES) > cfg.max_input_voltage_v or HIGH_A > cfg.load_max_current_a:
        raise RuntimeError("matrix exceeds the reviewed bench envelope")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    times = [(index + OFFSET_POINTS) * INTERVAL_S for index in range(POINTS)]
    bench = N6700BuckBench(cfg)
    rows: list[dict[str, object]] = []
    identity = ""
    source_model = ""
    load_model = ""
    try:
        bench.connect()
        assert bench.driver is not None
        assert bench.load is not None
        modules = bench.driver.discover_modules()
        source_model = modules[cfg.source_channel].model
        load_caps = modules[cfg.load_channel]
        load_model = load_caps.model
        identity = bench.driver.get_identity()
        if not load_caps.supports_digitizing:
            raise RuntimeError(f"{load_model} is not digitizing-capable")

        for vin in VIN_VALUES:
            for direction, initial_a, final_a, slope in (
                ("up", LOW_A, HIGH_A, "pos"),
                ("down", HIGH_A, LOW_A, "neg"),
            ):
                bench.prepare_operating_point(vin, initial_a)
                before = bench.measure_once()

                bench.load.configure_digitize(
                    "voltage",
                    points=POINTS,
                    interval_s=INTERVAL_S,
                    offset_points=OFFSET_POINTS,
                )
                bench.load.configure_digitize_trigger(
                    "current",
                    level=TRIGGER_A,
                    slope=slope,
                    source_channel=cfg.load_channel,
                )
                bench.driver.check_errors()
                bench.load.arm_digitize()
                bench.load.wait_digitize_armed(timeout_s=5.0)

                # CC mode was selected before arming. Re-sending FUNC CURR here
                # is forbidden by real firmware while acquisition is active.
                bench.load.set_load_current(final_a)
                bench.driver.check_errors()
                bench.load.wait_digitize_complete(timeout_s=5.0)

                # Fetch before any MEASure query, which would overwrite this
                # triggered buffer.
                capture = bench.load.fetch_digitized_array("voltage")
                if len(capture.values) != POINTS:
                    raise RuntimeError(
                        f"{vin:g} V {direction}: got {len(capture.values)} points, "
                        f"expected {POINTS}"
                    )
                metrics = analyze_load_transient(
                    times, capture.values, recovery_band_pct=2.0
                )
                if abs(metrics.event_index - -OFFSET_POINTS) > 8:
                    raise RuntimeError(
                        f"{vin:g} V {direction}: detected edge at sample "
                        f"{metrics.event_index}, expected near {-OFFSET_POINTS}"
                    )

                bench.wait_for_load_current(final_a)
                after = bench.measure_once()
                waveform = f"load_transient_{vin:g}V_{direction}.csv"
                write_waveform(OUTPUT_DIR / waveform, times, capture.values)
                row = {
                    "vin_v": vin,
                    "direction": direction,
                    "load_initial_a": initial_a,
                    "load_final_a": final_a,
                    "before": before,
                    "after": after,
                    "metrics": asdict(metrics),
                    "waveform": waveform,
                }
                rows.append(row)
                (OUTPUT_DIR / "load_transient_summary.partial.json").write_text(
                    json.dumps(rows, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                bench.idle_load()
    finally:
        bench.close()

    verify = N6700.connect_ethernet(cfg.resource)
    try:
        source_on = verify.get_output_state(cfg.source_channel)
        load_on = verify.load(cfg.load_channel).get_input()
        final_errors = [asdict(item) for item in verify.drain_errors()]
    finally:
        verify.disconnect()
    if source_on or load_on:
        raise RuntimeError(
            f"unsafe final state: source_on={source_on}, load_on={load_on}"
        )
    if final_errors:
        raise RuntimeError(f"final SCPI error queue is not empty: {final_errors}")

    result = {
        "driver_version": __version__,
        "instrument_identity": identity,
        "source_model": source_model,
        "load_model": load_model,
        "points": POINTS,
        "interval_s": INTERVAL_S,
        "offset_points": OFFSET_POINTS,
        "trigger_source": "load current",
        "trigger_level_a": TRIGGER_A,
        "rows": rows,
        "final_source_on": source_on,
        "final_load_on": load_on,
        "final_error_queue": final_errors,
    }
    summary = OUTPUT_DIR / "load_transient_summary.json"
    summary.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
