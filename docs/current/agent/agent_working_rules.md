# Agent Working Rules

## Evidence And Scope

- Verify the actual failure before editing.
- Prefer metadata first: directory names, symbols, grep hits, then narrow line ranges.
- Use full-file reads only for short files or when structure is required.
- If broader reading is needed, state why before doing it.
- Do not guess specs, compatibility, security, money, or external contracts. Verify or ask.

## Reviewable Changes

- A patch should map to one human-readable reason.
- Avoid scattering guards across unrelated files.
- If a helper is needed, place it near the behavior it supports and keep it named after the domain action.
- Do not rename public APIs, DB fields, env keys, or integration fields unless the task requires it.
- Keep generated, cache, build, log, and artifact paths out of normal reads.

## CodebaseMemory Triage

- Read `codebase_memory_triage_and_maintenance_ja.md` before acting on unused-node, low-confidence-call, similarity, or semantic-related findings.
- Treat `in_degree = 0`, low confidence, `SIMILAR_TO`, and `SEMANTICALLY_RELATED` as investigation leads, not proof of dead code or permission to refactor.
- Verify graph findings with source, focused text search, compatibility tests, dynamic/string dispatch, monkeypatches, and dependency direction before editing.
- Never rewrite otherwise clear production code only to improve CodebaseMemory confidence or make a graph edge resolve differently.
- Do not merge domain-specific readers, backend adapters, standalone operational scripts, compatibility seams, or test fakes solely because their implementations are similar.
- When intentional duplication is repeatedly flagged and the boundary has been verified, use a concise `codebase-memory: keep-separate` comment stating the design reason. Do not add such comments mechanically.
- For low-confidence `CALLS`, separate builtins, external SDK calls, test fakes/protocols, dynamic dispatch, and app-to-app edges before deciding whether any source change is justified.
- If an explicit readable call is misresolved by CodebaseMemory, preserve the source and record it as a graph/parser false positive rather than coding around the tool.

## Subagent Prompts

Subagents are useful but forgetful. Give them everything needed in a compact packet:

- Role: what kind of specialist they are.
- Goal: one concrete question or deliverable.
- Scope: exact files, commands, or cloud resources they may inspect.
- Limits: what they must not touch or repeat.
- Output: concise facts, file references, commands run, and remaining uncertainty.
- Stop: when to return instead of continuing.

## Simulations

- Run simulations against already-ingested local data by default.
- Do not call forecast APIs, Sheets, Drive, or other external services during comparison simulations unless explicitly asked.
- Prefer fast replay or diff scripts that reuse saved inputs over full production workflows.

## Financial Decision Integrity

- Every CSV field used by tariffs, revenue, penalties, or SOC cost optimization must have an end-to-end fixture test that passes through the production CSV reader and the downstream calculation. Tests that inject already-parsed dictionaries are necessary but not sufficient.
- Treat an aggregate zero as invalid when the selected source rows contain a non-zero value for that field. The production decision must fail closed or explicitly mark the input unavailable; it must not silently substitute zero.
- Before deploying a financial objective, compare at least one raw-source aggregate with the value recorded in the generated plan. A mismatch blocks deployment.
- Temporary tariff or contract assumptions must be explicit in production configuration and documented with their business condition. Revalidate the condition before each related production change; do not preserve an expired assumption only because it is already deployed.
- Add directional invariant tests for money semantics: purchase must not reduce cost, recognized sales revenue must not increase cost, and a penalty mode must not be used as a substitute for unverified contract status.
- When a simulation repeatedly produces an implausible boundary value such as zero monthly purchases, investigate the source pipeline before accepting or deploying the result. Sensitivity tests with hand-built values do not validate production data wiring.

## Reporting

- Report briefly: changed files, reason, checks run, risks, and open questions.
- Create milestone reports or files under `docs/completed/reports/` only when the user explicitly asks.
- Follow `report_template.md` for tracked report hygiene: use repository-relative source links, do not force-add ignored `.log` files, and keep secrets or local absolute paths out of tracked evidence.
