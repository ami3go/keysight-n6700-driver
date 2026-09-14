"""Module support matrix."""

from __future__ import annotations

SUPPORTED_MODULE_FAMILIES = {
    "N673x": "Power supply modules, CV/CC, official N6700 docs",
    "N674x": "Power supply modules, CV/CC, official N6700 docs",
    "N675x": "Power supply modules, advanced functions where documented",
    "N676x": "Precision power supply modules, digitizer functions where documented",
    "N677x": "Power supply modules, CV/CC, official N6700 docs",
    "N678xA": "SMU modules, voltage/current priority, official N6700 docs",
    "N679xA": (
        "Electronic Load Modules (N6791A=100W, N6792A=200W). Four priority "
        "modes (voltage/current/resistance/power), selected with the same "
        "FUNCtion command the N678xA SMU uses (CURRent|VOLTage), plus "
        "RESistance|POWer. The load's input terminals are programmed with "
        "the ordinary OUTP command, not a separate load-specific command "
        "(Keysight N6705C User's Guide, Quick Reference, Note 1). Confirmed "
        "against official Keysight documentation 2026-09-14; see "
        "Keysight_documents/ in this repository."
    ),
    "SIM_LOAD": "Simulator-only electronic load used for no-hardware tests",
}

VERIFIED_REAL_ELECTRONIC_LOAD_MODELS: dict[str, str] = {
    "N6791A": "100W Electronic Load Module",
    "N6792A": "200W Electronic Load Module (double-wide)",
}
