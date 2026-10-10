# N679xA Digitizer HIL and Reference DSO Qualification

**Status: hardware tests NOT EXECUTED by the code change. No new hardware qualification or production GO is claimed.**

This procedure executes PR08 of [the scope remediation plan](N679XA_SCOPE_REMEDIATION_PLAN.md). It separates *protocol reproducibility* (a passive BUS-triggered capture) from *metrological qualification* (an independent, commonly triggered DSO capture). No acceptance result is legitimate without both.

## 1. Preconditions and safety

1. Inspect and approve the physical bench wiring, isolation, supply/load ranges and emergency shutdown path. Confirm N6700 mainframe firmware, N6791A/N6792A model and serial; record DSO model, probe attenuation, bandwidth, calibration date and external shared trigger wiring.
2. A qualified operator approves the JSON profile based on [the example](bench_scope_safety_profile.example.json). The script refuses network activity before it verifies `N6700_SCOPE_HIL_APPROVED=YES_AND_REVIEWED`, operator, approval ID, exact channel/model, and positive current/voltage safety envelope.
3. The capture script *never enables the load input*, commands it OFF, triggers a passive digitizer capture over BUS, and re-queries OUTP OFF during teardown. It does not switch the supply or generate a load transient. A separate, independently approved stimulus is required to test transient accuracy. If the output-OFF query fails, the artifact reports FAILED_UNVERIFIED_SAFE_STATE.
4. Run only with the reviewed physical setup. TCP port 5025 must be reachable; do not put equipment on an unrestricted network. No fake instrument mode is permitted for a HIL qualification artifact.
5. This is a reproducible protocol smoke test, **not a 24/7 safety watchdog**. Hardware disconnects and external safety interlocks are still necessary.

## 2. Guarded passive capture

~~~bash
export N6700_SCOPE_HIL_APPROVED=YES_AND_REVIEWED
python scripts/scope_hil_capture.py \
  --host 192.168.0.6 --channel 2 --operator "Bench operator" \
  --approved-profile docs/bench_scope_safety_profile.example.json \
  --output results/scope_hil/20261010_n6791a_capture
~~~

The example profile is **not approved** (`approved: false`) and must be copied and reviewed before use. This command does not perform a real capture until the operator supplies a valid approved profile and explicit environment variable. The script writes digitizer.csv and comparison_evidence.json with IDN, module identity, interval/offset, format, driver version, SHA256 and safe-state result. Status CAPTURED_PENDING_DSO means a waveform exists, **not** PASS.

## 3. Reference DSO comparison

Capture an independent DSO waveform from the same electrical node as the digitizer, with identical trigger polarity and a **verified common physical trigger time origin**. Export independent reference data as UTF-8 CSV with columns `time_s,value`; do not independently auto-align waveforms in software because that can hide a real trigger delay.

~~~bash
python scripts/scope_compare_reference.py \
  --digitizer results/scope_hil/20261010_n6791a_capture/digitizer.csv \
  --reference-dso results/scope_hil/20261010_n6791a_capture/reference_dso.csv \
  --common-trigger-verified --limit-us 300 --jitter-us 2 \
  --output results/scope_hil/20261010_n6791a_capture/reference_comparison.json
~~~

The comparator uses the first 50% step-crossing sample of both series without post-hoc time shifting, combines each sampling clock's rectangular quantization uncertainty and operator-supplied characterized trigger jitter, and uses k=2 expanded uncertainty. It returns PASS, FAIL, INCONCLUSIVE, or NOT_RESOLVED. A nominal limit shorter than five digitizer sample intervals reports NOT_RESOLVED. The comparator does **not** establish analog bandwidth, recovery-time accuracy or current-load transient metrology by itself.

## 4. Hardware test matrix and sign-off

Record N6791A and N6792A separately, with each actual mainframe model, firmware, channel, and option combination. Include BUS and voltage/current positive/negative level triggers; offset = 0 / -512 / positive; RES20 / RES40 and rounded sample rates; 256 / 1024 / 100000 samples; 2 MiB transport boundary (without energizing a DUT); TCP and VISA when physically available; intentionally missing trigger, cancellation, disconnect, malformed response and recovery. Extend after operator review; do not run stress tests on an unreviewed load.

For independent DSO correlation, record at least 100 repetitions per reviewed transient condition, upward/downward events, slow/fast edges, source voltage/load points, probe uncertainty and instrument measurement ranges. Define accuracy tolerances *before* executing the test; capture raw DSO and digitizer arrays and SHA256 digests. Document timing jitter, analog bandwidth and any unresolved events instead of masking them as PASS.

## 5. Release gate

A code-only CI pass may mark protocol/unit features implemented but never mark F12 fully closed. Production GO requires signed-off reports showing physical output OFF on all exits, no unresolved HIGH/BLOCKER issues, defensible DSO agreement with guard bands and repeated acquisitions, and an extended reviewed reliability soak. Any missing physical evidence produces **GO for guarded engineering only** or **NO-GO for unattended production**, not a fabricated HIL pass.
