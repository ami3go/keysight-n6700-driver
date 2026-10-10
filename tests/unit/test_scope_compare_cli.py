"""Reference comparison CLI must not mark inconclusive outcomes as success."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

from scripts.scope_compare_reference import main


def _write_step(path: Path, *, dt: float, count: int, trigger_time_s: float) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("time_s", "value"))
        for i in range(count):
            t = (i - count // 2) * dt
            writer.writerow((t, 0 if t < trigger_time_s else 1))


@pytest.mark.parametrize(
    ("common_trigger", "limit_us", "expected_status", "expected_exit"),
    [
        (False, 300.0, "INCONCLUSIVE", 1),
        (True, 30.0, "NOT_RESOLVED", 1),
        (True, 300.0, "PASS", 0),
    ],
)
def test_reference_cli_exit_code_is_fail_closed(
    monkeypatch, tmp_path: Path,
    common_trigger: bool, limit_us: float,
    expected_status: str, expected_exit: int,
) -> None:
    d = tmp_path / "digitizer.csv"
    r = tmp_path / "dso.csv"
    dest = tmp_path / "comparison.json"
    _write_step(d, dt=20.48e-6, count=256, trigger_time_s=0.0)
    _write_step(r, dt=1e-6, count=4096, trigger_time_s=0.0)
    argv = [
        "scope_compare_reference.py",
        "--digitizer", str(d),
        "--reference-dso", str(r),
        "--limit-us", str(limit_us),
        "--output", str(dest),
    ]
    if common_trigger:
        argv.append("--common-trigger-verified")
    monkeypatch.setattr(sys, "argv", argv)
    assert main() == expected_exit
    result = json.loads(dest.read_text(encoding="utf-8"))
    assert result["status"] == expected_status
    assert result["digitizer_sha256"]
    assert result["reference_sha256"]
