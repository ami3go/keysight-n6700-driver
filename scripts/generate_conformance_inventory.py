#!/usr/bin/env python3
"""Generate ``tests/conformance/data/method_inventory.yaml`` (LPDS-019 §5).

The inventory is derived from :class:`keysight_n6700.driver.N6700`'s actual
public methods, not hand-maintained, so it cannot silently drift from the
real API. A method is classified ``device_facing: true`` unless its name
matches one of the local-bookkeeping patterns below (session/alias
management, capability/configuration introspection, and construction
shortcuts) — none of those send SCPI traffic.

Run from the repository root: ``python scripts/generate_conformance_inventory.py``
"""

from __future__ import annotations

import inspect
from pathlib import Path

import yaml

from keysight_n6700.driver import N6700

_NOT_DEVICE_FACING = {
    "close",  # alias of disconnect
    "connect_usb",
    "connect_visa",
    "connect_ethernet",
    "connect_simulated",  # construction shortcuts; connect() itself is device-facing
    "export_diagnostics",
    "export_driver_configuration",
    "find_driver_capabilities",
    "get_capability_model",
    "get_communication_timeout",
    "get_current_alias",
    "get_driver_capability",
    "get_driver_capabilities",
    "get_driver_configuration",
    "get_driver_configuration_schema",
    "get_driver_default_configuration",
    "get_driver_features",
    "get_driver_information",
    "import_driver_configuration",
    "list_connected_aliases",
    "load_driver_configuration",
    "refresh_driver_capabilities",
    "reset_driver_configuration",
    "save_driver_configuration",
    "select",
    "set_auto_shutdown_on_disconnect",
    "validate_driver_capabilities",
    "validate_driver_configuration",
}


def _canonical_method(name: str) -> str:
    canonical_map = {
        "idn": "get_identity",
        "reset": "reset_device",
        "get_error": "get_device_error",
        "drain_errors": "get_all_device_errors",
    }
    return canonical_map.get(name, name)


def build_inventory() -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for name in sorted(dir(N6700)):
        if name.startswith("_"):
            continue
        attr = getattr(N6700, name)
        if not callable(attr) or isinstance(attr, type):
            continue
        try:
            signature = str(inspect.signature(attr))
        except (TypeError, ValueError):
            signature = "(...)"
        params = list(inspect.signature(attr).parameters.values())[1:]  # drop self
        required = [p.name for p in params if p.default is inspect.Parameter.empty and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)]
        optional = [p.name for p in params if p.default is not inspect.Parameter.empty]
        entries.append(
            {
                "method": name,
                "canonical_method": _canonical_method(name),
                "aliases": [] if _canonical_method(name) == name else [name],
                "implementation_target": f"keysight_n6700.driver.N6700.{name}",
                "signature": f"{name}{signature}",
                "required_arguments": required,
                "optional_arguments": optional,
                "device_facing": name not in _NOT_DEVICE_FACING,
                "execution_status": "not_yet_run",
            }
        )
    return entries


def main() -> None:
    inventory = build_inventory()
    output_path = Path(__file__).resolve().parent.parent / "tests" / "conformance" / "data" / "method_inventory.yaml"
    document = {
        "lpds019_version": "1.1",
        "driver_name": "keysight_n6700",
        "driver_class": "keysight_n6700.driver.N6700",
        "generated_by": "scripts/generate_conformance_inventory.py",
        "methods": inventory,
    }
    output_path.write_text(yaml.safe_dump(document, sort_keys=False, width=100))
    device_facing = sum(1 for entry in inventory if entry["device_facing"])
    print(f"wrote {len(inventory)} methods ({device_facing} device-facing) to {output_path}")


if __name__ == "__main__":
    main()
