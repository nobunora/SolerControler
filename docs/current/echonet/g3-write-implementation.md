# G3 — Detailed implementation specification

## Future ownership

Create a dedicated control adapter/service; do not add write methods to the read service. Project-owned command types encode semantic intent (for example a verified charge-power command), not raw EPC/EDT.

## Command transaction

1. Check global write opt-in.
2. Reconfirm target identity and current Set Property Map.
3. Validate command against verified model/firmware contract and numeric/domain range.
4. Encode EDT in one command-specific encoder.
5. Send exactly one SET request.
6. Classify response as accepted/rejected/unknown.
7. Read back the authoritative property/state.
8. Return `APPLIED`, `REJECTED`, `MISMATCH`, or `UNKNOWN`; only `APPLIED` is success.

## Retry semantics

Transport retries are allowed only before a write is sent. Once send outcome is uncertain, reconcile by GET. Never issue a second write solely because the first response timed out.

## Audit

Record timestamp, target EOJ, semantic command name, requested normalized value, outcome, read-back value, and correlation ID. Do not store credentials or unnecessary raw payloads.

## Compatibility

Writable contracts are keyed by evidence, not guessed firmware branching. Unknown firmware/capability drift fails closed.