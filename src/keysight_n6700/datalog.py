"""CSV measurement logging helpers."""

from __future__ import annotations

import csv
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from .driver import N6700
from .exceptions import DriverUnsupportedOperationError
from .scpi import require_number


def log_measurements_csv(
    instrument: N6700,
    path: str | Path,
    channels: Sequence[int],
    interval_s: float,
    duration_s: float | None = None,
    fields: Sequence[Literal["voltage", "current", "power"]] = ("voltage", "current", "power"),
    append: bool = True,
    alias: str | None = None,
    stop_event: threading.Event | None = None,
) -> None:
    """Log measurements to CSV on monotonic deadlines.

    ``stop_event`` allows a caller to stop a long-running logger cleanly. Work
    time is accounted for in the cadence instead of being added to every
    interval, so long runs do not accumulate sleep-after-work drift.
    """
    interval = require_number("interval_s", interval_s, minimum=1e-6)
    duration = (
        require_number("duration_s", duration_s, minimum=0.0)
        if duration_s is not None
        else None
    )
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    write_header = not target.exists() or not append
    start = time.monotonic()
    next_deadline = start

    with target.open(mode, newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
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
            if stop_event is not None and stop_event.is_set():
                break
            now = time.monotonic()
            if duration is not None and now - start > duration:
                break

            for channel_number in channels:
                channel = instrument.channel(channel_number, alias=alias)
                measurement = channel.measure()
                try:
                    enabled = channel.get_status_snapshot().output_or_input_enabled
                except DriverUnsupportedOperationError:
                    enabled = None
                writer.writerow(
                    {
                        "timestamp_iso": measurement.timestamp_iso,
                        "timestamp_unix": measurement.timestamp_unix,
                        "channel": channel_number,
                        "voltage_V": measurement.voltage_V if "voltage" in fields else None,
                        "current_A": measurement.current_A if "current" in fields else None,
                        "power_W": measurement.power_W if "power" in fields else None,
                        "power_source": measurement.power_source,
                        "module_model": channel.capabilities.model,
                        "enabled": enabled,
                    }
                )
            handle.flush()

            next_deadline += interval
            remaining = next_deadline - time.monotonic()
            if remaining > 0:
                if stop_event is None:
                    time.sleep(remaining)
                elif stop_event.wait(remaining):
                    break
