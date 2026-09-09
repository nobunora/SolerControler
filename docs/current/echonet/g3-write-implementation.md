# G3 — Detailed implementation specification

## Current implementation

`SafeWriteService` is the sole production write-transaction owner. It receives `VerifiedWriteCommand` definitions and calls the gateway `set_properties` primitive exactly once per transaction.

## Transaction

1. Require explicit `enabled=True` supplied by runtime configuration after gate approval.
2. Require the runtime freshness/health interlock to be true; re-check after waiting for the target lock.
3. Validate semantic command name, target, EPC, payload and exactly one read-back expectation.
4. Serialize writes per gateway target.
5. Send one `set_properties` request.
6. Gateway rejection -> REJECTED.
7. SET timeout -> delivery uncertain; issue only live GET reconciliation, never blind retry.
8. Accepted SET -> live GET read-back.
9. Matching read-back -> APPLIED.
10. Accepted SET + mismatch -> MISMATCH.
11. Uncertain SET + nonmatching/unavailable read-back -> UNKNOWN.
12. Append the completed semantic result to the configured audit sink.

`JsonlAuditSink` provides an append-only local audit option without storing credentials. Production retention/rotation policy remains a deployment concern.

## Concrete command definitions

This PR intentionally does not invent RC-307A writable EPC semantics. `VerifiedWriteCommand` is infrastructure, not permission to create arbitrary user input. Concrete constructors such as future battery-mode/charge-target commands must live in a reviewed RC-307A profile module and encode only verified values.

## Still requiring RC-307A evidence

Before automatic control: capture current writable-property evidence and exact value semantics, lock the supported RC-307A/firmware profile, define concrete semantic command constructors, establish production freshness thresholds/audit retention, and perform controlled hardware SET + live GET tests.