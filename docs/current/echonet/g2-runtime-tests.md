# G2 — Test specification and cases

| ID | Case | Expected |
|---|---|---|
| G2-01 | SolarControler starts before echonet-list | bounded reconnect; control disabled |
| G2-02 | echonet-list restarts | pending requests fail; fresh initial_state required |
| G2-03 | RC-307A goes offline | stale/offline state; no write allowed |
| G2-04 | RC-307A returns | fresh topology/read before control resumes |
| G2-05 | SolarControler restart after uncertain write | no write replay; read-only reconciliation first |
| G2-06 | notification burst | bounded receive loop; no task explosion |
| G2-07 | Raspberry Pi 1 h soak | stable RSS/task count and reconnect behavior |
| G2-08 | LAN interruption | backoff, no busy loop, recovery after network return |
| G2-09 | Windows diagnostic client | same protocol contract works without OS-specific domain code |
| G2-10 | WSS certificate failure | fail closed; no insecure fallback |

Hardware/runtime cases are executed after the Raspberry Pi is available.