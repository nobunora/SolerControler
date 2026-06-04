# AGENTS.md

Keep the default context small.

Rules:
- Read this file first; open other docs only when needed.
- Match the existing design, names, boundaries, and error handling.
- Do not guess unclear specs, compatibility, security, or external contracts; ask.
- Do not rename public APIs, DB fields, config/env keys, or integration fields unless required.
- Report briefly: what changed, why, what was checked, and the risks.

Read when needed:
- Token policy: [docs/codex_token_usage_rules.md](docs/codex_token_usage_rules.md)
- Design/spec judgment: [docs/design_intent_rules.md](docs/design_intent_rules.md)
- Review: [docs/code_review.md](docs/code_review.md)
- Bad patterns: [docs/bad_patterns.md](docs/bad_patterns.md)
- Report template: [docs/report_template.md](docs/report_template.md)
- Project docs: [README.md](README.md)

Workflow:
- Use `rg` first.
- Read only the relevant ranges.
- Avoid rereading unchanged context.
- Test near the changed code first.
