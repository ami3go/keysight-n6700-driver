"""Guarded buck-converter HIL: a power-supply channel feeds a DUT buck's VIN
and an N679xA load sinks the buck's VOUT.

Disabled unless ``N6700_HIL_ENABLED=true`` and ``N6700_RESOURCE`` are both set
(see ``conftest.py``), **and** ``N6700_HIL_BUCK_ENABLED=true`` with every DUT
rating below given explicitly -- there are no defaults for anything that
energizes the DUT:

- ``N6700_HIL_BUCK_PS_CHANNEL`` / ``N6700_HIL_BUCK_LOAD_CHANNEL``
- ``N6700_HIL_BUCK_VIN_V``, ``N6700_HIL_BUCK_VIN_MIN_V``, ``N6700_HIL_BUCK_VIN_MAX_V``
- ``N6700_HIL_BUCK_INPUT_CURRENT_LIMIT_A`` -- the supply's current limit
- ``N6700_HIL_BUCK_VOUT_V`` -- nominal buck output
- ``N6700_HIL_BUCK_MAX_LOAD_A`` -- largest current the load may sink (<= 2.5 A)
- ``N6700_HIL_BUCK_CONFIRM=RUN_REVIEWED_BUCK`` -- attests the wiring below

Optional: ``N6700_HIL_BUCK_VOUT_TOLERANCE`` (static, default 0.03) and
``N6700_HIL_BUCK_TRANSIENT_TOLERANCE`` (dynamic, default 0.10), both fractions
of ``VOUT_V``.

Phases: start-up with the load off; static load regulation across CC steps;
line regulation at half load across VIN min/nominal/max; then a CC LIST step
(low -> max -> low) captured twice by the load's digitizer -- once as current,
proving the load really sinks the profile, once as VOUT, measuring the
transient deviation. A hard envelope check aborts and shuts down if VIN, VOUT,
or either current leaves the reviewed limits. Writes every reading and both
captures to ``results/hardware_buck_regulation/<timestamp>.json``.

Side effect: none beyond the sweep's own settings; LIST is cleared and both
channels are left OFF.
"""

from __future__ import annotations

import json
import math
import os
import time
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean
from typing import Literal

import pytest

from keysight_n6700 import N6700, DriverError
from keysight_n6700.types import ArrayMeasurement

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS_DIR = REPO_ROOT / "results" / "hardware_buck_regulation"

LOAD_MODELS = {"N6791A", "N6792A"}
DUT_MAX_LOAD_A = 2.5
CONFIRM_PHRASE = "RUN_REVIEWED_BUCK"

SETTLE_S = 0.3
STARTUP_TIMEOUT_S = 3.0
LOAD_STEPS = 5
# LIST dwell per step and the capture covering all three steps.
LIST_DWELL_S = (0.05, 0.1, 0.05)
CAPTURE_POINTS = 1024
CAPTURE_INTERVAL_S = 2.048e-4


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class BuckLimits:
    ps_channel: int
    load_channel: int
    vin_v: float
    vin_min_v: float
    vin_max_v: float
    input_current_limit_a: float
    vout_v: float
    max_load_a: float
    vout_tolerance: float
    transient_tolerance: float


@dataclass
class Point:
    phase: str
    vin_setpoint: float
    load_setpoint: float
    vin: float
    iin: float
    vout: float
    iout: float

    @property
    def efficiency(self) -> float | None:
        p_in = self.vin * self.iin
        return (self.vout * self.iout) / p_in if p_in > 0.05 else None


class EnvelopeViolation(RuntimeError):
    """A measurement left the reviewed DUT envelope; independent of any phase's expectation."""


def _limits_from_env() -> BuckLimits:
    names = (
        "N6700_HIL_BUCK_PS_CHANNEL",
        "N6700_HIL_BUCK_LOAD_CHANNEL",
        "N6700_HIL_BUCK_VIN_V",
        "N6700_HIL_BUCK_VIN_MIN_V",
        "N6700_HIL_BUCK_VIN_MAX_V",
        "N6700_HIL_BUCK_INPUT_CURRENT_LIMIT_A",
        "N6700_HIL_BUCK_VOUT_V",
        "N6700_HIL_BUCK_MAX_LOAD_A",
    )
    raw = {name: os.environ.get(name) for name in names}
    missing = [name for name, value in raw.items() if not value]
    if missing:
        pytest.fail(f"missing required buck HIL variables: {missing}")
    if os.environ.get("N6700_HIL_BUCK_CONFIRM") != CONFIRM_PHRASE:
        pytest.fail(
            f"set N6700_HIL_BUCK_CONFIRM={CONFIRM_PHRASE} to confirm the PS channel feeds the "
            "buck VIN and the buck VOUT feeds the load input, correct polarity, nothing else attached"
        )
    limits = BuckLimits(
        ps_channel=int(raw["N6700_HIL_BUCK_PS_CHANNEL"] or ""),
        load_channel=int(raw["N6700_HIL_BUCK_LOAD_CHANNEL"] or ""),
        vin_v=float(raw["N6700_HIL_BUCK_VIN_V"] or ""),
        vin_min_v=float(raw["N6700_HIL_BUCK_VIN_MIN_V"] or ""),
        vin_max_v=float(raw["N6700_HIL_BUCK_VIN_MAX_V"] or ""),
        input_current_limit_a=float(raw["N6700_HIL_BUCK_INPUT_CURRENT_LIMIT_A"] or ""),
        vout_v=float(raw["N6700_HIL_BUCK_VOUT_V"] or ""),
        max_load_a=float(raw["N6700_HIL_BUCK_MAX_LOAD_A"] or ""),
        vout_tolerance=float(os.environ.get("N6700_HIL_BUCK_VOUT_TOLERANCE", "0.03")),
        transient_tolerance=float(os.environ.get("N6700_HIL_BUCK_TRANSIENT_TOLERANCE", "0.10")),
    )
    values = [v for v in asdict(limits).values() if isinstance(v, float)]
    if not all(math.isfinite(v) and v > 0 for v in values):
        pytest.fail(f"buck HIL limits must be finite and positive: {limits}")
    if not limits.vin_min_v <= limits.vin_v <= limits.vin_max_v:
        pytest.fail("N6700_HIL_BUCK_VIN_V must lie within VIN_MIN_V..VIN_MAX_V")
    if limits.vout_v >= limits.vin_min_v:
        pytest.fail("a buck's VOUT_V must be below VIN_MIN_V")
    if limits.max_load_a > DUT_MAX_LOAD_A:
        pytest.fail(f"N6700_HIL_BUCK_MAX_LOAD_A is capped at {DUT_MAX_LOAD_A} A")
    if not (limits.vout_tolerance < 0.2 and limits.transient_tolerance < 0.5):
        pytest.fail("buck tolerances are fractions of VOUT_V; refusing implausibly wide bands")
    return limits


class BuckBench:
    def __init__(self, drv: N6700, limits: BuckLimits) -> None:
        self.drv = drv
        self.lim = limits
        self.load = drv.load(limits.load_channel)
        self.points: list[Point] = []
        self.captures: dict[str, dict[str, object]] = {}

    def _check_envelope(self, vin: float, iin: float, vout: float, iout: float) -> None:
        lim = self.lim
        checks = (
            ("VIN", vin, lim.vin_max_v + 0.5),
            ("input current", iin, lim.input_current_limit_a * 1.05),
            ("VOUT", vout, lim.vout_v * 1.2),
            ("load current", iout, lim.max_load_a * 1.15),
        )
        for label, value, cap in checks:
            if abs(value) > cap:
                raise EnvelopeViolation(f"{label} {value:.4g} exceeds hard cap {cap:.4g}")

    def measure(self, phase: str, vin_setpoint: float, load_setpoint: float) -> Point:
        time.sleep(SETTLE_S)
        ps, ld = self.lim.ps_channel, self.lim.load_channel
        point = Point(
            phase,
            vin_setpoint,
            load_setpoint,
            vin=self.drv.measure_dc_voltage(ps),
            iin=self.drv.measure_dc_current(ps),
            vout=self.drv.measure_dc_voltage(ld),
            iout=self.drv.measure_dc_current(ld),
        )
        self.points.append(point)
        self._check_envelope(point.vin, point.iin, point.vout, point.iout)
        self.drv.check_errors()
        return point

    def assert_regulating(self, point: Point, label: str) -> None:
        band = self.lim.vout_v * self.lim.vout_tolerance
        assert abs(point.vout - self.lim.vout_v) <= band, (
            f"{label}: VOUT {point.vout:.4f} V outside {self.lim.vout_v} V +/- {band:.4f} V"
        )
        if point.load_setpoint > 0:
            assert abs(point.iout - point.load_setpoint) <= max(0.01, 0.03 * point.load_setpoint), (
                f"{label}: load sank {point.iout:.4f} A, commanded {point.load_setpoint:.4f} A"
            )

    def power_up(self) -> None:
        lim = self.lim
        self.load.set_load_mode("cc")
        self.load.set_load_current(0.0)
        self.drv.set_dc_voltage(lim.vin_v, lim.ps_channel)
        self.drv.set_dc_current(lim.input_current_limit_a, lim.ps_channel)
        self.drv.check_errors()
        self.drv.enable_output(lim.ps_channel)
        deadline = time.monotonic() + STARTUP_TIMEOUT_S
        band = lim.vout_v * lim.vout_tolerance
        while True:
            vout = self.drv.measure_dc_voltage(lim.load_channel)
            if abs(vout - lim.vout_v) <= band:
                break
            if vout > lim.vout_v * 1.2:
                raise EnvelopeViolation(f"VOUT {vout:.4g} V overshot during start-up")
            if time.monotonic() > deadline:
                pytest.fail(f"buck VOUT did not reach {lim.vout_v} V within {STARTUP_TIMEOUT_S} s (last {vout:.4f} V)")
            time.sleep(0.05)
        self.assert_regulating(self.measure("startup_no_load", lim.vin_v, 0.0), "startup")

    def load_regulation(self) -> None:
        lim = self.lim
        self.load.input_on()
        self.drv.check_errors()
        for idx in range(LOAD_STEPS + 1):
            setpoint = lim.max_load_a * idx / LOAD_STEPS
            self.load.set_load_current(setpoint)
            point = self.measure("load_regulation", lim.vin_v, setpoint)
            self.assert_regulating(point, f"load_regulation[{setpoint:.3f} A]")
            if setpoint >= 0.25 * lim.max_load_a and point.efficiency is not None:
                assert 0.5 < point.efficiency <= 1.0, (
                    f"load_regulation[{setpoint:.3f} A]: implausible efficiency {point.efficiency:.3f}"
                )

    def line_regulation(self) -> None:
        lim = self.lim
        setpoint = lim.max_load_a / 2
        self.load.set_load_current(setpoint)
        for vin in (lim.vin_min_v, lim.vin_v, lim.vin_max_v, lim.vin_v):
            self.drv.set_dc_voltage(vin, lim.ps_channel)
            self.drv.check_errors()
            point = self.measure("line_regulation", vin, setpoint)
            assert abs(point.vin - vin) <= max(0.1, 0.01 * vin), f"line_regulation: VIN {point.vin:.3f} != {vin}"
            self.assert_regulating(point, f"line_regulation[{vin:g} V]")

    def _capture_list_step(self, function: Literal["voltage", "current"]) -> ArrayMeasurement:
        lim = self.lim
        low = lim.max_load_a / 10
        self.load.input_off()
        self.load.set_load_mode("cc")
        self.load.set_load_current(low)
        self.drv.configure_load_list(
            lim.load_channel, "cc", [low, lim.max_load_a, low], list(LIST_DWELL_S),
            maximum_level=lim.max_load_a, maximum_duration_s=1.0,
            begin_step_triggers=[True, False, False],
        )
        self.drv.configure_load_transient_trigger(lim.load_channel, "bus")
        self.load.input_on()
        time.sleep(SETTLE_S)
        self.load.configure_digitize(function, points=CAPTURE_POINTS, interval_s=CAPTURE_INTERVAL_S)
        self.load.configure_digitize_trigger("transient")
        self.load.arm_digitize()
        self.load.wait_digitize_armed(timeout_s=5.0)
        token = self.drv.arm_load_transient(lim.load_channel)
        assert self.drv.wait_load_transient_ready(lim.load_channel, token, timeout_s=5.0).waiting_for_trigger
        self.drv.trigger_load_transient(lim.load_channel, token)
        assert self.drv.wait_load_transient_complete(lim.load_channel, token, timeout_s=5.0).state == "complete"
        self.load.wait_digitize_complete(timeout_s=5.0)
        # Fetch before any MEASure: a later measurement overwrites the buffer.
        capture = self.load.fetch_digitized_array(function)
        assert len(capture.values) == CAPTURE_POINTS
        self.captures[function] = {
            "interval_s": capture.interval_s,
            "offset_points": capture.offset_points,
            "unit": capture.unit,
            "values": list(capture.values),
        }
        return capture

    @staticmethod
    def _segment(capture: ArrayMeasurement, start_s: float, end_s: float) -> list[float]:
        interval = capture.interval_s or CAPTURE_INTERVAL_S
        lo, hi = int(start_s / interval), int(end_s / interval)
        return list(capture.values[lo:hi])

    def dynamic_list_step(self) -> None:
        lim = self.lim
        low = lim.max_load_a / 10
        t0, t1, t2 = LIST_DWELL_S
        # Middle 60 % of each dwell, clear of the edges.
        windows = (
            (0.2 * t0, 0.8 * t0, low),
            (t0 + 0.2 * t1, t0 + 0.8 * t1, lim.max_load_a),
            (t0 + t1 + 0.2 * t2, t0 + t1 + 0.8 * t2, low),
        )

        current = self._capture_list_step("current")
        for start, end, expected in windows:
            segment = self._segment(current, start, end)
            assert segment, f"capture too short for window {start:.3f}..{end:.3f} s"
            mean = fmean(segment)
            assert abs(mean - expected) <= max(0.01, 0.03 * expected), (
                f"LIST step at {start:.3f} s sank {mean:.4f} A, commanded {expected:.4f} A"
            )

        voltage = self._capture_list_step("voltage")
        band = lim.vout_v * lim.transient_tolerance
        worst = max(voltage.values, key=lambda v: abs(v - lim.vout_v))
        assert abs(worst - lim.vout_v) <= band, (
            f"VOUT transient {worst:.4f} V outside {lim.vout_v} V +/- {band:.4f} V"
        )
        for start, end, _ in windows:
            settled = fmean(self._segment(voltage, start, end))
            assert abs(settled - lim.vout_v) <= lim.vout_v * lim.vout_tolerance, (
                f"VOUT settled at {settled:.4f} V in window {start:.3f}..{end:.3f} s"
            )
        self.captures["voltage"]["worst_deviation_v"] = worst - lim.vout_v

    def shutdown(self) -> None:
        # Load first, so the buck is never back-driven or left loaded unpowered.
        with suppress(DriverError):
            self.load.input_off()
        with suppress(DriverError):
            self.drv.clear_load_profile(self.lim.load_channel)
        with suppress(DriverError):
            self.load.set_load_mode("cc")
        with suppress(DriverError):
            self.drv.disable_output(self.lim.ps_channel)
        with suppress(DriverError):
            self.drv.safe_shutdown()


@pytest.mark.hardware
def test_guarded_buck_regulation(hardware_driver: N6700) -> None:
    if not _truthy(os.environ.get("N6700_HIL_BUCK_ENABLED")):
        pytest.skip("set N6700_HIL_BUCK_ENABLED=true to run the guarded buck HIL")
    lim = _limits_from_env()

    modules = hardware_driver.discover_modules()
    ps_caps = modules.get(lim.ps_channel)
    load_caps = modules.get(lim.load_channel)
    if ps_caps is None or ps_caps.module_type not in {"power_supply", "smu"}:
        pytest.fail(f"channel {lim.ps_channel} is not a discovered power-supply/SMU channel: {ps_caps}")
    if load_caps is None or load_caps.model not in LOAD_MODELS or not load_caps.supports_list_mode:
        pytest.fail(f"channel {lim.load_channel} is not an N679xA load with LIST support: {load_caps}")
    if hardware_driver.get_output_state(lim.ps_channel):
        pytest.fail(f"channel {lim.ps_channel} output is already ON; refusing to touch it")
    load = hardware_driver.load(lim.load_channel)
    if load.get_input():
        pytest.fail(f"channel {lim.load_channel} load input is already ON; refusing to touch it")
    idle_vout = load.measure_voltage()
    if abs(idle_vout) > 0.5:
        pytest.fail(f"buck VOUT reads {idle_vout:g} V with the supply OFF; something else is powering it")
    hardware_driver.check_errors()

    bench = BuckBench(hardware_driver, lim)
    try:
        bench.power_up()
        bench.load_regulation()
        bench.line_regulation()
        bench.dynamic_list_step()
        assert load.get_protection_status().active is False
        assert hardware_driver.power_supply(lim.ps_channel).get_protection_status().active is False
        hardware_driver.check_errors()
    finally:
        bench.shutdown()
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        report = {
            "limits": asdict(lim),
            "points": [asdict(p) | {"efficiency": p.efficiency} for p in bench.points],
            "captures": bench.captures,
        }
        (RESULTS_DIR / f"{timestamp}.json").write_text(json.dumps(report, indent=2))

    assert hardware_driver.get_output_state(lim.ps_channel) is False
    assert load.get_input() is False
