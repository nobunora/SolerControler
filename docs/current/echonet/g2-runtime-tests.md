# G2 — Test specification and cases

| ID | Case | Expected |
|---|---|---|
| G2-01 | normal start/stop | one listener/task; clean shutdown |
| G2-02 | poll slower than interval | no overlap |
| G2-03 | 3 consecutive timeouts | degraded/offline transition; stale data |
| G2-04 | gateway returns | recovery -> online; failure counter reset |
| G2-05 | cancellation during I/O | cancellation propagates; resources close |
| G2-06 | notification burst | bounded work/cache updates; no task explosion |
| G2-07 | UDP/3610 already bound | explicit startup failure |
| G2-08 | Windows 10/11 soak | 1 h read-only run, stable task/socket count |
| G2-09 | Raspberry Pi OS soak | 1 h read-only run, stable task/socket count |
| G2-10 | gateway reboot during soak | automatic bounded recovery |
| G2-11 | network interface interruption | stale/offline then recovery |
| G2-12 | logs | no secrets/environment dump; state transitions diagnosable |

For soak tests record start/end RSS, task count, socket count, successful polls, timeouts and recovery latency. Thresholds must be agreed from observed baseline rather than invented.