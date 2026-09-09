# ECHONET Local Gateway — Index

Purpose: add a local, read-first ECHONET Lite path for RC-307A using `pychonet`, without coupling protocol I/O to planning, KP-NET, dashboard, or domain logic.

## Target

- CHOSHU Smart PV Multi 9.8 kWh / CB-P98M06A
- Battery: CB-LMP98A2
- PCS: PCS-RP2A (2.04.01)
- Gateway: RC-307A (03.05)
- Observed EOJ classes: `0x0279` household solar generation, `0x027D` storage battery
- Runtime: Windows 10/11 or Raspberry Pi OS, Python 3.12

## Reading order / token budget

Read only this file, then the current gate's three files. Do not read later gates unless the current gate passes. Existing architecture remains authoritative; use `docs/current/architecture/03-code-map.md` only when a boundary question exists.

| Gate | Exit condition | Documents |
|---|---|---|
| G0 Boundary | architecture and safety contract accepted | [spec](g0-boundary-spec.md) · [implementation](g0-boundary-implementation.md) · [tests](g0-boundary-tests.md) |
| G1 Read | RC-307A discovery + read-only normalized snapshot works | [spec](g1-read-spec.md) · [implementation](g1-read-implementation.md) · [tests](g1-read-tests.md) |
| G2 Runtime | Windows/RPi long-running behavior is bounded and observable | [spec](g2-runtime-spec.md) · [implementation](g2-runtime-implementation.md) · [tests](g2-runtime-tests.md) |
| G3 Write | explicit, capability-checked, read-back-verified control is safe | [spec](g3-write-spec.md) · [implementation](g3-write-implementation.md) · [tests](g3-write-tests.md) |

## Non-negotiable boundaries

Dependency direction: `runtime/dashboard/energy_plan -> echonet service -> echonet adapter -> pychonet`; `domain/configuration/parsing` never depend on `pychonet`. ECHONET transport owns packets/property maps; normalization owns units/sign/staleness; orchestration owns retry/poll cadence; business planning never sends EPCs.

`pychonet` is in maintenance mode upstream. It is therefore isolated behind one adapter and pinned to a compatible minor range. Replacing it must not change the service contract.

## Review rule

A gate cannot pass from source inspection alone. Tests prove deterministic contracts; Codex performs environment/runtime verification. No production deployment is part of these gates.