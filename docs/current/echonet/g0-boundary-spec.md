# G0 — Boundary specification

## Ideal specification

- `pychonet` is replaceable infrastructure, never a domain model.
- One adapter owns all `pychonet` calls and translates library exceptions/data into project-owned types.
- EOJ instance IDs are discovered; only class codes `0x0279` and `0x027D` are required.
- Device property maps are the runtime capability source of truth.
- Read and write paths are separate. G0–G2 are structurally read-only.
- Raw identity (`host`, EOJ, EPC) remains available for diagnosis while normalized state has project-owned names/units.

## Forbidden specification

- Hard-code `027901`/`027D01` as the only valid EOJs.
- Let `energy_plan`, `domain`, dashboard views, or KP-NET code call `pychonet` directly.
- Infer unsupported EPCs from the ECHONET standard instead of the device property map.
- Convert timeout/missing/malformed values to zero.
- Add SET/SETC as a generic raw HTTP/UI endpoint.
- Hide raw protocol identity from logs/errors, or log credentials/secrets.

## Normal flow

`RC-307A -> pychonet -> adapter -> normalized service -> consumer`. The adapter returns typed observations plus capability metadata. Consumers never interpret EDT bytes.

## Abnormal flow

Protocol timeout, unsupported EPC, decode failure, duplicate/missing target EOJ, or library incompatibility is explicit and attributable to the adapter boundary. Last-known values may be retained only with `stale=true`; they are never represented as fresh.

## Exit criteria

Architecture, dependency direction, ownership, read-only boundary, and error semantics are reviewable and covered by G0 tests.