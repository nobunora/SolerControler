# Skill creation rules

Use this guide when creating or changing a reusable Codex Skill. It adapts the public [Anthropic Skill Creator guide](https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md) to this repository; use the source for its current examples and evaluation tooling.

## Define the contract first

- Establish the task the Skill enables, realistic trigger phrases, inputs, outputs, safety limits, and success criteria from the request and repository context.
- Ask only for missing choices that materially affect the result. Do not create a broad Skill from an unclear one-off task.
- Make the `description` name both the capability and the situations that should trigger it. This metadata is the primary discovery mechanism.

## Keep the Skill focused

- Use `SKILL.md` with YAML `name` and `description` frontmatter.
- Keep the body concise, imperative, and under 500 lines when practical. Explain the reason for important constraints.
- Put deterministic, repeated work in `scripts/`; detailed domain information in `references/`; output assets in `assets/`.
- Link each optional resource from `SKILL.md` and state when to read or run it. Do not add auxiliary READMEs, changelogs, or duplicated guidance.
- Preserve the user's intent. Never embed hidden data collection, destructive behavior, credentials, or misleading instructions.

## Build and validate deliberately

1. Create the Skill with the provided initializer when available, then remove template placeholders.
2. Validate its structure with the available validator. If a required validator dependency is missing, install it only with user authorization or record a manual validation.
3. Test every added executable script with a safe, representative invocation.
4. For non-trivial Skills, write 2–3 realistic user prompts and evaluate the Skill against an appropriate baseline. Keep evaluation artifacts outside the Skill folder.
5. Inspect the resulting instructions and outputs, simplify unnecessary guidance, and iterate until the Skill generalizes beyond the original example.

## Repository integration

- Keep project-specific tools under version control in the project. A globally installed Skill may reference them, but must state the expected repository root and safety boundary.
- Update the relevant agent or documentation index so future work discovers this guide.
- Do not deploy or mutate production while evaluating a Skill unless the user separately authorizes that operation.
