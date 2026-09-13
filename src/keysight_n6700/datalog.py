"""CSV measurement logging helpers."""

from __future__ import annotations

import csv
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from .driver import N6700


def log_measurements_csv(
    instrument: N6700,
    path: str | Path,
    channels: Sequence[int],
    interval_s: float,
    duration_s: float | None = None,
    fields: Sequence[Literal["voltage", "current", "power"]] = ("voltage", "current", "power"),
    append: bool = True,
    alias: str | None = None,
) -> None:
    """Log measurements to an Excel/pandas-readable CSV file."""
    target = Path(path)
    start = time.time()
    mode = "a" if append else "w"
    write_header = not target.exists() or not append
    with target.open(mode, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "timestamp_iso",
                "timestamp_unix",
                "channel",
                "voltage_V",
                "current_A",
                "power_W",
                "power_source",
                "module_model",
                "enabled",
            ],
        )
        if write_header:
            writer.writeheader()
        while True:
            for ch in channels:
                chan = instrument.channel(ch, alias=alias)
                meas = chan.measure()
                try:
                    enabled = chan.get_status_snapshot().output_or_input_enabled
                except Exception:
                    enabled = None
                row = {
                    "timestamp_iso": meas.timestamp_iso,
                    "timestamp_unix": meas.timestamp_unix,
                    "channel": ch,
                    "voltage_V": meas.voltage_V if "voltage" in fields else None,
                    "current_A": meas.current_A if "current" in fields else None,
                    "power_W": meas.power_W if "power" in fields else None,
                    "power_source": meas.power_source,
                    "module_model": chan.capabilities.model,
                    "enabled": enabled,
                }
                writer.writerow(row)
            f.flush()
            if duration_s is not None and time.time() - start >= duration_s:
                break
            time.sleep(interval_s)
