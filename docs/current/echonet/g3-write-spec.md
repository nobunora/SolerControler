# G3 — Write-control specification

## Ideal specification

Write control is a separate, opt-in capability added only after G0–G2 pass. Every writable command is a named domain action backed by a verified EPC contract, Set Property Map evidence, value/range validation, SET response handling, GET read-back, and audit result.

## Forbidden specification

- Generic `set_epc(epc, bytes)` exposed to API/UI/business logic.
- Writes enabled by default.
- Writing an EPC merely because the standard marks it writable.
- Retry of non-idempotent/unknown-outcome writes without reconciliation.
- Treating SET acknowledgement alone as success.
- Control when device identity/firmware/capability assumptions are unverified.

## Normal flow

Explicit enable -> validate target identity/capability -> validate requested domain value -> SET -> response -> GET read-back -> compare -> success audit.

## Abnormal flow

Unsupported property/range: reject before I/O. Timeout after SET: outcome UNKNOWN, perform read-only reconciliation before any retry. Read-back mismatch: failure, no blind retry. Device topology/firmware drift: control disabled until revalidated.

## Exit criteria

No write implementation is authorized by this PR. G3 requires a separate explicit review after real RC-307A writable maps and exact ECHONET property semantics are captured.