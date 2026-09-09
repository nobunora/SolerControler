# G0 — Test specification and cases

## Strategy

Static/unit tests only. No RC-307A and no UDP required.

| ID | Case | Expected |
|---|---|---|
| G0-01 | construct identity with instance != 1 | EOJ preserves discovered instance |
| G0-02 | zero observation | zero remains valid, not missing |
| G0-03 | missing observation | represented as `None`, never zero |
| G0-04 | capabilities | get/set/notify remain distinct immutable sets |
| G0-05 | service with fake adapter | no `pychonet` object escapes service contract |
| G0-06 | adapter failure | project-owned error preserves diagnostic context |
| G0-07 | source dependency search | `pychonet` import exists only in adapter boundary |
| G0-08 | write surface search | no SET/SETC/raw-write method exists before G3 |

## Gate decision

PASS only when G0-01..08 pass. A missing dependency-boundary check is INCONCLUSIVE, not PASS.