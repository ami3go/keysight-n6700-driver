#!/usr/bin/env python3
"""Compare a previously captured N679xA CSV against independent DSO data.

Both CSVs require time_s,value columns on a VERIFIED shared-trigger time axis.
This tool never connects to or modifies instrument outputs.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

from keysight_n6700.scope_qualification import WaveformSeries, compare_triggered_events


def read_csv(path: Path) -> WaveformSeries:
    with path.open(newline="", encoding="utf-8") as stream:
        rows = csv.DictReader(stream)
        if not {"time_s", "value"}.issubset(rows.fieldnames or []):
            raise ValueError(f"{path} must contain time_s,value columns")
        pairs = [(float(row["time_s"]), float(row["value"])) for row in rows]
    return WaveformSeries(tuple(t for t, _ in pairs), tuple(v for _, v in pairs))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--digitizer", type=Path, required=True)
    parser.add_argument("--reference-dso", type=Path, required=True)
    parser.add_argument("--limit-us", type=float, required=True)
    parser.add_argument("--jitter-us", type=float, default=0.0)
    parser.add_argument("--common-trigger-verified", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = compare_triggered_events(
        read_csv(args.digitizer),
        read_csv(args.reference_dso),
        common_trigger_verified=args.common_trigger_verified,
        timing_limit_s=args.limit_us * 1e-6,
        known_trigger_jitter_s=args.jitter_us * 1e-6,
    )
    report = result.to_dict()
    report.update({
        "schema_version": "1.0",
        "digitizer_sha256": hashlib.sha256(args.digitizer.read_bytes()).hexdigest(),
        "reference_sha256": hashlib.sha256(args.reference_dso.read_bytes()).hexdigest(),
        "common_trigger_verified": args.common_trigger_verified,
        "algorithm": "sampled first 50%-crossing; k=2 standard uncertainties; no time shifting",
    })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"{result.status}: {result.reason}")
    return 0 if result.status in {"PASS", "INCONCLUSIVE", "NOT_RESOLVED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
