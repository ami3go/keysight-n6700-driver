"""Offline tests of the automatic HIL suite. These do NOT connect to hardware."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from keysight_n6700 import N6700
from keysight_n6700.channel import ElectronicLoadChannel
from keysight_n6700.module_capabilities import classify_module
from keysight_n6700.scope_hil_suite import run_hil_plan, validate_plan
from scripts.run_scope_hil_suite import validate_connection


def _profile() -> dict[str, Any]:
    return {
        "approved": True,
        "channel": 3,
        "expected_module": "N6791A",
        "expected_serial": "SIM0003",
        "expected_idn": "N6705C",
        "operator": "verified-operator",
        "bench_review_id": "review-123",
        "max_voltage_v": 20.0,
        "max_current_a": 2.5,
        "connection": {"type": "ethernet", "resource": "192.168.0.6"},
        "stop_on_failure": True,
        "cases": [
            {"name": "small-voltage", "scenario": "bus", "function": "voltage",
             "points": 256, "interval_s": 20.48e-6, "offset_points": -64,
             "transfer_format": "ascii", "repetitions": 2},
            {"name": "large-current", "scenario": "bus", "function": "current",
             "points": 4096, "interval_s": 20.48e-6, "offset_points": 0,
             "transfer_format": "real32", "repetitions": 1},
            {"name": "operator-abort", "scenario": "abort", "function": "voltage",
             "points": 128, "interval_s": 20.48e-6},
            {"name": "missing-trigger", "scenario": "missing_trigger", "function": "voltage",
             "points": 128, "interval_s": 20.48e-6, "wait_timeout_s": 0.2},
        ],
    }


class SimBench:
    """A simulator-backed test double; never used by the public HIL CLI."""

    def __init__(self, driver: N6700) -> None:
        self.driver = driver
        self.channel = ElectronicLoadChannel(driver, 3, classify_module("N6791A"))
        self.off_commands = 0

    def get_identity(self) -> str:
        return self.driver.get_identity()

    def channel_model(self, number: int) -> str:
        return "N6791A" if number == 3 else self.driver.channel_model(number)

    def channel_serial(self, number: int) -> str:
        return self.driver.channel_serial(number)

    def load(self, number: int) -> ElectronicLoadChannel:
        assert number == 3
        return self.channel

    def channel_count(self) -> int:
        return self.driver.channel_count()

    def query_scpi(self, command: str) -> str:
        return self.driver.query_scpi(command)

    def check_errors(self) -> None:
        self.driver.check_errors()


def test_passive_hil_matrix_writes_independent_evidence(tmp_path: Path) -> None:
    with N6700.connect_simulated() as simulator:
        profile = validate_plan(_profile())
        evidence = run_hil_plan(SimBench(simulator), profile, tmp_path / "hil")
    assert evidence["result"] == "PASS"
    assert evidence["qualification"] == "PROTOCOL_ONLY_NOT_METROLOGY"
    assert evidence["final_output_off_verified"] is True
    assert len(evidence["cases"]) == 5
    assert {case["status"] for case in evidence["cases"]} == {"PASS"}
    assert [x["final_acquisition_state"] for x in evidence["cases"]] == [
        "fetched", "fetched", "fetched", "aborted", "aborted"
    ]
    wave = evidence["cases"][2]["waveform"]
    assert wave["format"] == "real"
    assert wave["raw_sha256"]
    assert Path(wave["path"]).is_file()
    assert wave["sha256"]
    assert json.loads((tmp_path / "hil" / "summary.json").read_text())["result"] == "PASS"
    assert 'failures="0"' in (tmp_path / "hil" / "junit.xml").read_text()


def test_instrument_identity_failure_never_commands_unapproved_output(tmp_path: Path) -> None:
    class Untrusted:
        def get_identity(self) -> str:
            return "OTHER,DEVICE,SERIAL,VERSION"

        def channel_model(self, channel: int) -> str:
            return "NOT_A_LOAD"

        def channel_serial(self, channel: int) -> str:
            return "UNEXPECTED"

        def load(self, channel: int) -> None:
            raise AssertionError("output command sent to unidentified instrument")

    result = run_hil_plan(Untrusted(), validate_plan(_profile()), tmp_path / "untrusted")
    assert result["result"] == "FAIL"
    assert result["final_output_off_verified"] is False
    assert "identity/model/serial" in result["error"]
    junit = (tmp_path / "untrusted" / "junit.xml").read_text()
    assert 'failures="1"' in junit


@pytest.mark.parametrize("edit", [
    {"approved": False},
    {"channel": 0},
    {"expected_module": "N6799Z"},
    {"max_voltage_v": float("nan")},
    {"cases": []},
    {"cases": [{"name": "../../escape", "points": 128, "interval_s": 20.48e-6}]},
    {"cases": [{"name": "overflow", "points": 524288, "interval_s": 20.48e-6,
                "repetitions": 200}]},
    {"cases": [{"name": "short-fill", "points": 128, "offset_points": -100,
                "interval_s": 1.0, "wait_timeout_s": 0.1}]},
])
def test_invalid_plans_are_rejected_before_hardware(edit: dict[str, Any]) -> None:
    plan = _profile()
    plan.update(edit)
    with pytest.raises(ValueError):
        validate_plan(plan)


def test_real_transport_requires_two_independent_approval_signals(monkeypatch) -> None:
    p = _profile()
    monkeypatch.delenv("N6700_HIL_ENABLED", raising=False)
    monkeypatch.setenv("N6700_SCOPE_HIL_APPROVED", "YES_AND_REVIEWED")
    with pytest.raises(ValueError, match="BOTH required"):
        validate_connection(p, operator=p["operator"])
    monkeypatch.setenv("N6700_HIL_ENABLED", "true")
    monkeypatch.delenv("N6700_SCOPE_HIL_APPROVED", raising=False)
    with pytest.raises(ValueError, match="BOTH required"):
        validate_connection(p, operator=p["operator"])
    monkeypatch.setenv("N6700_SCOPE_HIL_APPROVED", "YES_AND_REVIEWED")
    assert validate_connection(p, operator=p["operator"])["type"] == "ethernet"
    with pytest.raises(ValueError, match="operator differs"):
        validate_connection(p, operator="different")


def test_real_transport_never_accepts_simulated(monkeypatch) -> None:
    monkeypatch.setenv("N6700_HIL_ENABLED", "true")
    monkeypatch.setenv("N6700_SCOPE_HIL_APPROVED", "YES_AND_REVIEWED")
    p = _profile()
    p["connection"] = {"type": "simulated", "resource": "any"}
    with pytest.raises(ValueError, match="only real"):
        validate_connection(p, operator=p["operator"])
