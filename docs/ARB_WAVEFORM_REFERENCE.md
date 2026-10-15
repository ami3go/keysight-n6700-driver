# N6700 power-module arbitrary waveform generation (ARB): command and capability reference

**Reference revision:** 1.4 (2026-10-15)  
**Driver baseline:** keysight-n6700-driver v0.7.0; catalog published in documentation release v0.7.1; external code references updated in v0.7.2; forum/Stack Overflow research added in v0.7.3; third-party software design review added in v0.7.4; expanded source-by-source analysis published in v0.7.5.  
**Purpose:** collect the documented hardware waveform functions, their SCPI command families, hardware gating, and the difference between vendor support and the Python driver's implemented surface.

> **Critical status:** This is a **manufacturer-command inventory and implementation backlog**, not a declaration that every command below works on every installed module or is implemented by this driver. A SCPI command appearing in a common manual does not grant model capability. Never generate an energizing waveform without a reviewed module/option matrix, DUT voltage/current/power envelope, explicit output enable, readback verification, and a safe abort path.

## 1. Sources and scope

1. **Primary official source:** [Keysight N6700C Series Modular Power System Operating and Service Guide, 9018-04957](https://www.keysight.com/nz/en/assets/9018-04957/service-manuals/9018-04957.pdf). See printed pages 123–135 (*Generating Arbitrary Waveforms*) and 188–232 (*SCPI Programming Reference*, ARB commands); printed pages 109–110 cover the transient trigger lifecycle. These page numbers refer to the printed document, **not** the PDF zero-based page index.
2. [Keysight N6700 Programmer's Reference Help Guide, 9018-03617](https://www.keysight.com/nl/en/assets/9018-03617/programming-guides/9018-03617.pdf) (validate command availability on the actual firmware/mainframe).
3. [Keysight N6700 family specifications guide, 9018-04533](https://www.keysight.com/cd/en/assets/9018-04533/technical-specifications/9018-04533.pdf) (model capabilities, Option 054, bandwidth/accuracy limits).
4. Repository's [source-voltage transient implementation contract](VOLTAGE_TRANSIENT_IMPLEMENTATION.md), with the bundled [N6705 programming-guide extraction](../Keysight_documents/N6775A.pdf.md).
5. All external documentation is vendor-owned; this file is a **paraphrased command inventory**, not a reproduction of the manual.

This is for N6700-family modular DC power **source/SMU modules** first. N6791A/N6792A electronic loads are described only to distinguish their supported **power/resistance** Arb types; digitized acquisition ("scope") is a separate subsystem and is **not** the arbitrary waveform generator.

## 2. Hardware and option applicability

| Module/mainframe context | Voltage Arb | Current Arb | Power / resistance Arb | Qualification |
| --- | --- | --- | --- | --- |
| N673xA, N674xA, N675xA, N677xA in N6700C | Documented **with Option 054 installed** | Documented **with Option 054 installed** | **Not** power-source modes | Verify exact model, installed option, firmware and bandwidth |
| N676xA precision source | Model-dependent; confirm exact support in current family guide | Model-dependent; confirm exact support | Not power-source modes | No family-wide assumption |
| N678xA SMU | Voltage/current behavior and priority-mode restrictions require model review | Same | Not power-source modes | Must test voltage/current priority and two-/four-quadrant limits |
| N6791A/N6792A electronic load | Vendor command family includes voltage/current modes | Same | **Power and resistance types are load-only (N679xA)** | Do not expose as power-supply functionality |
| N6705C DC Power Analyzer | Dedicated integrated Arb facilities exist without the same source-module Option 054 requirement described for N6700C | Depends on installed module | Load-only for power/resistance | Confirm per-module documented restrictions |
| N6700B and other mainframes | **Unverified by this N6700C inventory** | **Unverified** | **Unverified** | Consult that mainframe's exact programming guide |

**Option 054:** The N6700C guide explicitly requires Option 054 for Arb operation on N673xA/N674xA/N675xA/N677xA (printed p.123); power and resistance Arb types are explicitly marked N679xA only (pp.124 and 191). The N6705C statement is drawn from the family specifications guide / repository README and **must not** be transposed to N6700C.

Before configuring, discover module model and options via **SYST:CHAN:MOD? (@n)** and **SYST:CHAN:OPT? (@n)**, check capability declarations, and fail closed on unknown combinations. A successful parsing of an Arb command is **not** evidence of physical timing accuracy.

## 3. Arb architecture and shapes

The Arb generator modulates a channel's commanded output parameter using hardware-timed steps. Only **one Arb type** can be active on a channel at a time:

- **VOLTage:** voltage waveform (source and certain priority-mode-dependent channels).
- **CURRent:** current waveform (source and certain priority-mode-dependent channels).
- **POWer / RESISTANCE:** N679xA **electronic-load** mode only; do not advertise for source modules.

The **ARB:FUNCtion:SHAPe** selector supports the following values:

| Shape | SCPI selector value | Effect / principal parameters |
| --- | --- | --- |
| Step | STEP | start/end level; pre-step and post-step timing |
| Ramp | RAMP | start/end level; start delay, rise time and end hold |
| Staircase | STAircase (STA) | start/end; number of steps; start/stair/end timing |
| Sinusoid | SINusoid (SIN) | amplitude, offset, frequency |
| Pulse | PULSe (PULS) | start/top level; start/top/end time |
| Trapezoid | TRAPezoid (TRAP) | start/top level; start/rise/top/fall/end time |
| Exponential | EXPonential (EXP) | start/end level; start time, total time, time constant |
| User-defined | UDEFined (UDEF) | point-by-point levels, dwells, optional beginning-of-step trigger flags |
| Constant dwell | CDWell (CDW) | vector of levels sharing one dwell |
| Sequence | SEQuence (SEQ) | ordered waveform steps, counts and pacing |
| Disabled | NONE | no Arb selected |

The instrument's actual update interval and analog bandwidth are **module-dependent**. A requested sine frequency or short dwell does not mean the installed source achieves that analog waveform.

## 4. Common Arb SCPI command inventory

Every command below is **channel-scoped** unless noted. SCPI long names are shown with the manufacturer's upper-/lower-case abbreviated syntax; **ARB** may be prefixed by **SOURce:**.

| Purpose | Canonical SCPI form | Typical short form / note |
| --- | --- | --- |
| Select/query type | ARB:FUNCtion:TYPE type, (@n) / TYPE? | ARB:FUNC:TYPE VOLT,(@1); CURR, VOLT, POW, RES |
| Select/query shape | ARB:FUNCtion:SHAPe shape, (@n) / SHAPe? | ARB:FUNC:SHAP RAMP,(@1) |
| Set/query count | ARB:COUNt count, (@n) / COUNt? | Hardware supports finite/INFinity, but driver must keep finite bound |
| Set/query terminate behavior | ARB:TERMinate:LAST bool, (@n) / LAST? | OFF restores pre-Arb settings; ON holds final Arb value |
| Convert a standard shape to points | ARB:VOLTage:CONVert (@n), ARB:CURRent:CONVert (@n) | Current type/shape must first be selected; load variants POW/RES also exist |
| Set active voltage/current transient mode | VOLTage:MODE ARB, (@n) / CURRent:MODE ARB, (@n) | Setting shape alone does **not** arm or run it |
| Select/query trigger source | TRIGger:TRANsient:SOURce BUS\|EXT\|IMM\|PINn\|TRANn, (@n) / SOURce? | Exact options depend on installed model and configured digital pins |
| Arm | INITiate:TRANsient (@n) | Must check trigger-waiting status before issuing BUS trigger |
| Read transient operation status | STATus:OPERation:CONDition? (@n) | WTG_transient bit 4 (mask 16), TRAN_active bit 6 (mask 64) |
| Channel-targeted trigger | TRIGger:TRANsient (@n) | Prefer this to mainframe-wide *TRG |
| Mainframe-wide bus trigger | *TRG | Can trigger **other armed channels**; unsafe as a generic per-channel helper |
| Abort | ABORt:TRANsient (@n) | Cleanup also disables continuous transient initiation |
| Disable continuous initiation | INITiate:CONTinuous:TRANsient OFF, (@n) | Needed after abort in bounded driver lifecycle |

**Safety note:** do not silently use infinite repetition or **ARB:TERM:LAST ON**. The latter changes the channel's immediate setpoint on completion, which may be hazardous. Output OFF must be independently verified after failure/abort, and protection trips must not be auto-cleared.

## 5. Parameter command catalog

For the following patterns substitute **T = VOLTage or CURRent** when targeting power sources. The guide documents **POWer** and **RESISTANCE** variants for eligible N679xA loads only. A setter typically has **value, (@n)** and a query is the same path followed by **? (@n)**. The table lists *SCPI command paths*, not driver method names. Consult the vendor pages cited above for each property's allowed range, default and precise waveform interpretation.

| Shape | Command paths after ARB:T: | Parameters / notes |
| --- | --- | --- |
| STEP | STEP:STARt; STEP:END; STEP:STARt:TIMe; STEP:END:TIMe | Before/after step levels and timing; check end-time availability on exact firmware |
| RAMP | RAMP:STARt; RAMP:END; RAMP:STARt:TIMe; RAMP:RTIMe; RAMP:END:TIMe | Ramp endpoints, pre-delay, rise and post-delay |
| PULSe | PULSe:STARt; PULSe:TOP; PULSe:STARt:TIMe; PULSe:TOP:TIMe; PULSe:END:TIMe; PULSe:FREQuency | Start/top levels, pulse width, delays and **frequency (coupled to pulse timing)** |
| TRAPezoid | TRAPezoid:STARt; TRAPezoid:TOP; TRAPezoid:STARt:TIMe; TRAPezoid:RTIMe; TRAPezoid:TOP:TIMe; TRAPezoid:FTIMe; TRAPezoid:END:TIMe | Start/top levels, rise/top/fall timing and holds |
| STAircase | STAircase:STARt; STAircase:END; STAircase:NSTeps; STAircase:STARt:TIMe; STAircase:TIMe; STAircase:END:TIMe | Levels, step count, timing |
| EXPonential | EXPonential:STARt; EXPonential:END; EXPonential:STARt:TIMe; EXPonential:TIMe; EXPonential:TCONstant | Start/end levels, delay, duration and time constant |
| SINusoid | SINusoid:AMPLitude; SINusoid:OFFSet; SINusoid:FREQuency | Amplitude, offset, Hz; command-programming range does not guarantee output bandwidth |
| CDWell | CDWell[:LEVel]; CDWell:DWELl; CDWell:POINts? | Point vector; one common dwell; query point count |
| UDEFined | UDEFined:LEVel; UDEFined:DWELl; UDEFined:BOSTep | Per-point levels/dwells and beginning-of-step trigger flags |
| UDEFined counts | UDEFined:LEVel:POINts?; UDEFined:DWELl:POINts?; UDEFined:BOSTep:POINts? | Count queries; enforce consistent arrays/readbacks |

**SCPI examples only — NOT a runnable or output-enabling sequence:**

~~~text
ARB:FUNC:TYPE VOLT,(@1)
ARB:FUNC:SHAP SIN,(@1)
ARB:VOLT:SIN:AMPL 0.5,(@1)
ARB:VOLT:SIN:OFFS 5,(@1)
ARB:VOLT:SIN:FREQ 10,(@1)
ARB:FUNC:SHAP? (@1)
ARB:FUNC:TYPE? (@1)
~~~

Do not run examples on a connected DUT without capability and envelope checks; configuration parameters can change the next output transient. Each listed command needs firmware/model validation before promotion to the typed driver API.

### 5.1 Point and count constraints are NOT interchangeable

- **UDEFined:** up to **511** points per waveform in the guide. The total sequence waveform-point budget is **511**.
- **CDWell:** up to **65,535** constant-dwell points in the guide, subject to resource constraints and specific interface/firmware support. CD repeat count has its own bound (**256**) in the SCPI reference.
- **ARB sequence:** at most **100 indexed steps (0–99)** and **511 total generated points**; instrument query **ARB:SEQ:QUAL?** reports smooth-curve approximation quality from **16–100** points.
- **Sequence repeats:** **ARB:SEQ:COUN** finite **1–4096** (hardware also permits INFinity). Step repeats (for triggered pacing) have a different **1–16,777,216** range. Do **not** use a shared count validator.
- **Sine frequency:** the reference lists **3.8147e-5–1e4 Hz** as a command range; real output bandwidth is specific to the installed module.
- **Dwell/time values:** the reference repeatedly gives **0–262.144 s** for relevant timing parameters. Exact minimum, quantization and legal zero-dwell use must be checked per shape/model; do not infer precise analog dwell from a SCPI round-trip.
- **Existing driver API:** currently supports only **finite bounded voltage LIST** (max 512 points) and **finite bounded voltage UDEF Arb** (max 511 points), with stricter user-set maximum duration and explicit approved voltage envelope; it does **not** expose CDW or standard-shape APIs.

## 6. Sequence ARB command inventory

| Purpose | SCPI path |
| --- | --- |
| Repetitions of whole sequence | ARB:SEQuence:COUNt / COUNt? |
| Number of sequence steps | ARB:SEQuence:LENGth? |
| Smooth-waveform representation quality | ARB:SEQuence:QUALity? |
| Reset sequence definition | ARB:SEQuence:RESet |
| Keep final value | ARB:SEQuence:TERMinate:LAST / LAST? |
| Create/alter indexed shape | ARB:SEQuence:STEP:FUNCtion:SHAPe shape, step_index, (@n) |
| Configure indexed step pacing | ARB:SEQuence:STEP:PACing DWELL\|TRIGgered, step_index, (@n) |
| Indexed step repetitions | ARB:SEQuence:STEP:COUNt count, step_index, (@n) |
| Indexed shape parameters | ARB:SEQuence:STEP:T:shape:parameter value, step_index, (@n) |

Sequence indexing is **0–99** and new steps must be added sequentially, matching the current **ARB:SEQ:LENG?**; changing the shape resets that step's parameters. **DWELL** pacing advances by elapsed dwell; **TRIGgered** pacing waits for the next trigger. The vendor reference describes the step repeat count with the triggered-pacing restriction. For safety, a future typed sequence builder should reject infinite counts and bound total duration, triggers and point memory **before any SCPI write**.

## 7. Safe configuration and execution contract

1. Query **IDN, channel count, model, options, limits, OVP, existing output state and errors** without clearing/resetting. Refuse unknown or unsupported module/option combinations.
2. Require selected channel output **OFF** and acquire an exclusive session/channel transient ownership lock; serialize entire configure-query-readback transaction.
3. Validate all levels including intermediate extrema (**offset ± amplitude** for sine), current/power limits, dwell, point count, repetition count, duration, OVP and reviewed DUT/fixture limits. Reject NaN, infinity and implicit power-on.
4. Save relevant pre-transient mode/settings, abort a stale transient, disable continuous initiation, program type/shape and parameters, set safe repeat/termination, set **VOLT:MODE ARB** or **CURR:MODE ARB** as appropriate, and read back values.
5. **Configuration never enables the output.** An explicit approved output-enable action is required separately.
6. Select transient trigger source; initiate; poll for WTG_transient (mask 16) with a bounded deadline. Only then issue channel-targeted **TRIG:TRAN (@n)** for BUS operation.
7. Poll active/wait bits and instrument errors through completion; use bounded waits and cancellation-aware abort. Avoid **\*TRG** for a single channel in a multi-channel mainframe.
8. On timeout, cancellation, protection trip, communication failure, or partial configuration: best-effort **ABOR:TRAN**, **INIT:CONT:TRAN OFF**, output OFF, safe-state readback; if readback is impossible mark state **UNKNOWN** and require operator intervention.
9. Treat **OUTP:PROT:CLE** as hazardous: after a fault it may restore a formerly enabled output; do not automatically clear it.
10. Preserve complete audit/evidence: IDN, module/firmware/Option 054, configured and readback profile, trigger origin, clock source, status, SCPI errors, analog reference measurement/uncertainty and hashes.

For live benches, physical E-stop/inhibit and independent hardware voltage/current protection must not depend on the Python process.

## 8. Current repository coverage and implementation backlog

| Capability | v0.7.0 driver status | Follow-up |
| --- | --- | --- |
| Voltage slew rate | **Implemented in software** | Physical edge/slew qualification pending |
| Finite voltage LIST | **Implemented** | Real N6775A analog timing HIL/DSO pending |
| Finite voltage UDEF Arb | **Implemented** | Real N6775A analog timing HIL/DSO pending |
| BUS/external triggering; arm/wait/abort, token fencing | **Implemented** | Stress and hardware tests pending |
| Standard shape STEP, RAMP, STAircase, SIN, PULS, TRAP, EXP | **Not implemented as typed APIs** | Per-model capability gate, serializable typed parameter objects, readback tests |
| CDWell | **Not implemented** | Separate memory/256-repeat limits, robust bulk transfer |
| Arb sequence | **Not implemented** | Atomic planning and global 511-point budget; index/pacing tests |
| Current Arb | **Not implemented as a typed current-profile API** | Priority-mode matrix, overload/overcurrent tests |
| Power/resistance Arb | **Not supported for power-source channels** | Load-specific design and validation only |
| Infinite Arb repetition | **Intentionally excluded** | Independent watchdog/energy risk review required |
| File import/export and arbitrary binary upload | **Not implemented** | Schema, checksum, IEEE block format/byte order tests |

Future work should be separate reviewed pull requests: (A) model/option capability matrix and command map, (B) finite standard voltage shapes with simulator and query readbacks, (C) bounded current shapes, (D) constant dwell, (E) finite sequences, (F) guarded DSO HIL. Review each PR against the exact manual/model before merge.

## 9. Verification matrix before claiming production readiness

- **Offline negative tests:** invalid models/options, output-on refusal, out-of-range levels, sine extrema exceeding OVP, non-finite numbers, missing/mismatched UDEF arrays, count and memory limits, infinite count rejection, SCPI error injection and partial configuration recovery.
- **Protocol simulations:** exact outbound SCPI and ordering for all shapes, query responses, channel scoping, trigger source/WTG state, arm/abort token fencing and concurrent-call serialization.
- **Guarded physical HIL:** N6700C + documented Option 054 power source; known passive reviewed load/DUT; baseline voltage/current tests, independent DSO capture, no output left on, error queue empty. Repeat under BUS and external trigger, including missed trigger, communication loss and protective shutdown.
- **Physical metrology:** measured amplitude and timing, rise/fall and dwell, trigger-to-waveform latency and jitter, overshoot, digital-vs-analog waveform correlation, cable/probe/load setup, uncertainty budget, reproducibility across approved modules/firmware.
- **Acceptance tiers:** software simulation pass != command-protocol pass != analog performance qualification != unattended production authorization. Do not claim an AWG bandwidth or timing resolution not verified against the installed module's specifications.

## 10. Related repository material

- [Implemented finite voltage LIST/UDEF API and safety gates](VOLTAGE_TRANSIENT_IMPLEMENTATION.md)
- [Hardware acceptance](hardware_acceptance_tests.md)
- [Scope/digitizer qualification (measurement, not Arb generation)](SCOPE_HIL_QUALIFICATION.md)
- [Known risks](../review/known_risks.md)
- [Current module capability model](../capability/capability_model.yaml)

**Change scope:** reference documentation only. No Arb/driver command behavior or qualification status changes in v0.7.1–v0.7.5.

## 11. Reviewed external code examples and references (2026-10-15)

These are **inspected source-code examples** for implementation research, not certified software, validated Keysight SCPI instructions, or endorsements. **The official Keysight programming reference and exact installed module/firmware remain authoritative.** GitHub links are pinned to specific commits where available. Do not copy third-party implementations verbatim without checking their licenses.

### 11.1 Direct N6700/N6705 waveform and transient code on GitHub

| Source | Relevant functions and command pattern | Applicability and review notes |
| --- | --- | --- |
| [HarishKumarSedu/IVM6311ATE — \`Instruments/KeySight_N670x.py\`](https://github.com/HarishKumarSedu/IVM6311ATE/blob/4b41017d16d87b0050eeccc5588c850b9acd57bd/Instruments/KeySight_N670x.py) | An N670x-family Python adapter with \`arbFunction_Priority__Voltage\`, sequence shape/reset helpers and \`arb_Pulse__Voltage\` / \`arb_Pulse__Current\`. It writes \`ARB:VOLT:PULS:STAR\`, \`TOP\`, \`STAR:TIM\`, \`TOP:TIM\`, and \`END:TIM\` (and corresponding current commands). | **High relevance for proposed typed PULSE and sequence APIs.** The existence of a setter is not proof it works on all models/options. Validate output-OFF prerequisites, parameter ranges, readback and error handling independently. |
| [PDSHan/VoltFraud — \`VoltFraud-sgx/lib/DCpower_ctr.py\`](https://github.com/PDSHan/VoltFraud/blob/20a9030a8dea9d59e481cf00a220c5f51c496068/VoltFraud-sgx/lib/DCpower_ctr.py) | Voltage \`UDEF\` Arb for fault injection: \`ARB:FUNC:TYPE VOLT\`, \`ARB:FUNC:SHAP UDEF\`, \`ARB:VOLT:UDEF:LEV\` / \`DWEL\`, external trigger selection and \`INIT:TRAN\`. | **High relevance for voltage dips/peaks and external triggering.** Contains \`OUTP ON\`, \`INIT:CONT:TRAN ON\` and polling; **not safe as a production driver template** without explicit authorization, finite counts, deadlines and fault cleanup. |
| [CHENYU0112/PoL-GUI — \`pverifyDrivers/powsup.py\`](https://github.com/CHENYU0112/PoL-GUI/blob/71f13604baf9455518766981c029b92658adf6bc/pverifyDrivers/powsup.py) | Shape/type selection with \`ARB:FUNC:SHAP\` / \`TYPE\`; generic top/start/end levels, rise/fall/hold times and \`TRIG:TRAN:SOUR\`. | **Useful API coverage checklist** for standard shapes. Generic string interpolation should be replaced by validated per-shape parameter objects, a verified SCPI command map and model-specific support checks. |
| [katzgo/EIS_keysight — \`Pruebas_EIS/EIS_GenerateARB_v7_clean.py\`](https://github.com/katzgo/EIS_keysight/blob/4a8912dc58d689e166d0d67dc519b252821569b0/Pruebas_EIS/EIS_GenerateARB_v7_clean.py) | Current \`CDW\` Arb: \`ARB:FUNC:TYPE CURR\`, \`ARB:FUNC:SHAP CDW\`, \`ARB:CURR:CDW:DWELL\`, and PyVISA \`write_ascii_values('ARB:CURR:CDW ', ...)\`. | **High relevance for constant-dwell bulk transfer.** This research script sets \`ARB:COUN INF\` and enables the output, so only borrow reviewed transfer concepts. Verify vector termination, channel lists and readback against real N6700 firmware. |
| [katzgo/EIS_keysight — \`Pruebas_EIS/EIS_ELOG_GenerateARB_V1.py\`](https://github.com/katzgo/EIS_keysight/blob/4a8912dc58d689e166d0d67dc519b252821569b0/Pruebas_EIS/EIS_ELOG_GenerateARB_V1.py) | Voltage CDW setup, ASCII array writes, transient initiation and triggering. | **CDW waveform and acquisition workflow reference.** The example includes infinite repeat, explicit output-on and acquisition/logger operations that should not be mixed into a safe Arb configuration transaction. |

**Implementation observations:** The public code demonstrates PULSE parameter grouping, UDEF level/dwell vectors, CDW ASCII transfer and channel-scoped initiation. Its trigger variants (including \`TRIG:ARB:SOUR\` in some projects versus \`TRIG:TRAN:SOUR\` in the current driver) **must not be assumed interchangeable**; the exact command tree and firmware behavior must be verified against the vendor guide and [existing implementation contract](VOLTAGE_TRANSIENT_IMPLEMENTATION.md).

### 11.2 Related GitHub work — architecture only, not validated N6700 Arb

| Source | What can be learned | What must **not** be inferred |
| --- | --- | --- |
| [catec/n6705_psu_ui — device adapter](https://github.com/catec/n6705_psu_ui/blob/main/n6705_ui/device/n6705.py) and [TCP SCPI client](https://github.com/catec/n6705_psu_ui/blob/main/n6705_ui/communication/scpi_client.py) | N6705-specific device abstraction and synchronized SCPI-over-TCP communications. | No reviewed Arb waveform implementation was identified in these files; they are **transport** references only. |
| [ami3go/PS_N6700B-USB-Python — \`src/PS_N6700B_class.py\`](https://github.com/ami3go/PS_N6700B-USB-Python/blob/main/src/PS_N6700B_class.py) | Prior USB/PyVISA-style N6700B device access. | Do not transplant legacy structure, assume Arb capability, or bypass the current \`scpi-driver-core\` architecture. |
| [Labber-software/Drivers — Keysight B2961A](https://github.com/Labber-software/Drivers/blob/24cc68e33dd9b6c2b28264b25e65a9255e7c1229/Keysight_B2961A/Keysight_B2961A.py) | Ramp Arb driver structure on **another Keysight source model**. | B2961A commands, voltage/current ranges and timing behavior are **not** an N6700 command/feature guarantee. |

### 11.3 Manufacturer resources and programming examples

- [Keysight N6700 family Programmer's Reference](https://www.keysight.com/nl/en/assets/9018-03617/programming-guides/9018-03617.pdf) — authoritative SCPI definitions, query responses and model restrictions. Use to independently validate every linked community example.
- [Keysight N6705C User's Guide](https://www.keysight.com/us/en/assets/9018-01649/user-manuals/9018-01649.pdf), **Generating Arbitrary Waveforms** — operational examples for user-defined, pulse, staircase, constant-dwell and sequence waveforms, along with trigger/termination behavior. It is not an executable Python repository.
- [Keysight N6700 series Specifications Guide](https://www.keysight.com/cd/en/assets/9018-04533/technical-specifications/9018-04533.pdf) — model/Option 054 applicability and analog capability ceilings; a syntactically valid SCPI command is not proof that a source module can generate the requested physical waveform.
- [Local source-voltage LIST/UDEF driver code and test plan](VOLTAGE_TRANSIENT_IMPLEMENTATION.md) and [voltage-transient unit tests](../tests/unit/test_voltage_transient.py) — **preferred examples** for this project's state-machine, finite duration, DUT envelope, readback and cleanup requirements.

### 11.4 GitLab research outcome

Public GitLab-hosted N6700/N6705 **Arb source-code examples** were searched for using the device names and SCPI terms (including \`ARB:VOLT\` and \`ARB:FUNC:SHAP\`). **No independently verifiable relevant public GitLab file was identified**, so no GitLab implementation link is included. This is a search limitation, **not evidence that no such GitLab project exists**. [GitLab public project explorer](https://gitlab.com/explore/projects).

### 11.5 Rules for reusing public sample ideas

1. **Do not copy safety shortcuts:** \`OUTP ON\` in initialization, infinite \`ARB:COUN INF\`, continuous transient initiation, unbounded polling, unchecked \`*RST\`, broad \`clear()\` or ignored exceptions are not acceptable defaults.
2. **Check device applicability:** power-source voltage/current Arb is not electronic-load power/resistance Arb; confirm exact module model, mainframe, firmware and Option 054.
3. **Design typed APIs:** separate \`VoltagePulseArb\`, \`VoltageRampArb\`, \`CurrentConstantDwellArb\` and \`ArbSequence\` instead of a raw user-supplied shape/parameter string.
4. **Validate the entire waveform before writing:** amplitude extrema, DUT limits, slew/current/OVP, dwell quantization, point-memory budget, per-step counts and maximum duration.
5. **Preserve reviewed lifecycle:** output OFF at configuration, parameter/query readbacks, explicit output enable by the caller, generation token, wait-for-trigger verification, channel-targeted triggering, deadline, and abort plus verified safe shutdown.
6. **Test offline and physically:** protocol tests cover exact SCPI and failure recovery; independent DSO measurement is required for analog amplitude, edge speed, timing and jitter qualification.

**Evidence scope:** This review confirms the cited public files contain the noted examples. It does **not** claim that third-party code is correct, licensed for copying, successfully tested on the user's specific hardware, or approved for industrial use.

## 12. Forum and Stack Overflow research (2026-10-15)

**Scope and evidence level.** This section catalogs first-hand reports on N6700/N6705 instrument control, measurement capture, triggering, channel selection, cross-channel protection, and communication reliability. Community posts are **not** manufacturer's capability statements or proof of Arb waveform performance. A forum post about digitized acquisition is not evidence about Arb generation: the two are separate instrument subsystems.

### 12.1 Source-linked findings

| ID | Public discussion | Verified observation | Engineering implication | Evidence strength |
| --- | --- | --- | --- | --- |
| F01 | [Stack Overflow: PyVISA timing out with a Keysight power generator](https://stackoverflow.com/questions/44119718/pyvisa-timing-out-with-a-keysight-power-generator) (22 May 2017) | An N6705 script could query identity and instantaneous measurements but timed out in a digitized acquisition workflow. The author later posted working code using `SENS:SWE:TINT .001`, `INIT:ACQ`, `TRIG:ACQ` and `FETC:ARR:VOLT? (@1)`, attributing the issue to spacing; the precise failed command was not conclusively identified. | Distinguish connection success from command support and acquisition readiness; verify documented SCPI syntax, readbacks, operation state, termination and error queue. Record actual outbound commands. | **First-hand report; specific root cause uncertain; relates to acquisition, not Arb.** |
| F02 | [NI Community: N6705C invalid acquisition for voltage/power](https://forums.ni.com/t5/Instrument-Control-GPIB-Serial/Labview-Error-when-using-Keysight-N6705C/td-p/4453137) (15 Sep 2025) | A user reported `+303, "There is not a valid acquisition to fetch from"` for `FETC:ARR:VOLT? (@1)` and `FETC:ARR:POW? (@1)`, whereas `FETC:ARR:CURR? (@1)` succeeded. | Validate enabled measurement function **and** matching completed acquisition before fetch; reject wrong-function/stale-buffer reads; add `+303` fault-injection regression. | **First-hand unresolved report; not proof of a firmware defect or of the reporter's diagnosis.** |
| F03 | [NI Community: N6700 active-channel requirement](https://forums.ni.com/t5/Instrument-Control-GPIB-Serial/n6700-query-voltage-current-settings-via-property-mode/td-p/2916518) (10 Jul 2014) | An IVI/LabVIEW voltage/current readback problem was resolved by supplying the correct **active channel** to the driver node. | Require explicit channel addressing and per-channel model/option checks for Arb setup, LIST arrays, readback, triggers and safety controls. Avoid ambient mutable active-channel state in concurrent applications. | **Author-reported resolution, but through IVI rather than direct Python Arb.** |
| F04 | [NI Community: N6700 current-maximum query times out](https://forums.ni.com/t5/Instrument-Control-GPIB-Serial/n6700-query-max-current-is-timing-out/td-p/2916384) (10 Jul 2014) | An IVI driver returned `-101 Invalid character` and `-420 Query UNTERMINATED`; switching to a vendor-supplied Agilent driver resolved the issue. A reply recommended capturing the actual VISA I/O trace and comparing with the manual. | Classify SCPI errors separately from transport timeouts; log bounded, redacted command/reply traces and instrument-driver versions; verify termination, query sequencing and driver compatibility. | **Resolved operator report; not evidence that all NI VISA stacks are defective.** |
| F05 | [NI Community: N6700 protection-fault coupling](https://forums.ni.com/t5/Instrument-Control-GPIB-Serial/SCPI-Command-for-PowerSupply-Agilent-N6700-to-disable-outputs-if/m-p/556805) (26–27 Jul 2007) | The author identified the documented `OUTPut:PROTection:COUPle` setting: with coupling enabled, a protection fault on one channel disables all channels. | Cross-channel protection state and coupling are important in multi-module Arb tests. Read/verify the approved coupling policy before energizing; never clear a protection latch automatically. | **Forum points to a documented instrument feature; verify exact mainframe/firmware behavior.** |
| F06 | [NI Community: HiSLIP "instrument Not Present" — N6700 comment](https://forums.ni.com/t5/Instrument-Control-GPIB-Serial/HiSlip-instrument-Not-Present/m-p/3985054) (6 Nov 2019 comment) | A contributor reported intermittent mDNS resolution for an **N6700**, with connectivity disappearing during runs longer than about ten minutes; using a numeric IP or allowing mDNS fixed that setup. The opening question concerns an N9020A, **not** an N6700. | For long-running Arb sequences, pin and record the approved endpoint, distinguish name-resolution failures from SCPI errors, and enforce fail-safe recovery on lost connection. Prefer a stable validated IP when appropriate. | **Anecdotal N6700 comment in a different instrument's thread; environment-specific.** |

### 12.2 Engineering articles and official cross-checks

| Reference | Directly useful observation | Required design consequence |
| --- | --- | --- |
| [Keysight N6705C User's Guide — Running the Arbs](https://www.keysight.com/us/en/assets/9018-01649/user-manuals/9018-01649.pdf) | The N6705C operating-guide Arb example selects `TRIG:ARB:SOUR BUS`, `IMM` or `EXT`, then enables the matching `VOLT:MODE ARB`/`CURR:MODE ARB` mode. | **Command-tree review gate:** existing driver [voltage-transient contract](VOLTAGE_TRANSIENT_IMPLEMENTATION.md) uses `TRIG:TRAN:SOUR` for N6700-style transient initiation. Do not assume `TRIG:ARB:SOUR` and `TRIG:TRAN:SOUR` are aliases or universally supported; verify both command paths against exact instrument model, firmware, Options 054 and applicable source guide. |
| [Watt's Up? — Extending the usable bandwidth of the DC source](https://wattsup-powersupply.blogspot.com/2014/07/extending-usable-bandwidth-of-dc-source.html) (14 Jul 2014) | The engineer describes increasing waveform amplitude to compensate for measured analog bandwidth roll-off and notes increased current demand from capacitive loads. His numeric example is on an **N7951A APS**, not an N6775A. | Treat programmed points, amplitude and sine frequency as **commands**, not guaranteed DUT-pin results. Independently measure attenuation, phase, overshoot, load capacitance and peak current; do **not** transpose APS compensation factors to N6700 modules. |
| [Watt's Up? — Synchronize Your Measurements with Your List Transients](https://wattsup-powersupply.blogspot.com/search/label/APS) (28 Feb 2015; post under the APS archive) | A Keysight-oriented engineering example configures a finite output LIST and a digitizer with enough samples to cover the transient; common triggering is used to align data. The example uses an **N6762A**. | For a source Arb/LIST + measurement HIL run, arm source and acquisition independently, validate both waiting states, compute sample budget before start, and retain a physically verified common time origin. Do not assume one instrument's acquisition capability applies to every module. |

### 12.3 Recurring patterns and proposed test coverage

| Pattern | Community evidence | Recommended test or contract | Priority |
| --- | --- | --- | --- |
| **P01 — "IDN works" does not establish acquisition/Arb readiness** | F01, F02 | Fake device returns valid `*IDN?` yet rejects unsupported commands, times out on absent response, or returns `+303` for a non-existent acquisition. Assert typed error, bounded timeout and verified safe-state recovery. | High |
| **P02 — Exact channel context must be explicit** | F03, F04 | Two- and four-channel SCPI trace matrix: every channel-capable command carries the intended `(@n)`; unrelated channels remain unaffected; reject wrong-channel token and module/option mismatch. | High |
| **P03 — Status/trigger state machine beats sleep-based timing** | F01, F02, Keysight Arb guide | Test configure → arm → observe WTG → trigger → observe completion → fetch; inject early trigger, missing trigger, timeout and abort; do not perform fetch on stale/aborted acquisition. Distinguish `ACQ` from `TRAN` state machines. | High |
| **P04 — Protocol and transport errors require separate diagnosis** | F01, F04, F06 | Inject `-101`, `-420`, `+303`, socket timeout and name-resolution loss. Preserve exact safe redacted SCPI trace and root exception. No automatic retry of non-idempotent triggers or `OUTP` writes. | High |
| **P05 — Protection coupling changes other modules' behavior** | F05 | On a simulated four-channel mainframe, inject a trip on one channel for coupling OFF/ON; assert only documented channels disable; verify output OFF rather than clearing protection. | High |
| **P06 — Programmed waveform differs from measured waveform** | Keysight bandwidth article | HIL/DSO matrix across frequency, amplitude, source current limit, load capacitance, lead impedance and settling. Record uncertainty and reject limits that cannot be resolved by the scope. | High for qualification |
| **P07 — Shared trigger and acquisition must be synchronized** | Keysight LIST/digitizer article | Mixed transient/acquisition integration test: enough pre/post-trigger samples, independent arming, explicit channel targeting and timing alignment. | High for HIL |

**Clarification on source authority:** P01–P07 are **engineering recommendations derived from multiple observations**, not previously confirmed bugs in this repository. The forums do not establish that a particular driver command is wrong on the user's firmware. Hardware changes or claims of pass/fail must be backed by local SCPI traces, official manual pages and guarded HIL evidence.

### 12.4 Explicitly rejected patterns

- Do **not** copy `*RST`, `OUTP ON`, infinite `ARB:COUN INF`, `INIT:CONT:TRAN ON`, or `OUTP:PROT:CLE` from sample scripts into connection/initialization paths.
- Do **not** treat a timeout as permission to retry an output-energizing/triggering SCPI write: the original command may have executed.
- Do **not** conflate digitizer `INIT:ACQ` / `FETC:ARR` state and Arb `INIT:TRAN` / output-trigger state.
- Do **not** let an acquisition/query failure silently pass a waveform test, claim timing quality from SCPI readbacks alone, or use a forum anecdote to override the manufacturer command reference.

**Search outcome:** Direct community discussions of all N6700-specific **standard waveform shape programming APIs** were sparse; the strongest code examples remain in [section 11](#11-reviewed-external-code-examples-and-references-2026-10-15). The forum evidence is most valuable for safety, transport, readback and test design.

## 13. Third-party software design review: code patterns, pitfalls and solutions (2026-10-15)

**Review method:** inspected the linked **source files**, not merely their READMEs or search snippets; compared their SCPI command examples against the Keysight N6700C/N6705 Programmer's Reference, and compared architectural patterns with the current driver's bounded finite LIST/UDEF implementation. A line of third-party source code is **evidence of what the code attempts**, not evidence that it works on the target mainframe/firmware. **No independent execution, physical HIL or defect report has been established for these third-party projects.**

### 13.1 Reviewed implementations

| ID | Software and pinned source | Concrete implementation pattern | Relevance |
| --- | --- | --- | --- |
| S01 | [SweepMe! N6705 Signal driver — Python](https://github.com/SweepMe/instrument-drivers/blob/f69a9a17cca86ef6d83788634aaedcab3cc0af89/src/Signal-Keysight_N6705/main.py) | Registry/dictionary of seven shapes (sine, step, ramp, staircase, pulse, trapezoid, exponential), each mapping GUI-friendly parameter names to SCPI suffixes; generic parameter writer and waveform-specific calculations. | **High** — scalable shape schema, GUI options and volts/seconds units |
| S02 | [SweepMe! N6705 SMU driver — Python](https://github.com/SweepMe/instrument-drivers/blob/f69a9a17cca86ef6d83788634aaedcab3cc0af89/src/SMU-Keysight_N6705/main.py) | Distinguishes N678x SMU current/voltage compliance from standard power-source settings; generates voltage/current pulse using shared timing; treats synchronized measurement separately. | **High** — capability-specific mode/compliance handling |
| S03 | [pyVirtualLab N6705C Signals — Python](https://github.com/Ben3094/pyVirtualLab/blob/221ba738ed9968ede3c44524f0504a8942ddd479/pyVirtualLab/Instruments/Keysight/KeysightN6705C/Signals.py) | Object model with SignalType enum and Step/Ramp/Staircase/Sinusoid/Pulse/Trapezoid/Exponential/CDW/Sequence/UDEF classes, query/set property decorators and channel-scoped transport. | **High** — typed object decomposition, but implementation is incomplete/uneven |
| S04 | [KK_Lab N6705C — Python](https://github.com/KversKv/KK_Lab/blob/ec7e800cedbc939a49bb4dc71602c3bc110f2929/instruments/power/keysight/n6705c.py) | Dedicated methods for step/staircase/pulse/current slew, explicit channel numbers and pulse frequency setting. | **High** — shape-specific method signatures and coupled pulse-frequency behavior |
| S05 | [MrBuslic SERVER — C++ pulse configuration](https://github.com/MrBuslic/SERVER/blob/a797986e16d07bece03aeacbedecc65711b410b5/src/Devices/PUOP/PowerSource/PSModule.cpp) | Constructs pulse timing fractions from requested frequency, programs voltage/current pulse parameters, then initiates a BUS-triggered waveform. | **Medium** — deterministic timing plan concept; global-trigger and infinite-loop hazards |
| S06 | [tmbinc/random — Agilent n67xx notes](https://github.com/tmbinc/random/blob/81459d9bd0678243f8971a12b784327b55b0d5bb/agilent/n67xx/README.md) | Contrasts repeated host-side voltage writes with the module's stored waveform/LIST mechanism and discusses model-dependent analog slew/bandwidth. | **Medium** — architectural caution; not an Arb driver |

### 13.2 Source-level findings with fixes

Severity assesses **risk if the pattern were copied into this driver's safety-sensitive API**, not severity of a defect in the original author's intended application.

| Finding | Priority | Source observation | Problem/risk when reused | Required design solution |
| --- | --- | --- | --- | --- |
| **TP01 — Reset/energize hidden in lifecycle** | **Blocker** | S01 \`initialize()\` issues \`*RST\`; \`poweron()\` selects Arb, \`ARB:COUNT INF\`, \`OUTP ON\`, and \`INIT:TRAN\`. S02 initializes with \`*RST\` and starts pulse output in \`poweron()\`. | A shared mainframe may have other active channels. Resetting or energizing as an implicit setup side effect can alter unrelated DUTs; infinite generation defeats a bounded test run. | Connection/initialization must be **read-only**. Separate \`configure_arb(...)\` (output verified OFF) from explicit, approved output enable/arm/trigger; default to **finite repetitions and bounded duration**. |
| **TP02 — Dynamic parameter registry without safety schema** | **High** | S01 stores shape metadata as \`{"command": "TOP:TIME", "unit": "s"}\` and writes raw values via \`set_parameter\`. | The registry is extensible, but string-only metadata cannot enforce per-module amplitude/compliance, finite inputs, waveform extrema or shape-specific timing relationships. | Preserve the **declarative shape registry** but bind each field to a typed validator, unit, range rule, paired readback path, module gating and output-state prerequisite; validate the **complete planned waveform before writing**. |
| **TP03 — Reconfigure while output is live** | **High** | S01 \`apply()\` sends \`ABORT:TRAN; *WAI\`, rewrites waveform parameters and sends \`INIT:TRAN\`; S02 uses the same pattern for pulse sweeps. | \`*WAI\` waits for device commands but does **not** prove analog output OFF, transient idle, restored setpoint, absent protection, or successful SCPI parameter acceptance. A partially updated profile could be rearmed. | Reject ordinary configuration while output ON. A separately reviewed **live-update** feature would require its own explicit authorization, atomic profile/ownership design, state readbacks, recovery and HIL qualification. |
| **TP04 — Shape type silently hardcoded** | **High** | S03 has \`SignalType.Voltage\` and \`SignalType.Current\`, but \`UserDefinedSignal\` binds its \`BOST\`, \`DWEL\` and \`LEV\` paths to \`SOUR:ARB:CURR:UDEF:...\` regardless of selected type. | Voltage UDEF requests through this abstraction could target the **current** Arb command family, depending on caller usage. | Derive \`ARB:{type}:UDEF:* \` from an allowlisted enum and test **all supported type × shape × channel** combinations. Never interpolate untrusted SCPI tokens. |
| **TP05 — Exact-float readback comparisons** | **Medium** | S03 pulse properties set a value, query it and compare with Python \`!=\`; UDEF list properties do exact list equality. | Firmware may quantize dwell, timing or levels. Binary floating-point representation and instrument resolution can make exact equality reject an otherwise valid profile; conversely, plain readback equality cannot prove analog accuracy. | Normalize returned settings to the documented instrument resolution, compare within a **documented programming tolerance**, retain requested and actual values, and require independent DSO characterization for analog claims. |
| **TP06 — Abort command accidentally written as query** | **High** | S04 \`arb_off()\` calls \`write("ABOR:TRAN? (@n)")\`. | Keysight's N6705 Programmer's Reference defines \`ABORt:TRANsient (@<chanlist>)\` as a **command**, with no documented query syntax. The \`?\` spelling is inconsistent with the reference; using \`write()\` on a real query can also desynchronize the response stream. | Emit \`ABOR:TRAN (@n)\` **without \`?\`**, disable continuous transient initiation, then query \`STAT:OPER:COND? (@n)\` to confirm idle. Fault-inject invalid-query and missing-response cases. |
| **TP07 — Pulse frequency is a coupled setter** | **High** | S04 programs \`ARB:VOLT:PULS:FREQ\` after writing start/top/end times; S05 instead derives those times from frequency explicitly. | The pulse-frequency SCPI command **is valid** (Keysight N6700C Operating and Service Guide, SCPI reference), but it is **coupled** to pulse time commands; command order can change the effective timing. | Provide **one authoritative timing mode** (explicit times **or** frequency/width specification), derive a consistent validated timing plan, program in documented order and query frequency/times afterward. Reject mutually inconsistent timing arguments. |
| **TP08 — Mainframe-wide trigger in multi-channel system** | **Blocker** | S04 has \`trg()\` issuing global \`*TRG\`; S05 initiates with \`*TRG\` after pulse setup. | \`*TRG\` may trigger **every other armed BUS-trigger channel**, not merely the intended Arb. | Prefer scoped \`TRIG:TRAN (@n)\` where the confirmed model/firmware supports it; restrict global triggers to an explicitly reviewed synchronized multi-channel plan and test cross-channel isolation. |
| **TP09 — Indefinite waveform count** | **High** | S01/S02/S05 use \`ARB:COUN INF\` for repetitive output. | Legitimate for an interactive signal generator, but **not safe by default** in a DUT verification driver if process abort, network loss or watchdog failure occurs. | Finite count/duration limits; infinite mode, if ever added, requires dedicated operator confirmation, hardware cutoff/watchdog and independently verified stop/fault recovery. |
| **TP10 — Missing transport/error-policy separation** | **High** | S04 sends shape commands as a sequence of \`instr.write()\` calls and exposes broad SCPI methods; S01 uses \`parameter ...; *WAI\` and reinitializes without an explicit per-write error/readback transaction in the reviewed portion. | If a mid-profile write is rejected, later writes can leave stale/inconsistent settings. SCPI \`-113\` unknown command, transport timeout and protection faults require different recovery. | Use one serialized configure transaction, bounded write/query timeouts, per-shape readback, deferred error-queue examination and **fail-closed UNKNOWN state** on uncertain completion. Never automatically retry an ambiguous non-idempotent trigger or output command. |
| **TP11 — Host loop is not hardware timing** | **Medium** | S06 describes repeated host-side \`set voltage\` writes as an Arb alternative; it separately notes hardware LIST capability. | Network/USB transport, Python scheduling and instrument parsing jitter make per-point host writes unsuitable for precisely timed dips, pulses and waveform edges. | Use hardware-timed LIST/ARB whenever DUT acceptance depends on dwell/edge timing; use host writes only for explicitly non-time-critical control, with performance evidence. |
| **TP12 — Per-module compliance affects valid shape envelopes** | **High** | S02 handles N678x current limit differently from ordinary source modules and selects different voltage/current compliance paths. | Copying one generic compliance command to every source/SMU could silently reject programming or fail to limit energy as intended. | Define a model/option-specific capability and compliance matrix; validate **both** Arb extrema and limiting quadrants, and verify readback of actual limits before output enable. |

**Discipline:** TP04 is a directly visible source/design mismatch; TP06 conflicts with the cited manufacturer command syntax. The remaining items are mostly **integration and safety risks**, not claims of observed failures on a real N6700 or negative assertions about the third-party maintainers' target use cases.

### 13.3 Positive reusable architecture patterns

1. **Declarative waveform descriptors (S01)** — represent shape, supported parameters, SCPI setter/query, label and unit in a registry so CLI/GUI/help can be generated from one contract. **Improve:** immutable typed parameter specifications, model-capability flags and validation, rather than raw strings passed to SCPI.
2. **Mode-aware power-source and SMU controls (S02)** — retain per-module differences for compliance, sensing and current/voltage priority. **Improve:** explicit capability objects from discovered channel identity/options and negative tests for wrong family.
3. **Shape classes and channel-scoped I/O (S03)** — reuse \`PulseArb\`, \`RampArb\`, \`StaircaseArb\` and \`UserDefinedArb\` across voltage/current types. **Improve:** ensure all commands derive from actual Arb type; avoid exact equality after quantized readback.
4. **Shape-specific method signatures (S04)** — simplify operator code. **Improve:** reject conflicting frequency and individual timing parameters rather than writing both without a documented precedence rule.
5. **Compute timing instead of relying on a host loop (S05/S06)** — compile requested frequency/duty cycle into a finite hardware profile, and verify the programmed timing **and** physical waveform.

### 13.4 Implementation model and example

**Illustrative typed planning model; NOT a tested public API or executable SCPI runner:**

~~~python
from dataclasses import dataclass

@dataclass(frozen=True)
class VoltagePulseArb:
    base_v: float
    top_v: float
    start_s: float
    top_s: float
    end_s: float
    repeat_count: int = 1

def validate_pulse(plan: VoltagePulseArb, *, min_v: float, max_v: float) -> None:
    import math
    values = (plan.base_v, plan.top_v, plan.start_s, plan.top_s, plan.end_s)
    if not all(math.isfinite(v) for v in values):
        raise ValueError("non-finite Arb parameter")
    if not (min_v <= min(plan.base_v, plan.top_v)
            and max(plan.base_v, plan.top_v) <= max_v):
        raise ValueError("pulse exceeds reviewed output envelope")
    if min(plan.start_s, plan.top_s, plan.end_s) < 0 or plan.top_s <= 0:
        raise ValueError("invalid pulse timing")
    if not 1 <= plan.repeat_count <= 1000:
        raise ValueError("repeat count exceeds application limit")
~~~

The **real driver** must additionally enforce module/Option 054 capabilities, OVP/current/power/energy limits, instrument timing resolution and point budget, output OFF, channel ownership, finite maximum **total** duration, readback, error queue, state tokens, abort and physical qualification. The example's application repeat bound of 1000 is illustrative and **not** a manufacturer's ARB count limit.

**Recommended command planner contract:**

~~~text
DISCOVER(model, options, firmware, output_state, limits)
  -> VALIDATE(type, shape, all extrema, timing, repeats, DUT envelope)
  -> PREPARE(output OFF; abort stale transient; continuous OFF)
  -> PROGRAM(type + shape + shape fields + finite count + TERM:LAST OFF)
  -> READBACK(fields, effective timing, SCPI errors)
  -> READY_TO_ARM (configuration alone never enables output)
  -> [separate approved output enable]
  -> ARM -> WAIT WTG -> CHANNEL TRIGGER -> WAIT COMPLETE
  -> VERIFY -> ABORT/CLEANUP -> OUTPUT OFF
  -> on any unverified step: UNKNOWN + operator-required recovery
~~~

### 13.5 Tests and implementation priorities

| PR proposal | Tests to add | Acceptance gate |
| --- | --- | --- |
| **A — Command truth and capability matrix** | Golden SCPI vectors per shape, \`ARB:VOLT:PULS:FREQ\` coupling/readbacks, unsupported module/option, \`ABOR:TRAN?\` rejection, \`TRIG:ARB:SOUR\` versus \`TRIG:TRAN:SOUR\` on documented models | No undocumented command assumed supported; no side effects during identity/discovery |
| **B — Typed standard voltage shapes** | Table-driven \`shape × parameter × channel\`, limits/extrema, zero/negative/nonfinite timing, output-ON refusal, unknown shape, partial write rollback | Offline pass with explicit readback; not energized by configure |
| **C — Current shapes and SMU compliance** | Voltage/current priority × model, OVP/OCP/quadrant behavior, wrong-current/voltage path, channel isolation | Verified safe limits before enable |
| **D — Controlled runtime/trigger lifecycle** | Channel A armed while B idle, global \`*TRG\` refused by default, early/missed trigger, abort timeout, reconnect, stale generation token, continuous mode accidentally retained | WTG/active status and safe OFF independently verified |
| **E — DSO hardware qualification** | Programmed vs measured pulse amplitude, width, frequency coupling, edges, overshoot, latency/jitter; at least one approved N677x/Option 054 + appropriate SMU matrix | Independently signed-off HIL evidence and uncertainty guard bands |

**No runtime code was changed or executed by this research.** This section supplies issue hypotheses, suggested design constraints and test priorities; it is not an assertion that the repository currently has TP01–TP12.

### 13.6 Manufacturer cross-checks and source provenance

- [Keysight N6700C Operating and Service Guide](https://go.keysight.com/us/en/assets/9018-04957/service-manuals/9018-04957.pdf): **ARB:VOLT:PULS:FREQ** is documented and coupled to pulse timing; **ABOR:TRAN** is a command. Follow the installed module's option and firmware applicability tables.
- [Keysight N6705 Programmer's Reference](https://www.keysight.com/zz/en/assets/9018-03616/programming-guides/9018-03616.pdf): \`ABORt:TRANsient (@<chanlist>)\` cancels pending actions but **does not turn off \`INIT:CONT:TRAN ON\`**. An abort alone is insufficient cleanup.
- [Keysight N6700 modular power system official example programs](https://www.keysight.com/hk/en/lib/software-detail/programming-examples/example-programs-for-the-keysight-n6700-modular-power-system-family-of-products-394142.html): downloadable \`EZSample\`, \`Sequence\`, \`List\`, \`Digitizer\` examples (older N6700B/N6705A generations; **Sequence is sequenced power-on, not necessarily Arb sequence**). Useful as **manufacturer-style I/O examples**, with version/model restrictions and historical dependencies.
- Community code referenced in [§11](#11-reviewed-external-code-examples-and-references-2026-10-15) and engineering discussion evidence in [§12](#12-forum-and-stack-overflow-research-2026-10-15) remain supplementary.

**External software licenses:** do not copy third-party code verbatim into this repository without checking licensing and attribution. The review records concise behavioral observations and original improvement suggestions.

## 14. Expanded cross-source analysis and prioritized findings (v0.7.5)

See [**ARB_SOURCE_CODE_ANALYSIS.md**](ARB_SOURCE_CODE_ANALYSIS.md) for line-targeted analysis of ten third-party source files, five static findings in the current driver (including the N6700C Option 054 capability mismatch), confirmed vendor SCPI semantics, proposed safe APIs, and a 14-case regression/HIL matrix. This is an engineering analysis only; no implementation or physical qualification is implied.
