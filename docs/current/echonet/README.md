# ECHONET Gateway — Index

Purpose: make `echonet-list` the dedicated ECHONET Lite process boundary for RC-307A while SolarControler owns semantic decisions, safety interlocks, read-back verification, and audit.

## Target

- CHOSHU Smart PV Multi 9.8 kWh / CB-P98M06A
- Battery: CB-LMP98A2
- PCS: PCS-RP2A (2.04.01)
- Gateway: RC-307A (03.05)
- Observed classes: `0x0279` household solar generation, `0x027D` storage battery
- Reference production host: Raspberry Pi OS; Windows remains supported for development/diagnostics
- ECHONET process: `koizuka/echonet-list`

## Architecture

`energy_plan/runtime -> semantic control/read services -> EchonetListGateway -> WebSocket JSON -> echonet-list -> ECHONET Lite -> RC-307A`.

SolarControler must not own UDP/3610, multicast, ECHONET frame parsing, TID handling, interface monitoring, or device discovery. `echonet-list` must not decide charge targets, optimization policy, write eligibility, or whether a command is safe.

## Reading order / token budget

Read this file, then only the active gate's three files. Do not bulk-read later gates.

| Gate | Exit condition | Documents |
|---|---|---|
| G0 Boundary | ownership, failure containment and security contract accepted | [spec](g0-boundary-spec.md) · [implementation](g0-boundary-implementation.md) · [tests](g0-boundary-tests.md) |
| G1 Gateway | echonet-list protocol/client compatibility proven | [spec](g1-read-spec.md) · [implementation](g1-read-implementation.md) · [tests](g1-read-tests.md) |
| G2 Runtime | long-running read/reconnect behavior proven on target host | [spec](g2-runtime-spec.md) · [implementation](g2-runtime-implementation.md) · [tests](g2-runtime-tests.md) |
| G3 Control | verified semantic writes are fail-closed and reconciled | [spec](g3-write-spec.md) · [implementation](g3-write-implementation.md) · [tests](g3-write-tests.md) |

## Safety invariants

1. Raw arbitrary EPC writes are never exposed to optimizer/UI code.
2. Writes are disabled unless explicitly enabled.
3. A gateway `command_result.success=true` is not final success; required commands use GET read-back.
4. A SET timeout is an unknown outcome. Reconcile by GET before considering another SET.
5. Missing/stale/offline state is never converted to zero or treated as fresh.
6. Unknown RC-307A firmware/property semantics fail closed.

## Source contracts

`echonet-list` WebSocket protocol is the external contract. Pin/test the known compatible version before Raspberry Pi production use. External protocol changes must be caught by contract fixtures before deployment.