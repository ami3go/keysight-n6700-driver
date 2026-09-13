"""Logging helpers.

Per LPDS-006 §12.1, this driver never calls ``logging.basicConfig()``; a
caller wires its own handlers, or uses :func:`configure_rotating_log` as a
convenience for one common case.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

logger = logging.getLogger("keysight_n6700")


def configure_rotating_log(path: str | Path, *, level: int = logging.INFO) -> logging.Logger:
    logger.setLevel(level)
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    return logger
