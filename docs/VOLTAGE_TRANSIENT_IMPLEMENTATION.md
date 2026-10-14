# Source-voltage LIST/ARB and slew implementation

**Implementation status:** software implemented; simulator/protocol verification required in CI; real N6775A HIL and oscilloscope qualification remain explicit release gates.

## Purpose

Provide typed, bounded APIs for generating source-voltage dips and peaks with N6700 power-supply channels. The feature uses the instrument's hardware-timed output transient system instead of timing ordinary `VOLT` writes from Python.

The first supported scope is:

- programmable voltage slew (`VOLT:SLEW`);
- finite voltage LIST profiles, up to 512 points;
- finite user-defined voltage ARB profiles, up to 511 points;
- BUS or external transient trigger selection;
- explicit configure, arm, ready, trigger, completion and abort lifecycle;
- Python and Robot Framework APIs;
- simulator, contract and guarded real-hardware verification.

Infinite repetition, standard ARB shapes, ARB sequences, file import/export and implicit output enable are deliberately excluded. They can be added only after the bounded implementation is physically qualified.

## Normative references

The repository retains the exact vendor material used for the implementation:

- [N6705 Programmer's Reference extraction](../Keysight_documents/N6775A.pdf.md): `VOLTage:MODE`, `LIST:VOLTage`, `LIST:DWELl`, `LIST:COUNt`, `LIST:STEP`, `LIST:TERMinate:LAST`, `ARB:VOLTage:UDEFined`, `ARB:COUNt`, `ARB:TERMinate:LAST`, `TRIGger:TRANsient:SOURce`, `INITiate:TRANsient`, `TRIGger:TRANsient`, `ABORt:TRANsient`, and `VOLTage:SLEW`.
- [N6705C User's Guide extraction](../Keysight_documents/N6775A_manual2.pd.md): *Advanced Source Settings*, *Generating Arbitrary Waveforms*, *Running the Arbs* and the WTG_transient/TRAN_active status-bit sequence.
- Official Keysight programming guide: https://www.keysight.com/us/en/assets/9018-03616/programming-guides/9018-03616.pdf
- Official Keysight user guide: https://www.keysight.com/us/en/assets/9018-01649/user-manuals/9018-01649.pdf

Important vendor constraints reflected in the API:

- LIST voltage, current, dwell and trigger arrays must have a common length or a singleton length.
- LIST supports at most 512 points and repeat count 1–256.
- User-defined voltage ARB supports at most 511 points.
- General LIST/UDEF dwell range for N6775A is 0–262.144 s, with range-dependent quantization.
- ARB repeat count is finite and at most 16,777,216 in this API.
- The output trigger system must be initiated and report WTG_transient before a trigger; early triggers can be ignored.
- `ABOR:TRAN` returns the trigger system to idle. It does not disable continuous triggering, so cleanup also sends `INIT:CONT:TRAN OFF`.
- `VOLT:SLEW` affects all voltage changes, including output on/off transitions.

## Public Python API

The driver exposes the following methods on `N6700` and corresponding methods on `PowerSupplyChannel`:

```python
set_voltage_slew_rate(channel, rate_v_per_s) -> float
get_voltage_slew_rate(channel) -> float

configure_voltage_list(
    channel,
    levels_v,
    dwell_s,
    *,
    maximum_voltage_v,
    minimum_voltage_v=0.0,
    repeat_count=1,
    step_mode="auto",
    terminate_last=False,
    maximum_duration_s=60.0,
) -> VoltageListConfiguration

configure_voltage_arb(
    channel,
    levels_v,
    dwell_s,
    *,
    maximum_voltage_v,
    minimum_voltage_v=0.0,
    repeat_count=1,
    terminate_last=False,
    maximum_duration_s=60.0,
) -> VoltageArbConfiguration

configure_voltage_transient_trigger(channel, source="bus")
arm_voltage_transient(channel) -> VoltageTransientToken
wait_voltage_transient_ready(channel, token, timeout_s=5.0)
trigger_voltage_transient(channel, token)
wait_voltage_transient_complete(channel, token, timeout_s=10.0)
abort_voltage_transient(channel)
get_voltage_transient_status(channel) -> VoltageTransientStatus
```

### Example: 12 V to 6 V dip for 20 ms

```python
from keysight_n6700 import N6700

with N6700.connect_ethernet("192.168.0.6") as driver:
    channel = 1

    # All configuration happens while output is OFF. The 18 V limit is the
    # reviewed DUT/fixture envelope, not merely the N6775A's hardware rating.
    driver.set_dc_current(1.0, channel)
    driver.set_dc_voltage(12.0, channel)
    actual_slew = driver.set_voltage_slew_rate(channel, "max")
    profile = driver.configure_voltage_list(
        channel,
        levels_v=[12.0, 6.0, 12.0],
        dwell_s=[0.001, 0.020, 0.001],
        minimum_voltage_v=0.0,
        maximum_voltage_v=18.0,
        maximum_duration_s=0.1,
    )
    driver.configure_voltage_transient_trigger(channel, "bus")

    try:
        driver.enable_output(channel)  # separate, explicit energization
        driver.wait_for_voltage_in_range(channel, 11.9, 12.1)
        token = driver.arm_voltage_transient(channel)
        driver.wait_voltage_transient_ready(channel, token)
        driver.trigger_voltage_transient(channel, token)
        status = driver.wait_voltage_transient_complete(channel, token)
        assert status.state == "complete"
    finally:
        driver.abort_voltage_transient(channel)
        driver.disable_output(channel)
        driver.safe_shutdown()
```

For a user-defined ARB, replace `configure_voltage_list` with `configure_voltage_arb`. The safe lifecycle is otherwise identical.

## Safety contract

### Configuration

1. The source channel must advertise voltage LIST/ARB/slew support.
2. LIST and ARB configuration is rejected if the selected output is ON. The check runs before any write, so a refused call never aborts or alters a profile that is already armed or running.
3. Every number must be finite; boolean-as-number, NaN and infinity are rejected except the explicit string `"max"` for slew.
4. Every voltage point must be within caller-supplied `minimum_voltage_v..maximum_voltage_v`.
5. The caller's maximum may not exceed the lower of the selected instrument range and OVP readback.
6. Point count, dwell, repeat count and calculated total duration are bounded before the first profile write.
7. Infinite repeat is unavailable from the typed API.
8. LIST writes an explicit singleton current-limit list and disabled BOST/EOST arrays so stale instrument arrays cannot invalidate the profile or emit unreviewed triggers.
9. Profile arrays and voltage mode are read back before configuration is reported successful.
10. Partial configuration failure sends `ABOR:TRAN`, disables continuous transient initiation, selects FIXED voltage mode, and marks local state unknown.

### Execution

1. Configuration never enables an output.
2. Arming requires a verified profile and an already-enabled output.
3. Each arm returns a generation token. Stale or cross-channel tokens are rejected.
4. Software trigger requires BUS mode and a successful WTG_transient readiness observation.
5. Completion requires both WTG_transient and TRAN_active to be clear after the matching trigger generation.
6. Waits are bounded. Completion timeout attempts `ABOR:TRAN` before returning the timeout.
7. `safe_shutdown()` attempts transient abort before commanding each source output OFF. `ABOR:TRAN` and output-OFF commands remain permitted from any thread during an active digitizer lease; `ABOR:ACQ` stays restricted to the lease owner.

The driver validates instrument and caller-supplied limits. It cannot discover the actual DUT, harness, capacitor energy, relay state or external interlock. The application/fixture remains responsible for those limits.

## SCPI mapping

| Typed operation | Commands/readbacks |
|---|---|
| Set slew | `VOLT:SLEW`, `VOLT:SLEW?` |
| Prepare profile | `ABOR:TRAN`, `INIT:CONT:TRAN OFF`, `VOLT:MODE FIX` |
| LIST arrays | `LIST:VOLT`, `LIST:CURR`, `LIST:DWEL`, `LIST:TOUT:BOST`, `LIST:TOUT:EOST` |
| LIST policy | `LIST:COUN`, `LIST:STEP`, `LIST:TERM:LAST`, `VOLT:MODE LIST` |
| UDEF ARB | `ARB:FUNC:TYPE VOLT`, `ARB:FUNC:SHAP UDEF`, `ARB:VOLT:UDEF:LEV`, `ARB:VOLT:UDEF:DWEL`, `ARB:VOLT:UDEF:BOST` |
| ARB policy | `ARB:COUN`, `ARB:TERM:LAST`, `VOLT:MODE ARB` |
| Trigger source | `TRIG:TRAN:SOUR BUS|EXT` plus readback |
| Arm/readiness | `INIT:TRAN`, `STAT:OPER:COND?` bit 4 / decimal 16 |
| Channel-scoped software trigger | `TRIG:TRAN (@n)` |
| Completion | `STAT:OPER:COND?` bit 6 / decimal 64 and WTG clear |
| Abort | `ABOR:TRAN`, `INIT:CONT:TRAN OFF` |

`TRIG:TRAN (@n)` is used instead of global `*TRG` so the API cannot accidentally trigger other initiated channels in the same mainframe.

## Verification strategy

### 1. Offline unit and simulator tests

Run:

```bash
uv run --extra dev --extra robotframework pytest -q
uv run ruff check src tests adapters scripts
uv run mypy src/keysight_n6700
```

Required cases in `tests/unit/test_voltage_transient.py`:

- numeric and maximum slew round-trip;
- LIST array/dwell readback and finite execution;
- UDEF ARB array/dwell readback and termination behavior;
- output-ON configuration refusal;
- out-of-envelope, non-finite, mismatched dwell, excess duration/count/points rejection;
- stale token rejection;
- WTG readiness and completion status;
- missing trigger timeout followed by verified abort;
- `safe_shutdown()` clearing transient status and disabling output.

Adapter translation tests cover engineering units, comma-separated Robot lists, serialization and generation-token forwarding.

### 2. Protocol conformance

Regenerate and validate the repository-owned artifacts after API changes:

```bash
uv run python scripts/generate_capability_model.py
uv run python scripts/generate_ai_contract.py
uv run python scripts/generate_conformance_inventory.py
uv run --extra dev --extra robotframework pytest tests/conformance tests/capability tests/unit/test_ai_contract.py -q
```

Protocol vectors must prove exact SCPI for slew and abort. LIST/ARB unit tests must inspect the simulator transport history for the ordered multi-command transaction; real-hardware conformance must not trigger a profile without the dedicated energization gate.

### 3. Guarded N6775A protocol HIL

`tests/hardware/test_hardware_voltage_transient.py` is disabled unless the general HIL gate, all electrical parameters and the exact confirmation phrase are supplied. Example only—replace every value with the approved bench limits:

```bash
N6700_HIL_ENABLED=true \
N6700_RESOURCE=192.168.0.6 \
N6700_CONNECTION_TYPE=ethernet \
N6700_HIL_VOLTAGE_TRANSIENT_ENABLED=true \
N6700_HIL_VOLTAGE_TRANSIENT_CHANNEL=1 \
N6700_HIL_VOLTAGE_TRANSIENT_BASELINE_V=12 \
N6700_HIL_VOLTAGE_TRANSIENT_DIP_V=6 \
N6700_HIL_VOLTAGE_TRANSIENT_DWELL_S=0.02 \
N6700_HIL_VOLTAGE_TRANSIENT_CURRENT_LIMIT_A=1 \
N6700_HIL_VOLTAGE_TRANSIENT_MAXIMUM_V=18 \
N6700_HIL_VOLTAGE_TRANSIENT_CONFIRM=GENERATE_REVIEWED_DIP \
uv run --extra dev pytest tests/hardware/test_hardware_voltage_transient.py -v
```

The test refuses a non-N6775A channel, an already-on output, inverted voltage levels, excessive dwell or missing parameters. It verifies setpoint stabilization, WTG, completion, protection status, error queue and final output OFF.

Passing this test is **protocol HIL**, not waveform qualification.

### 4. Oscilloscope qualification

Use an independently calibrated DSO on the N6775A output, with an external trigger or a verified common time origin. Predefine limits before running. For both LIST and UDEF ARB, record at least:

- requested and read-back points/dwells/slew;
- actual baseline, dip/peak voltage and steady portions;
- falling/rising 10–90% transition times;
- dwell measured at threshold crossings;
- trigger latency and jitter over at least 100 repetitions;
- overshoot/undershoot and recovery;
- load current, cable/fixture topology, probe bandwidth/attenuation and calibration;
- mainframe/module model, serial, firmware, driver commit and raw waveform hashes;
- output/protection/error state after every case.

Minimum matrix:

| Axis | Values |
|---|---|
| Mode | LIST, UDEF ARB |
| Edge | dip, peak |
| Slew | one slow characterized value, maximum |
| Trigger | BUS, external |
| Load | no-load/minimum, nominal, reviewed maximum |
| VIN | low, nominal, approved high |
| Dwell | short, nominal, long |
| Failure | missing trigger, abort while waiting, communication loss after arm |

Acceptance must use guard bands that include source accuracy, DSO/probe uncertainty, sample interval and trigger jitter. A waveform that merely appears on screen is not a timing qualification.

## Release gates

- **Software complete:** all offline tests, lint, typing, generated contracts and build pass.
- **Guarded engineering support:** software complete plus the N6775A protocol HIL passes with retained identity and safe-state evidence.
- **Timing-qualified support:** DSO matrix passes predefined uncertainty-aware limits.
- **Unattended-production support:** timing-qualified support plus repeated fault injection, communication-loss recovery, emergency interlock test and an approved reliability soak.

Until the last two gates are complete, documentation and release notes must say “implemented; physical timing qualification pending,” not claim a guaranteed transient amplitude, dwell or edge rate.

## Known limitations and follow-up

- The simulator reproduces command/state behavior, not N6775A analog dynamics.
- The completion decision relies on a generation token, observed WTG before software trigger, and WTG/TRAN_active clear afterward. Very short hardware profiles must still be characterized against a DSO.
- Dwell values are returned by the instrument but may be quantized. Callers must use the returned configuration as the authoritative programmed profile.
- Cross-process controllers are not coordinated. One mainframe must have one controlling process during a transient test.
- External-trigger execution can be armed and observed, but the software-trigger method correctly refuses it. The fixture owns the external event.
- Infinite profiles are intentionally unavailable; a future API needs a separate explicit hazard review and watchdog contract.
- Standard step/ramp/sine/pulse/trapezoid/exponential/constant-dwell ARB shapes and multi-shape sequences remain future work.
