# G1 — Test specification and cases

| ID | Case | Expected |
|---|---|---|
| G1-01 | discover PV + battery instance 01 | both selected |
| G1-02 | discover non-01 instances | discovered IDs preserved |
| G1-03 | PV missing | explicit topology error |
| G1-04 | battery missing | explicit topology error |
| G1-05 | duplicate target class | ambiguity error; no arbitrary selection |
| G1-06 | property maps loaded | get/set/notify copied separately |
| G1-07 | request unsupported EPC | rejected before adapter network read |
| G1-08 | read returns numeric zero | zero preserved |
| G1-09 | timeout | explicit communication error; no synthetic value |
| G1-10 | malformed registry/library shape | compatibility error, fail closed |
| G1-11 | real RC-307A probe | `0x0279xx`, `0x027Dxx` and maps observed; no write ESV emitted |
| G1-12 | Windows firewall/network failure | actionable bind/discovery error |
| G1-13 | Raspberry Pi interface selection | configured host reachable and multicast registration succeeds when used |

G1-11..13 are Codex/manual integration checks. Unit tests must not require LAN hardware.