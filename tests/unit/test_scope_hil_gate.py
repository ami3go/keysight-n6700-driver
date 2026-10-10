"""Safety gate must prevent unattended HIL unless explicitly authorized."""
from __future__ import annotations

import pytest

from scripts.scope_hil_capture import validate_approval


def profile() -> dict[str, object]:
    return {
        "approved": True,
        "operator": "operator",
        "bench_review_id": "review-123",
        "channel": 2,
        "expected_module": "N6791A",
        "max_voltage_v": 20.0,
        "max_current_a": 1.0,
    }


def test_hil_refuses_hardware_without_environment_approval(monkeypatch) -> None:
    monkeypatch.delenv("N6700_SCOPE_HIL_APPROVED", raising=False)
    with pytest.raises(RuntimeError, match="HIL disabled"):
        validate_approval(profile(), operator="operator", channel=2)


def test_hil_rejects_inaccurate_or_unauthorized_profile(monkeypatch) -> None:
    monkeypatch.setenv("N6700_SCOPE_HIL_APPROVED", "YES_AND_REVIEWED")
    for edit in (
        {"approved": False}, {"channel": 3}, {"operator": "other"},
        {"expected_module": "N6799A"}, {"max_voltage_v": float("nan")},
        {"bench_review_id": ""},
    ):
        settings = profile()
        settings.update(edit)
        with pytest.raises(RuntimeError):
            validate_approval(settings, operator="operator", channel=2)


def test_hil_accepts_exact_reviewed_parameters_offline(monkeypatch) -> None:
    monkeypatch.setenv("N6700_SCOPE_HIL_APPROVED", "YES_AND_REVIEWED")
    validate_approval(profile(), operator="operator", channel=2)
