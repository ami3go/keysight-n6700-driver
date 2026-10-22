# ARB protocol and parameter coverage (ARB-R05)

What the simulator-based suite proves about the source ARB/LIST command grammar,
parameter limits and failure handling, and what it does not. Everything here is
simulator evidence; none of it is measurement accuracy or hardware qualification.

## Layers

| Layer | File | What it pins |
|---|---|---|
| Golden SCPI vectors | `tests/conformance/data/arb_golden_vectors.yaml`, `test_arb_golden_vectors.py` | The exact ordered mutating commands for 27 driver calls across every family |
| Independent oracle | `tests/conformance/arb_vectors.py` | Every emitted header must exist in the N6705 Programmer's Reference command tree (`Keysight_documents/N6775A.pdf.md`), so a vector cannot just snapshot a driver typo |
| Boundary matrix | `tests/unit/test_arb_protocol_matrix.py` | Accept/reject at each documented limit; a rejected request sends nothing |
| Fault matrix | same file, generated | For every readback a configuration issues: a malformed reply, a mismatching reply and a timeout |
| Not-run register | same file | Combinations the simulator cannot judge, reported as skips |

## Golden vectors (27)

| Family | Vectors |
|---|---|
| Parametric shapes (voltage) | step, ramp, staircase, sine, pulse, trapezoid, exponential; repeat 3; infinite with `TERM:LAST ON` |
| Parametric shapes (current) | sine, trapezoid |
| User-defined | voltage, voltage with begin-of-step triggers, current |
| LIST | voltage, voltage once-mode with begin/end triggers, current |
| Constant dwell | voltage, current |
| Sequences | voltage with 0-based and 1-based firmware step numbering, current |
| Sequence files | `MMEM:STOR:ARB:SEQ`, `MMEM:LOAD:ARB:SEQ` |
| STEP mode | voltage, voltage with trigger output, current |

Each vector is also checked to: start with `INIT:CONT:TRAN OFF`, `ABOR:TRAN`, `VOLT:MODE FIX`,
`CURR:MODE FIX`; end with the mode switch that arms the profile; address only the selected
channel; and never contain `OUTP`, `INIT:TRAN`, `TRIG:`, `*TRG` or `*RST`. Configuration
therefore cannot energize, initiate or trigger.

## Boundaries, from the vendor parameter tables

| Parameter | Vendor range | Driver |
|---|---|---|
| Every shape time (start/rise/top/fall/end/time constant) | 0 - 262.144 s | same; NaN, inf, negative rejected |
| Sine frequency | 3.8147E-5 - 1.0E4 Hz | same |
| Staircase `NSTeps` | 0 - 500 | **1 - 500** (0 would program a staircase with no steps; deliberately stricter) |
| `ARB:COUNt` single Arb | 1 - 16,777,216, `INFinity` | same |
| `ARB:COUNt` constant dwell | 1 - 256 | same |
| `LIST:COUNt` | 1 - 256 | same |
| `ARB:SEQuence:COUNt` | 1 - 4096 | same, but **`'infinite'` is refused** through the normal API (a loaded file needs `allow_infinite=True`) |
| User-defined points | up to 511 | same |
| LIST points | up to 512 | same |
| Constant-dwell points | up to 65,535 | same (the 64 KiB ASCII cap is tested separately in `test_arb_cdw.py`) |
| Sequence steps | 1 - 100 | same |
| UDEF/LIST dwell | 0 - 262.144 s | same (268.435 s on N678xA, in `test_source_waveforms.py`) |
| Constant-dwell dwell | 10.24 us - 0.30 s | same |
| Level | caller envelope, module range | at the envelope accepted, beyond rejected; negative on a unipolar supply rejected |

## Fault matrix (759 generated cases)

For each golden vector the test finds the parameter readbacks the call issues (for example
`ARB:VOLT:SIN:AMPL?`, `ARB:COUN?`, `VOLT:MODE?`), then injects each of malformed text, a clearly
different value, and a timeout at that one query. For every case it requires: a `DriverError`;
channel state `unknown` with no stored configuration; the four cleanup commands sent after the
failure point; the failing query asked exactly once (no retry); the profile never armed after the
failure; no `OUTP ON`; and the output still OFF.

Mutation checks used to confirm the matrix has teeth (each is caught): dropping `ABOR:TRAN` from
failure cleanup, widening the readback tolerance, an off-by-one single-Arb repeat maximum, a
300 s time ceiling, and a misspelled sine-frequency mnemonic.

## Covered elsewhere (unchanged by R05)

Level extrema and duration bounds (`test_source_waveforms.py`), dwell quantization
(`test_voltage_transient.py`), SMU priority and compliance, ordinary-supply current-limit
modulation (`test_current_arb.py`), CDW transfer limits and cleanup (`test_arb_cdw.py`), sequence
point budget, quality and interrupted upload (`test_arb_sequence.py`), trigger dialects and
status-bit handling (`test_source_waveforms.py`, `test_voltage_transient.py`), and lease/fault
recovery (DRV-14).

## Not run

Reported as skips (`NOT_RUN`), not passes:

- source LIST/ARB analog output: no Option 054 module on the bench (ARB-R07)
- N6784A bipolar voltage Arb and SMU negative-current quadrants: no N678xA installed (issue #34)
- external-trigger edge capture accuracy: needs an independent DSO (ARB-R07)
- continuous-initiation abort, sequence step numbering and the 65,535-point constant-dwell
  transfer on real firmware: need an Option 054 module (ARB-R07)
- dwell quantization beyond the documented half-quantum rule: instrument specific
