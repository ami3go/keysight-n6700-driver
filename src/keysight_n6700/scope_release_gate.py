"""Final scope release gate: fail closed without independently reviewed HIL evidence.

A JSON manifest is only a supporting record, never cryptographic proof of
physical setup or independent review. This module reports readiness to request
a human approval, not an automatic unattended-production GO.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from .module_capabilities import classify_module
from .version import __version__

SCOPE_METHODS = frozenset({
    "configure_digitize",
    "configure_digitize_trigger",
    "arm_digitize",
    "wait_digitize_armed",
    "trigger_digitize",
    "wait_digitize_complete",
    "abort_digitize",
    "fetch_digitized_array",
})

MIN_REPETITIONS_PER_CONDITION = 100
MIN_SOAK_HOURS = 24.0


@dataclass(frozen=True)
class ScopeReadiness:
    verdict: Literal["NO_GO", "HUMAN_REVIEW_REQUIRED"]
    software_checks_pass: bool
    physical_evidence_complete: bool
    reasons: tuple[str, ...]
    driver_version: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _valid_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def validate_hil_manifest(manifest: Any) -> tuple[str, ...]:
    """Return all unresolved hardware release-gate evidence conditions."""
    if not isinstance(manifest, dict):
        return ("A reviewed, per-instrument qualification manifest is missing.",)

    problems: list[str] = []
    if manifest.get("schema_version") != "1.0":
        problems.append("Unsupported qualification manifest schema.")
    if manifest.get("module_model") not in {"N6791A", "N6792A"}:
        problems.append("Exact supported electronic load model not established.")
    if not str(manifest.get("mainframe_idn") or "").strip():
        problems.append("Mainframe IDN and firmware missing.")
    if not str(manifest.get("module_serial") or "").strip():
        problems.append("Load module serial missing.")
    if manifest.get("output_off_verified") is not True:
        problems.append("Safe output/input OFF state not independently verified.")
    if not _valid_sha256(manifest.get("digitizer_sha256")):
        problems.append("Full digitizer waveform SHA256 evidence missing.")
    if not _valid_sha256(manifest.get("dso_sha256")):
        problems.append("Independent reference DSO waveform SHA256 evidence missing.")
    if not str(manifest.get("reference_dso_idn") or "").strip():
        problems.append("Independent reference DSO identity missing.")
    if manifest.get("common_trigger_verified") is not True:
        problems.append("Common physical trigger was not verified.")
    if manifest.get("reference_comparison_status") != "PASS":
        problems.append("Independent DSO guard-banded comparison must PASS.")
    repeats = manifest.get("minimum_repetitions_per_condition")
    if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < MIN_REPETITIONS_PER_CONDITION:
        problems.append("At least 100 accepted repetitions per reviewed condition required.")
    soak = manifest.get("protected_bench_soak_hours")
    if (
        isinstance(soak, bool)
        or not isinstance(soak, (int, float))
        or not math.isfinite(soak)
        or soak < MIN_SOAK_HOURS
    ):
        problems.append("Protected hardware soak of at least 24 hours required.")
    if manifest.get("unresolved_blockers") != 0 or manifest.get("unresolved_high") != 0:
        problems.append("No unresolved BLOCKER/HIGH findings allowed.")
    if not str(manifest.get("independent_reviewer") or "").strip():
        problems.append("Named independent technical reviewer required.")
    if not str(manifest.get("review_record_id") or "").strip():
        problems.append("Independent review record ID required.")
    return tuple(problems)


def audit_scope_readiness(manifest: Any = None) -> ScopeReadiness:
    """Software checks and evidence-gap classification; never self-certify GO."""
    from .driver import N6700

    checks: list[str] = []
    driver = N6700()
    if driver.metadata.version != __version__:
        checks.append("Runtime driver metadata differs from canonical package version.")
    capabilities = driver.get_capability_model()
    advertised = {
        entry["binding"]["python_method"]
        for entry in capabilities
        if entry["binding"]["python_method"] in SCOPE_METHODS
        and entry["mutating"] is True
    }
    if advertised != SCOPE_METHODS:
        checks.append("The full eight-method mutating capability catalog is incomplete.")
    for model in ("N6791A", "N6792A"):
        cap = classify_module(model)
        if not (cap.supports_digitizing and cap.supports_array_measurement):
            checks.append(f"{model} digitizer/array capability declaration is missing.")
    if classify_module("N6799Z").supports_digitizing:
        checks.append("Unknown N679 model is incorrectly advertised as supported.")
    hw = validate_hil_manifest(manifest)
    return ScopeReadiness(
        verdict="NO_GO" if checks or hw else "HUMAN_REVIEW_REQUIRED",
        software_checks_pass=not checks,
        physical_evidence_complete=not hw,
        reasons=tuple(checks) + hw or (
            "Evidence fields are present but require physical integrity verification, "
            "independent reviewer approval and bench release authorization.",
        ),
        driver_version=__version__,
    )


def write_scope_audit(path: Path, manifest: Any = None) -> ScopeReadiness:
    result = audit_scope_readiness(manifest)
    document = result.as_dict()
    document["schema_version"] = "1.0"
    document["human_approval_required"] = True
    document["automated_production_go_permitted"] = False
    document["manifest_sha256"] = (
        hashlib.sha256(json.dumps(manifest, sort_keys=True).encode("utf-8")).hexdigest()
        if manifest is not None else None
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return result
