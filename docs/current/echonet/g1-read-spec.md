# G1 — Read-only specification

## Ideal specification

- Prefer configured RC-307A IPv4 unicast discovery; multicast is an explicit fallback, not an assumption.
- Discover EOJ instances dynamically and require exactly one usable `0x0279` and one usable `0x027D` for the initial system profile.
- Fetch property maps before reads and request only gettable EPCs.
- Normalize only EPCs whose semantics are verified; unknown EPCs remain raw/capability metadata, never guessed.
- A snapshot is atomic at service level: timestamp, PV section, battery section, errors.

## Forbidden specification

- Guess EPC meaning, scale, sign, or sentinel handling.
- Read every standard EPC blindly.
- Treat a partial response as a fully healthy snapshot.
- Collapse communication failure, unsupported property, and decode failure into one zero/default.
- Require multicast when a fixed gateway IP is available.

## Normal flow

Configured host -> discover -> select `0x0279xx`/`0x027Dxx` -> property maps -> supported read set -> normalized snapshot. Initial implementation exposes discovery/capabilities safely even before all device-specific EPC mappings are verified.

## Abnormal flow

No gateway: connection error. Missing target class: topology error. Multiple target instances: ambiguity error unless configuration selects one. Unsupported EPC: omitted with capability evidence. Timeout/decode failure: error + stale/missing observation, never fabricated data.

## Exit criteria

Deterministic fake-adapter tests pass; Codex then verifies discovery/property maps against RC-307A without sending write services.