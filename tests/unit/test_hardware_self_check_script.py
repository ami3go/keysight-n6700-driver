"""Verify scripts/run_hardware_self_check.py's logic against the simulator.

The script's CLI only accepts real-transport connection types (LPDS-009
§18: no silent simulator fallback for a hardware tool) — its ``main()`` is
intentionally not exercised here. This test imports the script as a module
and drives its checking/reporting functions directly against
``N6700.connect_simulated()``, which is the only way to get any automated
confidence in this logic without physical hardware.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from keysight_n6700 import N6700

SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "run_hardware_self_check.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_hardware_self_check", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script() -> ModuleType:
    return _load_script()


@pytest.fixture
def connected_driver() -> N6700:
    drv = N6700.connect_simulated()
    try:
        yield drv
    finally:
        drv.disconnect()


def test_run_check_records_pass_and_fail(script: ModuleType) -> None:
    results: list = []
    script.run_check(results, "ok", lambda: 42)
    script.run_check(results, "boom", lambda: (_ for _ in ()).throw(RuntimeError("nope")))
    assert results[0].status == "pass"
    assert results[0].value == 42
    assert results[1].status == "fail"
    assert "RuntimeError" in results[1].detail


def test_run_read_only_checks_against_the_simulator(script: ModuleType, connected_driver: N6700) -> None:
    results: list = []
    modules = script.run_read_only_checks(connected_driver, results)
    assert modules
    assert results
    failed = [r for r in results if r.status == "fail"]
    assert not failed, failed


def test_guarded_output_test_enables_measures_and_disables(
    script: ModuleType, connected_driver: N6700
) -> None:
    modules = connected_driver.discover_modules()
    outcome = script.run_guarded_output_test(
        connected_driver,
        modules,
        channel=1,
        voltage=5.0,
        current_limit=0.5,
        settle_timeout_s=2.0,
        confirm=lambda _prompt: True,
    )
    assert outcome["status"] == "pass"
    assert outcome["measured_voltage_v"] == pytest.approx(5.0, rel=0.1)
    assert connected_driver.get_output_state(1) is False  # disabled again afterward


def test_guarded_output_test_refuses_an_already_energized_channel(
    script: ModuleType, connected_driver: N6700
) -> None:
    modules = connected_driver.discover_modules()
    connected_driver.set_dc_voltage(3.0, 1)
    connected_driver.enable_output(1)
    try:
        outcome = script.run_guarded_output_test(
            connected_driver,
            modules,
            channel=1,
            voltage=5.0,
            current_limit=0.5,
            settle_timeout_s=2.0,
            confirm=lambda _prompt: True,
        )
        assert outcome["status"] == "skip"
        assert "already ON" in outcome["detail"]
    finally:
        connected_driver.disable_output(1)


def test_guarded_output_test_skipped_without_confirmation(
    script: ModuleType, connected_driver: N6700
) -> None:
    modules = connected_driver.discover_modules()
    outcome = script.run_guarded_output_test(
        connected_driver,
        modules,
        channel=1,
        voltage=5.0,
        current_limit=0.5,
        settle_timeout_s=2.0,
        confirm=lambda _prompt: False,
    )
    assert outcome["status"] == "skip"
    assert connected_driver.get_output_state(1) is False


def test_guarded_output_test_rejects_a_non_power_channel(
    script: ModuleType, connected_driver: N6700
) -> None:
    modules = connected_driver.discover_modules()
    outcome = script.run_guarded_output_test(
        connected_driver,
        modules,
        channel=3,  # SIM_LOAD in the default simulator map
        voltage=5.0,
        current_limit=0.5,
        settle_timeout_s=2.0,
        confirm=lambda _prompt: True,
    )
    assert outcome["status"] == "skip"


def test_write_report_produces_json_and_markdown(script: ModuleType, tmp_path) -> None:
    report = script.Report(resource="sim", connection_type="simulated", identity={"model": "N6700B"})
    report.results.append(script.CheckResult("get_identity", "pass", value="KEYSIGHT,N6700B"))
    report.results.append(script.CheckResult("bad_check", "fail", detail="boom"))
    run_dir = script.write_report(report, tmp_path)
    assert (run_dir / "report.json").exists()
    assert (run_dir / "summary.md").exists()
    import json

    document = json.loads((run_dir / "report.json").read_text())
    assert document["summary"] == {"pass": 1, "fail": 1, "skip": 0}
    assert "boom" in (run_dir / "summary.md").read_text()


def test_parse_args_requires_all_output_test_flags_together(script: ModuleType) -> None:
    args = script.parse_args(["--resource", "TCPIP::x::INSTR", "--output-test"])
    assert args.output_test is True
    assert args.output_channel is None
