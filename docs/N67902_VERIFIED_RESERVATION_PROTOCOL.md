# N679-02: verified digitizer resource protocol (offline-only)

## Manufacturer evidence and limits

Keysight's N6700 Programmer's Reference (publication N6700-90902, *SENSe:SWEep:POINts*) defines `SENS:SWE:POIN?` as the number of **configured** acquisition points, not the number of **unallocated** points. The N6705C Operating and Service Guide (p. 214, *Adjust Measurement Sample Rate*) documents a 512-Ki aggregate sample budget shared between enabled measurement parameters. Neither reference establishes an atomic, queryable instrument-side free-point reservation API.

References:

- https://www.keysight.com/us/en/assets/9018-03617/programming-guides/9018-03617.pdf
- https://www.keysight.com/tz/en/assets/9018-04227/service-manuals/9018-04227.pdf

**No driver should claim that `524288 - sum(known allocations)` is an instrument-verified free-point reading.** It is at most a local estimate and can be invalidated by external controllers, modules with undocumented behavior, and concurrent reconfiguration.

## Public APIs

- `N6700.get_verified_digitize_available_points(channel, alias=None) -> int` **fails closed** with `DriverUnsupportedOperationError` on known N679xA configurations. It does not invent a value.
- `N6700.configure_digitize_verified(channel, function, *, points, interval_s, offset_points=0, alias=None) -> DigitizeTiming` is the conservative alternative. It issues the existing N679xA measurement configuration, checks exact instrument point/offset and effective interval readbacks, verifies that no known in-process competing allocation exceeds the documented aggregate limit, and checks the error queue. It holds the shared-session operation lock throughout; after return the ordinary acquisition lease remains with the originating thread/channel until verified abort or fully validated fetch.

`DigitizeTiming.points` is the instrument-reported exact configured count, **not** currently available/free points. `actual_interval_s` is the instrument-reported effective sweep interval; it is not independent timebase calibration. The returned value is evidence from configuration completion, not a persistent authorization token.

## Ownership, generations, invalidation

Only one channel/thread holds the in-process mainframe lease per connection alias. `configure_digitize_verified` atomically spans parameter writes and readback with respect to calls through the **same N6700 instance**. `arm_digitize` advances acquisition generation; previous buffer data is invalidated when a new acquisition is armed. `abort_digitize` or `fetch_digitized_array` releases ownership only on verified success.

A failed/malformed configuration retains uncertain acquisition state and lease until an explicit verified abort; a terminally faulted transport may invalidate host-side ownership only under the separate DRV-14 recovery contract. Neither implies instrument idle or electrical output safe-OFF. After abort, reset, session replacement, transport fault, or competing reconfiguration a previous `DigitizeTiming` must not be reused as proof of current allocation.

**Excluded:** cross-process SCPI arbitration, external controllers, guaranteed available-memory reporting, per-model N6791A/N6792A physical qualification, and DUT PASS/FAIL.

## Downstream N679-03 decision

Automatic "maximum *free* points" selection stays disabled without instrument-verified free-capacity semantics. An operator/application may request an exact depth through `configure_digitize_verified` and verify the returned requested/actual timing. Failure must be recorded and must never silently reduce requested depth. Preserve prior fixed/custom modes. Any usage requires a reviewed downstream pin update and separate offline and physical qualification gates.

No instruments were connected or energized by this change.
