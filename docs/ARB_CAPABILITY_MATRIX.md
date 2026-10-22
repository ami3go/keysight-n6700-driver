# ARB / LIST capability matrix (ARB-R02, issue #21)

Decision table behind `module_capabilities.classify_module()`. The rule is
**fail closed**: LIST/ARB is advertised only for a reviewed mainframe, a
well-formed firmware identity, a well-formed option inventory and a module
the vendor documents as supporting it. Anything else classifies as
unsupported, and every ARB entry point then raises
`DriverUnsupportedOperationError` before the first mutating SCPI command.

## Vendor evidence

Keysight N6700C Operating and Service Guide, 9018-04957 (text extracted
2026-10-22; printed page numbers):

| Page | Statement |
|---|---|
| 43 | Model Differences: "Arbitrary waveform generation — Option 054" for the N673xB/N674xB/N675xA/N677xA columns; standard for N676xA |
| 46 | "054 High-Speed Test Extensions. Includes digitized output measurements and output list capability. Available for Models N673xB, -4xB, -5xA, -7xA. Included with Models N676xA, N678xA SMU, and N6783A." |
| 34 | SCPI tree: "ARB (ARB commands only on N676xA, N678xA SMU, Option 054)" |
| 37 | SCPI tree: "LIST (LIST commands only on N676xA, N678xA SMU and Option 054)" |
| 102, 106, 107 | Power and resistance programming apply to N679xA only; N678xA SMU and N679xA share the extended current range |

The N6705C statement comes from the N6705 Programmer's Reference bundled in
`Keysight_documents/`; it is **not** transposed to the N6700C.

## Matrix

| Mainframe | Module | Option 054 | LIST / voltage ARB / current ARB / ARB trigger dialect |
|---|---|---|---|
| N6700C | N673xB, N674xB, N675xA, N677xA (3–7) | absent | unsupported |
| N6700C | same | present | supported, `channel_scoped` |
| N6700C | N676xA (precision) | not required | supported, `channel_scoped` |
| N6700C | N6781A-N6786A (SMU, N6783A included) | not required | supported, `channel_scoped` |
| N6700C | N6791A / N6792A (load) | not applicable | LIST only, `mainframe_global`; no source ARB |
| N6705C | any of the above | not required | as above; load LIST via `mainframe_global` |
| N6700A/B, N6701A/C, N6702A/C, N6705A/B, unknown or empty | any | any | **unsupported** (not reviewed; fail closed) |
| any | unknown/misspelled model | any | module type `unknown`, nothing advertised |
| any | any | option list unreadable or malformed | unsupported |
| any | any | firmware empty, `UNKNOWN`, or malformed | unsupported |

The LIST, user-defined ARB, parametric shapes, constant-dwell, sequence and
step families are all gated by the same module flags, so the matrix is the
same for each. `tests/unit/test_arb_capability_matrix.py` asserts that for 14
entry points (voltage and current LIST, ARB, shape, constant-dwell,
sequence, step, and the sequence store/load calls) in five denied contexts no
non-query SCPI command is sent, that ordinary DC commands still work there,
and that the same entry points succeed in a granted context.

## Real hardware cross-check

Read-only query of N6700C `MY56320259`, firmware `E.02.09.3271`, on
2026-10-22:

- channel 1 N6775A: `SYST:CHAN:OPT?` returns no option → LIST/ARB
  unsupported. Consistent with the guide: an N6775A needs Option 054.
  The retained 2026-10-16 run of `test_hardware_voltage_transient.py`
  (`results/scope_hil/20261016-n6775a-transient-pytest.log`) shows the same
  denial end to end: the guarded N6775A voltage-dip test was refused with
  "N6775A does not advertise source LIST/ARB support" before any output was
  enabled. This bench therefore cannot run source LIST/ARB HIL until an
  Option 054 module is fitted.
- channel 2 N6791A: no option needed → load LIST supported
  (`mainframe_global`), confirmed by the load-profile and buck HIL tests.

No bench cell exercises a positive N6700C + Option 054 source LIST/ARB; that
cell rests on the vendor statements above and remains unqualified on
hardware (ARB-R07).

## What this does not cover

- Mainframes other than N6700C and N6705C are deliberately unreviewed, even
  though the guide's mainframe list includes some. Add one only with vendor
  evidence for it.
- Firmware beyond the identity-format check. No firmware-specific dialect
  differences are modeled except the sequence step-index probe.
