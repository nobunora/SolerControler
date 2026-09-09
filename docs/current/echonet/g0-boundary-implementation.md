# G0 — Detailed implementation specification

## Ownership

- `app/echonet/models.py`: project-owned immutable identities, capabilities, observations, snapshots. No external I/O.
- `app/echonet/adapter.py`: the only production module allowed to import/call `pychonet`.
- `app/echonet/service.py`: discovery selection, normalization orchestration, stale/error semantics. Depends on an adapter protocol, not concrete `pychonet` classes.
- Runtime scheduling belongs outside these modules.

## Contracts

`DeviceIdentity(host, eojgc, eojcc, eojci)` preserves EOJ identity and exposes a six-hex-digit EOJ string. `DeviceCapabilities(gettable,settable,notify)` uses immutable integer sets. `PropertyObservation` distinguishes `value=None` from numeric zero and records freshness/source. `SystemSnapshot` contains optional PV/battery sections plus explicit errors.

Adapter methods are async and narrow: discover target devices, load capability maps, read requested EPCs. No generic write method exists before G3.

## Dependency guard

`models` imports stdlib only. `service` imports project models and typing only. `adapter` may import `pychonet`. Existing upper layers may consume the service later; reverse imports are forbidden.

## Diagnostics

Errors include host/EOJ/EPC where applicable but never raw environment variables. Exceptions crossing the adapter boundary use project-owned exception classes with original exception chaining.