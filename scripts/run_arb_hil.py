#!/usr/bin/env python3
"""Run the guarded source Arb HIL plan (ARB-R06).

Dry run is the default and uses only the simulator; its result is always
``NOT_RUN`` evidence.  ``--hardware`` is required to touch an instrument and then
every gate in ``keysight_n6700.arb_hil`` must pass *before* a connection is
opened: an approved plan whose SHA-256 matches, the two environment signals, the
named operator, and a new output directory.  Energizing a case additionally needs
the plan's per-case ``energize_ack`` and the case id in
``N6700_ARB_HIL_ENERGIZE_CASES``.  See docs/ARB_HIL_RUNNER.md.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from keysight_n6700 import N6700, DriverError
from keysight_n6700.arb_hil import (
    HilRefused,
    _person,
    check_environment,
    run_arb_hil,
    validate_arb_plan,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True, help="reviewed, approved plan JSON")
    parser.add_argument("--output", type=Path, required=True, help="NEW results directory")
    parser.add_argument("--operator", required=True, help="must equal approval.operator")
    parser.add_argument("--hardware", action="store_true", help="touch the real instrument (default: dry run)")
    args = parser.parse_args(argv)

    try:
        try:
            raw = json.loads(args.plan.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HilRefused([f"cannot read the plan {args.plan}: {exc}"]) from exc
        plan = validate_arb_plan(raw)
        if _person(args.operator) != _person(plan.operator):
            raise HilRefused(["--operator differs from approval.operator in the plan"])
        if args.output.exists():
            raise HilRefused([f"output already exists: {args.output} (refusing evidence overwrite)"])
        if args.hardware:
            check_environment(os.environ)
    except HilRefused as refusal:
        for reason in refusal.reasons:
            print(f"REFUSED: {reason}", file=sys.stderr)
        return 2

    driver = N6700()
    try:
        if args.hardware:
            connection = plan.connection
            driver.connect(
                resource=connection["resource"],
                connection_type=connection["type"],
                port=int(connection.get("port", 5025)),
                timeout_s=float(connection.get("timeout_s", 15.0)),
                discover=True,
                reset_on_connect=False,
                clear_errors_on_connect=False,
            )
        else:
            driver = N6700.connect_simulated(
                models={plan.channel: plan.module_model}, mainframe_model="N6705C"
            )
        summary = run_arb_hil(driver, plan, args.output, env=os.environ, dry_run=not args.hardware)
    except HilRefused as refusal:
        for reason in refusal.reasons:
            print(f"REFUSED: {reason}", file=sys.stderr)
        return 2
    except (DriverError, FileExistsError) as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        # Keep instrument state intact: no reset, no clear.
        try:
            driver.disconnect()
        except Exception as exc:
            print(f"disconnect failed: {type(exc).__name__}: {exc}", file=sys.stderr)

    label = "DRY RUN (simulator, not evidence)" if not args.hardware else "HIL"
    print(f"{label}: {summary['result']}  ({args.output}/summary.json)")
    if not args.hardware:
        return 0 if summary["result"] != "FAIL" else 1
    return 0 if summary["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
