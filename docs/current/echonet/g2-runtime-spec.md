# G2 — Windows / Raspberry Pi runtime specification

## Ideal specification

- Same Python application code on Windows and Raspberry Pi; OS-specific wrappers only start/stop it.
- One UDP listener per process; one owner for polling/retry lifecycle.
- Notifications update cache when available; bounded polling guarantees eventual refresh.
- Every value carries `observed_at` and stale state. Health is separate from measurements.
- Retry uses bounded backoff and cancellation-safe shutdown.

## Forbidden specification

- OS branches in domain/normalization code.
- Busy retry loops, overlapping polls, unbounded task creation, or swallowing `CancelledError`.
- Docker bridge networking as the reference deployment for multicast.
- Reporting last-known data as fresh after communication loss.
- Writing logs indefinitely without rotation/host supervision.

## Normal flow

Start after network availability -> bind listener -> discover -> subscribe/register notifications -> poll -> update cache -> graceful stop/unregister.

## Abnormal flow

Bind conflict, interface change, gateway reboot, Wi-Fi interruption, malformed packet, or task cancellation cannot terminate the process silently. Health becomes degraded/offline, data becomes stale, retries remain bounded.

## Exit criteria

Soak/reconnect/shutdown tests pass on both target OS families before enabling any write capability.