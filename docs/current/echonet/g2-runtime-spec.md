# G2 — Raspberry Pi / Windows runtime specification

## Ideal specification

- Production reference: Raspberry Pi OS with echonet-list and SolarControler as separate supervised processes.
- echonet-list owns multicast/interface recovery and ECHONET device lifecycle.
- SolarControler owns WebSocket reconnect/backoff, gateway readiness, stale-state age, and command suspension while degraded.
- Windows remains a supported development/diagnostic client against the same WebSocket contract.
- TLS/WSS is enabled for LAN use unless both processes communicate strictly over loopback.

## Forbidden specification

- Busy reconnect loops or unbounded task creation.
- Starting write control before initial_state/topology readiness.
- Treating cached values as fresh after gateway/device offline notifications.
- Exposing echonet-list directly to the Internet.
- Letting service restart automatically replay an unresolved write.

## Exit criteria

Raspberry Pi soak, echonet-list restart, RC-307A restart, LAN interruption, SolarControler restart and graceful shutdown behavior are measured before automatic writes are enabled.