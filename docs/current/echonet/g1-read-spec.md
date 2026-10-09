# G1 — Gateway compatibility specification

## Ideal specification

- SolarControler connects to echonet-list via persistent WebSocket and waits for `initial_state` before declaring gateway-ready.
- `requestId` correlates every command_result with exactly one request.
- `list_devices` is the stable cache source; `get_properties` is explicit live I/O.
- Target classes `0279:*` and `027D:*` are discovered dynamically; instance numbers are never hard-coded.
- `device_offline`, `device_online`, `device_added`, and `property_changed` update local gateway state without inventing freshness.
- Gateway error codes remain distinguishable at the project boundary.

## Forbidden specification

- Assume initial_state/list_devices/get_properties have identical freshness semantics.
- Guess undocumented response shapes without fixture coverage.
- Treat WebSocket connected as RC-307A online.
- Hide TARGET_NOT_FOUND, ECHONET_TIMEOUT, ECHONET_DEVICE_ERROR, or communication errors behind one generic zero/default.

## Exit criteria

Protocol-fixture tests pass against the documented echonet-list message format, then Raspberry Pi integration confirms actual RC-307A device identities and live reads.