# Call/protocol conformance (LPDS-019)

```bash
pytest tests/conformance
```

- `tests/conformance/data/method_inventory.yaml` — every public method on
  `N6700`, generated from the class itself
  (`python scripts/generate_conformance_inventory.py`), never hand-maintained.
- `tests/conformance/data/protocol_vectors.yaml` — 25 realized vectors
  covering connection lifecycle, identity, error queue, voltage/current
  setpoints, output control, measurement, SMU/load mode, and one protocol-
  error path. Each is a genuine transport-boundary assertion: the test reads
  `ScriptedScpiTransport.history`, the actual decoded SCPI text sent, not a
  mock of the driver's own methods.
- `tests/conformance/data/exclusions.yaml` — every other device-facing method,
  each with a specific, reviewed reason (usually: "same protocol shape as an
  already-vectored method" or "planned for the next conformance pass"), per
  LPDS-009 §28.3. `test_every_device_facing_method_has_a_vector_or_exclusion`
  fails the build if a method is added without either.

This is a v0.1.0 conformance pass, not a claim of exhaustive coverage: see
[`review/known_risks.md`](../review/known_risks.md) for what is deferred and
why.
