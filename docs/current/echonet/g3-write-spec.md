# G3 — Safe control specification

## Ideal specification

- Writes are semantic SolarControler commands backed by reviewed RC-307A/ECHONET mappings; arbitrary EPC writes are not a public application API.
- Control is disabled by default and only enabled after G0–G2 pass on the target Raspberry Pi.
- Every command validates target identity, expected firmware/profile evidence, state freshness, value/range, and operation preconditions before SET.
- `set_properties` success is followed by live `get_properties` read-back before returning APPLIED.
- A SET timeout is UNKNOWN, not failed: perform GET reconciliation and never blindly repeat the SET.
- An accepted SET with mismatching read-back is MISMATCH, not success.

## Forbidden specification

- Optimizer/UI calling echonet-list `set_properties` directly.
- Generic `write(epc, raw EDT)` endpoints.
- Automatic replay of unresolved writes after timeout/restart.
- Treating cached list_devices state as authoritative read-back.
- Enabling an unverified command because PropertyDescription says a value is representable.

## Exit criteria

Concrete battery commands are added only after the Raspberry Pi/RC-307A session captures actual writable behavior and reference semantics. Each concrete command requires fixture tests, safe hardware test steps, and read-back evidence.