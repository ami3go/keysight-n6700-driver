#!/usr/bin/env python3
"""Guarded N679xA BUS-trigger waveform evidence capture (NO DUT energization).

Refuses to touch any hardware unless the operator sets
N6700_SCOPE_HIL_APPROVED=YES_AND_REVIEWED and supplies a signed-off JSON
profile identifying the mainframe, channel and safety envelope.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from keysight_n6700 import N6700, __version__


def validate_approval(profile: dict[str, Any], *, operator: str, channel: int) -> None:
    if os.getenv("N6700_SCOPE_HIL_APPROVED") != "YES_AND_REVIEWED":
        raise RuntimeError("HIL disabled: set N6700_SCOPE_HIL_APPROVED=YES_AND_REVIEWED")
    if profile.get("approved") is not True or profile.get("operator") != operator:
        raise RuntimeError("hardware approval missing or operator mismatch")
    if profile.get("channel") != channel:
        raise RuntimeError("approved profile must identify the selected channel")
    if profile.get("expected_module") not in {"N6791A", "N6792A"}:
        raise RuntimeError("approved profile must select an explicitly supported load module")
    for key in ("max_voltage_v", "max_current_a"):
        value = profile.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RuntimeError(f"required positive safety envelope {key} missing")
        if not math.isfinite(value) or value <= 0:
            raise RuntimeError(f"required positive safety envelope {key} invalid")
    if not str(profile.get("bench_review_id", "")).strip():
        raise RuntimeError("bench_review_id must identify a reviewed physical setup")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--channel", type=int, required=True)
    parser.add_argument("--operator", required=True)
    parser.add_argument("--approved-profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # The authorization check must precede network access.
    profile = json.loads(args.approved_profile.read_text(encoding="utf-8"))
    validate_approval(profile, operator=args.operator, channel=args.channel)
    if args.channel not in (1, 2, 3, 4):
        raise RuntimeError("invalid N6700 channel")
    args.output.mkdir(parents=True, exist_ok=True)
    outcome: dict[str, Any] = {
        "schema_version": "1.0",
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "operator": args.operator,
        "bench_review_id": profile["bench_review_id"],
        "approved_envelope": {k: profile[k] for k in ("max_voltage_v", "max_current_a")},
        "driver_version": __version__,
        "host": args.host,
        "channel": args.channel,
        "state": "FAILED",
        "hardware_qualified": False,
    }
    driver: N6700 | None = None
    try:
        driver = N6700.connect_ethernet(args.host)
        model = driver.channel_model(args.channel)
        if model != profile["expected_module"]:
            raise RuntimeError(f"unexpected module {model}, profile expects {profile['expected_module']}")
        load = driver.load(args.channel)
        outcome["idn"] = driver.get_identity()
        outcome["module_model"] = model
        outcome["module_serial"] = driver.channel_serial(args.channel)
        # Passive BUS trigger only; no load input is enabled by this script.
        load.input_off()
        load.configure_digitize("voltage", points=256, interval_s=20.48e-6, offset_points=-128)
        load.configure_digitize_trigger("bus")
        load.arm_digitize()
        load.wait_digitize_armed(timeout_s=5.0)
        load.trigger_digitize()
        load.wait_digitize_complete(timeout_s=5.0)
        data = load.fetch_digitized_array(transfer_format="real32")
        if len(data.values) != 256:
            raise RuntimeError("unexpected waveform count")
        path = args.output / "digitizer.csv"
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(("time_s", "value"))
            for index, sample in enumerate(data.values):
                writer.writerow(((index + (data.offset_points or 0)) * (data.interval_s or 0), sample))
        outcome.update({
            "state": "CAPTURED_PENDING_DSO",
            "sample_count": len(data.values),
            "sample_interval_s": data.interval_s,
            "offset_points": data.offset_points,
            "format": data.format,
            "raw_payload_sha256": data.raw_sha256,
            "csv_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    except Exception as exc:
        outcome["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if driver is not None:
            try:
                channel = driver.load(args.channel)
                if channel.digitize_status.state.value not in {"aborted", "fetched", "idle"}:
                    channel.abort_digitize()
                channel.input_off()
                outcome["output_off_confirmed"] = driver.query_scpi(
                    f"OUTP? (@{args.channel})"
                ).strip() in {"0", "+0"}
            except Exception as exc:
                outcome["output_off_confirmed"] = False
                outcome["cleanup_error"] = f"{type(exc).__name__}: {exc}"
            try:
                outcome["instrument_errors"] = [
                    {"code": e.code, "message": e.message}
                    for e in driver.drain_errors()
                ]
            except Exception as exc:
                outcome["error_queue_unavailable"] = str(exc)
            try:
                driver.disconnect()
            except Exception as exc:
                outcome["disconnect_error"] = str(exc)
        if outcome.get("output_off_confirmed") is not True:
            outcome["state"] = "FAILED_UNVERIFIED_SAFE_STATE"
        evidence = args.output / "comparison_evidence.json"
        evidence.write_text(json.dumps(outcome, indent=2) + "\n", encoding="utf-8")
    print(outcome["state"])
    return 0 if outcome["state"] == "CAPTURED_PENDING_DSO" else 1


if __name__ == "__main__":
    sys.exit(main())
