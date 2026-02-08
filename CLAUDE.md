# AI-Tooling Project Instructions

## Mandatory: Read before working
- ALWAYS read `ai-notes/AI_LEARNING.md` at the start of every session (if it exists)
- ALWAYS read `templates/GUARDRAILS.template.md` for the full policy

## Guardrails (enforced)
- Do NOT execute agent/tools if `ai-notes/AI_PLAN.md` does not exist or does not contain `STATUS: REVIEWED`
- Do NOT guess or fabricate file paths, commands, or outputs
- Do NOT dump large outputs into chat — write everything to `ai-notes/`
- Local mode = text only (scan/plan/validation, $0). Cloud mode = execution with tools (on-demand)

## Feedback Loop
- At the end of every session, update `ai-notes/AI_LEARNING.md` with:
  - Technical decisions made and why
  - Errors encountered and how they were resolved
  - Patterns that worked or failed
- Do NOT store learnings in `.claude/` or private agent memory
- ALL project knowledge goes to `ai-notes/` (shared with team and future agents)

## Project Structure
- `vendor/claude-code-proxy/` — Anthropic→OpenAI proxy (hot-reload via bind mount)
- `scripts/` — CLI tools (cc-scan, cc-plan, cc-agent-cloud, etc.)
- `profile-envs/` — Per-provider environment configs
- `templates/` — AI_CONTEXT, AI_PLAN, AI_LEARNING, GUARDRAILS templates
- `ai-notes/` — Session artifacts (plans, analyses, learnings)

## Workflow
1. Create `ai-notes/AI_CONTEXT.md` (from template)
2. `cc-scan <files>` — analyze (local, tools OFF)
3. `cc-plan` — generate plan (local, tools OFF, outputs STATUS: DRAFT)
4. Human reviews plan → changes STATUS to REVIEWED + adds Reviewed-by
5. `cc-agent-cloud` — execute (cloud, tools ON, validates REVIEWED)
