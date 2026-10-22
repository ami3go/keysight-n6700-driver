# Supervised source runs (ARB-R04)

Source LIST/ARB profiles can be configured to repeat without end, and an arm can
be made continuous. Those runs never reach a normal "complete" state, so they
need an owner that can stop them. This page maps where unbounded output is
possible and what the supervisor guarantees.

## Where unbounded output is possible

Three different limits are easy to confuse:

- **Vendor repeat-count limit**: the largest finite count the instrument accepts.
- **One-pass time**: the duration of a single traversal of the profile.
- **Wall-clock runtime**: how long output actually continues. For `'infinite'`
  or continuous arming this is unbounded unless something stops it.

| Mode | Finite repeat limit | `'infinite'` | Continuous arm (`INIT:CONT:TRAN ON`) |
|---|---|---|---|
| LIST (`configure_*_list`) | 1..256 | yes | yes |
| Single ARB: shapes and user-defined (`configure_*_arb`, `*_arb_waveform`) | 1..16,777,216 | yes | yes |
| Constant-dwell ARB | 1..256 | yes | yes |
| Sequence (`sequence_count`, per-step `repeat_count`) | count 1..4096 | yes | yes |
| STEP | not repeated | no | yes |

`maximum_duration_s` bounds **one pass** for an `'infinite'` profile, not the
run. Sequences loaded from a file need `allow_infinite=True` to accept an
infinite count.

## The contract

1. `wait_voltage_transient_complete()` keeps refusing infinite and
   continuous-armed runs. Finite ordinary calls are unchanged.
2. `supervise_voltage_transient(channel, token, *, maximum_runtime_s, ...)` is the
   supported way to run them. `maximum_runtime_s` is mandatory, finite, and at
   most `SUPERVISED_MAXIMUM_RUNTIME_S` (3600 s). Longer soaks must be restarted by
   their owner.
3. Each iteration checks, in order: superseded token, protection fault, output
   dropped out, the caller's `abort_requested` callback, the deadline, then (finite
   non-continuous runs only) completion. It ends at the first that applies:

   | `reason` | Trigger | Transient aborted | Output |
   |---|---|---|---|
   | `completed` | finite, non-continuous run ended cleanly | n/a | left ON |
   | `protection` | protection fault became active | yes | OFF and read back |
   | `output_off` | output dropped out | yes | OFF and read back |
   | `abort_requested` | caller's callback returned true | yes | left as is, unless a fault is found at exit |
   | `deadline` | `maximum_runtime_s` elapsed | yes | OFF and read back (unless `output_off_on_expiry=False` and no fault at exit) |
   | `superseded` | someone else aborted or re-armed | no (would cancel the newer arm) | untouched |

   On `abort_requested` and `deadline` the supervisor re-reads protection status and
   `OUTP?` once more and switches the output OFF if a fault coincided with the stop.
   The generation check and the abort happen under the channel's transient lock, so a
   concurrent re-arm is never cancelled.

4. Output-OFF is commanded and then verified with `OUTP?`. If it still reads ON,
   the call raises `DriverOperationUncertainError` and the channel is `unknown`.
5. Any of these sends one best-effort `INIT:CONT:TRAN OFF`, `ABOR:TRAN` and
   `OUTP OFF`, marks the channel `unknown`, and re-raises the original error with
   the cleanup outcome in `details["supervised_transient_cleanup"]`: a transport
   or SCPI fault while polling, a failed completion check (protection trip or a
   queued SCPI error), and an exception from `abort_requested`. A failed write is
   not proof the output is off. Nothing is retried, and the supervisor never sends
   a trigger.
6. The shared session lock is never held across polls, so other channels keep
   working. Each poll issues three queries (`STAT:OPER:COND?`, `STAT:QUES:COND?`,
   `OUTP?`), so choose `poll_interval_s` with transport load in mind; it is capped
   at `maximum_runtime_s`. Channel-scoped trigger commands only; no mainframe-global
   `*TRG`.

```python
token = driver.arm_voltage_transient(1)
driver.wait_voltage_transient_ready(1, token)
driver.trigger_voltage_transient(1, token)
result = driver.supervise_voltage_transient(
    1, token, maximum_runtime_s=30.0, abort_requested=operator_stop.is_set
)
assert result.output_off_verified is not False
```

## Not covered

- Electronic-load profiles (`configure_load_*`) also accept `'infinite'`. They
  keep the existing refusal in `wait_load_transient_complete()` and
  `abort_load_transient()`, but have no supervisor yet.
- Software supervision is not a physical interlock. It cannot act if the process
  dies; pair long runs with the instrument I/O watchdog
  (`enable_io_watchdog`) and an independent output-off path.
- External-edge capture accuracy is not claimed without instrument evidence.
- Everything here is simulator-verified. No source LIST/ARB has run on the
  bench because no Option 054 module is fitted
  ([ARB_CAPABILITY_MATRIX.md](ARB_CAPABILITY_MATRIX.md)).
