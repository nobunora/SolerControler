---
name: solar-production-deployment
description: "Validate, deploy, resume and audit SolarController releases using canonical wrappers, failure-prevention checks and separate browser acceptance. Use for production deployment, preflight failures or interrupted-release recovery."
---

# Solar production deployment

Use only in `C:\VSC\SolerControler`. Project scripts are version-controlled there.
The tracked copy of this Skill is `skills/solar-production-deployment/SKILL.md`;
keep the installed copy synchronized when changing it.

## Every release or recovery

1. Read `docs/current/ops/PRODUCTION_DEPLOYMENT_RUNBOOK_JA.md` in full and
   `docs/current/ops/DEPLOYMENT_FAILURE_RULES_JA.md`. Confirm the requested scope
   and existing user authorization. Analysis/check requests alone do not authorize deployment.
2. Run `pwsh -NoProfile -File scripts/pre_release_local.ps1 -CheckPrerequisites`
   for early environment diagnosis. It prints the current failure rules and checks
   `python -I` can load Asia/Tokyo. Failure stops work; do not automatically install
   global packages. This command is not a release gate.
3. On the final clean commit, run
   `pwsh -NoProfile -File scripts/production_deployment_gate.ps1 -RunPreRelease`.
   Require same-commit `passed` and every required check `success`, including
   `release_prerequisites`. Read the displayed rules; the state stores their hash,
   not proof that someone read them. Never use skip flags to turn a partial gate into approval.
4. Use the runbook's canonical wrapper for the explicit scope and a new explicit
   StatePath. Separated forecast requires a same-commit prepared adapter fixture
   and immutable planner digest SDK proof. Do not expand forecast/dashboard into
   full/control to work around a rejected command.

## Known fragile boundaries

- Canonical local/integration pytest uses `--capture=sys` to avoid Windows native
  subprocess WinError 6. Preserve the full test selection. Redirecting the log alone
  did not fix this failure. Invoke the code-quality-audit Skill before tests and
  keep the fixed static checks ahead of pytest.
- Test fake failures require contract-correct fixtures and rerunning the gate,
  not skips or weakened assertions.
- Cloud count fields may be omitted. Use canonical receipt readers; absent is
  UNKNOWN unless the authoritative schema/terminal evidence proves otherwise.
- On timeout, inspect the exact execution and restoration read-only before any
  retry. Do not launch another build/SDK write/device SET while running or unknown.
- Resume with the original commit/layout/scope/StatePath and identity inputs.
  Only successful, identity-matching stages can be reused. Never edit state to success.
- Keep credentials, resource IDs and raw exceptions out of chat/tracked files.
  Use production_env.ps1 and canonical wrappers, not credential-bearing command reconstruction.

## Acceptance and reporting

Report deployment, SDK, forecast execution, browser and physical control separately.
Follow the runbook's scope-specific verification. Forecast/dashboard do not require
07 DryRun or physical settings round-trip; do not introduce those mutations.
Runner/full retain mandatory read-back, holding interval, restoration and backup.
DRY_RUN must not enter the production lease-write path.

For dashboard changes or forecast changes that update displayed data, run:

```powershell
pwsh -NoProfile -File scripts/check_dashboard_browser_from_env.ps1 `
  -InventoryPath <private-before-inventory.json> `
  -OutputDirectory artifacts/browser-acceptance-<unique-stamp>
```

Use the canonical deployment's private inventory (`web.status.url`), never a
credential-bearing URL. The read-only checker uses installed Playwright/Chromium,
scopes Basic auth to that origin, and checks initial charts, lazy history, all→month,
mobile width and browser errors. Missing dependencies/authentication/timeouts are
nonzero and never PASS. Do not silently install a browser or retry production.
Read receipt.json and inspect the PNGs. Check forecast/actual/SOC/provenance against
the applicable API/source evidence separately; chart rendering does not prove values.

An API 200, build success or Job success cannot override a browser FAIL/INCONCLUSIVE.
Do not claim the full workflow passed while acceptance is missing or failed.
For new failures, add observed evidence, an executable regression and a stop rule
to the project failure rules. Validate the Skill with the skill-creator validator;
evaluate it without production mutations.
