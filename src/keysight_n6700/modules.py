"""Module support matrix.

Real electronic load entries must be added only after official command
documentation is verified.
"""

from __future__ import annotations

SUPPORTED_MODULE_FAMILIES = {
    "N673x": "Power supply modules, CV/CC, official N6700 docs",
    "N674x": "Power supply modules, CV/CC, official N6700 docs",
    "N675x": "Power supply modules, advanced functions where documented",
    "N676x": "Precision power supply modules, digitizer functions where documented",
    "N677x": "Power supply modules, CV/CC, official N6700 docs",
    "N678xA": "SMU modules, voltage/current priority, official N6700 docs",
    "N679x": (
        "Power supply modules (voltage/current setpoint, measurement, output "
        "control). No SMU priority-mode support: FUNC:MODE? does not reply on "
        "real hardware (N6791A, confirmed 2026-09-14) and is never sent to this family."
    ),
    "SIM_LOAD": "Simulator-only electronic load used for no-hardware tests",
}

VERIFIED_REAL_ELECTRONIC_LOAD_MODELS: dict[str, str] = {}
