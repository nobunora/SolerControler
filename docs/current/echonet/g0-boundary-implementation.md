# G0 — Detailed implementation specification

## Ownership

- `app/echonet/adapter.py`: only echonet-list WebSocket protocol implementation; requestId matching, connection state, gateway error translation, raw gateway cache.
- `app/echonet/models.py`: project-owned immutable identity/result types; no I/O.
- `app/echonet/service.py`: read/topology orchestration; no WebSocket details.
- `app/echonet/control.py`: write enable gate, one-shot transaction ownership, read-back reconciliation and outcome classification.
- Optimization/runtime code may call services but must not know `set_properties` JSON.

## Dependency direction

`energy_plan/runtime -> app.echonet.control/service -> app.echonet.adapter -> websockets`. `domain/configuration/parsing` remain independent of gateway/network code.

## Gateway contract

Use echonet-list documented `initial_state`, `list_devices`, `get_properties`, `set_properties`, `property_changed`, `device_offline`, `device_online`, and `command_result`. Unknown message types may be ignored only when they do not satisfy a pending command; malformed required fields fail explicitly.

## Security

Production uses loopback/LAN-restricted WSS and validated certificates. `--insecure-tls` is probe-only development behavior. No external Internet exposure is part of this design.