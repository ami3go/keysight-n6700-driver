# N679xA electronic-load LIST and ARB profiles

**Status:** implemented, simulator-tested, and protocol-HIL tested on an N6700C
(firmware E.02.09.3271) with an N6791A, with **no source connected** to the
load. A pass shows that commands, readbacks, triggering and cleanup work on
real hardware. It does not show sinking accuracy, edge timing or recovery
behaviour. Those still need a reviewed powered fixture and an independent
oscilloscope.

N6791A/N6792A loads have LIST and ARB built in; unlike the N673x/N674x/N675x/
N677x source modules in an N6700C, they need no Option 054. A load can run a
timed current, voltage, resistance or power profile, timed by the module
itself, and its own digitizer can capture the result.

## API

All methods are on `ElectronicLoadChannel` (`driver.load(n)`), and most have an
`N6700` facade that takes the channel number first.

| Method | Purpose |
| --- | --- |
| `configure_load_list(mode, levels, dwell_s, ...)` | LIST, up to 512 steps, per-step dwell, BOST/EOST trigger outs |
| `configure_load_arb(mode, levels, dwell_s, ...)` | User-defined ARB, up to 511 points |
| `configure_load_constant_dwell_arb(mode, levels, dwell_s, ...)` | Constant-dwell ARB, one shared dwell of 10.24 µs to 0.3 s |
| `configure_load_transient_trigger("bus" \| "external")` | Select what starts the profile |
| `arm_load_transient()` → token | `INIT:TRAN`; the input must already be ON |
| `wait_load_transient_ready(token)` | Wait for WTG_transient |
| `trigger_load_transient(token)` | Channel-scoped `TRIG:TRAN`, never `*TRG` |
| `wait_load_transient_complete(token)` | Wait for idle, then require input ON and no protection fault |
| `abort_load_transient()` / `clear_load_profile()` | Return to idle / also set every mode to FIX |
| `get_load_range(mode)` | Live range in A, V, ohm or W |

`mode` is the load priority mode: `"cc"`, `"cv"`, `"cr"` or `"cp"`. It must
match the live `FUNC` (call `set_load_mode()` first).

```python
load = driver.load(2)
load.set_load_mode("cc")
load.set_load_current(0.05)
driver.configure_load_list(
    2, "cc", [0.05, 0.5, 0.05], [0.1, 0.2, 0.1],
    maximum_level=0.5,
    begin_step_triggers=[True, False, False],  # needed for a transient-triggered capture
)
driver.configure_load_transient_trigger(2, "bus")
load.input_on()
load.configure_digitize("current", points=512, interval_s=1.024e-3)
load.configure_digitize_trigger("transient")
load.arm_digitize()
load.wait_digitize_armed(timeout_s=5.0)
token = driver.arm_load_transient(2)
driver.wait_load_transient_ready(2, token)
driver.trigger_load_transient(2, token)
driver.wait_load_transient_complete(2, token)
load.wait_digitize_complete(timeout_s=5.0)
capture = load.fetch_digitized_array("current")
load.input_off()
driver.clear_load_profile(2)
```

## Safety envelope

Every configuration call:

- refuses before any write if the input is ON, the priority mode differs,
  protection is active, or the approved maximum exceeds the live range;
- requires `0 <= minimum_level < maximum_level`, with every level inside that
  range, and `minimum_level > 0` for resistance (0 ohm is a short circuit);
- for CV/CR/CP, requires `maximum_current_a` to cover the worst-case sinking
  current, computed from instrument-enforced settings only:

| Mode | Worst-case current | Notes |
| --- | --- | --- |
| CC | the levels themselves | `maximum_current_a` is rejected |
| CV | `CURR:LIM` | `CURR:LIM` can only be set in voltage priority (`+315` otherwise) |
| CP | `maximum_level / VOLT:INH:VON` | at the 10 mV default VON, 1 W can mean 100 A, so raise VON first |
| CR | `VOLT:RANG / minimum_level` | e.g. 61.2 V / 0.5 A requires at least 122.4 ohm |

After programming, every level, dwell, count, step mode, termination, trigger
flag, ARB function/shape and all four `*:MODE` states are read back. Any
failure returns every mode to FIX.

## Hardware findings (N6700C E.02.09.3271, N6791A)

These were confirmed on the bench with the input OFF, or ON with nothing
connected:

1. **ARB start trigger is mainframe-wide.** `TRIG:ARB:SOUR` takes no channel
   list. `TRIG:ARB:SOUR? (@2)` returns `-108 Parameter not allowed`, and the
   global query returns `IMM` by default. With `IMM`, an ARB starts as soon as
   it is armed with `INIT:TRAN`, without any trigger. With `BUS`, the
   channel-scoped `TRIG:TRAN (@n)` (or `*TRG`) starts it. It cannot be changed
   while any channel's transient system is initiated (`+308`). The driver
   models this as the `mainframe_global` ARB trigger dialect, sets the
   mainframe source when configuring an ARB trigger, and refuses to arm while
   it reads `IMM`. A LIST uses only its channel's `TRIG:TRAN:SOUR`.
2. **`ARB:RESISTANCE` has no short form.** `ARB:RES:CDW` and `ARB:RES:UDEF:*`
   return `-113 Undefined header`, but `ARB:RESISTANCE:...` works, while
   `ARB:FUNC:TYPE` accepts and returns `RES`. `LIST:RES` works.
3. **`ABOR:TRAN` settles asynchronously.** An armed ARB still reports
   TRAN_active about 200 ms after the abort, and `*:MODE` writes before it is
   idle return `+308`. The driver polls until idle after every abort.
4. **A transient-triggered capture needs a trigger out.**
   `TRIG:ACQ:SOUR TRAN<n>` fires only through `LIST:TOUT:BOST`/`EOST` (or
   `ARB:<type>:UDEF:BOST`) flags. Without one, the digitizer waits forever.
5. **Status latency.** The first `STAT:OPER:COND?` after a trigger or abort
   answers about 200 ms late, so profiles shorter than that are already over
   before the first status read.

## Guarded hardware test

`tests/hardware/test_hardware_load_profiles.py` runs four cases on a real
N679xA:

1. a CC LIST with a transient-triggered digitizer capture;
2. a CC user-defined ARB;
3. a CR constant-dwell ARB;
4. an abort while armed.

It requires the general HIL gate, `N6700_HIL_LOAD_PROFILE_ENABLED=true`,
`..._CHANNEL`, `..._MAX_CURRENT_A` (≤ 1 A), `..._MAX_INPUT_V` and
`N6700_HIL_LOAD_PROFILE_CONFIRM=RUN_REVIEWED_LOAD_PROFILE`. It refuses if the
input is already ON or the measured input voltage exceeds `MAX_INPUT_V`. It
leaves the mainframe-wide `TRIG:ARB:SOUR` at `BUS`.
