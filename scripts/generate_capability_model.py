#!/usr/bin/env python3
"""Generate the LPDS-013 capability-model artifacts from the driver's own
``@capability``-annotated methods (:mod:`keysight_n6700.capability_model`).

Writes:
- ``capability/capability_model.yaml`` — the full record set.
- ``capability/examples/capability_snapshot.json`` — one example instance's
  ``get_capability_model()`` output, byte-for-byte what a caller sees.

Run from the repository root: ``python scripts/generate_capability_model.py``
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from keysight_n6700 import N6700

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    driver = N6700()
    model = driver.get_capability_model()

    capability_dir = ROOT / "capability"
    (capability_dir / "examples").mkdir(parents=True, exist_ok=True)

    document = {
        "lpds013_version": "1.0",
        "driver_name": "keysight_n6700",
        "generated_by": "scripts/generate_capability_model.py",
        "capabilities": model,
    }
    (capability_dir / "capability_model.yaml").write_text(
        yaml.safe_dump(document, sort_keys=False, width=100)
    )
    (capability_dir / "examples" / "capability_snapshot.json").write_text(
        json.dumps(model, indent=2) + "\n"
    )
    validation = driver.validate_driver_capabilities()
    if not validation["valid"]:
        raise SystemExit(f"capability model failed self-validation: {validation['problems']}")
    print(f"wrote {len(model)} capabilities to {capability_dir / 'capability_model.yaml'}")


if __name__ == "__main__":
    main()
