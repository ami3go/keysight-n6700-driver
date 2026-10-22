"""ARB-R06: authorization gate, evidence contract and fail-closed runner (simulator only).

Nothing here is hardware evidence.  A stand-in "hardware" driver is the simulator
with its transport descriptor patched to look like TCP, so the real-run path can be
exercised; every refusal test asserts that no output was ever commanded ON.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from scpi_driver_core.simulation.scripted import ScriptedScpiTransport
from scpi_driver_core.transport.models import TransportDescriptor

from keysight_n6700 import N6700, arb_hil
from keysight_n6700.arb_hil import (
    ENV_APPROVED,
    ENV_ENABLED,
    ENV_ENERGIZE_CASES,
    HilRefused,
    check_environment,
    energize_blockers,
    plan_sha256,
    run_arb_hil,
    validate_arb_plan,
    validate_case_evidence,
    verify_evidence_dir,
)

SINE = {"shape": "ArbSine", "args": [1.0, 3.0, 100.0]}
ENV_OK = {ENV_ENABLED: "true", ENV_APPROVED: "YES_AND_REVIEWED"}


def make_plan(*mutations: Any, rehash: bool = True) -> dict[str, Any]:
    """A valid approved plan matching the simulator's N6700C/N6775A, with optional edits."""
    plan: dict[str, Any] = {
        "schema_version": "1.0",
        "approved": True,
        "approval": {"reference": "SITE-2026-014", "operator": "Operator A", "reviewer": "Reviewer B"},
        "connection": {"type": "ethernet", "resource": "192.0.2.10"},
        "instrument": {
            "idn_contains": "N6700C", "mainframe_serial": "SIM000001", "firmware": "B.00.00",
            "channel": 1, "module_model": "N6775A", "module_serial": "SIM0001", "options": ["054"],
        },
        "fixture": {"wiring_revision": "rev C", "load": "10 ohm 5 W resistor", "emergency_stop_verified": True},
        "envelope": {
            "minimum_voltage_v": 0.0, "maximum_voltage_v": 6.0, "baseline_voltage_v": 3.0,
            "current_limit_a": 0.1, "maximum_duration_s": 2.0,
        },
        "calibration": None,
        "cases": [
            {
                "case_id": "CFG-SINE-001", "qualification_level": "protocol_nonenergizing",
                "waveform": SINE, "energize": False, "energize_ack": None, "watchdog_s": 5.0,
            }
        ],
        "stop_on_failure": True,
    }
    for mutate in mutations:
        mutate(plan)
    if rehash:
        try:
            plan["approval"]["plan_sha256"] = plan_sha256(plan)
        except ValueError:  # a deliberately non-finite value cannot be hashed
            plan["approval"]["plan_sha256"] = "0" * 64
    return plan


def energized_case(case_id: str = "RUN-SINE-001", level: str = "protocol_energized", **extra: Any) -> dict[str, Any]:
    case = {
        "case_id": case_id, "qualification_level": level, "waveform": SINE,
        "energize": True, "energize_ack": f"ENERGIZE_{case_id}", "watchdog_s": 5.0,
    }
    case.update(extra)
    return case


def set_cases(*cases: dict[str, Any]) -> Any:
    return lambda plan: plan.__setitem__("cases", list(cases))


def calibration_block(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    record = tmp_path / "dso_cal.pdf"
    record.write_bytes(b"calibration certificate")
    block = {
        "dso_model": "DSO-X 3024T", "calibration_record": str(record),
        "calibration_record_sha256": hashlib.sha256(record.read_bytes()).hexdigest(),
        "calibration_due": "2999-01-01", "bandwidth_hz": 2.0e8, "probe_attenuation": 10.0,
        "uncertainty": "k=2, 1.5 % of reading",
    }
    block.update(overrides)
    return block


def metrology_case(**extra: Any) -> dict[str, Any]:
    return energized_case(
        "MET-SINE-001", "analog_metrology",
        acceptance={
            "vendor_limit_source": "N6700C specification 9018-04533",
            "numeric_limits": {"amplitude_error_v": 0.05}, "pass_fail_rule": "|error| <= limit with k=2 margin",
        },
        **extra,
    )


def _hardware_standin(monkeypatch: pytest.MonkeyPatch, mainframe: str) -> N6700:
    driver = N6700.connect_simulated(models={1: "N6775A", 2: "N6775A"}, mainframe_model=mainframe)
    transport = driver._session(None).transport
    assert isinstance(transport, ScriptedScpiTransport)
    transport.instrument.channels[1].option = "054"
    transport.instrument.channels[2].option = "054"
    driver.discover_modules()
    monkeypatch.setattr(
        type(transport), "descriptor", property(lambda self: TransportDescriptor(kind="tcp", address="192.0.2.10"))
    )
    return driver


@pytest.fixture
def hardware(monkeypatch: pytest.MonkeyPatch):
    """A simulator-backed N6700C that presents itself as a TCP instrument."""
    driver = _hardware_standin(monkeypatch, "N6700C")
    yield driver
    driver.disconnect()


@pytest.fixture
def hardware_n6705c(monkeypatch: pytest.MonkeyPatch):
    """Same, as an N6705C, whose ARB trigger source is channel-scoped."""
    driver = _hardware_standin(monkeypatch, "N6705C")
    yield driver
    driver.disconnect()


def on_n6705c(plan: dict[str, Any]) -> None:
    plan["instrument"]["idn_contains"] = "N6705C"


def history(driver: N6700) -> list[str]:
    transport = driver._session(None).transport
    assert isinstance(transport, ScriptedScpiTransport)
    return list(transport.history)


def never_energized(driver: N6700) -> None:
    offenders = [
        c for c in history(driver)
        if c.upper().startswith(("OUTP ON", "OUTP 1", "INIT:TRAN", "INIT:CONT:TRAN ON", "TRIG:TRAN ("))
    ]
    assert offenders == [], offenders


def run(driver: N6700, plan_dict: dict[str, Any], tmp_path: Path, *, env=ENV_OK, dry_run=False):
    plan = validate_arb_plan(plan_dict)
    return run_arb_hil(driver, plan, tmp_path / "run", env=env, dry_run=dry_run)


def run_with_watchdog(driver: N6700, plan_dict: dict[str, Any], tmp_path: Path, seconds: float, *, env=ENV_OK):
    """Run a validated plan whose watchdog is shortened afterwards (validation forbids this)."""
    import dataclasses

    plan = validate_arb_plan(plan_dict)
    plan = dataclasses.replace(plan, cases=tuple(dataclasses.replace(c, watchdog_s=seconds) for c in plan.cases))
    return run_arb_hil(driver, plan, tmp_path / "run", env=env, dry_run=False)


def case_files(tmp_path: Path) -> dict[str, dict[str, Any]]:
    return {p.stem: json.loads(p.read_text()) for p in (tmp_path / "run").glob("*.json") if p.name not in {"summary.json", "plan.json"}}


# -- plan validation: every refusal happens before any I/O -------------------------------


def test_a_valid_plan_is_accepted_and_hashed() -> None:
    plan = validate_arb_plan(make_plan())
    assert plan.sha256 == plan_sha256(plan.raw) and plan.cases[0].case_id == "CFG-SINE-001"


REFUSALS = [
    ("unapproved", lambda p: p.__setitem__("approved", False), "not approved"),
    ("wrong schema", lambda p: p.__setitem__("schema_version", "9"), "schema_version"),
    ("no approval ref", lambda p: p["approval"].pop("reference"), "approval.reference"),
    ("no reviewer", lambda p: p["approval"].pop("reviewer"), "approval.reviewer"),
    ("self review", lambda p: p["approval"].__setitem__("reviewer", "Operator A"), "different person"),
    ("placeholder text", lambda p: p["fixture"].__setitem__("load", "REPLACE ME"), "fixture.load"),
    ("estop unverified", lambda p: p["fixture"].__setitem__("emergency_stop_verified", False), "emergency_stop_verified"),
    ("estop missing", lambda p: p["fixture"].pop("emergency_stop_verified"), "emergency_stop_verified"),
    ("no serial", lambda p: p["instrument"].pop("module_serial"), "instrument.module_serial"),
    ("bad channel", lambda p: p["instrument"].__setitem__("channel", 9), "instrument.channel"),
    ("options not list", lambda p: p["instrument"].__setitem__("options", "054"), "instrument.options"),
    ("simulated connection", lambda p: p["connection"].__setitem__("type", "simulated"), "no simulator"),
    ("no resource", lambda p: p["connection"].pop("resource"), "connection.resource"),
    ("nan envelope", lambda p: p["envelope"].__setitem__("maximum_voltage_v", math.nan), "envelope.maximum_voltage_v"),
    ("inverted envelope", lambda p: p["envelope"].update(minimum_voltage_v=7.0), "below maximum"),
    ("baseline outside", lambda p: p["envelope"].__setitem__("baseline_voltage_v", 9.0), "baseline"),
    ("zero current", lambda p: p["envelope"].__setitem__("current_limit_a", 0.0), "current_limit_a must be positive"),
    ("long envelope", lambda p: p["envelope"].__setitem__("maximum_duration_s", 600.0), "maximum_duration_s"),
    ("no cases", lambda p: p.__setitem__("cases", []), "at least one case"),
    ("duplicate ids", set_cases(*[make_plan()["cases"][0]] * 2), "unique"),
    ("unsafe id", lambda p: p["cases"][0].__setitem__("case_id", "../x"), "case_id"),
    ("unknown level", lambda p: p["cases"][0].__setitem__("qualification_level", "great"), "qualification_level"),
    ("level/energize mismatch", lambda p: p["cases"][0].__setitem__("energize", True), "must match the level"),
    ("missing ack", set_cases(energized_case(energize_ack=None)), "energize_ack"),
    ("wrong ack", set_cases(energized_case(energize_ack="yes")), "energize_ack"),
    ("ack on passive case", lambda p: p["cases"][0].__setitem__("energize_ack", "ENERGIZE_CFG-SINE-001"), "must not carry"),
    ("no watchdog", lambda p: p["cases"][0].pop("watchdog_s"), "watchdog_s"),
    ("huge watchdog", lambda p: p["cases"][0].__setitem__("watchdog_s", 9999), "watchdog_s"),
    ("nan watchdog", lambda p: p["cases"][0].__setitem__("watchdog_s", math.nan), "watchdog_s"),
    ("unsupported shape", lambda p: p["cases"][0].__setitem__("waveform", {"shape": "ArbSquare"}), "unsupported shape"),
    ("udef not supported", lambda p: p["cases"][0].__setitem__("waveform", {"shape": "ArbUserDefined"}), "unsupported shape"),
    ("invalid waveform args", lambda p: p["cases"][0].__setitem__("waveform", {"shape": "ArbSine", "args": [1.0]}), "invalid waveform"),
    ("beyond envelope", lambda p: p["cases"][0].__setitem__("waveform", {"shape": "ArbSine", "args": [4.0, 5.0, 100.0]}), "exceed the approved envelope"),
    ("below envelope", lambda p: p["cases"][0].__setitem__("waveform", {"shape": "ArbRamp", "args": [-1.0, 2.0, 0.01]}), "exceed the approved envelope"),
    ("too long", lambda p: p["cases"][0].__setitem__("waveform", {"shape": "ArbRamp", "args": [1.0, 2.0, 5.0]}), "above the envelope duration"),
    ("metrology without calibration", set_cases(metrology_case()), "valid calibration"),
    ("unhashable shape (list)", lambda p: p["cases"][0].__setitem__("waveform", {"shape": []}), "unsupported shape"),
    ("unhashable shape (dict)", lambda p: p["cases"][0].__setitem__("waveform", {"shape": {}}), "unsupported shape"),
    ("overflowing waveform argument", lambda p: p["cases"][0].__setitem__("waveform", {"shape": "ArbSine", "args": [10**400, 3.0, 100.0]}), "invalid waveform"),
    ("watchdog shorter than the run", lambda p: p["cases"][0].__setitem__("watchdog_s", 1.0), "at least the waveform duration"),
    ("reviewer is the operator in other case", lambda p: p["approval"].__setitem__("reviewer", " operator   a"), "different person"),
    ("bad port", lambda p: p["connection"].__setitem__("port", 70000), "connection.port"),
    ("bad timeout", lambda p: p["connection"].__setitem__("timeout_s", 0), "connection.timeout_s"),
]


@pytest.mark.parametrize(("name", "mutate", "needle"), REFUSALS, ids=[r[0] for r in REFUSALS])
def test_plan_refusals_name_the_reason(name, mutate, needle) -> None:
    with pytest.raises(HilRefused) as caught:
        validate_arb_plan(make_plan(mutate))
    assert needle in "; ".join(caught.value.reasons), caught.value.reasons


def test_editing_the_plan_after_approval_invalidates_it() -> None:
    plan = make_plan()
    plan["envelope"]["maximum_voltage_v"] = 5.9  # edited after the hash was recorded
    with pytest.raises(HilRefused, match="edited after approval"):
        validate_arb_plan(plan)
    plan = make_plan()
    plan["cases"][0]["watchdog_s"] = 6.0
    with pytest.raises(HilRefused, match="edited after approval"):
        validate_arb_plan(plan)


def test_a_plan_without_a_recorded_hash_is_refused() -> None:
    plan = make_plan()
    del plan["approval"]["plan_sha256"]
    with pytest.raises(HilRefused, match="plan_sha256"):
        validate_arb_plan(plan)


def test_non_object_plans_are_refused() -> None:
    for bad in (None, [], "plan", 3):
        with pytest.raises(HilRefused):
            validate_arb_plan(bad)


def test_all_problems_are_reported_together() -> None:
    plan = make_plan(lambda p: p.__setitem__("approved", False), lambda p: p["fixture"].pop("load"))
    with pytest.raises(HilRefused) as caught:
        validate_arb_plan(plan)
    assert len(caught.value.reasons) >= 2


# -- calibration -------------------------------------------------------------------------


def with_metrology(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    return make_plan(
        lambda p: p.__setitem__("calibration", calibration_block(tmp_path, **overrides)),
        set_cases(metrology_case()),
    )


def test_a_valid_calibration_enables_a_metrology_case(tmp_path: Path) -> None:
    plan = validate_arb_plan(with_metrology(tmp_path))
    assert plan.calibration is not None and plan.cases[0].qualification_level == "analog_metrology"


CAL_FAILS = [
    ("expired", {"calibration_due": "2020-01-01"}, "expired"),
    ("bad date", {"calibration_due": "soon"}, "ISO date"),
    ("missing file", {"calibration_record": "/nonexistent/cal.pdf"}, "not found"),
    ("hash mismatch", {"calibration_record_sha256": "0" * 64}, "does not match the file"),
    ("no dso", {"dso_model": ""}, "dso_model"),
    ("zero bandwidth", {"bandwidth_hz": 0}, "bandwidth_hz"),
    ("no uncertainty", {"uncertainty": ""}, "uncertainty"),
    ("bad probe", {"probe_attenuation": math.nan}, "probe_attenuation"),
]


@pytest.mark.parametrize(("name", "override", "needle"), CAL_FAILS, ids=[c[0] for c in CAL_FAILS])
def test_calibration_failures_block_a_metrology_plan(tmp_path, name, override, needle) -> None:
    with pytest.raises(HilRefused) as caught:
        validate_arb_plan(with_metrology(tmp_path, **override))
    assert needle in "; ".join(caught.value.reasons)


def test_metrology_requires_acceptance_limits_defined_up_front(tmp_path: Path) -> None:
    bare = energized_case("MET-SINE-001", "analog_metrology")
    plan = make_plan(lambda p: p.__setitem__("calibration", calibration_block(tmp_path)), set_cases(bare))
    with pytest.raises(HilRefused, match="acceptance limits"):
        validate_arb_plan(plan)


# -- environment and energization gates ---------------------------------------------------


@pytest.mark.parametrize(
    "env",
    [
        {}, {ENV_ENABLED: "true"}, {ENV_APPROVED: "YES_AND_REVIEWED"},
        {ENV_ENABLED: "1", ENV_APPROVED: "YES_AND_REVIEWED"},
        {ENV_ENABLED: "true", ENV_APPROVED: "yes"},
        {ENV_ENABLED: "TRUE", ENV_APPROVED: "YES_AND_REVIEWED"},
    ],
)
def test_the_per_run_opt_in_needs_both_exact_signals(env) -> None:
    with pytest.raises(HilRefused, match="HIL not authorized"):
        check_environment(env)


def test_a_hardware_run_without_the_opt_in_never_creates_evidence_or_touches_the_driver(hardware, tmp_path) -> None:
    before = len(history(hardware))
    with pytest.raises(HilRefused):
        run(hardware, make_plan(), tmp_path, env={ENV_ENABLED: "true"})
    assert not (tmp_path / "run").exists()
    assert len(history(hardware)) == before


def test_energization_needs_the_env_list_as_well_as_the_plan_ack() -> None:
    case = validate_arb_plan(make_plan(set_cases(energized_case()))).cases[0]
    assert energize_blockers(case, {}) and "does not list" in energize_blockers(case, {})[0]
    assert energize_blockers(case, {ENV_ENERGIZE_CASES: "OTHER-001"})
    assert energize_blockers(case, {ENV_ENERGIZE_CASES: "OTHER-001, RUN-SINE-001"}) == []
    passive = validate_arb_plan(make_plan()).cases[0]
    assert energize_blockers(passive, {}) == []


def test_a_denied_energization_is_not_run_and_never_turns_the_output_on(hardware, tmp_path) -> None:
    summary = run(hardware, make_plan(set_cases(energized_case())), tmp_path)  # env lacks the case list
    entry = case_files(tmp_path)["RUN-SINE-001"]
    assert entry["verdict"] == "NOT_RUN" and "energization refused" in entry["reason"]
    assert summary["result"] == "INCOMPLETE"
    never_energized(hardware)


# -- identity, capability and output-state gates (hardware path) --------------------------

IDENTITY_FAILS = [
    ("mainframe serial", lambda p: p["instrument"].__setitem__("mainframe_serial", "OTHER"), "mainframe serial"),
    ("module serial", lambda p: p["instrument"].__setitem__("module_serial", "SIM9999"), "module serial"),
    ("firmware", lambda p: p["instrument"].__setitem__("firmware", "B.00.01"), "firmware"),
    ("module model", lambda p: p["instrument"].__setitem__("module_model", "N6774A"), "module model"),
    ("options", lambda p: p["instrument"].__setitem__("options", []), "options"),
    ("extra option", lambda p: p["instrument"].__setitem__("options", ["054", "760"]), "options"),
    ("idn", lambda p: p["instrument"].__setitem__("idn_contains", "N6705C"), "IDN"),
]


@pytest.mark.parametrize(("name", "mutate", "needle"), IDENTITY_FAILS, ids=[i[0] for i in IDENTITY_FAILS])
def test_an_identity_mismatch_refuses_without_sending_any_command(hardware, tmp_path, name, mutate, needle) -> None:
    transport = hardware._session(None).transport
    transport.clear_history()
    with pytest.raises(HilRefused) as caught:
        run(hardware, make_plan(mutate), tmp_path)
    assert needle in "; ".join(caught.value.reasons)
    sent = [c for c in history(hardware) if not c.split(None, 1)[0].endswith("?")]
    assert sent == [], "no command may reach an unidentified instrument"
    summary = json.loads((tmp_path / "run" / "summary.json").read_text())
    assert summary["result"] == "FAIL" and "final_output_off_verified" not in summary


def test_a_simulated_transport_can_never_produce_hardware_evidence(tmp_path) -> None:
    driver = N6700.connect_simulated(models={1: "N6775A"}, mainframe_model="N6700C")
    try:
        with pytest.raises(HilRefused, match="simulated transport"):
            run(driver, make_plan(), tmp_path)
    finally:
        driver.disconnect()


def test_another_channel_that_is_on_blocks_the_run(hardware, tmp_path) -> None:
    hardware.set_dc_voltage(1.0, 2)
    hardware.enable_output(2)
    transport = hardware._session(None).transport
    transport.clear_history()
    with pytest.raises(HilRefused, match="unreviewed output 2"):
        run(hardware, make_plan(), tmp_path)
    assert not [c for c in history(hardware) if c.startswith("OUTP") and not c.startswith("OUTP?")], (
        "no output may be commanded after a refusal"
    )
    assert hardware.get_output_state(2) is True
    summary = json.loads((tmp_path / "run" / "summary.json").read_text())
    assert "final_output_off_verified" not in summary


def test_the_selected_output_already_on_blocks_the_run(hardware, tmp_path) -> None:
    hardware.set_dc_voltage(1.0, 1)
    hardware.enable_output(1)
    hardware._session(None).transport.clear_history()
    with pytest.raises(HilRefused, match="selected output 1"):
        run(hardware, make_plan(), tmp_path)
    assert not [c for c in history(hardware) if c.startswith("OUTP") and not c.startswith("OUTP?")], (
        "an output it refused must stay untouched"
    )
    assert hardware.get_output_state(1) is True


def test_a_module_without_arb_support_is_not_run_and_nothing_is_configured(hardware, tmp_path) -> None:
    transport = hardware._session(None).transport
    transport.instrument.channels[1].option = ""
    hardware.discover_modules()
    plan = make_plan(lambda p: p["instrument"].__setitem__("options", []))
    transport.clear_history()
    summary = run(hardware, plan, tmp_path)
    entry = case_files(tmp_path)["CFG-SINE-001"]
    assert entry["verdict"] == "NOT_RUN" and "do not advertise" in entry["reason"]
    assert summary["result"] == "INCOMPLETE"
    assert not [c for c in history(hardware) if c.startswith(("ARB:", "VOLT:MODE ARB"))]
    never_energized(hardware)


# -- dry run ------------------------------------------------------------------------------


def test_a_dry_run_needs_no_opt_in_and_can_only_be_not_run(tmp_path) -> None:
    driver = N6700.connect_simulated(models={1: "N6775A"}, mainframe_model="N6705C")
    try:
        summary = run(driver, make_plan(set_cases(*make_plan()["cases"], energized_case())), tmp_path, env={}, dry_run=True)
    finally:
        driver.disconnect()
    assert summary["dry_run"] is True and summary["result"] == "INCOMPLETE"
    for entry in case_files(tmp_path).values():
        assert entry["verdict"] == "NOT_RUN" and entry["dry_run"] is True
        assert entry["qualification_level"] == "software_dry_run"
        assert "not hardware evidence" in entry["reason"]
        assert validate_case_evidence(entry) == []
    assert verify_evidence_dir(tmp_path / "run") == []


# -- runs on the stand-in hardware ---------------------------------------------------------


def test_a_passive_case_passes_with_the_output_never_on(hardware, tmp_path) -> None:
    summary = run(hardware, make_plan(), tmp_path)
    evidence = case_files(tmp_path)["CFG-SINE-001"]
    assert evidence["verdict"] == "PASS" and summary["result"] == "PASS"
    assert evidence["safety"]["output_off_verified"] is True and evidence["safety"]["unknown_state"] is False
    assert evidence["observed"]["error_queue"] == [] and evidence["observed"]["scpi_readbacks"]["shape"] == "ArbSine"
    assert evidence["instrument"]["serial"] == "SIM0001" and evidence["plan_sha256"] == summary["plan_sha256"]
    assert summary["qualification"] == "PROTOCOL_ONLY_NOT_METROLOGY"
    assert summary["final_output_off_verified"] is True
    never_energized(hardware)
    assert verify_evidence_dir(tmp_path / "run") == []


def test_an_authorized_energized_case_runs_under_supervision_and_ends_off(hardware_n6705c, tmp_path) -> None:
    hardware = hardware_n6705c
    env = {**ENV_OK, ENV_ENERGIZE_CASES: "RUN-SINE-001"}
    plan = make_plan(on_n6705c, set_cases(energized_case("RUN-SINE-001", watchdog_s=3.0)))
    summary = run(hardware, plan, tmp_path, env=env)
    evidence = case_files(tmp_path)["RUN-SINE-001"]
    assert evidence["verdict"] == "PASS", evidence["reason"]
    assert [e["event"] for e in evidence["observed"]["status_timeline"]] == ["armed", "supervised"]
    assert evidence["safety"]["output_off_verified"] is True
    assert summary["result"] == "PASS" and summary["final_output_off_verified"] is True
    sent = history(hardware)
    assert "OUTP ON,(@1)" in sent and sent[::-1][sent[::-1].index("OUTP OFF,(@1)")] == "OUTP OFF,(@1)"
    assert hardware.get_output_state(1) is False and verify_evidence_dir(tmp_path / "run") == []


def test_a_metrology_case_stays_inconclusive_without_dso_evidence(hardware_n6705c, tmp_path) -> None:
    hardware = hardware_n6705c
    plan = make_plan(
        on_n6705c, lambda p: p.__setitem__("calibration", calibration_block(tmp_path)), set_cases(metrology_case())
    )
    env = {**ENV_OK, ENV_ENERGIZE_CASES: "MET-SINE-001"}
    summary = run(hardware, plan, tmp_path, env=env)
    evidence = case_files(tmp_path)["MET-SINE-001"]
    assert evidence["verdict"] == "INCONCLUSIVE" and "DSO evidence is not attached" in evidence["reason"]
    assert summary["result"] == "INCOMPLETE"
    assert hardware.get_output_state(1) is False


def test_n6700c_source_arb_trigger_dialect_fails_the_case_before_the_output_is_enabled(hardware, tmp_path) -> None:
    """Finding: the N6700C takes TRIG:ARB:SOUR without a channel list, so the source's
    channel-scoped command is rejected (-108).  The runner fails closed, output still OFF."""
    env = {**ENV_OK, ENV_ENERGIZE_CASES: "RUN-SINE-001"}
    summary = run(hardware, make_plan(set_cases(energized_case())), tmp_path, env=env)
    evidence = case_files(tmp_path)["RUN-SINE-001"]
    assert evidence["verdict"] == "FAIL" and "-108" in evidence["reason"]
    never_energized(hardware)
    assert not [c for c in history(hardware) if c.startswith("OUTP ON")]
    assert summary["result"] == "FAIL" and summary["final_output_off_verified"] is True


# -- fault handling -------------------------------------------------------------------------


def test_a_crashing_case_is_a_failure_with_the_output_off_and_later_cases_not_run(hardware, tmp_path, monkeypatch) -> None:
    second = {**make_plan()["cases"][0], "case_id": "CFG-SINE-002"}
    plan = make_plan(set_cases(make_plan()["cases"][0], second))

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("injected runner crash")

    monkeypatch.setattr(hardware, "configure_voltage_arb_waveform", boom)
    summary = run(hardware, plan, tmp_path)
    files = case_files(tmp_path)
    assert files["CFG-SINE-001"]["verdict"] == "FAIL" and "injected runner crash" in files["CFG-SINE-001"]["reason"]
    assert files["CFG-SINE-002"]["verdict"] == "NOT_RUN" and "earlier case failed" in files["CFG-SINE-002"]["reason"]
    assert summary["result"] == "FAIL" and summary["final_output_off_verified"] is True
    never_energized(hardware)


def test_without_stop_on_failure_later_cases_still_run(hardware, tmp_path, monkeypatch) -> None:
    second = {**make_plan()["cases"][0], "case_id": "CFG-SINE-002"}
    plan = make_plan(set_cases(make_plan()["cases"][0], second), lambda p: p.__setitem__("stop_on_failure", False))
    real = hardware.configure_voltage_arb_waveform
    calls = []

    def fail_first(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("only the first fails")
        return real(*args, **kwargs)

    monkeypatch.setattr(hardware, "configure_voltage_arb_waveform", fail_first)
    summary = run(hardware, plan, tmp_path)
    files = case_files(tmp_path)
    assert files["CFG-SINE-001"]["verdict"] == "FAIL" and files["CFG-SINE-002"]["verdict"] == "PASS"
    assert summary["result"] == "FAIL"


def test_watchdog_expiry_fails_the_case_and_forces_the_safe_state_from_its_own_thread(hardware, tmp_path, monkeypatch) -> None:
    real = hardware.configure_voltage_arb_waveform
    threads: list[str] = []
    real_disable = hardware.disable_output

    def slow(*args: Any, **kwargs: Any) -> Any:
        time.sleep(0.4)  # the body hangs past its watchdog
        return real(*args, **kwargs)

    def record_disable(*args: Any, **kwargs: Any) -> Any:
        threads.append(threading.current_thread().name)
        return real_disable(*args, **kwargs)

    monkeypatch.setattr(hardware, "configure_voltage_arb_waveform", slow)
    monkeypatch.setattr(hardware, "disable_output", record_disable)
    summary = run_with_watchdog(hardware, make_plan(), tmp_path, 0.05)
    evidence = case_files(tmp_path)["CFG-SINE-001"]
    assert evidence["verdict"] == "FAIL" and evidence["safety"]["watchdog_expired"] is True
    assert "watchdog expired" in evidence["reason"]
    assert evidence["safety"]["watchdog_cleanup_verified"] is True
    assert any(name != threading.main_thread().name for name in threads), "watchdog did not act on its own thread"
    assert summary["result"] == "FAIL" and hardware.get_output_state(1) is False


def test_a_body_still_running_after_the_watchdog_cannot_energize_the_output(hardware_n6705c, tmp_path, monkeypatch) -> None:
    hardware = hardware_n6705c
    real = hardware.configure_voltage_arb_waveform

    def slow(*args: Any, **kwargs: Any) -> Any:
        time.sleep(0.4)
        return real(*args, **kwargs)

    monkeypatch.setattr(hardware, "configure_voltage_arb_waveform", slow)
    env = {**ENV_OK, ENV_ENERGIZE_CASES: "RUN-SINE-001"}
    run_with_watchdog(hardware, make_plan(on_n6705c, set_cases(energized_case())), tmp_path, 0.05, env=env)
    evidence = case_files(tmp_path)["RUN-SINE-001"]
    assert evidence["verdict"] == "FAIL" and evidence["safety"]["watchdog_expired"] is True
    assert not [c for c in history(hardware) if c.startswith("OUTP ON")], "the guard must stop the body"
    assert hardware.get_output_state(1) is False


def test_an_energized_run_that_never_completes_is_a_failure_not_a_deadline_pass(hardware_n6705c, tmp_path) -> None:
    hardware = hardware_n6705c
    hardware._session(None).transport.instrument.transient_active_polls = 10**9  # never finishes
    env = {**ENV_OK, ENV_ENERGIZE_CASES: "RUN-SINE-001"}
    plan = make_plan(on_n6705c, set_cases(energized_case("RUN-SINE-001", watchdog_s=2.5)))
    summary = run(hardware, plan, tmp_path, env=env)
    evidence = case_files(tmp_path)["RUN-SINE-001"]
    assert evidence["verdict"] == "FAIL" and "deadline" in evidence["reason"]
    assert evidence["safety"]["watchdog_expired"] is False, "the supervisor deadline comes before the watchdog"
    assert evidence["safety"]["output_off_verified"] is True and summary["result"] == "FAIL"


def test_a_capability_check_failure_is_recorded_as_a_failed_case(hardware, tmp_path, monkeypatch) -> None:
    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("discovery timed out")

    real = hardware.discover_modules
    calls = {"n": 0}

    def fail_after_identity(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        return broken() if calls["n"] > 0 else real(*args, **kwargs)

    monkeypatch.setattr(hardware, "discover_modules", fail_after_identity)
    summary = run(hardware, make_plan(), tmp_path)
    evidence = case_files(tmp_path)["CFG-SINE-001"]
    assert evidence["verdict"] == "FAIL" and "capability check failed" in evidence["reason"]
    assert summary["result"] == "FAIL"


def test_a_denied_energization_does_no_capability_discovery(hardware, tmp_path, monkeypatch) -> None:
    def must_not_run(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("discovery ran for a refused case")

    monkeypatch.setattr(hardware, "discover_modules", must_not_run)
    run(hardware, make_plan(set_cases(energized_case())), tmp_path)  # env lacks the case list
    assert case_files(tmp_path)["RUN-SINE-001"]["verdict"] == "NOT_RUN"


def test_a_keyboard_interrupt_finishes_cleanup_writes_evidence_and_propagates(hardware, tmp_path, monkeypatch) -> None:
    def interrupt(*args: Any, **kwargs: Any) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(hardware, "configure_voltage_arb_waveform", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run(hardware, make_plan(), tmp_path)
    evidence = case_files(tmp_path)["CFG-SINE-001"]
    assert evidence["verdict"] == "FAIL" and "interrupted" in evidence["reason"]
    assert evidence["safety"]["output_off_verified"] is True
    summary = json.loads((tmp_path / "run" / "summary.json").read_text())
    assert summary["final_output_off_verified"] is True and hardware.get_output_state(1) is False


def test_forced_output_off_failure_loses_the_safe_state_and_stops_everything(hardware, tmp_path, monkeypatch) -> None:
    second = {**make_plan()["cases"][0], "case_id": "CFG-SINE-002"}
    plan = make_plan(set_cases(make_plan()["cases"][0], second), lambda p: p.__setitem__("stop_on_failure", False))
    monkeypatch.setattr(hardware, "get_output_state", lambda *a, **k: True)  # OFF never verifies
    summary = run(hardware, plan, tmp_path)
    files = case_files(tmp_path)
    first = files["CFG-SINE-001"]
    assert first["verdict"] == "FAIL" and "could not be verified OFF" in first["reason"]
    assert first["safety"]["output_off_verified"] is False and first["safety"]["unknown_state"] is True
    assert files["CFG-SINE-002"]["verdict"] == "NOT_RUN", "must stop even when stop_on_failure is false"
    assert summary["result"] == "FAILED_UNVERIFIED_SAFE_STATE" and summary["final_output_off_verified"] is False


def test_an_scpi_error_in_the_queue_fails_the_case(hardware, tmp_path, monkeypatch) -> None:
    real_body = arb_hil._run_case_body

    def body_then_error(*args: Any, **kwargs: Any) -> None:
        real_body(*args, **kwargs)
        hardware._session(None).transport.instrument.errors.append((-221, "Settings conflict"))

    monkeypatch.setattr(arb_hil, "_run_case_body", body_then_error)
    summary = run(hardware, make_plan(), tmp_path)
    evidence = case_files(tmp_path)["CFG-SINE-001"]
    assert evidence["verdict"] == "FAIL" and "SCPI errors" in evidence["reason"] and summary["result"] == "FAIL"


def test_an_unreadable_error_queue_fails_the_case(hardware, tmp_path, monkeypatch) -> None:
    def unreadable(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("queue timeout")

    monkeypatch.setattr(hardware, "drain_errors", unreadable)
    run(hardware, make_plan(), tmp_path)
    assert case_files(tmp_path)["CFG-SINE-001"]["verdict"] == "FAIL"


# -- evidence files: atomic, complete, tamper-evident ---------------------------------------


def test_a_crash_while_writing_the_summary_leaves_it_missing_and_detectable(hardware, tmp_path, monkeypatch) -> None:
    real = arb_hil._atomic_write

    def crash_on_summary(path: Path, text: str) -> None:
        if path.name == "summary.json":
            raise OSError("disk full")
        real(path, text)

    monkeypatch.setattr(arb_hil, "_atomic_write", crash_on_summary)
    with pytest.raises(OSError, match="disk full"):
        run(hardware, make_plan(), tmp_path)
    run_dir = tmp_path / "run"
    assert not (run_dir / "summary.json").exists()
    assert (run_dir / "CFG-SINE-001.json").exists(), "completed case evidence is kept"
    problems = verify_evidence_dir(run_dir)
    assert problems and "summary.json is missing" in problems[0]
    assert hardware.get_output_state(1) is False


def test_tampered_or_missing_case_files_are_detected(hardware, tmp_path) -> None:
    run(hardware, make_plan(), tmp_path)
    run_dir = tmp_path / "run"
    assert verify_evidence_dir(run_dir) == []
    target = run_dir / "CFG-SINE-001.json"
    original = target.read_text()
    target.write_text(original.replace('"PASS"', '"FAIL"', 1))
    assert any("SHA-256 differs" in p for p in verify_evidence_dir(run_dir))
    target.write_text("{not json")
    assert any("SHA-256 differs" in p or "not valid JSON" in p for p in verify_evidence_dir(run_dir))
    target.unlink()
    assert any("missing" in p for p in verify_evidence_dir(run_dir))


def test_a_truncated_summary_is_detected(hardware, tmp_path) -> None:
    run(hardware, make_plan(), tmp_path)
    (tmp_path / "run" / "summary.json").write_text('{"cases": [')
    assert verify_evidence_dir(tmp_path / "run") == ["summary.json is not valid JSON"]


def test_an_existing_output_directory_is_never_overwritten(hardware, tmp_path) -> None:
    (tmp_path / "run").mkdir()
    with pytest.raises(FileExistsError):
        run(hardware, make_plan(), tmp_path)
    never_energized(hardware)


# -- evidence schema ------------------------------------------------------------------------


def good_evidence(hardware, tmp_path) -> dict[str, Any]:
    run(hardware, make_plan(), tmp_path)
    return case_files(tmp_path)["CFG-SINE-001"]


def test_pass_requires_a_verified_safe_state_and_clean_queue(hardware, tmp_path) -> None:
    evidence = good_evidence(hardware, tmp_path)
    assert validate_case_evidence(evidence) == []
    for path, value, needle in (
        (("safety", "unknown_state"), True, "unknown_state"),
        (("safety", "output_off_verified"), False, "output_off_verified"),
        (("safety", "output_off_verified"), None, "output_off_verified"),
        (("safety", "watchdog_expired"), True, "watchdog"),
        (("observed", "error_queue"), ["-100,Command error"], "error queue"),
        (("observed", "error_queue"), None, "error queue"),
        (("utc_end",), None, "timestamps"),
        (("instrument", "serial"), None, "identified instrument"),
    ):
        broken = copy.deepcopy(evidence)
        target = broken
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        assert any(needle in problem for problem in validate_case_evidence(broken)), path


def test_the_schema_rejects_unknown_verdicts_and_dry_run_passes(hardware, tmp_path) -> None:
    evidence = good_evidence(hardware, tmp_path)
    for field, value in (("verdict", "GREEN"), ("qualification_level", "great"), ("schema_version", "0.1")):
        broken = {**evidence, field: value}
        assert validate_case_evidence(broken)
    assert any("dry run" in p for p in validate_case_evidence({**evidence, "dry_run": True}))
    assert any("analog_metrology" in p for p in validate_case_evidence({**evidence, "qualification_level": "analog_metrology"}))
    assert validate_case_evidence({k: v for k, v in evidence.items() if k != "safety"})
    assert validate_case_evidence("not a dict") == ["evidence must be an object"]


def test_not_run_and_executed_non_pass_verdicts_are_well_formed(hardware, tmp_path) -> None:
    evidence = good_evidence(hardware, tmp_path)
    assert validate_case_evidence({**evidence, "verdict": "NOT_RUN"}) == []
    assert validate_case_evidence({**evidence, "verdict": "FAIL"}) == []
    assert validate_case_evidence({**evidence, "verdict": "INCONCLUSIVE"}) == []
    assert any("utc_start" in p for p in validate_case_evidence({**evidence, "verdict": "FAIL", "utc_start": None}))


# -- stronger evidence verification -----------------------------------------------------------


def passing_run(hardware, tmp_path) -> Path:
    second = {**make_plan()["cases"][0], "case_id": "CFG-SINE-002"}
    run(hardware, make_plan(set_cases(make_plan()["cases"][0], second)), tmp_path)
    return tmp_path / "run"


def rewrite_summary(run_dir: Path, mutate: Any) -> None:
    summary = json.loads((run_dir / "summary.json").read_text())
    mutate(summary)
    (run_dir / "summary.json").write_text(json.dumps(summary))


def test_dropping_a_case_from_the_summary_and_disk_is_detected(hardware, tmp_path) -> None:
    run_dir = passing_run(hardware, tmp_path)
    assert verify_evidence_dir(run_dir) == []
    (run_dir / "CFG-SINE-002.json").unlink()
    rewrite_summary(run_dir, lambda s: s["cases"].pop())
    assert any("differ from the cases in plan.json" in p for p in verify_evidence_dir(run_dir))


def test_a_doctored_summary_result_is_detected(hardware, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(hardware, "configure_voltage_arb_waveform", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    run(hardware, make_plan(), tmp_path)
    run_dir = tmp_path / "run"
    assert verify_evidence_dir(run_dir) == []
    rewrite_summary(run_dir, lambda s: s.__setitem__("result", "PASS"))
    assert any("not supported by the case verdicts" in p for p in verify_evidence_dir(run_dir))
    rewrite_summary(run_dir, lambda s: s.__setitem__("result", "INCOMPLETE"))
    assert any("hides a failed case" in p for p in verify_evidence_dir(run_dir))


def test_verdict_mismatch_between_summary_and_case_file_is_detected(hardware, tmp_path) -> None:
    run_dir = passing_run(hardware, tmp_path)
    rewrite_summary(run_dir, lambda s: s["cases"][0].__setitem__("verdict", "FAIL"))
    assert any("verdict differs" in p for p in verify_evidence_dir(run_dir))


def test_an_extra_case_file_and_an_edited_plan_are_detected(hardware, tmp_path) -> None:
    run_dir = passing_run(hardware, tmp_path)
    (run_dir / "CFG-SINE-099.json").write_text("{}")
    assert any("not listed in the summary" in p for p in verify_evidence_dir(run_dir))
    (run_dir / "CFG-SINE-099.json").unlink()
    plan = json.loads((run_dir / "plan.json").read_text())
    plan["envelope"]["maximum_voltage_v"] = 99
    (run_dir / "plan.json").write_text(json.dumps(plan))
    assert any("no longer matches" in p for p in verify_evidence_dir(run_dir))


def test_a_path_traversing_or_malformed_summary_cannot_escape_or_crash(hardware, tmp_path) -> None:
    run_dir = passing_run(hardware, tmp_path)
    rewrite_summary(run_dir, lambda s: s["cases"][0].__setitem__("file", "../plan.json"))
    assert any("unexpected file name" in p for p in verify_evidence_dir(run_dir))
    (run_dir / "summary.json").write_text("[]")
    assert verify_evidence_dir(run_dir) == ["summary.json has no case list"]
    (run_dir / "summary.json").write_text('{"cases": [3]}')
    assert any("not an object" in p for p in verify_evidence_dir(run_dir))


def test_validate_case_evidence_survives_malformed_sections(hardware, tmp_path) -> None:
    evidence = good_evidence(hardware, tmp_path)
    for key in ("safety", "observed", "instrument", "requested"):
        assert validate_case_evidence({**evidence, key: None}) == [f"{key} must be an object"]
    assert validate_case_evidence({**evidence, "qualification_level": "software_dry_run"})
    assert any("dry_run" in p for p in validate_case_evidence({**evidence, "dry_run": True, "verdict": "NOT_RUN"}) + validate_case_evidence({**evidence, "dry_run": False, "qualification_level": "software_dry_run", "verdict": "NOT_RUN"}))
    no_readbacks = {**evidence, "observed": {**evidence["observed"], "scpi_readbacks": None}}
    assert any("readbacks" in p for p in validate_case_evidence(no_readbacks))
    energized = {**evidence, "requested": {**evidence["requested"], "energize": True}}
    assert any("status timeline" in p for p in validate_case_evidence(energized))
