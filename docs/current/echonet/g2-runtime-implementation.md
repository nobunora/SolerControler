# G2 — Detailed runtime implementation specification

## Process model

On Raspberry Pi, run echonet-list and SolarControler as separate systemd units. SolarControler depends on network-online and the local gateway endpoint, but must tolerate gateway restarts after startup. Do not merge the two processes merely to reduce service count.

## SolarControler connection owner

A single runtime owner creates `EchonetListGateway`, performs connect, waits for initial_state, validates topology, and exposes readiness. On disconnect it marks gateway unavailable, suspends control, closes pending requests, then reconnects with capped exponential backoff and jitter.

## Restart safety

No unresolved write command is automatically replayed after process/gateway restart. Recovery begins with read-only reconciliation and fresh topology/state acquisition.

## Observability

Log connection state, gateway error code, target EOJ, request type, correlation/request ID where useful, and elapsed time. Do not log TLS private material, full environment, or unnecessary raw device payloads.

## Deployment

Prefer WSS for LAN endpoints. If SolarControler and echonet-list are on the same Raspberry Pi, loopback WS may be allowed by explicit deployment configuration; never bind an unauthenticated write-capable endpoint broadly by accident.