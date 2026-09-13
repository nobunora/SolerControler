# G2 — Detailed runtime implementation specification

## Process model

On Raspberry Pi, run echonet-list and SolarControler as separate systemd units. SolarControler must tolerate echonet-list restarts after startup. Do not merge the two processes merely to reduce service count.

## Implemented SolarControler runtime owner

`app/echonet/runtime.py::GatewayRuntime` owns exactly one gateway lifecycle. It:

- connects and requires successful `list_devices` before ONLINE
- performs periodic read-only health requests
- marks write readiness false unless state is ONLINE and freshness is within `stale_after`
- closes a failed connection before reconnecting
- reconnects with capped exponential backoff
- never replays a write command during reconnect
- performs cancellation-safe shutdown

The WebSocket adapter itself invalidates connection state and fails pending request futures if its receiver loop dies.

## Still deferred to deployment validation

Backoff jitter, structured production logging, systemd unit tuning, process watchdog policy and final freshness thresholds require target-host evidence. These are not reasons to block hardware-free unit/protocol tests.

## Restart safety

No unresolved write command is automatically replayed after process/gateway restart. Recovery begins with read-only gateway/topology acquisition. `SafeWriteService` receives runtime readiness as an interlock.

## Deployment

Prefer WSS for remote LAN endpoints. If SolarControler and echonet-list are on the same Raspberry Pi, loopback WS may be allowed by explicit deployment configuration; never bind an unauthenticated write-capable endpoint broadly by accident.