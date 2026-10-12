#!/usr/bin/env python3
"""Run the N679xA scope production-readiness audit. Never issues a hardware command."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from keysight_n6700.scope_release_gate import write_scope_audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, help="Independently reviewed qualification manifest JSON")
    parser.add_argument("--output", type=Path, required=True, help="JSON report to create")
    args = parser.parse_args()
    manifest = (
        json.loads(args.manifest.read_text(encoding="utf-8")) if args.manifest else None
    )
    result = write_scope_audit(args.output, manifest)
    print(f"{result.verdict}: {'; '.join(result.reasons)}")
    return 0 if result.verdict == "HUMAN_REVIEW_REQUIRED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
