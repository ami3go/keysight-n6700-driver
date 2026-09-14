"""100-point real-hardware sweep: a power-supply channel wired into an
electronic-load channel's input, exercising the supply's CV region, its CC
(current-limiting) crossover, and the load's CR and CV priority modes.

Disabled unless ``N6700_HIL_ENABLED=true`` and ``N6700_RESOURCE`` are both
set (see ``conftest.py``), **and** all of:

- ``N6700_HIL_SWEEP_TEST_ENABLED=true``
- ``N6700_HIL_SWEEP_PS_CHANNEL`` — the power-supply/SMU channel number
- ``N6700_HIL_SWEEP_LOAD_CHANNEL`` — the electronic-load channel number
- ``N6700_HIL_SWEEP_CONFIRM=yes`` — attests that the PS channel's output is
  physically wired to the load channel's input, with correct polarity, and
  that both channels are otherwise disconnected from anything else

Four separate explicit signals beyond the base HIL gate, so no combination
of defaults can accidentally energize two channels wired together. Every
setpoint used here (2V-20V, 0.2A-2A) is a fixed, already-reviewed value —
this test does not accept caller-supplied voltage/current, unlike the
single-channel guarded output test in ``test_hardware_acceptance.py``,
because the safe envelope depends on both channels' ratings interacting,
not just one.

A hard 20V/2A safety envelope, independent of any per-phase expectation,
aborts and shuts down immediately if ever approached — it was never reached
in the run this test is based on. See ``docs/hardware_acceptance_tests.md``
and ``review/known_risks.md`` for the real-hardware results this codifies.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

from keysight_n6700 import N6700, DriverError

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS_DIR = REPO_ROOT / "results" / "hardware_load_sweep"

ENVELOPE_MAX_VOLTAGE = 20.0
ENVELOPE_MAX_CURRENT = 2.0
ENVELOPE_MARGIN = 1.15  # hard-abort threshold = envelope * margin

SETTLE_S = 0.3
POINTS_PER_PHASE = 25


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Point:
    phase: str
    index: int
    params: dict[str, float]
    ps_v: float
    ps_i: float
    load_v: float
    load_i: float


class EnvelopeViolation(RuntimeError):
    """A measurement exceeded the hard safety cap; independent of any phase's expectation."""


class Sweep:
    def __init__(self, drv: N6700, ps_channel: int, load_channel: int) -> None:
        self.drv = drv
        self.ps_channel = ps_channel
        self.load_channel = load_channel
        self.load = drv.load(load_channel)
        self.points: list[Point] = []

    def _check_envelope(self, ps_v: float, ps_i: float, load_v: float, load_i: float) -> None:
        v_limit = ENVELOPE_MAX_VOLTAGE * ENVELOPE_MARGIN
        i_limit = ENVELOPE_MAX_CURRENT * ENVELOPE_MARGIN
        for label, v, i in (("ps", ps_v, ps_i), ("load", load_v, load_i)):
            if abs(v) > v_limit:
                raise EnvelopeViolation(f"{label} voltage {v:.3f}V exceeds hard cap {v_limit:.3f}V")
            if abs(i) > i_limit:
                raise EnvelopeViolation(f"{label} current {i:.3f}A exceeds hard cap {i_limit:.3f}A")

    def _measure(self, phase: str, index: int, params: dict[str, float]) -> Point:
        time.sleep(SETTLE_S)
        ps_v = self.drv.measure_dc_voltage(self.ps_channel)
        ps_i = self.drv.measure_dc_current(self.ps_channel)
        load_v = self.drv.measure_dc_voltage(self.load_channel)
        load_i = self.drv.measure_dc_current(self.load_channel)
        self._check_envelope(ps_v, ps_i, load_v, load_i)
        self.drv.check_errors()
        point = Point(phase, index, params, ps_v, ps_i, load_v, load_i)
        self.points.append(point)
        return point

    @staticmethod
    def _linspace(lo: float, hi: float, n: int) -> list[float]:
        if n == 1:
            return [lo]
        step = (hi - lo) / (n - 1)
        return [lo + step * i for i in range(n)]

    def phase1_ps_cv_region(self) -> None:
        fixed_current = 0.2
        ps_current_limit = 1.0
        voltages = self._linspace(2.0, 20.0, POINTS_PER_PHASE)

        self.drv.set_dc_voltage(voltages[0], self.ps_channel)
        self.drv.set_dc_current(ps_current_limit, self.ps_channel)
        self.load.set_load_mode("cc")
        self.load.set_load_current(fixed_current)
        self.drv.check_errors()
        self.drv.enable_output(self.ps_channel)
        self.drv.check_errors()
        self.load.input_on()
        self.drv.check_errors()

        try:
            for idx, v in enumerate(voltages):
                self.drv.set_dc_voltage(v, self.ps_channel)
                self.drv.check_errors()
                p = self._measure(
                    "phase1_cv", idx, {"ps_voltage_setpoint": v, "load_current": fixed_current}
                )
                assert abs(p.ps_v - v) <= max(0.1, 0.05 * v), f"phase1[{idx}]: ps_v={p.ps_v} != setpoint={v}"
                assert abs(p.load_i - fixed_current) <= max(0.02, 0.08 * fixed_current), (
                    f"phase1[{idx}]: load_i={p.load_i} != fixed_current={fixed_current}"
                )
        finally:
            self.load.input_off()
            self.drv.disable_output(self.ps_channel)
            self.drv.safe_shutdown()

    def phase2_ps_cc_crossover(self) -> None:
        ps_voltage = 20.0
        ps_current_limit = 1.0
        currents = self._linspace(0.2, 1.8, POINTS_PER_PHASE)

        self.drv.set_dc_voltage(ps_voltage, self.ps_channel)
        self.drv.set_dc_current(ps_current_limit, self.ps_channel)
        self.load.set_load_mode("cc")
        self.load.set_load_current(currents[0])
        self.drv.check_errors()
        self.drv.enable_output(self.ps_channel)
        self.drv.check_errors()
        self.load.input_on()
        self.drv.check_errors()

        try:
            for idx, i_set in enumerate(currents):
                self.load.set_load_current(i_set)
                self.drv.check_errors()
                p = self._measure(
                    "phase2_cc_crossover",
                    idx,
                    {"ps_voltage_setpoint": ps_voltage, "ps_current_limit": ps_current_limit, "load_current_setpoint": i_set},
                )
                if i_set < ps_current_limit * 0.95:
                    assert abs(p.ps_v - ps_voltage) <= max(0.2, 0.05 * ps_voltage), (
                        f"phase2[{idx}] (CV region): ps_v={p.ps_v} != {ps_voltage}"
                    )
                    assert abs(p.ps_i - i_set) <= max(0.03, 0.08 * i_set), (
                        f"phase2[{idx}] (CV region): ps_i={p.ps_i} != {i_set}"
                    )
                else:
                    assert p.ps_i <= ps_current_limit * 1.1, (
                        f"phase2[{idx}] (CC region): ps_i={p.ps_i} exceeds limit {ps_current_limit}"
                    )
                    assert p.ps_v <= ps_voltage * 1.02, (
                        f"phase2[{idx}] (CC region): ps_v={p.ps_v} did not drop below {ps_voltage}"
                    )
        finally:
            self.load.input_off()
            self.drv.disable_output(self.ps_channel)
            self.drv.safe_shutdown()

    def phase3_load_cr_mode(self) -> None:
        resistance = 20.0
        ps_current_limit = 1.5
        voltages = self._linspace(2.0, 20.0, POINTS_PER_PHASE)

        self.drv.set_dc_voltage(voltages[0], self.ps_channel)
        self.drv.set_dc_current(ps_current_limit, self.ps_channel)
        self.load.set_load_mode("cr")
        self.load.set_load_resistance(resistance)
        self.drv.check_errors()
        self.drv.enable_output(self.ps_channel)
        self.drv.check_errors()
        self.load.input_on()
        self.drv.check_errors()

        try:
            for idx, v in enumerate(voltages):
                self.drv.set_dc_voltage(v, self.ps_channel)
                self.drv.check_errors()
                p = self._measure("phase3_cr_mode", idx, {"ps_voltage_setpoint": v, "resistance": resistance})
                expected_i = p.load_v / resistance
                assert abs(p.load_i - expected_i) <= max(0.02, 0.1 * expected_i), (
                    f"phase3[{idx}]: load_i={p.load_i} != V/R={expected_i}"
                )
        finally:
            self.load.input_off()
            self.drv.disable_output(self.ps_channel)
            self.drv.safe_shutdown()

    def phase4_load_cv_mode(self) -> None:
        ps_voltage = 15.0
        ps_current_limit = 1.0
        load_current_limit = 1.0
        targets = self._linspace(20.0, 2.0, POINTS_PER_PHASE)

        self.drv.set_dc_voltage(ps_voltage, self.ps_channel)
        self.drv.set_dc_current(ps_current_limit, self.ps_channel)
        self.load.set_load_mode("cv")
        self.load.set_load_voltage(targets[0])
        self.load.set_load_current_limit(load_current_limit)
        self.drv.check_errors()
        self.drv.enable_output(self.ps_channel)
        self.drv.check_errors()
        self.load.input_on()
        self.drv.check_errors()

        try:
            for idx, target in enumerate(targets):
                self.load.set_load_voltage(target)
                self.drv.check_errors()
                p = self._measure(
                    "phase4_cv_mode", idx, {"ps_voltage": ps_voltage, "load_voltage_target": target}
                )
                if target >= ps_voltage:
                    assert p.load_i <= max(0.05, 0.05 * load_current_limit), (
                        f"phase4[{idx}]: load drawing current ({p.load_i}A) while target above bus"
                    )
                else:
                    assert p.load_i <= load_current_limit * 1.1, (
                        f"phase4[{idx}]: load_i={p.load_i} exceeded CURR:LIM={load_current_limit}"
                    )
        finally:
            self.load.input_off()
            self.drv.disable_output(self.ps_channel)
            self.drv.safe_shutdown()


def test_ps_to_load_cv_cc_cr_sweep(hardware_driver: N6700) -> None:
    if not _truthy(os.environ.get("N6700_HIL_SWEEP_TEST_ENABLED")):
        pytest.skip("set N6700_HIL_SWEEP_TEST_ENABLED=true to run the PS-to-load sweep test")

    ps_channel_raw = os.environ.get("N6700_HIL_SWEEP_PS_CHANNEL")
    load_channel_raw = os.environ.get("N6700_HIL_SWEEP_LOAD_CHANNEL")
    if not (ps_channel_raw and load_channel_raw):
        pytest.fail(
            "N6700_HIL_SWEEP_TEST_ENABLED=true requires N6700_HIL_SWEEP_PS_CHANNEL and "
            "N6700_HIL_SWEEP_LOAD_CHANNEL to both be set"
        )
    if not _truthy(os.environ.get("N6700_HIL_SWEEP_CONFIRM")):
        pytest.fail(
            "set N6700_HIL_SWEEP_CONFIRM=yes to confirm the PS channel's output is physically "
            "wired to the load channel's input, with correct polarity"
        )

    ps_channel = int(ps_channel_raw)
    load_channel = int(load_channel_raw)

    modules = hardware_driver.discover_modules()
    ps_caps = modules.get(ps_channel)
    load_caps = modules.get(load_channel)
    if ps_caps is None or ps_caps.module_type not in {"power_supply", "smu"}:
        pytest.fail(f"channel {ps_channel} is not a discovered power-supply/SMU channel: {ps_caps}")
    if load_caps is None or load_caps.module_type != "electronic_load" or not load_caps.verified_real_load_commands:
        pytest.fail(f"channel {load_channel} is not a discovered, verified electronic-load channel: {load_caps}")
    if hardware_driver.get_output_state(ps_channel):
        pytest.fail(f"channel {ps_channel} output is already ON; refusing to touch it")
    if hardware_driver.load(load_channel).get_input():
        pytest.fail(f"channel {load_channel} load input is already ON; refusing to touch it")
    hardware_driver.check_errors()

    sweep = Sweep(hardware_driver, ps_channel, load_channel)
    try:
        sweep.phase1_ps_cv_region()
        sweep.phase2_ps_cc_crossover()
        sweep.phase3_load_cr_mode()
        sweep.phase4_load_cv_mode()
    finally:
        with suppress(DriverError):
            hardware_driver.load(load_channel).input_off()
        with suppress(DriverError):
            hardware_driver.disable_output(ps_channel)
        with suppress(DriverError):
            hardware_driver.safe_shutdown()

        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_path = RESULTS_DIR / f"{timestamp}.json"
        out_path.write_text(json.dumps([asdict(p) for p in sweep.points], indent=2))

    assert hardware_driver.get_output_state(ps_channel) is False
    assert hardware_driver.load(load_channel).get_input() is False
    assert len(sweep.points) == 4 * POINTS_PER_PHASE
