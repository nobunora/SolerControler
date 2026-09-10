# G3 — Test specification and cases

| ID | Case | Expected |
|---|---|---|
| G3-01 | writes disabled | rejected before gateway I/O |
| G3-02 | malformed/unverified command | rejected before gateway I/O |
| G3-03 | valid SET + matching live GET | APPLIED |
| G3-04 | gateway SET rejection | REJECTED |
| G3-05 | accepted SET + mismatching GET | MISMATCH |
| G3-06 | SET timeout + matching GET | APPLIED after reconciliation; exactly one SET |
| G3-07 | SET timeout + mismatching/unavailable GET | UNKNOWN; exactly one SET |
| G3-08 | restart after UNKNOWN | no automatic replay; read-only reconciliation first |
| G3-09 | stale/offline prerequisite | command blocked before SET |
| G3-10 | firmware/profile drift | command blocked/fail closed |
| G3-11 | concurrent writes same target | serialized by single command owner before auto-control release |
| G3-12 | audit | command identity/request/outcome/read-back recorded without secrets |
| G3-13 | safe RC-307A hardware write | expected SET then live GET sequence verified |

Current unit tests cover disabled writes, accepted+read-back, rejection, and timeout reconciliation/no blind retry. G3-09..13 are release blockers to be completed after the Raspberry Pi and RC-307A test environment are available.