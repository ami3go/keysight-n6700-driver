# N6791A internal-buffer load-transient evidence — 2026-10-09

This directory retains the real-hardware evidence for the v0.6.0
level-triggered acquisition qualification.

## Bench

- Mainframe: Keysight N6700C, serial `MY56320259`, firmware `E.02.09.3271`
- Source: channel 1, N6775A
- Electronic load and waveform source: channel 2, N6791A
- Connection: raw Ethernet at `192.168.0.6:5025`
- DUT input points: 8 V, 13.5 V, 18 V
- Load steps: 0.5 A → 2.5 A and 2.5 A → 0.5 A
- Trigger: N6791A measured current crossing 1.5 A, positive/negative slope
- Capture: N6791A measured voltage, 1024 points, 20.48 µs/sample,
  `offset_points=-512`

## Result

All six acquisitions completed. The detected load edge landed at sample
511 or 512 in every waveform. Worst VOUT deviation was 3.3568% / 110.55 mV
(8 V, downward load step); recovery into the downstream test's 2% band was
20.48–40.96 µs. No source or load protection flag became active. Final
independent verification reported source output OFF, load input OFF, and an
empty SCPI error queue.

The downstream acceptance limits were disabled during characterization.
The captured values nevertheless fall inside the stored provisional limits
of 10% maximum deviation and 20 ms maximum recovery time.

## Files

- `load_transient_summary.json`: configuration, identities, steady-state
  readings, derived metrics, and final safety state.
- `load_transient_*V_{up,down}.csv`: raw trigger-relative time and VOUT
  samples for each case.
- `validation_harness.py`: exact guarded harness executed for this evidence.
  It imports the downstream `power_supply_test` safety envelope and analysis
  helpers from the sibling checkout used on the bench.

## Real-firmware sequencing findings

1. Configure CC priority before arming acquisition. Sending `FUNC CURR`
   while acquisition is active is rejected with SCPI error 315.
2. Fetch the array immediately after acquisition completes. A later
   `MEASure` command starts another acquisition and overwrites the triggered
   buffer; this was reproduced before the final matrix was run.

Every energized attempt used `finally`-guarded shutdown. The two preliminary
sequencing failures and the final matrix all ended with the source and load
confirmed off.
