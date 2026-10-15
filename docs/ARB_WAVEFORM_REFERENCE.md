# N6700 power-module arbitrary waveform generation (ARB): command and capability reference

**Reference revision:** 1.0 (2026-10-15)  
**Driver baseline:** keysight-n6700-driver v0.7.0; catalog published in documentation release v0.7.1.  
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
| PULSe | PULSe:STARt; PULSe:TOP; PULSe:STARt:TIMe; PULSe:TOP:TIMe; PULSe:END:TIMe | Start/top levels, pulse width and delays |
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

**Change scope:** reference documentation only. No Arb/driver command behavior or qualification status changes in v0.7.1.
