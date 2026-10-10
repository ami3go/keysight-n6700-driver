"""PR09 final release gate never silently self-certifies production readiness."""
from __future__ import annotations

import json

import pytest

from keysight_n6700.scope_release_gate import (
    SCOPE_METHODS,
    audit_scope_readiness,
    validate_hil_manifest,
    write_scope_audit,
)


def _complete_manifest() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "module_model": "N6791A",
        "mainframe_idn": "KEYSIGHT,N6700C,REVIEWED,E.02.09",
        "module_serial": "REVIEWED-EXAMPLE",
        "output_off_verified": True,
        "digitizer_sha256": "a" * 64,
        "dso_sha256": "b" * 64,
        "reference_dso_idn": "OSCILLOSCOPE,REVIEWED-EXAMPLE",
        "common_trigger_verified": True,
        "reference_comparison_status": "PASS",
        "minimum_repetitions_per_condition": 100,
        "protected_bench_soak_hours": 24.0,
        "unresolved_blockers": 0,
        "unresolved_high": 0,
        "independent_reviewer": "Review-required placeholder",
        "review_record_id": "Review-required placeholder",
    }


def test_software_passes_but_no_hardware_means_production_no_go() -> None:
    result = audit_scope_readiness()
    assert result.verdict == "NO_GO"
    assert result.software_checks_pass
    assert not result.physical_evidence_complete
    assert len(SCOPE_METHODS) == 8


def test_presence_of_hardware_fields_never_self_authorizes_go() -> None:
    assert not validate_hil_manifest(_complete_manifest())
    result = audit_scope_readiness(_complete_manifest())
    assert result.verdict == "HUMAN_REVIEW_REQUIRED"
    assert result.physical_evidence_complete
    assert "approval" in " ".join(result.reasons)


@pytest.mark.parametrize(
    "bad",
    [
        {"output_off_verified": False},
        {"reference_comparison_status": "INCONCLUSIVE"},
        {"common_trigger_verified": False},
        {"minimum_repetitions_per_condition": 99},
        {"protected_bench_soak_hours": 23.9},
        {"unresolved_high": 1},
        {"digitizer_sha256": "not-a-hash"},
        {"independent_reviewer": ""},
        {"module_model": "N6799Z"},
        {"reference_dso_idn": ""},
    ],
)
def test_each_evidence_defect_blocks_release(bad: dict[str, object]) -> None:
    manifest = _complete_manifest()
    manifest.update(bad)
    assert validate_hil_manifest(manifest)
    assert audit_scope_readiness(manifest).verdict == "NO_GO"


def test_release_gate_writes_machine_readable_report(tmp_path) -> None:
    path = tmp_path / "release_audit.json"
    result = write_scope_audit(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["verdict"] == result.verdict == "NO_GO"
    assert raw["automated_production_go_permitted"] is False
    assert raw["human_approval_required"] is True
    assert raw["manifest_sha256"] is None
