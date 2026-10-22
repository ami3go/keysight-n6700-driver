"""ARB-R06: the CLI opens no connection until every gate has passed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from keysight_n6700 import N6700
from keysight_n6700.arb_hil import ENV_APPROVED, ENV_ENABLED
from scripts.run_arb_hil import main
from tests.unit.test_arb_hil import make_plan

HARDWARE_ENV = {ENV_ENABLED: "true", ENV_APPROVED: "YES_AND_REVIEWED"}


@pytest.fixture
def connects(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record real-connection attempts and make them fail loudly, never reaching a socket."""
    calls: list[dict[str, Any]] = []
    real_connect = N6700.connect

    def fake_connect(self: N6700, *args: Any, **kwargs: Any) -> Any:
        if kwargs.get("connection_type") == "simulated":
            return real_connect(self, *args, **kwargs)  # the dry run's simulator is allowed
        calls.append(kwargs)
        raise RuntimeError("connect reached")

    monkeypatch.setattr(N6700, "connect", fake_connect)
    return calls


def write_plan(tmp_path: Path, plan: dict[str, Any] | None = None) -> Path:
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan or make_plan()), encoding="utf-8")
    return path


def argv(tmp_path: Path, *extra: str, operator: str = "Operator A") -> list[str]:
    return ["--plan", str(tmp_path / "plan.json"), "--output", str(tmp_path / "out"), "--operator", operator, *extra]


def clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (ENV_ENABLED, ENV_APPROVED, "N6700_ARB_HIL_ENERGIZE_CASES"):
        monkeypatch.delenv(name, raising=False)


def test_dry_run_is_the_default_and_never_connects_to_hardware(tmp_path, monkeypatch, connects, capsys) -> None:
    clear_env(monkeypatch)
    write_plan(tmp_path)
    assert main(argv(tmp_path)) == 0
    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    assert summary["dry_run"] is True and summary["result"] == "INCOMPLETE"
    assert "not evidence" in capsys.readouterr().out
    assert connects == []


@pytest.mark.parametrize("env", [{}, {ENV_ENABLED: "true"}, {ENV_APPROVED: "YES_AND_REVIEWED"}])
def test_hardware_without_the_full_opt_in_is_refused_before_any_connection(tmp_path, monkeypatch, connects, env, capsys) -> None:
    clear_env(monkeypatch)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    write_plan(tmp_path)
    assert main(argv(tmp_path, "--hardware")) == 2
    assert "REFUSED" in capsys.readouterr().err
    assert connects == [] and not (tmp_path / "out").exists()


def test_every_other_refusal_also_happens_before_any_connection(tmp_path, monkeypatch, connects) -> None:
    for key, value in HARDWARE_ENV.items():
        monkeypatch.setenv(key, value)
    bad_plans = {
        "unapproved": make_plan(lambda p: p.__setitem__("approved", False)),
        "edited": {**make_plan(), "fixture": {**make_plan()["fixture"], "load": "something else"}},
    }
    for name, plan in bad_plans.items():
        write_plan(tmp_path, plan)
        assert main(argv(tmp_path, "--hardware")) == 2, name
    write_plan(tmp_path)
    assert main(argv(tmp_path, "--hardware", operator="Somebody Else")) == 2
    (tmp_path / "out").mkdir()
    assert main(argv(tmp_path, "--hardware")) == 2
    assert connects == []


def test_a_missing_or_malformed_plan_file_is_a_refusal_not_a_crash(tmp_path, monkeypatch, connects, capsys) -> None:
    for key, value in HARDWARE_ENV.items():
        monkeypatch.setenv(key, value)
    assert main(argv(tmp_path, "--hardware")) == 2  # no plan.json at all
    (tmp_path / "plan.json").write_text("{not json")
    assert main(argv(tmp_path, "--hardware")) == 2
    assert "cannot read the plan" in capsys.readouterr().err
    assert connects == []


def test_the_operator_check_ignores_case_and_spacing(tmp_path, monkeypatch, connects) -> None:
    clear_env(monkeypatch)
    write_plan(tmp_path)
    assert main(argv(tmp_path, operator="  operator   a ")) == 0


def test_a_connection_failure_is_reported_not_a_traceback(tmp_path, monkeypatch, capsys) -> None:
    from keysight_n6700 import DriverError

    for key, value in HARDWARE_ENV.items():
        monkeypatch.setenv(key, value)
    real_connect = N6700.connect

    def refuse(self: N6700, *args: Any, **kwargs: Any) -> Any:
        if kwargs.get("connection_type") == "simulated":
            return real_connect(self, *args, **kwargs)
        raise DriverError("injected connection failure")

    monkeypatch.setattr(N6700, "connect", refuse)
    write_plan(tmp_path)
    assert main(argv(tmp_path, "--hardware")) == 1
    assert "injected connection failure" in capsys.readouterr().err


def test_only_a_fully_authorized_run_reaches_the_connection_call_with_the_plan_settings(tmp_path, monkeypatch, connects) -> None:
    for key, value in HARDWARE_ENV.items():
        monkeypatch.setenv(key, value)
    write_plan(tmp_path)
    with pytest.raises(RuntimeError, match="connect reached"):
        main(argv(tmp_path, "--hardware"))
    assert len(connects) == 1
    call = connects[0]
    assert call["resource"] == "192.0.2.10" and call["connection_type"] == "ethernet"
    assert call["reset_on_connect"] is False and call["clear_errors_on_connect"] is False


def test_the_shipped_example_plan_can_never_be_run_as_is(tmp_path, monkeypatch, connects) -> None:
    from keysight_n6700.arb_hil import HilRefused, validate_arb_plan

    example = json.loads((Path(__file__).resolve().parents[2] / "docs" / "arb_hil_plan.example.json").read_text())
    with pytest.raises(HilRefused) as caught:
        validate_arb_plan(example)
    reasons = "; ".join(caught.value.reasons)
    assert "not approved" in reasons and "plan_sha256" in reasons and "emergency_stop_verified" in reasons
    for key, value in HARDWARE_ENV.items():
        monkeypatch.setenv(key, value)
    (tmp_path / "plan.json").write_text(json.dumps(example))
    assert main(argv(tmp_path, "--hardware", operator="REPLACE-operator-name")) == 2
    assert connects == []
