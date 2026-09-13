"""LPDS-015 driver-side plugin provider.

Advertised through the ``lpds.drivers`` entry-point group (see
``pyproject.toml``). A generic LPDS-aware application can enumerate this
without importing :mod:`keysight_n6700.driver`, decide whether it is
compatible, and only then instantiate it — this class never performs I/O.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

__all__ = ["KeysightN6700Plugin"]


class KeysightN6700Plugin:
    """Side-effect-free plugin provider for the ``keysight.n6700`` driver."""

    @classmethod
    def get_descriptor(cls) -> dict[str, Any]:
        """Return the packaged plugin manifest, without importing the driver."""
        package = resources.files("keysight_n6700.resources")
        with (package / "plugin_manifest.json").open("r", encoding="utf-8") as handle:
            manifest: dict[str, Any] = json.load(handle)
        return manifest

    @classmethod
    def validate_environment(cls) -> dict[str, Any]:
        """Check whether this driver's mandatory dependencies are importable."""
        problems: list[str] = []
        try:
            import scpi_driver_core  # noqa: F401
        except ImportError as exc:
            problems.append(f"scpi_driver_core is not importable: {exc}")
        return {"status": "PASS" if not problems else "FAIL", "problems": problems}

    @classmethod
    def create(cls, **kwargs: Any) -> Any:
        """Instantiate the driver. Performs no I/O; does not connect."""
        from .driver import N6700

        return N6700(**kwargs)
