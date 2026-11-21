"""The virtual-only simulator service must start without compiled/optional dependencies.

Small boards (e.g. riscv64 SBCs) have no wheel for ``rpds-py`` (a ``jsonschema`` dependency) and
no room to build one. ``jsonschema`` is only needed for configuration validation, so no module of
this package may import it, or ``yaml`` / ``referencing`` / ``rpds``, at module level: the
simulator import chain (``keysight_n6700.sim_server``) would then need them at start-up.

This is a static, in-process check (no subprocess): it parses every module of the package and
rejects module-level imports of the blocked distributions. Imports inside functions are allowed,
because they only run when that feature is used. No physical instrument or HIL involved.
"""
from __future__ import annotations

import ast
from pathlib import Path

import keysight_n6700

_BLOCKED = {"jsonschema", "yaml", "referencing", "rpds", "jsonschema_specifications"}
_PACKAGE_DIR = Path(keysight_n6700.__file__).resolve().parent


def _module_level_imports(statements: list[ast.stmt]) -> list[tuple[int, str]]:
    """Imports that execute when the module is imported (not inside a function body)."""
    found: list[tuple[int, str]] = []
    for node in statements:
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name.split(".")[0]) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node.lineno, node.module.split(".")[0]))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue  # runs only when called
        else:
            for field in ("body", "orelse", "finalbody", "handlers"):
                children = getattr(node, field, None)
                if isinstance(children, list):
                    inner = [c for c in children if isinstance(c, ast.stmt)]
                    for handler in (c for c in children if isinstance(c, ast.ExceptHandler)):
                        inner.extend(handler.body)
                    found.extend(_module_level_imports(inner))
    return found


def test_no_module_imports_jsonschema_yaml_or_rpds_at_module_level() -> None:
    offenders: list[str] = []
    for path in sorted(_PACKAGE_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno, root in _module_level_imports(tree.body):
            if root in _BLOCKED:
                offenders.append(f"{path.relative_to(_PACKAGE_DIR)}:{lineno} imports {root}")
    assert offenders == [], "module-level import of a compiled/optional dependency: " + "; ".join(offenders)


def test_the_check_would_catch_a_module_level_jsonschema_import() -> None:
    tree = ast.parse("import os\nimport jsonschema\n\ndef f():\n    import yaml\n")
    assert _module_level_imports(tree.body) == [(1, "os"), (2, "jsonschema")]
