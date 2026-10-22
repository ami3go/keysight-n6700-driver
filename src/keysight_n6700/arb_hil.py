"""Guarded source Arb hardware-in-the-loop runner and evidence contract (ARB-R06).

Three layers, each fail-closed and testable without hardware:

1. :func:`validate_arb_plan` -- pure checks of a reviewed, approved plan: approval
   record bound to the plan's SHA-256, envelope, fixture, calibration, and every
   case.  Raises :class:`HilRefused` listing *all* reasons before any I/O.
2. :func:`check_environment` / :func:`energize_blockers` -- the per-run opt-in and
   the separate per-case energization acknowledgement.
3. :func:`run_arb_hil` -- runs an already-connected driver through the cases with a
   watchdog independent of the test body, atomic evidence writes, and a safe-state
   check that decides the verdict.

A dry run (the default in the CLI) executes against the simulator and can only
ever produce ``NOT_RUN``: simulator data is not hardware evidence.  A case that
was not run is ``NOT_RUN``, never ``PASS``.  A passing protocol case is never a
metrology claim: ``analog_metrology`` cases stay ``INCONCLUSIVE`` until an
independent calibrated DSO result is attached (ARB-R07).

The approval is bound to the plan *content* (SHA-256) and to the exact instrument,
module, options and firmware; it has no expiry.  Any edit to the plan or any change
of the identified hardware invalidates it.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal

from .exceptions import DriverError
from .version import __version__
from .waveforms import (
    ArbExponential,
    ArbPulse,
    ArbRamp,
    ArbSine,
    ArbStaircase,
    ArbStep,
    ArbTrapezoid,
)

SCHEMA_VERSION = "1.0"
Verdict = Literal["PASS", "FAIL", "INCONCLUSIVE", "NOT_RUN"]
VERDICTS = ("PASS", "FAIL", "INCONCLUSIVE", "NOT_RUN")
LEVELS = ("software_dry_run", "protocol_nonenergizing", "protocol_energized", "analog_metrology")
QUALIFICATION_LABEL = "PROTOCOL_ONLY_NOT_METROLOGY"

ENV_ENABLED = "N6700_HIL_ENABLED"
ENV_APPROVED = "N6700_ARB_HIL_APPROVED"
ENV_APPROVED_VALUE = "YES_AND_REVIEWED"
ENV_ENERGIZE_CASES = "N6700_ARB_HIL_ENERGIZE_CASES"

CASE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_CASES = 40
MAX_WATCHDOG_S = 120.0
# Time reserved inside a case watchdog for setup, arming and cleanup; the supervised run
# gets the rest, so its own deadline always comes before the watchdog.
SETUP_HEADROOM_S = 2.0
MAX_ENVELOPE_DURATION_S = 60.0
SHAPES: dict[str, Any] = {
    cls.__name__: cls
    for cls in (ArbStep, ArbRamp, ArbStaircase, ArbSine, ArbPulse, ArbTrapezoid, ArbExponential)
}


class HilRefused(RuntimeError):
    """The plan, environment or hardware failed an authorization gate; nothing was energized."""

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons))


# -- plan -------------------------------------------------------------------------------


def plan_sha256(data: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical plan with the approval's own hash field removed."""
    scrubbed = json.loads(json.dumps(data))
    if isinstance(scrubbed.get("approval"), dict):
        scrubbed["approval"].pop("plan_sha256", None)
    canonical = json.dumps(scrubbed, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ArbHilCase:
    case_id: str
    qualification_level: str
    shape_name: str
    shape: Any
    energize: bool
    energize_ack: str | None
    watchdog_s: float
    acceptance: dict[str, Any] | None


@dataclass(frozen=True)
class ArbHilPlan:
    raw: dict[str, Any]
    sha256: str
    channel: int
    module_model: str
    module_serial: str
    mainframe_serial: str
    idn_contains: str
    firmware: str
    options: tuple[str, ...]
    minimum_voltage_v: float
    maximum_voltage_v: float
    baseline_voltage_v: float
    current_limit_a: float
    maximum_duration_s: float
    operator: str
    reviewer: str
    reference: str
    calibration: dict[str, Any] | None
    cases: tuple[ArbHilCase, ...]
    stop_on_failure: bool
    connection: dict[str, Any]


def _obj(parent: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    return value if isinstance(value, dict) else {}


def _get(parent: Mapping[str, Any], key: str) -> Any:
    """``dict.get`` typed as Any: values are validated at runtime, not by the type checker."""
    return parent.get(key)


def _person(value: Any) -> str:
    """Normalised name for the independence check (case and surrounding space ignored)."""
    return " ".join(str(value or "").split()).casefold()


def _number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "REPLACE" not in value.upper()


def validate_arb_plan(data: Any, *, today: date | None = None) -> ArbHilPlan:
    """Validate a reviewed plan without any I/O; raise :class:`HilRefused` with every reason."""
    if not isinstance(data, dict):
        raise HilRefused(["plan must be a JSON object"])
    today = today or datetime.now(timezone.utc).date()
    problems: list[str] = []

    def need(condition: bool, message: str) -> None:
        if not condition:
            problems.append(message)

    need(data.get("schema_version") == SCHEMA_VERSION, f"schema_version must be {SCHEMA_VERSION}")
    need(data.get("approved") is True, "plan is not approved")
    approval = _obj(data, "approval")
    for field in ("reference", "operator", "reviewer"):
        need(_text(approval.get(field)), f"approval.{field} is required")
    need(
        _person(approval.get("operator")) != _person(approval.get("reviewer")),
        "approval reviewer must be a different person from the operator",
    )
    try:
        actual_hash = plan_sha256(data)
    except (TypeError, ValueError):
        actual_hash = ""
        problems.append("plan is not canonical JSON (NaN, Infinity or non-serializable value)")
    else:
        need(
            approval.get("plan_sha256") == actual_hash,
            "approval.plan_sha256 does not match the plan content (edited after approval?)",
        )

    inst = _obj(data, "instrument")
    channel = _get(inst, "channel")
    need(isinstance(channel, int) and not isinstance(channel, bool) and 1 <= channel <= 4, "instrument.channel must be 1..4")
    for field in ("idn_contains", "mainframe_serial", "firmware", "module_model", "module_serial"):
        need(_text(inst.get(field)), f"instrument.{field} is required")
    options = _get(inst, "options")
    need(
        isinstance(options, list) and all(_text(item) for item in options),
        "instrument.options must be a list of option strings (use [] for none)",
    )

    fixture = _obj(data, "fixture")
    need(_text(fixture.get("wiring_revision")), "fixture.wiring_revision is required")
    need(_text(fixture.get("load")), "fixture.load is required")
    need(fixture.get("emergency_stop_verified") is True, "fixture.emergency_stop_verified must be true")

    env = _obj(data, "envelope")
    env_fields = (
        "minimum_voltage_v", "maximum_voltage_v", "baseline_voltage_v", "current_limit_a", "maximum_duration_s",
    )
    for field in env_fields:
        need(_number(env.get(field)), f"envelope.{field} must be a finite number")
    if all(_number(env.get(field)) for field in env_fields):
        need(env["minimum_voltage_v"] < env["maximum_voltage_v"], "envelope minimum must be below maximum")
        need(
            env["minimum_voltage_v"] <= env["baseline_voltage_v"] <= env["maximum_voltage_v"],
            "envelope.baseline_voltage_v must lie within the voltage envelope",
        )
        need(env["current_limit_a"] > 0, "envelope.current_limit_a must be positive")
        need(
            0 < env["maximum_duration_s"] <= MAX_ENVELOPE_DURATION_S,
            f"envelope.maximum_duration_s must be within (0, {MAX_ENVELOPE_DURATION_S:g}] s",
        )

    calibration = data.get("calibration")
    cal_ok = False
    if calibration is not None:
        cal_ok = _calibration_problems(calibration, today, problems)

    connection = _obj(data, "connection")
    need(connection.get("type") in ("ethernet", "visa"), "connection.type must be ethernet or visa (no simulator)")
    need(_text(connection.get("resource")), "connection.resource is required")
    port = connection.get("port", 5025)
    need(isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535, "connection.port must be 1..65535")
    timeout = connection.get("timeout_s", 15.0)
    need(_number(timeout) and 1 <= timeout <= 120, "connection.timeout_s must be 1..120")

    cases = data.get("cases")
    parsed: list[ArbHilCase] = []
    if not isinstance(cases, list) or not cases:
        problems.append("at least one case is required")
    elif len(cases) > MAX_CASES:
        problems.append(f"at most {MAX_CASES} cases")
    else:
        seen: set[str] = set()
        for index, item in enumerate(cases):
            case = _parse_case(item, index, env, cal_ok, seen, problems)
            if case is not None:
                parsed.append(case)

    if problems:
        raise HilRefused(problems)
    return ArbHilPlan(
        raw=data,
        sha256=actual_hash,
        channel=channel,
        module_model=inst["module_model"],
        module_serial=inst["module_serial"],
        mainframe_serial=inst["mainframe_serial"],
        idn_contains=inst["idn_contains"],
        firmware=inst["firmware"],
        options=tuple(sorted(options)),
        minimum_voltage_v=float(env["minimum_voltage_v"]),
        maximum_voltage_v=float(env["maximum_voltage_v"]),
        baseline_voltage_v=float(env["baseline_voltage_v"]),
        current_limit_a=float(env["current_limit_a"]),
        maximum_duration_s=float(env["maximum_duration_s"]),
        operator=approval["operator"],
        reviewer=approval["reviewer"],
        reference=approval["reference"],
        calibration=calibration if cal_ok else None,
        cases=tuple(parsed),
        stop_on_failure=data.get("stop_on_failure", True) is not False,
        connection=dict(connection),
    )


def _calibration_problems(calibration: Any, today: date, problems: list[str]) -> bool:
    before = len(problems)
    if not isinstance(calibration, dict):
        problems.append("calibration must be an object or null")
        return False
    for field in ("dso_model", "calibration_record", "calibration_record_sha256", "uncertainty"):
        if not _text(calibration.get(field)):
            problems.append(f"calibration.{field} is required")
    for field in ("bandwidth_hz", "probe_attenuation"):
        value = _get(calibration, field)
        if not _number(value) or value <= 0:
            problems.append(f"calibration.{field} must be a positive number")
    try:
        due = date.fromisoformat(str(calibration.get("calibration_due")))
        if due < today:
            problems.append(f"calibration expired on {due.isoformat()}")
    except ValueError:
        problems.append("calibration.calibration_due must be an ISO date")
    record = _get(calibration, "calibration_record")
    digest = _get(calibration, "calibration_record_sha256")
    if _text(record) and _text(digest):
        path = Path(record)
        if not path.is_file():
            problems.append(f"calibration record not found: {record}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            problems.append("calibration record SHA-256 does not match the file")
    return len(problems) == before


def _parse_case(
    item: Any, index: int, env: dict[str, Any], cal_ok: bool, seen: set[str], problems: list[str]
) -> ArbHilCase | None:
    where = f"cases[{index}]"
    if not isinstance(item, dict):
        problems.append(f"{where} must be an object")
        return None
    case_id = _get(item, "case_id")
    if not isinstance(case_id, str) or not CASE_RE.fullmatch(case_id) or case_id in seen:
        problems.append(f"{where}.case_id must be unique, 1..64 safe characters")
        return None
    seen.add(case_id)
    before = len(problems)
    level = _get(item, "qualification_level")
    energize = _get(item, "energize")
    if level not in LEVELS[1:]:
        problems.append(f"{case_id}: qualification_level must be one of {LEVELS[1:]}")
    if not isinstance(energize, bool):
        problems.append(f"{case_id}: energize must be true or false")
    elif energize != (level in ("protocol_energized", "analog_metrology")):
        problems.append(f"{case_id}: energize must match the level (energized levels only)")
    ack = _get(item, "energize_ack")
    if energize is True and ack != f"ENERGIZE_{case_id}":
        problems.append(f"{case_id}: energize_ack must be exactly ENERGIZE_{case_id}")
    if energize is False and ack is not None:
        problems.append(f"{case_id}: a non-energizing case must not carry energize_ack")
    watchdog = _get(item, "watchdog_s")
    if not _number(watchdog) or not 0 < watchdog <= MAX_WATCHDOG_S:
        problems.append(f"{case_id}: watchdog_s must be within (0, {MAX_WATCHDOG_S:g}] s")
    acceptance = _get(item, "acceptance")
    if level == "analog_metrology":
        if not cal_ok:
            problems.append(f"{case_id}: analog_metrology requires a valid calibration block")
        if not (
            isinstance(acceptance, dict)
            and _text(acceptance.get("vendor_limit_source"))
            and isinstance(acceptance.get("numeric_limits"), dict)
            and acceptance["numeric_limits"]
            and _text(acceptance.get("pass_fail_rule"))
        ):
            problems.append(
                f"{case_id}: analog_metrology requires acceptance limits and rule defined before the run"
            )
    spec = _obj(item, "waveform")
    shape_name = _get(spec, "shape")
    shape: Any = None
    if not isinstance(shape_name, str) or shape_name not in SHAPES:
        problems.append(f"{case_id}: unsupported shape {shape_name!r}")
    else:
        try:
            shape = SHAPES[shape_name](*spec.get("args", []), **spec.get("kwargs", {}))
        except (TypeError, ValueError, ArithmeticError, DriverError) as exc:
            problems.append(f"{case_id}: invalid waveform: {exc}")
    if shape is not None and all(_number(env.get(f)) for f in ("minimum_voltage_v", "maximum_voltage_v", "maximum_duration_s")):
        low, high = shape.level_bounds
        if low < env["minimum_voltage_v"] or high > env["maximum_voltage_v"]:
            problems.append(f"{case_id}: waveform levels {low:g}..{high:g} V exceed the approved envelope")
        if shape.duration_s > env["maximum_duration_s"]:
            problems.append(f"{case_id}: waveform lasts {shape.duration_s:g} s, above the envelope duration")
        if _number(watchdog) and watchdog < shape.duration_s + SETUP_HEADROOM_S:
            problems.append(
                f"{case_id}: watchdog_s must be at least the waveform duration plus {SETUP_HEADROOM_S:g} s"
            )
    if len(problems) != before:
        return None
    return ArbHilCase(
        case_id, level, shape_name, shape, bool(energize), ack, float(watchdog),
        acceptance if isinstance(acceptance, dict) else None,
    )


# -- environment gate -------------------------------------------------------------------


def check_environment(env: Mapping[str, str]) -> None:
    """The per-run opt-in: BOTH signals are required."""
    reasons = []
    if env.get(ENV_ENABLED) != "true":
        reasons.append(f"{ENV_ENABLED}=true is required")
    if env.get(ENV_APPROVED) != ENV_APPROVED_VALUE:
        reasons.append(f"{ENV_APPROVED}={ENV_APPROVED_VALUE} is required")
    if reasons:
        raise HilRefused(["HIL not authorized: " + "; ".join(reasons)])


def energize_blockers(case: ArbHilCase, env: Mapping[str, str]) -> list[str]:
    """Why this case may not energize an output (empty list means it may)."""
    if not case.energize:
        return []
    blockers = []
    if case.energize_ack != f"ENERGIZE_{case.case_id}":
        blockers.append("plan carries no matching energize_ack")
    listed = {item.strip() for item in env.get(ENV_ENERGIZE_CASES, "").split(",") if item.strip()}
    if case.case_id not in listed:
        blockers.append(f"{ENV_ENERGIZE_CASES} does not list {case.case_id}")
    return blockers


# -- evidence ---------------------------------------------------------------------------


def _atomic_write(path: Path, text: str) -> None:
    """Write-temp, flush to disk, rename, then flush the directory entry."""
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    with contextlib.suppress(OSError):  # directory fsync is unavailable on some platforms
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _json_text(data: Mapping[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n"


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _empty_evidence(plan: ArbHilPlan, case: ArbHilCase, dry_run: bool) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case.case_id,
        "qualification_level": "software_dry_run" if dry_run else case.qualification_level,
        "dry_run": dry_run,
        "verdict": "NOT_RUN",
        "reason": "not executed",
        "utc_start": None,
        "utc_end": None,
        "authorization_reference": plan.reference,
        "operator_and_reviewer": [plan.operator, plan.reviewer],
        "plan_sha256": plan.sha256,
        "instrument": {
            "idn": None, "serial": None, "firmware": None, "mainframe": None,
            "module_model": None, "channel": plan.channel, "options": [],
        },
        "software": {"driver_version": __version__},
        "fixture": {
            "wiring_revision": plan.raw["fixture"]["wiring_revision"],
            "load": plan.raw["fixture"]["load"],
            "emergency_stop_verified": plan.raw["fixture"]["emergency_stop_verified"],
        },
        "requested": {
            "waveform_type": case.shape_name,
            "levels": list(case.shape.level_bounds),
            "timebase_s": case.shape.duration_s,
            "repeat": 1,
            "trigger": "bus",
            "approved_envelope": {
                "min_v": plan.minimum_voltage_v, "max_v": plan.maximum_voltage_v,
                "current_limit_a": plan.current_limit_a,
            },
            "energize": case.energize,
        },
        "observed": {
            "scpi_readbacks": None, "error_queue": None, "status_timeline": None,
            "dso_metrics": None, "uncertainty": None,
        },
        "acceptance": case.acceptance
        or {"vendor_limit_source": None, "numeric_limits": None, "pass_fail_rule": "exact readback and clean safe state"},
        "safety": {
            "abort_attempted": False, "output_off_commanded": False,
            "output_off_verified": None, "unknown_state": None, "watchdog_expired": False,
        },
        "artifacts": {"scpi_trace": None, "raw_dso_data": None, "calibration_record": None, "sha256": {}},
    }


def validate_case_evidence(evidence: Any) -> list[str]:
    """Schema and consistency check; an empty list means the record is well formed."""
    if not isinstance(evidence, dict):
        return ["evidence must be an object"]
    problems: list[str] = []
    for key in (
        "schema_version", "case_id", "qualification_level", "verdict", "reason", "instrument",
        "software", "fixture", "requested", "observed", "acceptance", "safety", "artifacts",
    ):
        if key not in evidence:
            problems.append(f"missing {key}")
    if problems:
        return problems
    for key in ("instrument", "software", "fixture", "requested", "observed", "acceptance", "safety", "artifacts"):
        if not isinstance(evidence[key], dict):
            problems.append(f"{key} must be an object")
    if problems:
        return problems
    if evidence["schema_version"] != SCHEMA_VERSION:
        problems.append("unsupported schema_version")
    if evidence["verdict"] not in VERDICTS:
        problems.append(f"verdict must be one of {VERDICTS}")
    if evidence["qualification_level"] not in LEVELS:
        problems.append("unknown qualification_level")
    verdict = evidence["verdict"]
    level = evidence["qualification_level"]
    safety = evidence["safety"]
    observed = evidence["observed"]
    dry_run = evidence.get("dry_run") is True
    if dry_run != (level == "software_dry_run"):
        problems.append("dry_run and the software_dry_run level must go together")
    if dry_run and verdict != "NOT_RUN":
        problems.append("a dry run can only be NOT_RUN")
    if verdict == "PASS":
        if safety.get("unknown_state") is not False:
            problems.append("PASS requires safety.unknown_state == false")
        if safety.get("output_off_verified") is not True:
            problems.append("PASS requires safety.output_off_verified == true")
        if safety.get("watchdog_expired"):
            problems.append("PASS is impossible after a watchdog expiry")
        if observed.get("error_queue") != []:
            problems.append("PASS requires an empty SCPI error queue")
        if not observed.get("scpi_readbacks"):
            problems.append("PASS requires the recorded SCPI readbacks")
        if evidence["requested"].get("energize") and not observed.get("status_timeline"):
            problems.append("an energized PASS requires the recorded status timeline")
        if not evidence.get("utc_start") or not evidence.get("utc_end"):
            problems.append("PASS requires start and end timestamps")
        if not evidence["instrument"].get("serial"):
            problems.append("PASS requires an identified instrument")
        if level == "analog_metrology":
            problems.append("analog_metrology cannot PASS without attached DSO evidence (ARB-R07)")
    if verdict in ("PASS", "FAIL", "INCONCLUSIVE") and not evidence.get("utc_start"):
        problems.append("an executed case must record utc_start")
    return problems


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_evidence_dir(path: Path) -> list[str]:
    """Detect missing, partial, edited, inconsistent or malformed evidence in a run directory."""
    summary_path = path / "summary.json"
    if not summary_path.is_file():
        return ["summary.json is missing (run did not finish writing its report)"]
    try:
        summary = _read_json(summary_path)
    except ValueError:
        return ["summary.json is not valid JSON"]
    if not isinstance(summary, dict) or not isinstance(summary.get("cases"), list):
        return ["summary.json has no case list"]
    problems: list[str] = []

    planned: list[str] = []
    try:
        plan_raw = _read_json(path / "plan.json")
        if plan_sha256(plan_raw) != summary.get("plan_sha256"):
            problems.append("plan.json no longer matches the plan_sha256 recorded in the summary")
        if _obj(plan_raw, "approval").get("plan_sha256") != summary.get("plan_sha256"):
            problems.append("the approval hash in plan.json differs from the summary")
        planned = [str(case.get("case_id")) for case in plan_raw.get("cases", []) if isinstance(case, dict)]
    except (OSError, ValueError, TypeError, AttributeError):
        problems.append("plan.json is missing or unreadable")

    listed: list[str] = []
    verdicts: list[str] = []
    for entry in summary["cases"]:
        if not isinstance(entry, dict):
            problems.append("a summary case entry is not an object")
            continue
        case_id = str(entry.get("case_id"))
        listed.append(case_id)
        if entry.get("file") != f"{case_id}.json" or not CASE_RE.fullmatch(case_id):
            problems.append(f"{case_id}: unexpected file name {entry.get('file')!r}")
            continue
        case_path = path / entry["file"]
        if not case_path.is_file():
            problems.append(f"{entry['file']}: missing")
            continue
        if hashlib.sha256(case_path.read_bytes()).hexdigest() != entry.get("sha256"):
            problems.append(f"{entry['file']}: SHA-256 differs from the summary")
            continue
        try:
            evidence = _read_json(case_path)
        except ValueError:
            problems.append(f"{entry['file']}: not valid JSON")
            continue
        problems += [f"{entry['file']}: {item}" for item in validate_case_evidence(evidence)]
        if isinstance(evidence, dict):
            verdicts.append(str(evidence.get("verdict")))
            if evidence.get("verdict") != entry.get("verdict"):
                problems.append(f"{entry['file']}: verdict differs from the summary")
            if evidence.get("plan_sha256") != summary.get("plan_sha256"):
                problems.append(f"{entry['file']}: belongs to a different plan")

    if planned and listed != planned:
        problems.append("the cases in summary.json differ from the cases in plan.json")
    extra = sorted(
        item.name for item in path.glob("*.json") if item.name not in {"summary.json", "plan.json"}
        and item.stem not in listed
    )
    if extra:
        problems.append(f"case files not listed in the summary: {extra}")
    result = summary.get("result")
    if result == "PASS" and (summary.get("dry_run") or set(verdicts) != {"PASS"} or len(verdicts) != len(listed)):
        problems.append("summary result PASS is not supported by the case verdicts")
    if result == "INCOMPLETE" and "FAIL" in verdicts:
        problems.append("summary result INCOMPLETE hides a failed case")
    return problems


# -- runner -----------------------------------------------------------------------------


class _Watchdog:
    """Fires ``on_expire`` if the guarded body overruns; independent of the body's thread."""

    def __init__(self, seconds: float, on_expire: Callable[[], None], lock: threading.Lock) -> None:
        self.expired = threading.Event()
        self._on_expire = on_expire
        self._lock = lock
        self._timer = threading.Timer(seconds, self._fire)
        self._timer.daemon = True

    def _fire(self) -> None:
        self.expired.set()
        # Serialised with the main thread's own cleanup so the two never race over the
        # output readback or the error queue.
        with self._lock, contextlib.suppress(BaseException):
            self._on_expire()

    def __enter__(self) -> _Watchdog:
        self._timer.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._timer.cancel()


def _make_safe(driver: Any, plan: ArbHilPlan) -> bool:
    """Abort, switch the selected output OFF and read it back. Never raises."""
    with contextlib.suppress(Exception):
        driver.abort_voltage_transient(plan.channel)
    try:
        driver.disable_output(plan.channel)
        return driver.get_output_state(plan.channel) is False
    except Exception:
        return False


def _verify_hardware(driver: Any, plan: ArbHilPlan) -> dict[str, Any]:
    transport = driver._session(None).transport
    if getattr(getattr(transport, "descriptor", None), "kind", "simulated") == "simulated":
        raise HilRefused(["a simulated transport can never produce hardware evidence"])
    identity = driver.idn(refresh=True)
    idn = f"{identity.manufacturer},{identity.model},{identity.serial},{identity.firmware}"
    found = {
        "idn": idn,
        "serial": driver.channel_serial(plan.channel),
        "firmware": identity.firmware,
        "mainframe": identity.model,
        "module_model": driver.channel_model(plan.channel),
        "options": sorted(driver.channel_options(plan.channel)),
        "mainframe_serial": identity.serial,
    }
    problems = []
    if plan.idn_contains not in idn:
        problems.append(f"IDN {idn!r} lacks {plan.idn_contains!r}")
    if found["mainframe_serial"] != plan.mainframe_serial:
        problems.append("mainframe serial differs from the approved plan")
    if found["firmware"] != plan.firmware:
        problems.append("firmware differs from the approved plan")
    if found["module_model"] != plan.module_model:
        problems.append("module model differs from the approved plan")
    if found["serial"] != plan.module_serial:
        problems.append("module serial differs from the approved plan")
    if tuple(found["options"]) != plan.options:
        problems.append("installed options differ from the approved plan")
    if problems:
        raise HilRefused(problems)
    return found


def _preflight_outputs(driver: Any, plan: ArbHilPlan) -> None:
    for number in range(1, driver.channel_count() + 1):
        state = driver.query_scpi(f"OUTP? (@{number})").strip()
        if state not in ("0", "+0"):
            who = "selected" if number == plan.channel else "unreviewed"
            raise HilRefused([f"{who} output {number} is ON or unreadable; refusing to touch it"])


class _WatchdogExpired(RuntimeError):
    """Raised by the cooperative guard so a case body stops energizing once its watchdog fired."""


def _run_case_body(
    driver: Any,
    plan: ArbHilPlan,
    case: ArbHilCase,
    evidence: dict[str, Any],
    guard: Callable[[], None] = lambda: None,
) -> None:
    """Configure (output OFF), optionally energize and run, then record readbacks.

    ``guard`` is called before every step that changes the output so a body that is still
    running after its watchdog fired cannot switch the output back ON.
    """
    channel = plan.channel
    config = driver.configure_voltage_arb_waveform(
        channel, case.shape, maximum_voltage_v=plan.maximum_voltage_v,
        minimum_voltage_v=plan.minimum_voltage_v, maximum_duration_s=plan.maximum_duration_s,
    )
    evidence["observed"]["scpi_readbacks"] = {
        "shape": type(config.waveform).__name__,
        "minimum_level": config.minimum_level,
        "maximum_level": config.maximum_level,
        "repeat_count": config.repeat_count,
        "duration_s": config.duration_s,
    }
    timeline: list[dict[str, Any]] = []
    evidence["observed"]["status_timeline"] = timeline
    if not case.energize:
        evidence["reason"] = "profile configured and read back with the output OFF; nothing energized"
        return
    # Selecting the trigger source happens before the output is enabled, so a firmware
    # that refuses the dialect (the N6700C takes TRIG:ARB:SOUR without a channel list)
    # fails the case with the output still OFF.
    guard()
    driver.configure_voltage_transient_trigger(channel, "bus")
    guard()
    driver.set_dc_voltage(plan.baseline_voltage_v, channel)
    driver.set_dc_current(plan.current_limit_a, channel)
    guard()
    driver.enable_output(channel)
    guard()
    token = driver.arm_voltage_transient(channel)
    timeline.append({"event": "armed", "status": _status(driver, channel)})
    driver.wait_voltage_transient_ready(channel, token, timeout_s=5.0)
    guard()
    driver.trigger_voltage_transient(channel, token)
    # The supervisor's deadline sits inside the case watchdog (setup headroom), so a run
    # that never completes is reported by the supervisor, not by the watchdog.
    result = driver.supervise_voltage_transient(
        channel, token, maximum_runtime_s=case.watchdog_s - SETUP_HEADROOM_S, poll_interval_s=0.02,
    )
    timeline.append({"event": "supervised", "reason": result.reason, "elapsed_s": result.elapsed_s})
    if result.reason != "completed":
        # A finite single-pass waveform that did not complete is a failure, not a success.
        raise RuntimeError(f"supervised run ended with {result.reason}, expected completed")
    evidence["reason"] = "energized run completed under supervision"


def _status(driver: Any, channel: int) -> dict[str, Any]:
    status = driver.get_voltage_transient_status(channel)
    return {"state": status.state, "waiting": status.waiting_for_trigger, "active": status.transient_active}


def _execute_case(
    driver: Any, plan: ArbHilPlan, case: ArbHilCase, evidence: dict[str, Any], found: dict[str, Any]
) -> None:
    evidence["instrument"].update(
        {k: found[k] for k in ("idn", "serial", "firmware", "mainframe", "module_model", "options")}
    )
    evidence["utc_start"] = _utc_now()
    evidence["safety"]["unknown_state"] = True  # until the safe state is read back
    cleanup_lock = threading.Lock()

    def on_expire() -> None:
        evidence["safety"]["watchdog_cleanup_verified"] = _make_safe(driver, plan)

    def guard() -> None:
        if watchdog.expired.is_set():
            raise _WatchdogExpired("case watchdog expired; not touching the output again")

    failure: str | None = None
    interrupt: BaseException | None = None
    watchdog = _Watchdog(case.watchdog_s, on_expire, cleanup_lock)
    with watchdog:
        try:
            _run_case_body(driver, plan, case, evidence, guard)
        except BaseException as exc:
            failure = f"{type(exc).__name__}: {exc}"
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                interrupt = exc
    expired = watchdog.expired.is_set()
    safety = evidence["safety"]
    safety["watchdog_expired"] = expired
    # Record the queue before cleanup: the cleanup commands check (and so consume) it.
    collected: list[str] = []
    readable = True

    def drain() -> None:
        nonlocal readable, failure
        try:
            collected.extend(str(item) for item in driver.drain_errors())
        except Exception as exc:
            readable = False
            failure = failure or f"error queue unreadable: {exc}"

    with cleanup_lock:  # waits for a watchdog cleanup still in flight
        drain()
        safety["abort_attempted"] = True
        safety["output_off_commanded"] = True
        safe = _make_safe(driver, plan)
        safety["output_off_verified"] = safe
        safety["unknown_state"] = not safe
        if readable:
            drain()
    evidence["observed"]["error_queue"] = collected if readable else None
    evidence["utc_end"] = _utc_now()

    errors = evidence["observed"]["error_queue"]
    if not safe:
        evidence["verdict"], evidence["reason"] = "FAIL", "output could not be verified OFF"
    elif expired:
        evidence["verdict"], evidence["reason"] = "FAIL", "watchdog expired; the case overran its limit"
    elif failure is not None:
        evidence["verdict"], evidence["reason"] = "FAIL", failure
    elif errors:
        evidence["verdict"], evidence["reason"] = "FAIL", f"SCPI errors: {errors}"
    elif case.qualification_level == "analog_metrology":
        evidence["verdict"] = "INCONCLUSIVE"
        evidence["reason"] = "protocol path ran; independent calibrated DSO evidence is not attached"
    else:
        evidence["verdict"] = "PASS"
    if interrupt is not None:
        # The operator asked to stop: the safe state and evidence are already recorded.
        evidence["verdict"], evidence["reason"] = "FAIL", f"interrupted: {failure}"
        raise _Interrupted(interrupt)


class _Interrupted(Exception):
    """Carries a KeyboardInterrupt/SystemExit out of a case after its cleanup finished."""

    def __init__(self, original: BaseException) -> None:
        super().__init__(str(original))
        self.original = original


def run_arb_hil(
    driver: Any,
    plan: ArbHilPlan,
    output: Path,
    *,
    env: Mapping[str, str],
    dry_run: bool,
) -> dict[str, Any]:
    """Run the plan against an already-connected driver and persist evidence.

    ``dry_run`` executes nothing on an instrument: every case is recorded
    ``NOT_RUN`` after the configuration is exercised on the (simulated) driver.
    A missing opt-in raises :class:`HilRefused` before the output directory exists;
    an identity or output-state refusal is recorded in ``summary.json`` and re-raised;
    only read-only identification and output-state queries have reached the instrument
    by then, and nothing is switched.
    """
    if not dry_run:
        check_environment(env)
    output.mkdir(parents=True, exist_ok=False)
    summary: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "driver_version": __version__, "dry_run": dry_run,
        "plan_sha256": plan.sha256, "authorization_reference": plan.reference,
        "started_utc": _utc_now(), "cases": [], "result": "FAIL",
        "qualification": QUALIFICATION_LABEL,
    }
    _atomic_write(output / "plan.json", _json_text(plan.raw))

    def write_summary() -> None:
        summary["completed_utc"] = _utc_now()
        _atomic_write(output / "summary.json", _json_text(summary))

    found: dict[str, Any] | None = None
    owned = False  # True once the identity and output-state pre-flight passed
    stop = False
    interrupted: BaseException | None = None
    try:
        if not dry_run:
            found = _verify_hardware(driver, plan)
            _preflight_outputs(driver, plan)
            owned = True
        for case in plan.cases:
            evidence = _empty_evidence(plan, case, dry_run)
            if stop:
                evidence["reason"] = "not run: an earlier case failed or the safe state was lost"
            elif dry_run:
                _dry_run_case(driver, plan, case, evidence)
            else:
                blockers = energize_blockers(case, env)
                capable: bool | None = None
                if not blockers:
                    try:
                        capable = _capable(driver, plan)
                    except Exception as exc:
                        evidence["verdict"] = "FAIL"
                        evidence["reason"] = f"capability check failed: {type(exc).__name__}: {exc}"
                        evidence["utc_start"] = evidence["utc_end"] = _utc_now()
                        evidence["safety"]["unknown_state"] = None
                if blockers:
                    evidence["reason"] = "energization refused: " + "; ".join(blockers)
                elif evidence["verdict"] == "FAIL":
                    pass
                elif not capable:
                    evidence["reason"] = "module/mainframe/options do not advertise source Arb support"
                elif found is None:
                    raise HilRefused(["internal error: hardware identity was not established"])
                else:
                    try:
                        _execute_case(driver, plan, case, evidence, found)
                    except _Interrupted as stopped:
                        interrupted = stopped.original
            problems = validate_case_evidence(evidence)
            if problems:
                evidence["verdict"], evidence["reason"] = "FAIL", "evidence invalid: " + "; ".join(problems)
            file = f"{case.case_id}.json"
            _atomic_write(output / file, _json_text(evidence))
            digest = hashlib.sha256((output / file).read_bytes()).hexdigest()
            summary["cases"].append({"case_id": case.case_id, "file": file, "verdict": evidence["verdict"], "sha256": digest})
            if evidence["verdict"] == "FAIL" and (plan.stop_on_failure or evidence["safety"]["output_off_verified"] is False):
                stop = True
            if interrupted is not None:
                raise interrupted
        verdicts = [entry["verdict"] for entry in summary["cases"]]
        if "FAIL" in verdicts:
            summary["result"] = "FAIL"
        elif dry_run or any(v in ("NOT_RUN", "INCONCLUSIVE") for v in verdicts):
            summary["result"] = "INCOMPLETE"
        else:
            summary["result"] = "PASS"
    except BaseException as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        summary["result"] = "FAIL"
        raise
    finally:
        if owned:
            summary["final_output_off_verified"] = _make_safe(driver, plan)
            if not summary["final_output_off_verified"]:
                summary["result"] = "FAILED_UNVERIFIED_SAFE_STATE"
        write_summary()
    return summary


def _capable(driver: Any, plan: ArbHilPlan) -> bool:
    caps = driver.discover_modules().get(plan.channel)
    return bool(caps is not None and caps.supports_voltage_arb)


def _dry_run_case(driver: Any, plan: ArbHilPlan, case: ArbHilCase, evidence: dict[str, Any]) -> None:
    """Exercise configuration on the simulator; the result is never evidence."""
    evidence["utc_start"] = _utc_now()
    try:
        driver.configure_voltage_arb_waveform(
            plan.channel, case.shape, maximum_voltage_v=plan.maximum_voltage_v,
            minimum_voltage_v=plan.minimum_voltage_v, maximum_duration_s=plan.maximum_duration_s,
        )
        evidence["reason"] = "dry run on the simulator: configuration accepted; this is not hardware evidence"
    except Exception as exc:
        evidence["reason"] = f"dry run on the simulator: configuration refused ({type(exc).__name__}: {exc})"
    finally:
        driver.disable_output(plan.channel)
    evidence["utc_end"] = _utc_now()
