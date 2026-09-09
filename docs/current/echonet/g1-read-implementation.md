# G1 — Detailed implementation specification

## pychonet integration

Use `UDPServer`, `ECHONETAPIClient`, `discover(host)`, `register_multicast(host)`, `getAllPropertyMaps(...)`, and `Factory(...)`. Bind UDP/3610 on a configured local address. `pychonet` is pinned because upstream declares maintenance mode.

## Discovery adapter

1. Start listener once.
2. `discover(gateway_ip)` when configured.
3. Read pychonet's discovered instance registry; convert entries to project `DeviceIdentity`.
4. Filter by `(eojgc,eojcc) == (0x02,0x79)` and `(0x02,0x7D)`.
5. For selected identities call `getAllPropertyMaps` and expose copied capability sets.
6. Construct device via `Factory` only inside adapter.

Because pychonet internal registry shape is an external-library detail, keep its parsing in one private helper and fail closed on an unknown shape.

## Read API

`read_properties(identity, epcs)` first intersects requested EPCs with `gettable`; unsupported requests are rejected before network I/O. Returned values are copied into project-owned observations. No consumer receives a pychonet instance.

## Initial mapping policy

Do not encode RC-307A EPC mappings from memory. First implementation provides topology and property-map probing plus a narrow read primitive. Verified mappings discovered by Codex/real hardware are added as a separate reviewed patch with fixture bytes and source/reference.