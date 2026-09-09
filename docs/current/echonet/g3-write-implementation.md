# G3 — Detailed implementation specification

## Current implementation

`SafeWriteService` is the sole production write-transaction owner. It receives `VerifiedWriteCommand` definitions and calls the gateway `set_properties` primitive exactly once per transaction.

## Transaction

1. Require explicit `enabled=True` supplied by runtime configuration after gate approval.
2. Validate semantic command name, target, EPC and exactly one read-back expectation.
3. Send one `set_properties` request.
4. If gateway rejects it, return REJECTED.
5. If SET response times out, classify delivery as uncertain and issue only live GET reconciliation.
6. After accepted SET, issue live GET read-back.
7. Matching read-back -> APPLIED.
8. Accepted SET + mismatch -> MISMATCH.
9. Uncertain SET + nonmatching/unavailable read-back -> UNKNOWN.

## Concrete command definitions

This PR intentionally does not invent RC-307A writable EPC semantics. `VerifiedWriteCommand` is infrastructure, not permission to create arbitrary user input. Concrete constructors such as future battery-mode/charge-target commands must live in a reviewed RC-307A profile module and encode only verified values.

## Required next hardening

Before automatic control: add current Set Property Map evidence from echonet-list/RC-307A, firmware/profile identity checks, command serialization lock, persistent audit record/correlation ID, stale-state precondition checks, and restart reconciliation policy.