"""Automated, passive, fail-closed N679xA digitizer hardware-in-the-loop tests.

This module accepts an already-connected real driver; the CLI separately
enforces operator approval and refuses simulated transports. BUS-triggered
acquisitions qualify only the protocol, never analog transient metrology.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .acquisition import AcquisitionState
from .exceptions import DriverTimeoutError
from .version import __version__

Scenario = Literal["bus", "abort", "missing_trigger"]
Function = Literal["voltage", "current"]
Transfer = Literal["auto", "ascii", "real32"]
CASE_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
MAX_POINTS_PER_RUN = 5_000_000


@dataclass(frozen=True)
class HilCase:
    name: str
    scenario: Scenario
    function: Function
    points: int
    interval_s: float
    offset_points: int
    transfer_format: Transfer
    repetitions: int
    wait_timeout_s: float


@dataclass(frozen=True)
class HilPlan:
    channel: int
    expected_model: str
    expected_serial: str
    expected_idn: str
    operator: str
    bench_review_id: str
    cases: tuple[HilCase, ...]
    stop_on_failure: bool


def validate_plan(data: Any) -> HilPlan:
    """Reject unapproved plans and unsafe inputs before hardware connection."""
    if not isinstance(data, dict):
        raise ValueError("HIL plan must be a JSON object")
    if data.get("approved") is not True:
        raise ValueError("HIL plan must be explicitly approved")
    channel = data.get("channel")
    if isinstance(channel, bool) or not isinstance(channel, int) or channel not in range(1, 5):
        raise ValueError("channel must be 1..4")
    model = data.get("expected_module")
    if model not in ("N6791A", "N6792A"):
        raise ValueError("expected_module must be reviewed N6791A or N6792A")
    for field in ("expected_serial", "expected_idn", "operator", "bench_review_id"):
        if not isinstance(data.get(field), str) or not data[field].strip():
            raise ValueError(f"reviewed profile requires nonempty {field}")
    for field in ("max_voltage_v", "max_current_a"):
        value = data.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{field} must be finite positive")
    if not isinstance(data.get("cases"), list) or not data["cases"]:
        raise ValueError("at least one HIL scenario is required")
    if len(data["cases"]) > 40:
        raise ValueError("maximum 40 scenario definitions")
    cases: list[HilCase] = []
    names: set[str] = set()
    samples = 0
    for item in data["cases"]:
        if not isinstance(item, dict):
            raise ValueError("case must be an object")
        name = item.get("name")
        if not isinstance(name, str) or CASE_RE.fullmatch(name) is None or name in names:
            raise ValueError("case names must be unique, 1..64 safe characters")
        names.add(name)
        scenario = item.get("scenario", "bus")
        function = item.get("function", "voltage")
        transfer = item.get("transfer_format", "auto")
        if scenario not in ("bus", "abort", "missing_trigger"):
            raise ValueError(f"{name}: unsupported scenario")
        if function not in ("voltage", "current"):
            raise ValueError(f"{name}: unsupported measurement function")
        if transfer not in ("auto", "ascii", "real32"):
            raise ValueError(f"{name}: unsupported waveform format")
        points = item.get("points")
        offset = item.get("offset_points", 0)
        repetitions = item.get("repetitions", 1)
        if isinstance(points, bool) or not isinstance(points, int) or not 2 <= points <= 524288:
            raise ValueError(f"{name}: points must be 2..524288")
        if isinstance(offset, bool) or not isinstance(offset, int) or not -(points - 1) <= offset <= points:
            raise ValueError(f"{name}: offset_points invalid")
        if isinstance(repetitions, bool) or not isinstance(repetitions, int) or not 1 <= repetitions <= 200:
            raise ValueError(f"{name}: repetitions must be 1..200")
        interval = item.get("interval_s")
        if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not math.isfinite(interval) or not 10.24e-6 <= interval <= 1:
            raise ValueError(f"{name}: interval_s must be 10.24us..1s")
        timeout = item.get("wait_timeout_s", 5.0)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0.1 <= timeout <= 120:
            raise ValueError(f"{name}: wait_timeout_s must be 0.1..120s")
        if scenario == "missing_trigger" and timeout > 3:
            raise ValueError(f"{name}: timeout case limited to 3s")
        if offset < 0 and abs(offset) * interval + 0.005 >= timeout:
            raise ValueError(f"{name}: insufficient time for pretrigger fill")
        samples += points * repetitions if scenario == "bus" else 0
        cases.append(HilCase(name, scenario, function, points, float(interval), offset,
                             transfer, repetitions, float(timeout)))
    if samples > MAX_POINTS_PER_RUN:
        raise ValueError(f"waveform budget exceeds {MAX_POINTS_PER_RUN} points")
    stop = data.get("stop_on_failure", True)
    if not isinstance(stop, bool):
        raise ValueError("stop_on_failure must be boolean")
    return HilPlan(channel, model, data["expected_serial"], data["expected_idn"],
                   data["operator"], data["bench_review_id"], tuple(cases), stop)


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _write_waveform(path: Path, values: tuple[float, ...], dt: float, offset: int) -> str:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.writer(fp)
        writer.writerow(("time_s", "value"))
        for index, value in enumerate(values):
            writer.writerow(((index + offset) * dt, value))
    temporary.replace(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_state(driver: Any, channel: int) -> bool:
    """Do not reset hardware; switch off selected load then verify readback."""
    load = driver.load(channel)
    load.input_off()
    return load.get_input() is False


def _test_case(driver: Any, load: Any, case: HilCase, output: Path, repeat: int) -> dict[str, Any]:
    start = time.monotonic()
    result: dict[str, Any] = {
        "name": case.name, "scenario": case.scenario, "function": case.function,
        "repetition": repeat, "status": "FAIL", "reason": "not completed",
    }
    try:
        load.configure_digitize(case.function, points=case.points,
                                interval_s=case.interval_s, offset_points=case.offset_points)
        load.configure_digitize_trigger("bus")
        load.arm_digitize()
        load.wait_digitize_armed(timeout_s=case.wait_timeout_s)
        if case.scenario == "abort":
            load.abort_digitize()
            if load.digitize_status.state is not AcquisitionState.ABORTED:
                raise AssertionError("abort not acknowledged")
        elif case.scenario == "missing_trigger":
            try:
                load.wait_digitize_complete(timeout_s=case.wait_timeout_s)
            except DriverTimeoutError as exc:
                if load.digitize_status.state is not AcquisitionState.ABORTED:
                    raise AssertionError("missing trigger did not cleanly abort") from exc
            else:
                raise AssertionError("untriggered acquisition unexpectedly completed")
        else:
            load.trigger_digitize()
            load.wait_digitize_complete(timeout_s=case.wait_timeout_s)
            waveform = load.fetch_digitized_array(transfer_format=case.transfer_format)
            if len(waveform.values) != case.points or waveform.unit != ("V" if case.function == "voltage" else "A"):
                raise AssertionError("waveform size or unit mismatch")
            if not all(math.isfinite(x) for x in waveform.values):
                raise AssertionError("nonfinite samples")
            if waveform.offset_points != case.offset_points or waveform.interval_s is None or waveform.interval_s <= 0:
                raise AssertionError("timebase/offset mismatch")
            expected_format = (
                "real" if case.transfer_format == "real32" or (
                    case.transfer_format == "auto" and case.points >= 4096
                ) else "ascii"
            )
            if waveform.format != expected_format:
                raise AssertionError("transfer format mismatch")
            output.mkdir(parents=True, exist_ok=False)
            path = output / "digitizer.csv"
            digest = _write_waveform(path, waveform.values, waveform.interval_s,
                                     waveform.offset_points)
            result["waveform"] = {
                "path": str(path), "sha256": digest, "raw_sha256": waveform.raw_sha256,
                "format": waveform.format, "points": len(waveform.values),
                "unit": waveform.unit, "effective_interval_s": waveform.interval_s,
                "requested_interval_s": case.interval_s,
                "offset_points": waveform.offset_points,
                "minimum": min(waveform.values), "maximum": max(waveform.values),
            }
        driver.check_errors()
        result["status"] = "PASS"
        result["reason"] = "protocol lifecycle and readbacks valid"
    except Exception as exc:
        result["reason"] = f"{type(exc).__name__}: {exc}"
    finally:
        if load.digitize_status.state not in (AcquisitionState.FETCHED, AcquisitionState.ABORTED):
            try:
                load.abort_digitize()
            except Exception as exc:
                result["recovery_error"] = f"{type(exc).__name__}: {exc}"
        try:
            result["output_off_verified"] = _safe_state(driver, load.channel)
        except Exception as exc:
            result["output_off_verified"] = False
            result["safety_error"] = f"{type(exc).__name__}: {exc}"
        result["generation"] = load.digitize_status.generation
        result["final_acquisition_state"] = load.digitize_status.state.value
        result["duration_s"] = round(time.monotonic() - start, 6)
        if not result["output_off_verified"] or result.get("recovery_error"):
            result["status"] = "FAIL"
            result["reason"] += "; output/acquisition safe state unverified"
    return result


def _junit(path: Path, cases: list[dict[str, Any]], gate_error: str | None) -> None:
    failures = sum(case["status"] != "PASS" for case in cases) + int(gate_error is not None)
    root = ET.Element("testsuite", name="N679xA_HIL",
                      tests=str(len(cases) + int(gate_error is not None)),
                      failures=str(failures))
    for case in cases:
        node = ET.SubElement(root, "testcase",
                             name=f"{case['name']}-{case['repetition']}",
                             classname="scope_hil", time=str(case["duration_s"]))
        if case["status"] != "PASS":
            ET.SubElement(node, "failure", message=str(case["reason"]))
    if gate_error is not None:
        gate = ET.SubElement(root, "testcase", name="preflight_and_safe_shutdown",
                             classname="scope_hil", time="0")
        ET.SubElement(gate, "failure", message=gate_error)
    temporary = path.with_name(path.name + ".tmp")
    ET.ElementTree(root).write(temporary, encoding="utf-8", xml_declaration=True)
    temporary.replace(path)


def run_hil_plan(driver: Any, plan: HilPlan, output: Path) -> dict[str, Any]:
    """Execute a passive HIL matrix and persist an evidence report after each case."""
    output.mkdir(parents=True, exist_ok=False)
    report: dict[str, Any] = {
        "schema_version": "1.0", "driver_version": __version__,
        "operator": plan.operator, "bench_review_id": plan.bench_review_id,
        "channel": plan.channel, "cases": [], "result": "FAIL",
        "qualification": "PROTOCOL_ONLY_NOT_METROLOGY",
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    def save() -> None:
        report["completed_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _atomic_json(output / "summary.json", report)
        gate_error = report.get("error") or (
            "final output OFF state unverified"
            if report.get("final_output_off_verified") is False else None
        )
        _junit(output / "junit.xml", report["cases"], gate_error)

    identity_verified = False
    try:
        identity = driver.get_identity()
        model = driver.channel_model(plan.channel)
        serial = driver.channel_serial(plan.channel)
        report["instrument"] = {"idn": identity, "module": model, "serial": serial}
        if plan.expected_idn not in identity or model != plan.expected_model or serial != plan.expected_serial:
            raise RuntimeError("instrument identity/model/serial differs from approved bench profile")
        identity_verified = True
        if not _safe_state(driver, plan.channel):
            raise RuntimeError("selected load input cannot be verified OFF")
        # Refuse to change or test anything while an unrelated output is ON.
        for number in range(1, driver.channel_count() + 1):
            if number != plan.channel:
                response = driver.query_scpi(f"OUTP? (@{number})").strip()
                if response not in ("0", "+0"):
                    raise RuntimeError(f"unreviewed output {number} is ON or unreadable")
        load = driver.load(plan.channel)
        for case in plan.cases:
            for repetition in range(1, case.repetitions + 1):
                entry = _test_case(driver, load, case,
                                   output / f"{case.name}-{repetition:03d}", repetition)
                report["cases"].append(entry)
                save()
                if entry["status"] != "PASS" and (
                    plan.stop_on_failure or not entry["output_off_verified"]
                    or bool(entry.get("recovery_error"))
                ):
                    raise RuntimeError(f"stopped after {case.name}/{repetition}: {entry['reason']}")
        report["result"] = "PASS" if all(x["status"] == "PASS" for x in report["cases"]) else "FAIL"
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if identity_verified:
            try:
                report["final_output_off_verified"] = _safe_state(driver, plan.channel)
            except Exception as exc:
                report["final_output_off_verified"] = False
                report["final_cleanup_error"] = f"{type(exc).__name__}: {exc}"
        else:
            # Crucial: never send an OFF command to an unidentified or
            # unexpected instrument. Its output wiring may be unrelated.
            report["final_output_off_verified"] = False
            report["final_cleanup_error"] = "identity mismatch; no output command issued"
        if not report["final_output_off_verified"]:
            report["result"] = "FAIL"
        save()
    return report
