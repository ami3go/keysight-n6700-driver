"""LPDS-015 adapter-side plugin provider.

Advertised through the ``lpds.adapters`` entry-point group. ``bind()`` never
creates or connects a driver instance — it only wraps one the caller already
created, matching the LPDS-015 §10.5 adapter provider contract.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

__all__ = ["KeysightN6700RobotFrameworkAdapterPlugin"]


class KeysightN6700RobotFrameworkAdapterPlugin:
    """Side-effect-free plugin provider for the ``robotframework.keysight.n6700`` adapter."""

    @classmethod
    def get_descriptor(cls) -> dict[str, Any]:
        package = resources.files("keysight_n6700_robotframework_adapter.resources")
        with (package / "adapter_manifest.json").open("r", encoding="utf-8") as handle:
            manifest: dict[str, Any] = json.load(handle)
        return manifest

    @classmethod
    def validate_environment(cls) -> dict[str, Any]:
        problems: list[str] = []
        try:
            import robot  # noqa: F401
        except ImportError as exc:
            problems.append(f"robotframework is not importable: {exc}")
        return {"status": "PASS" if not problems else "FAIL", "problems": problems}

    @classmethod
    def bind(cls, driver: Any, *, alias: str | None = None) -> Any:
        """Wrap an already-created driver instance. Never connects it.

        The Robot Framework adapter owns its own driver instance internally
        (Robot's library-import model does not let a suite hand it one), so
        ``bind`` here is a documentation-only stub satisfying the LPDS-015
        provider contract; ``KeysightN6700Library()`` is what a suite actually
        imports.
        """
        from .adapter import KeysightN6700Library

        return driver if isinstance(driver, KeysightN6700Library) else KeysightN6700Library()
