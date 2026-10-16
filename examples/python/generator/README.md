# Waveform generator examples

Small scripts that show the N6705 ARB (arbitrary waveform) features of the
driver. Each one runs against the bundled simulator, so no hardware is needed:

```bash
python examples/python/generator/01_pulse.py
```

| Script | What it shows |
|---|---|
| `01_pulse.py` | Voltage dip 12 V → 6 V → 12 V (`ArbPulse`) |
| `02_ramp.py` | Linear ramp 0 → 10 V (`ArbRamp`) |
| `03_sine.py` | 50 Hz sine ripple, 10 cycles (`ArbSine`, `repeat_count`) |
| `04_staircase_and_trapezoid.py` | `ArbStaircase` and `ArbTrapezoid` |
| `05_user_defined_points.py` | Your own (voltage, dwell) points (`configure_voltage_arb`) |
| `06_computed_waveform.py` | Waveform computed in Python, one shared dwell (`configure_voltage_constant_dwell_arb`) |
| `07_sequence.py` | Several shapes chained into one profile (`ArbSequenceStep`) |
| `08_current_pulse.py` | Current ARB load pulse (`configure_current_arb_waveform`) |

| `gui.py` | Basic Tk window: pick a shape, edit its parameters, press **Run** |

`_play.py` holds the common run step: enable output, arm, trigger, wait for
completion, and always turn the output off afterwards.

Every `configure_*` call takes `maximum_voltage_v` / `maximum_current_a`: the
driver refuses any waveform that leaves that envelope.

## GUI

```bash
python examples/python/generator/gui.py
```

Choose **Simulator** or **Ethernet** (with an IP address), set the channel,
current limit, maximum voltage and repeat count, then pick a shape (pulse,
ramp, sine, staircase, trapezoid) and press **Run**. On Ethernet it asks for
confirmation before enabling the output. Requires Tk (`python3-tk` on
Debian/Ubuntu).

## Real hardware

Replace `N6700.connect_simulated()` with, for example,
`N6700.connect_ethernet("192.168.0.6")`. Before you do, set limits that are
safe for your DUT and wiring and read [docs/safety.md](../../../docs/safety.md).
The driver only runs ARBs on an N6705 mainframe with a module that supports them.
