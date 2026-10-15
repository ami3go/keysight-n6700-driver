from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from keysight_n6700 import N6700

SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "run_voltage_waveform.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_voltage_waveform", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script() -> ModuleType:
    return _load_script()


def test_pulse_profile_maps_user_parameters_to_three_arb_points(script: ModuleType) -> None:
    profile = script.build_pulse_profile(
        baseline_v=12.0,
        level_v=6.0,
        delay_s=0.01,
        width_s=0.02,
        recovery_s=0.01,
    )
    assert profile.levels_v == (12.0, 6.0, 12.0)
    assert profile.dwell_s == (0.01, 0.02, 0.01)
    assert profile.duration_s == pytest.approx(0.04)


def test_ramp_profile_is_bounded_and_preserves_requested_duration(script: ModuleType) -> None:
    profile = script.build_ramp_profile(
        start_v=1.0,
        end_v=5.0,
        delay_s=0.01,
        ramp_time_s=0.1,
        hold_s=0.02,
        points=100,
    )
    assert len(profile.levels_v) == 102
    assert profile.levels_v[-1] == pytest.approx(5.0)
    assert profile.duration_s == pytest.approx(0.13)
    assert type(profile.waveform).__name__ == "ArbRamp"


def test_sine_profile_uses_repeat_count_for_additional_cycles(script: ModuleType) -> None:
    profile = script.build_sine_profile(
        offset_v=5.0,
        amplitude_v=2.0,
        frequency_hz=10.0,
        points_per_cycle=100,
    )
    assert len(profile.levels_v) == 100
    assert min(profile.levels_v) == pytest.approx(3.0)
    assert max(profile.levels_v) == pytest.approx(7.0)
    assert profile.duration_s == pytest.approx(0.1)
    assert type(profile.waveform).__name__ == "ArbSine"
    summary = script.profile_summary(profile, repeat_count=3)
    assert summary["total_duration_s"] == pytest.approx(0.3)


def test_csv_profile_uses_named_voltage_and_dwell_columns(script: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "profile.csv"
    path.write_text("voltage_v,dwell_s\n12,0.01\n6,0.02\n12,0.01\n", encoding="utf-8")
    profile = script.load_csv_profile(path)
    assert profile.levels_v == (12.0, 6.0, 12.0)
    assert profile.dwell_s == (0.01, 0.02, 0.01)
    assert profile.baseline_v == 12.0


def test_profile_validation_enforces_voltage_envelope_and_duration(script: ModuleType) -> None:
    profile = script.build_pulse_profile(
        baseline_v=12.0, level_v=6.0, delay_s=0.01, width_s=0.02, recovery_s=0.01
    )
    with pytest.raises(ValueError, match="approved"):
        script.validate_profile(
            profile,
            minimum_voltage_v=7.0,
            maximum_voltage_v=18.0,
            repeat_count=1,
            maximum_duration_s=1.0,
        )
    with pytest.raises(ValueError, match="duration"):
        script.validate_profile(
            profile,
            minimum_voltage_v=0.0,
            maximum_voltage_v=18.0,
            repeat_count=10,
            maximum_duration_s=0.1,
        )


def test_preview_is_default_and_does_not_connect(script: ModuleType, monkeypatch) -> None:
    monkeypatch.setattr(
        script,
        "_connect",
        lambda _args: (_ for _ in ()).throw(AssertionError("preview must not connect")),
    )
    result = script.main(
        [
            "--maximum-voltage",
            "18",
            "pulse",
            "--baseline",
            "12",
            "--level",
            "6",
            "--width",
            "0.02",
        ]
    )
    assert result == 0


def test_run_profile_executes_and_cleans_up_against_simulator(script: ModuleType) -> None:
    args = script.parse_args(
        [
            "--maximum-voltage",
            "18",
            "--current-limit",
            "1",
            "--yes",
            "--execute",
            "pulse",
            "--baseline",
            "12",
            "--level",
            "6",
            "--width",
            "0.02",
        ]
    )
    profile = script.build_profile(args)
    driver = N6700.connect_simulated()
    try:
        outcome = script.run_profile(driver, profile, args, confirm=lambda _prompt: True)
        assert outcome["status"] == "complete"
        assert outcome["shape"] == "pulse"
        history = driver._session(None).transport.history
        assert "ARB:FUNC:SHAP PULS,(@1)" in history
        assert not any(command.startswith("ARB:VOLT:UDEF:LEV ") for command in history)
        assert driver.get_output_state(1) is False
    finally:
        driver.disconnect()


def test_run_profile_refuses_mainframe_with_active_channel(script: ModuleType) -> None:
    args = script.parse_args(
        [
            "--maximum-voltage",
            "18",
            "--current-limit",
            "1",
            "--execute",
            "pulse",
            "--baseline",
            "12",
            "--level",
            "6",
            "--width",
            "0.02",
        ]
    )
    profile = script.build_profile(args)
    driver = N6700.connect_simulated()
    driver.enable_output(2)
    try:
        with pytest.raises(ValueError, match="already enabled"):
            script.run_profile(driver, profile, args, confirm=lambda _prompt: True)
    finally:
        driver.disable_output(2)
        driver.disconnect()
