# G3 — Test specification and cases

| ID | Case | Expected |
|---|---|---|
| G3-01 | writes disabled | command rejected before I/O |
| G3-02 | EPC absent from Set map | rejected before I/O |
| G3-03 | invalid semantic value/range | rejected before I/O |
| G3-04 | valid command + matching read-back | APPLIED |
| G3-05 | SET rejected | REJECTED; no success |
| G3-06 | timeout before send | safe transport retry allowed |
| G3-07 | timeout after possible send | UNKNOWN -> GET reconciliation; no blind second SET |
| G3-08 | read-back mismatch | MISMATCH; no blind retry |
| G3-09 | firmware/capability drift | control disabled/fail closed |
| G3-10 | concurrent commands same target | serialized or rejected per command-owner contract |
| G3-11 | audit record | correlation + semantic values/outcome, no secrets |
| G3-12 | packet capture on RC-307A | exactly expected write/read-back sequence |

Hardware write tests require an explicitly safe operating window and user authorization. This PR intentionally contains no G3 production write code.