# G1 — Detailed implementation specification

## Connection

Use `websockets` asyncio client. One `EchonetListGateway` owns a persistent socket, one receive loop, pending request futures keyed by UUID requestId, and a cache populated from `initial_state` plus notifications.

## Requests

- `list_devices`: cache-oriented topology/device retrieval.
- `get_properties`: explicit live device read.
- `set_properties`: transport primitive used only by `SafeWriteService`.
- `get_property_description`: schema/reference metadata only; it does not by itself prove runtime writability.

## Device identity

Parse echonet-list EOJ strings as `<4 hex class>:<decimal instance>`, e.g. `027D:1`. Preserve host and instance. Initial topology requires exactly one `0x0279` and one `0x027D`; ambiguity fails closed.

## Error translation

Connection failure, gateway protocol/schema error, request timeout and command-result error are separate project exceptions. Unknown command_result requestIds are ignored as stale/foreign responses; malformed matched responses fail explicitly.

## Write boundary

The gateway client may implement `set_properties`, but no upper layer may call it directly. `SafeWriteService` owns all production write transactions.