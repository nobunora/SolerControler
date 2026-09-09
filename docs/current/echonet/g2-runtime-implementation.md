# G2 — Detailed implementation specification

## Runtime owner

A future `app/echonet/runtime.py` owns exactly one adapter/service lifecycle. It receives configuration; it does not read `.env` directly. Configuration parsing remains in `app/configuration`.

## State machine

`STARTING -> ONLINE -> DEGRADED -> OFFLINE -> RECOVERING -> ONLINE`; shutdown from any state -> `STOPPING -> STOPPED`. State transitions are logged once with reason; measurements are independent and become stale by age.

## Scheduling

Use one asyncio polling task. No next poll starts until the prior poll finishes. Default cadence is configuration, not protocol logic. Retry delay is capped; successful traffic resets failure count. Push callbacks only update cached state/signal refresh and must not launch unbounded work.

## OS boundary

Windows Service/Task Scheduler and systemd unit files are deployment wrappers, not application dependencies. Reference process accepts SIGINT/normal cancellation; platform-specific stop handling belongs at entrypoint.

## Observability

Structured fields: component, host, EOJ/EPC when applicable, state transition, attempt, elapsed_ms. Never log `.env`, credentials, full environment, or opaque external payloads unnecessarily.