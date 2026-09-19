# Codex investigation: KP-NET browser automation vs direct HTTP control

## Objective

Investigate the current KP-NET control path and determine, with evidence, why setting operations fail intermittently and whether the production path can be made reliably HTTP-only.

This is an **investigation-first PR**. Do not redesign the whole controller and do not introduce speculative device mappings. First establish what the repository and deployed jobs actually do today, reproduce the failure boundary where possible, and report concrete evidence.

## Background already observed

The repository currently contains two conceptually different control paths:

1. Legacy/local Playwright browser automation under `app/local_control/browser.py`.
2. A direct HTTP client under `app/kpnet/client.py` using `requests.Session()` and KP-NET form/AJAX endpoints.

The direct HTTP client appears to implement:

- `GET login`
- `POST processLogin`
- CSRF extraction and cookie/session retention
- settings-page navigation by HTTP POST
- asynchronous settings reads using `read/request` + `read/response`
- candidate-list reads
- confirmation POST
- asynchronous settings writes using `write/request` + `write/response`
- final completion request
- explicit read-back verification

The current 23:00 / 03:00 / 07:00 cloud-job control code also appears to call `run_kpnet_mode_only_profile()`, which constructs `KpNetClient` rather than driving Playwright.

Treat those points as hypotheses to verify from the current branch, not as assumptions.

## Investigation questions

Answer every item below in the final PR report.

### A. Determine the actual production control path

Trace the full call graph for the 23:00, 03:00, and 07:00 jobs from their entry point to the actual KP-NET I/O operation.

For each slot, record:

- entry point
- intermediate function calls
- whether Playwright/Chromium is instantiated
- whether `KpNetClient` is instantiated
- the exact code path that performs the setting write
- the exact code path that performs read-back verification

Also search the entire repository for all references to:

- `playwright`
- `apply_battery_setting`
- `login_monitoring_service`
- `KpNetClient`
- `write_setting`
- `run_kpnet_mode_only_profile`

Classify each browser-based path as one of:

- production-active
- test-only
- compatibility-only
- dead/legacy but still importable
- unknown

Do not delete anything yet.

### B. Verify what is actually deployed

Inspect deployment scripts, job definitions, Docker configuration, and build/deploy flow.

Determine whether the currently deployed Cloud Run Jobs for 23/03/07 are guaranteed to contain the current `master` implementation or whether an older image could still be running.

Record at minimum:

- image/tag strategy
- whether `:latest` is used
- whether job revisions pin an immutable image digest
- which command/entry point each job runs
- whether a deployment can succeed without updating all three jobs
- whether an old image can remain active after a source-code change

If GCP credentials/tools are available in the execution environment, inspect deployed job metadata and image digests read-only. Do not mutate Cloud Run, Scheduler, IAM, Secret Manager, VPC, or NAT configuration in this investigation.

If live GCP metadata cannot be inspected, state that explicitly and give the exact command(s) an operator should run to prove the deployed digest.

### C. Document the KP-NET HTTP transaction contract

From `app/kpnet/client.py`, build a compact state machine/table for the actual requests performed during:

1. login
2. settings-page open
3. current-settings read
4. candidate-map read
5. confirmation
6. write request
7. write response polling
8. completion
9. read-back
10. logout

For each network step, record:

- method
- path
- expected request data
- CSRF source used
- whether AJAX headers are used
- expected response type: HTML/JSON/etc.
- timeout behavior
- retry behavior
- whether repeating the request is safe/idempotent

Do **not** print credentials, cookies, CSRF values, Authorization headers, Secret Manager values, or full HAR contents.

### D. Find the real failure modes in the current implementation

Audit the HTTP path specifically for intermittent failures.

At minimum inspect:

- session expiry between login and write
- login redirect returned where JSON is expected
- CSRF expiry or CSRF token mismatch
- transient 429/5xx responses
- DNS/TLS/connect/read timeouts
- `requests.Session` cookie handling
- server-side asynchronous operation polling
- polling timeout boundaries
- response status values other than success
- malformed/non-object JSON handling
- write accepted by KP-NET but response lost
- write applied to the device but client timed out
- final read-back delayed relative to write completion
- read-back mismatch due to eventual consistency
- concurrent jobs sharing device ownership around slot boundaries
- duplicate write risk caused by retries above `KpNetClient`

Pay special attention to the semantic difference between:

- request failed before provider acceptance
- provider accepted but device result is pending
- device result succeeded but response was lost
- device state after a timeout is unknown

### E. Check for unsafe write retry behavior

This is mandatory.

Trace every caller above `KpNetClient.write_setting()` and determine whether a failed or timed-out write can be automatically retried.

For a KP-NET setting mutation, a timeout must be treated as potentially **UNKNOWN outcome** unless the implementation can prove the provider/device did not accept it.

Report whether the current system can do this sequence:

```text
write/request #1
-> request or response timeout
-> caller retries whole operation
-> write/request #2
```

If yes, identify the exact call stack and test coverage.

Do not fix it silently in this investigation PR. Report it as a high-priority finding with a proposed safe remediation:

```text
SET once
-> timeout/transport uncertainty
-> do not blindly SET again
-> perform fresh GET/read-back reconciliation
-> classify APPLIED / NOT_APPLIED / UNKNOWN
```

### F. Compare browser automation failure surface vs direct HTTP

Provide an evidence-based comparison for this repository, not a generic browser-vs-HTTP essay.

Compare:

- startup cost
- Chromium dependency
- DOM/selectors
- JavaScript/render waits
- session/cookie handling
- request count
- CPU/RAM footprint
- container size/dependencies
- observability
- deterministic timeout behavior
- write ambiguity after network failure

Conclude whether production battery settings should be HTTP-only while leaving browser automation, if still needed, for unrelated functions.

### G. Investigate source-IP/session coupling as a hypothesis only

Check code and available logs/evidence for whether KP-NET sessions might become invalid when outbound source IP changes.

Do not state that KP-NET binds sessions to IP unless evidence proves it.

If production runs on Cloud Run with non-static egress, document:

- current egress architecture if known
- whether requests within a single job execution can practically egress from different public IPs
- whether observed failures correlate with authentication/session reset symptoms

Do **not** add Cloud NAT or VPC egress in this investigation PR. Static egress is a separate remediation decision and must be justified by evidence.

## Instrumentation proposal

Design a minimal, secret-safe HTTP telemetry layer that would make the next failure diagnosable.

Propose structured records with fields such as:

- operation id
- slot/job name
- stage
- HTTP method
- sanitized endpoint name/path
- elapsed milliseconds
- HTTP status
- response content type
- provider JSON `status` when present
- communication sequence number only if it is non-secret and safe to log
- retry/reconcile decision
- exception class
- final classification

Never log:

- username/password
- cookies/session IDs
- CSRF token values
- Authorization headers
- Secret Manager payloads
- complete request/response bodies containing account/device data

## Tests to inspect or add later

For this investigation, identify existing coverage and list missing tests for at least:

1. login page returned instead of JSON during read polling
2. 500 during read polling
3. 500 during write polling
4. timeout before `write/request` receives a response
5. timeout after provider acceptance
6. delayed read-back after nominal write success
7. session expiry requiring reauthentication
8. caller-level retry after uncertain write
9. read-only retry safety
10. no Playwright dependency on 23/03/07 production path

Do not perform a real destructive/behavior-changing battery write just to satisfy the investigation. Live writes require a separate explicit instruction.

## Required execution

Run the repository-prescribed checks from `AGENTS.md` plus the relevant focused tests. At minimum:

```text
pytest tests/test_kpnet* -q
pytest tests/test_cloud_job_runner.py -q
```

Use the actual repository test filenames if they differ. Run Ruff/mypy scopes required by repository policy.

If a command fails because the current repository already has an unrelated/pre-existing failure, separate it clearly from findings introduced by this branch.

## Deliverable format

Post a PR comment titled:

`## Codex investigation result — KP-NET HTTP control path`

Include these sections:

1. **Executive conclusion**
   - Is 23/03/07 currently browser-driven or direct-HTTP-driven?
   - Is the deployed version proven or only repository state proven?

2. **Verified call graph**
   - one compact flow per job slot

3. **HTTP transaction table**
   - request sequence and retry/idempotency classification

4. **Failure findings**
   - severity: Critical / High / Medium / Low
   - exact file/function/line where possible
   - reproduction or evidence

5. **Write uncertainty analysis**
   - explicitly answer whether a timed-out mutation can be blindly retried today

6. **Browser vs HTTP recommendation**

7. **Deployment-state evidence**
   - image digest/revision if accessible, otherwise exact verification commands

8. **Proposed remediation order**
   - investigation-backed only; no speculative changes

9. **Tests executed**
   - command + pass/fail counts

10. **Open evidence gaps**

## Constraints

- Investigation first; do not perform broad refactors.
- Do not change battery settings on live hardware unless separately authorized.
- Do not expose secrets in logs, comments, artifacts, or test output.
- Do not add static egress/NAT based only on speculation.
- Do not remove Playwright paths until their runtime role is proven.
- Do not weaken existing 23/03/07 ownership/fail-safe timing contracts.
- Do not convert write timeouts into blind automatic retries.
- Preserve existing read-back verification behavior while investigating it.

The desired outcome of this PR is a defensible answer to: **what path is actually running, exactly where does it fail, and what is the smallest safe change that would make KP-NET control reliably HTTP-only?**
