# ADR-0028 — Remove hardcoded plan file path from cc-source enforcement note

**Status:** Accepted  
**Date:** 2026-07-17

## Context

When `plan_mode_source == "cc"` (Signal 1 — CC toggle activated plan mode), the Claude Code
client injects into the system prompt a specific, CC-assigned plan file path such as:

> "No plan file exists yet. You should create your plan at `~/.claude/plans/<assigned-name>.md`"

The proxy's `_PLAN_MODE_EXIT_NOTE` was additionally instructing the model:

> "Escribe el plan completo en `.claude/plans/<nombre>.md`"

This project-local path (`.claude/plans/`) conflicts with CC's global path (`~/.claude/plans/`).
The model followed the proxy note (last instruction wins) and wrote the plan to the project-local
directory. When the user clicked "Accept this plan?", CC looked for the plan at the global path
it had assigned → file not found → the plan preview dialog showed empty content.

Confirmed via CC binary strings (`strings ~/.local/share/claude/versions/2.1.165`):
- `"Custom directory for plan files, relative to project root. If not set, defaults to ~/.claude/plans/"`
- `"The plan file path (injected by normalizeToolInput)"` — CC injects path into ExitPlanMode input
- `"No plan file found at"` — CC error when file is missing at expected path

## Decision

Remove the hardcoded `.claude/plans/<nombre>.md` path from `_PLAN_MODE_EXIT_NOTE`.
Replace with a reference to the CC-assigned path already present in the system message.

For `_PLAN_MODE_PROXY_NOTE` (Signal 2 — proxy-only plan mode), keep a path reference but
change it to point to `~/.claude/plans/` (global default) to match CC's normalizeToolInput.

## Consequences

- The "Accept this plan?" dialog will show actual plan content instead of empty content.
- The model writes to wherever CC told it to write — proxy note no longer conflicts.
- No schema change to ExitPlanMode needed: CC's `normalizeToolInput` already injects the
  `planFilePath` from its own state before processing the tool call.
