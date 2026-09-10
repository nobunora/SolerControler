# G0 — Test specification and cases

| ID | Case | Expected |
|---|---|---|
| G0-01 | optimizer/service imports | no direct WebSocket or `set_properties` JSON outside adapter/control boundary |
| G0-02 | invalid EOJ device payload | ignored/fails explicitly; never fabricated identity |
| G0-03 | malformed gateway message | protocol error, not synthetic data |
| G0-04 | missing numeric value | remains missing, never zero |
| G0-05 | write disabled | rejected before gateway I/O |
| G0-06 | SET timeout | no blind second SET |
| G0-07 | gateway success + read-back mismatch | not reported as success |
| G0-08 | dependency search | no pychonet dependency/import remains |

PASS requires G0-01..08 plus review of dependency direction.