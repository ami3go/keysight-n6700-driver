#!/usr/bin/env python3
"""Run automated passive N679xA hardware-in-loop scope tests with real hardware.

Never connects if operator/bench approval or the two-signal environment gate
is missing. This program does not switch on any supply or electronic load.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

from keysight_n6700 import N6700
from keysight_n6700.scope_hil_suite import run_hil_plan, validate_plan


def validate_connection(profile: dict[str, Any], *, operator: str) -> dict[str, Any]:
    """Validate the entire authorization envelope without opening a session."""
    if os.getenv("N6700_HIL_ENABLED") != "true" or os.getenv(
        "N6700_SCOPE_HIL_APPROVED"
    ) != "YES_AND_REVIEWED":
        raise ValueError(
            "HIL not authorized: N6700_HIL_ENABLED=true and "
            "N6700_SCOPE_HIL_APPROVED=YES_AND_REVIEWED are BOTH required"
        )
    plan = validate_plan(profile)
    if operator != plan.operator:
        raise ValueError("operator differs from the approved bench plan")
    connection = profile.get("connection")
    if not isinstance(connection, dict):
        raise ValueError("approved plan requires a connection object")
    kind = connection.get("type")
    resource = connection.get("resource")
    if kind not in ("ethernet", "visa"):
        raise ValueError("only real ethernet/visa transports are supported, not a simulator")
    if not isinstance(resource, str) or not resource.strip() or "REPLACE" in resource.upper():
        raise ValueError("approved instrument address/resource is required")
    port = connection.get("port", 5025)
    timeout = connection.get("timeout_s", 15.0)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("invalid TCP port")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 1 <= timeout <= 120:
        raise ValueError("timeout_s must be 1..120")
    return {
        "type": kind, "resource": resource,
        "port": port, "timeout_s": float(timeout),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True, help="reviewed HIL JSON plan")
    parser.add_argument("--output", type=Path, required=True, help="NEW results directory")
    parser.add_argument("--operator", required=True)
    args = parser.parse_args()
    profile = json.loads(args.plan.read_text(encoding="utf-8"))
    # All preflight authorization and plan checks complete BEFORE any I/O.
    plan = validate_plan(profile)
    connection = validate_connection(profile, operator=args.operator)
    if args.output.exists():
        raise ValueError(f"output already exists: {args.output} (refusing evidence overwrite)")
    driver: N6700 | None = None
    passed = False
    try:
        driver = N6700()
        driver.connect(
            resource=connection["resource"],
            connection_type=connection["type"],
            port=connection["port"],
            timeout_s=connection["timeout_s"],
            discover=True,
            reset_on_connect=False,
            clear_errors_on_connect=False,
        )
        report = run_hil_plan(driver, plan, args.output)
        passed = report["result"] == "PASS" and report["final_output_off_verified"] is True
    finally:
        if driver is not None:
            # Keep instrument state intact; do not issue any reset or clear.
            try:
                driver.disconnect()
            except Exception as exc:
                passed = False
                if args.output.is_dir():
                    (args.output / "disconnect_error.txt").write_text(
                        f"{type(exc).__name__}: {exc}\n", encoding="utf-8"
                    )
    print(f"HIL protocol test: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
