"""Logging helpers without global logging side effects."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

logger = logging.getLogger("keysight_n6700")


def configure_rotating_log(path: str | Path, *, level: int = logging.INFO) -> logging.Logger:
    """Attach at most one rotating handler for the resolved target path."""
    logger.setLevel(level)
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    for existing in logger.handlers:
        if isinstance(existing, RotatingFileHandler):
            try:
                if Path(existing.baseFilename).resolve() == target:
                    existing.setLevel(level)
                    return logger
            except OSError:
                continue
    handler = RotatingFileHandler(
        target, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    return logger
