#!/usr/bin/env python3
"""Generate ``ai/ai_contract.yaml`` and ``ai/ai_contract.lock`` (LPDS-017).

The contract's ``capabilities`` section is generated from the same
``@capability``-annotated methods that back ``get_capability_model()``
(:mod:`keysight_n6700.capability_model`), so the two can never silently
diverge. Everything else (mental model, state machine, safety rules, ...) is
authored by hand in this script, since LPDS-017 explicitly requires
natural-language content no introspection can produce.

Run from the repository root: ``python scripts/generate_ai_contract.py``
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from keysight_n6700 import N6700
from keysight_n6700.base import SessionState
from keysight_n6700.version import PYTHON_VERSION

ROOT = Path(__file__).resolve().parent.parent


def build_contract() -> dict[str, object]:
    driver = N6700()
    capabilities = driver.get_capability_model()

    return {
        "contract_schema_version": "1.0",
        "identity": {
            "plugin_id": "keysight.n6700",
            "driver_name": "keysight_n6700",
            "distribution_name": "keysight-n6700-driver",
            "distribution_version": PYTHON_VERSION,
            "driver_import_path": "keysight_n6700.driver",
            "driver_class": "keysight_n6700.driver.N6700",
            "supported_models": list(driver.metadata.supported_models),
        },
        "mental_model": {
            "summary": (
                "One N6700 mainframe session exposes up to four independently "
                "addressed module channels. Installed modules determine "
                "whether a channel acts as a power source, SMU, measurement "
                "source, or verified electronic load."
            ),
            "session_model": (
                "One driver instance may hold several named sessions "
                "(aliases) at once; most methods accept an optional `alias` "
                "and default to the currently selected one."
            ),
            "state_ownership": (
                "The driver tracks session and connection state; the "
                "instrument is the sole authority for channel configuration, "
                "output state, protection state, and measurements."
            ),
            "safety_model": (
                "Typed configuration defaults outputs/load inputs off. "
                "disconnect()/disconnect_all() attempt a best-effort "
                "shutdown first unless set_auto_shutdown_on_disconnect(False) "
                "was called. Raw SCPI (write_scpi/query_scpi) bypasses typed "
                "safeguards."
            ),
        },
        "state_machine": {
            "reference": "keysight_n6700.base.SessionState (LPDS-003 canonical enum)",
            "public_projection": "keysight_n6700.base.SUPPORT_STATE_PROJECTION",
            "states": [state.value for state in SessionState],
        },
        "resources": {
            "consumed": [
                {"id": "visa_resource", "type": "exclusive", "description": "VISA resource string for USB/LAN/vendor transport."},
                {"id": "tcp_socket", "type": "exclusive", "description": "Host and TCP port, normally 5025."},
                {"id": "session_alias", "type": "exclusive_within_driver", "description": "Unique named session."},
                {"id": "mainframe_channel", "type": "exclusive_for_mutation", "description": "Channel 1..4; coordinate concurrent test ownership."},
            ],
            "provided": [
                {"id": "dc_source", "quantity": ["voltage", "current", "power"], "availability": "module-dependent"},
                {"id": "smu", "quantity": ["source_voltage", "source_current", "measure_voltage", "measure_current"], "availability": "SMU module-dependent"},
                {"id": "electronic_load", "quantity": ["cc", "cv", "cr", "cp"], "availability": "N679xA (N6791A/N6792A) verified against official Keysight documentation; other real load modules remain restricted unless explicitly supported"},
                {"id": "internal_measurement", "quantity": ["voltage", "current", "power"], "availability": "module-dependent"},
            ],
        },
        "dependencies": {
            "runtime": ["Python >=3.10", "scpi-driver-core"],
            "optional": ["pyvisa (via scpi-driver-core[visa]) for VISA/USB transports", "robotframework >=7,<9 for the adapter"],
            "external": [
                "A VISA backend (vendor IO suite or pyvisa-py) for VISA/USB resources",
                "Network reachability for raw Ethernet",
                "Reviewed cabling, fixtures, DUT limits, and interlocks",
            ],
        },
        "capabilities": capabilities,
        "error_catalogue": [
            {"error_code": "LPDS-CFG-001", "exception_class": "DriverConfigurationError", "condition": "Invalid driver/session configuration.", "retryable": "no", "recovery": "NONE_REQUIRED"},
            {"error_code": "LPDS-CFG-002", "exception_class": "DriverConfigurationError", "condition": "import_driver_configuration() given a document that fails schema validation.", "retryable": "no", "recovery": "NONE_REQUIRED"},
            {"error_code": "LPDS-TMO-001", "exception_class": "DriverTimeoutError", "condition": "A transport or *OPC? wait exceeded its timeout.", "retryable": "yes", "recovery": "RETRY_ALLOWED"},
            {"error_code": "LPDS-CON-001", "exception_class": "DriverConnectionError", "condition": "connect() called for an alias already connected without replace=True.", "retryable": "no", "recovery": "NONE_REQUIRED"},
            {"error_code": "LPDS-CON-002", "exception_class": "DriverConnectionError", "condition": "I/O attempted while the transport is not open.", "retryable": "no", "recovery": "RECONNECT_REQUIRED"},
            {"error_code": "LPDS-PRT-001", "exception_class": "DriverMalformedResponseError", "condition": "A response could not be parsed into the documented type.", "retryable": "no", "recovery": "RESYNC_REQUIRED"},
            {"error_code": "LPDS-PRT-003", "exception_class": "DriverMalformedResponseError", "condition": "SYST:ERR? returned something other than code,\"message\".", "retryable": "no", "recovery": "RESYNC_REQUIRED"},
            {"error_code": "LPDS-DEV-001", "exception_class": "DriverCommandRejectedError", "condition": "The instrument rejected a SCPI command.", "retryable": "no", "recovery": "READBACK_REQUIRED"},
            {"error_code": "LPDS-DEV-002", "exception_class": "DriverCommandRejectedError", "condition": "The instrument's error queue reported one or more entries.", "retryable": "no", "recovery": "READBACK_REQUIRED"},
            {"error_code": "LPDS-DEV-003", "exception_class": "DriverIdentityError", "condition": "*IDN? was missing, unparsable, or from an unsupported manufacturer.", "retryable": "no", "recovery": "OPERATOR_ACTION_REQUIRED"},
            {"error_code": "LPDS-DEV-004", "exception_class": "DriverUnsupportedOperationError", "condition": "The installed module/transport does not support the requested operation.", "retryable": "no", "recovery": "NOT_RECOVERABLE"},
            {"error_code": "LPDS-STA-002", "exception_class": "DriverPreconditionError", "condition": "A method requiring an active session was called on a disconnected alias.", "retryable": "no", "recovery": "RECONNECT_REQUIRED"},
            {"error_code": "LPDS-STA-005", "exception_class": "DriverPreconditionError", "condition": "A method was called while the session was not in a required state.", "retryable": "no", "recovery": "NONE_REQUIRED"},
            {"error_code": "LPDS-ARG-001", "exception_class": "DriverArgumentValueError", "condition": "Channel argument outside the installed range.", "retryable": "no", "recovery": "NONE_REQUIRED"},
        ],
        "safety_rules": [
            "connect() never energizes an output or load input by itself.",
            "Typed configuration methods (set_dc_voltage, set_dc_current, configure_*) never enable an output; enable_output()/set_input() are separate, explicit calls.",
            "disconnect()/disconnect_all() attempt a best-effort shutdown of every installed channel first, unless set_auto_shutdown_on_disconnect(False) was called explicitly.",
            "Raw SCPI (write_scpi/query_scpi) bypasses every typed safeguard; do not use it to work around a typed method's restriction.",
            "Electronic-load SCPI commands are refused for real modules unless the model matches keysight_n6700.module_capabilities.LOAD_PREFIXES (currently N679x); the simulator's SIM_LOAD is the only other load model exempted, for testing.",
        ],
        "verification_objectives": [
            {"id": "VO-IDENTITY", "description": "get_identity()/idn() returns a supported manufacturer, model, serial, and firmware."},
            {"id": "VO-MODULE-MAP", "description": "discover_modules() classifies every installed channel by module family."},
            {"id": "VO-SETPOINT", "description": "A programmed voltage/current setpoint reads back as programmed."},
            {"id": "VO-OUTPUT", "description": "enable_output()/disable_output() change, and get_output_state() reports, the output state."},
            {"id": "VO-MEASUREMENT", "description": "measure_dc_voltage/current/power return a finite value with a documented source (instrument vs. calculated)."},
            {"id": "VO-PROTECTION", "description": "get_protection_status()/clear_protection() report and clear a tripped protection condition."},
            {"id": "VO-SHUTDOWN", "description": "safe_shutdown() disables every installed channel and reports per-channel success."},
        ],
        "setup_teardown_contract": {
            "setup": "connect() (or a connect_* classmethod) must succeed and *IDN? must be answered by a supported manufacturer before any other method is called.",
            "teardown": "disconnect()/disconnect_all() should always be called, ideally from a finally block or context manager (`with N6700.connect_simulated() as drv:`), even after a failed operation.",
        },
        "limitations": [
            "N679xA (N6791A/N6792A) electronic-load commands are verified against the official Keysight N6705C documentation and, for reads only (FUNC?/VOLT?/CURR?/OUTP?), against real hardware; the write path (priority mode selection, level setpoints, input on/off) has not yet been exercised against real hardware. Every other real load module remains unsupported; only the simulator's SIM_LOAD is exercised for those.",
            "Remote/local control (get_remote_state/set_remote_state/remote_lockout) is implemented against the simulator only; real N6700 remote/local behavior is transport-specific and not yet verified.",
            "No real-hardware conformance run has been performed yet (see review/known_risks.md); driver status is `untested`, not `stable`.",
        ],
        "planning_hints": [
            "Always call discover_modules() (or let connect()'s default discover=True do it) before addressing a channel by type-specific accessor (power_supply/smu/load).",
            "Configure a channel's setpoints and protection with the output/load input off, then enable it explicitly, mirroring TEMPLATE-SAFE-SOURCE-CHANNEL.",
            "Prefer typed methods over write_scpi/query_scpi; raw SCPI has no capability or safety gating.",
        ],
        "unknown_handling": {
            "policy": "fail_closed",
            "blocking_unknowns": ["installed module map", "DUT/fixture wiring", "DUT limits", "external interlock", "stabilization requirements"],
            "planner_rule": "An AI planner may draft a plan with explicit UNKNOWN placeholders, but must not generate an executable energizing sequence until every blocking unknown is resolved and approved by a human operator.",
        },
        "conformance_rules": [
            "identity.plugin_id, driver_name, and driver_class must match the values a caller observes from the installed package.",
            "Every capability's risk_level must be one of none/low/medium/high/critical.",
            "Every capability's binding.python_method must exist on keysight_n6700.driver.N6700 (enforced by validate_driver_capabilities()).",
            "No capability entry may name a test-automation framework, keyword, fixture, or CLI command.",
            "Every error_catalogue entry's exception_class must be a class in keysight_n6700.exceptions.",
        ],
    }


def main() -> None:
    contract = build_contract()
    ai_dir = ROOT / "ai"
    ai_dir.mkdir(exist_ok=True)
    contract_path = ai_dir / "ai_contract.yaml"
    canonical = yaml.safe_dump(contract, sort_keys=False, width=100)
    contract_path.write_text(canonical)

    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    lock = {
        "contract_sha256": digest,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "scripts/generate_ai_contract.py",
    }
    (ai_dir / "ai_contract.lock").write_text(json.dumps(lock, indent=2) + "\n")
    print(f"wrote {contract_path} ({len(contract['capabilities'])} capabilities, sha256={digest[:12]}...)")


if __name__ == "__main__":
    main()
