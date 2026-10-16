# Examples

## Python (framework-independent)

- `python/basic_power_supply.py` — configure, enable, and measure one channel against the simulator.
- `python/multi_session.py` — hold two independent sessions in one driver instance.
- `python/ethernet_connection.py` — connect to real hardware over raw TCP/SCPI.
- `python/generator/` — short ARB waveform generator examples (pulse, ramp, sine,
  staircase, trapezoid, custom points, sequences, current ARB). See its
  [README](python/generator/README.md).

Run any of them directly: `python examples/python/basic_power_supply.py`.

## Voltage waveform generator

`scripts/run_voltage_waveform.py` turns the existing bounded voltage-ARB API
into a guarded command-line waveform generator. It previews by default and
does not connect to an instrument until `--execute` is supplied.

```bash
# Preview a 20 ms dip from 12 V to 6 V.
python scripts/run_voltage_waveform.py \
  --maximum-voltage 18 \
  pulse --baseline 12 --level 6 --width 0.02

# Execute it against the simulator.
python scripts/run_voltage_waveform.py \
  --maximum-voltage 18 --current-limit 1 --execute --yes \
  pulse --baseline 12 --level 6 --width 0.02

# Execute it on a real Ethernet-connected mainframe (interactive confirmation).
python scripts/run_voltage_waveform.py \
  --connection-type ethernet --resource 192.168.0.6 --channel 1 \
  --minimum-voltage 0 --maximum-voltage 18 --current-limit 1 --execute \
  pulse --baseline 12 --level 6 --delay 0.01 --width 0.02 --recovery 0.01
```

The common parameters are:

| Parameter | Meaning |
|---|---|
| `--minimum-voltage`, `--maximum-voltage` | Reviewed DUT and fixture safety envelope; the maximum is required and is not merely the module rating. |
| `--current-limit` | Source current limit; required for execution. |
| `--repeat-count` | Finite waveform repetitions. For `sine`, this is the number of cycles. |
| `--maximum-duration` | Hard upper bound for total duration after repetition. |
| `--slew` | Voltage slew rate in V/s, or `max`; this also affects output on/off edges. |
| `--execute` | Opt in to connecting, enabling the output, and triggering. Without it, the command only previews. |

Waveform-specific parameters:

| Waveform | Parameters |
|---|---|
| `pulse` | `--baseline`, dip/peak `--level`, pre-event `--delay`, event `--width`, and post-event `--recovery`. |
| `ramp` | `--start`, `--end`, `--delay`, `--ramp-time`, `--hold`, and staircase resolution `--points`. |
| `sine` | DC `--offset`, positive peak `--amplitude`, `--frequency`, and `--points-per-cycle`. |
| `csv` | `--file` containing `voltage_v,dwell_s` columns, one ARB point per row. |

Pulse, ramp and sine use the N6705 built-in ARB shapes; CSV remains a
user-defined ARB. Their analog shape and bandwidth still depend on the module,
load and wiring. Use an oscilloscope when waveform accuracy matters. ARB
execution is rejected on non-N6705 mainframes before any profile mutation.

## Robot Framework (optional adapter)

- `robot/01_simulator_smoke.robot` — connect, configure, measure, and disconnect using `KeysightN6700Library`.

Requires the `robotframework` extra: `pip install -e ".[robotframework]"`, then
run `robot examples/robot/01_simulator_smoke.robot` from the repository root.
