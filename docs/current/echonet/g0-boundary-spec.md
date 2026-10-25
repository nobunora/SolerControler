# G0 — Boundary specification

## Ideal specification

- `echonet-list` is the sole owner of ECHONET Lite transport/device-discovery mechanics.
- SolarControler owns semantic energy decisions, write eligibility, stale/offline policy, reconciliation, and audit.
- The boundary is WebSocket JSON with requestId correlation and explicit error codes.
- Gateway replacement must not change optimizer/domain contracts.
- Raspberry Pi process separation is treated as fault containment, not accidental complexity.

## Forbidden specification

- Reimplement UDP 3610, multicast discovery, TID/frame parsing, or ECHONET retry machinery inside SolarControler.
- Let `energy_plan`, `domain`, dashboard, or KP-NET code send `set_properties` directly.
- Treat gateway availability as device availability.
- Treat `command_result.success=true` as proof that the physical target now has the requested state.
- Auto-retry a write whose delivery outcome is uncertain.
- Expose arbitrary EPC/EDT write APIs to upper layers.

## Normal flow

`optimizer -> semantic command -> safety service -> EchonetListGateway -> echonet-list -> RC-307A`; reads return through the same boundary and are normalized above the gateway client.

## Abnormal flow

Gateway disconnect, malformed JSON, request timeout, device timeout, target-not-found, device error, offline notification and schema drift are distinct failures. Unknown state fails closed.

## Exit criteria

Ownership, dependency direction, failure containment and write-safety invariants are covered by static/unit tests and approved before hardware control work.