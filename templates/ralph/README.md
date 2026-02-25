# Ralph — Autonomous Claude Code Agent Framework

Ralph runs Claude Code in an automated loop with phased task execution, self-tracking progress, knowledge persistence, and circuit breaker protection.

## Quick Start

### 1. Copy to your project

```bash
cp -r templates/ralph/.ralph /path/to/your/project/
cp templates/ralph/.ralphrc.template /path/to/your/project/.ralphrc
```

### 2. Configure `.ralphrc`

Replace all `{{PLACEHOLDERS}}` with your project values:

```bash
PROJECT_NAME="my-project"
PROJECT_TYPE="python"
ALLOWED_TOOLS="Write,Read,Edit,Glob,Grep"
```

### 3. Fill in semantic files

| File | Action |
|------|--------|
| `.ralph/AGENT.md.template` | Rename to `AGENT.md`, set project type, build/test commands |
| `.ralph/PROMPT.md.template` | Rename to `PROMPT.md`, define objective, working directory, success criteria |
| `.ralph/fix_plan.md.template` | Rename to `fix_plan.md`, write your phased task checklist |
| `.ralph/specs/schema_reference.md.template` | Rename to `schema_reference.md`, add domain reference |
| `.ralph/specs/ai_learning.md.template` | Rename to `ai_learning.md` (leave empty — Ralph fills it) |

### 4. Create phase prompts

Copy `.ralph/prompts/fase-template.md` for each phase:

```bash
cp .ralph/prompts/fase-template.md .ralph/prompts/fase-0.md
cp .ralph/prompts/fase-template.md .ralph/prompts/fase-1.md
# etc.
```

Fill in the specific tasks for each phase.

### 5. Configure file boundary hook

Rename and edit `.ralph/hooks/validate-file-boundary.sh.template`:

```bash
mv .ralph/hooks/validate-file-boundary.sh.template .ralph/hooks/validate-file-boundary.sh
chmod +x .ralph/hooks/validate-file-boundary.sh
```

Set `BASE` and `ALLOWED_*` paths in the script.

Add to `.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [{"type": "command", "command": "bash .ralph/hooks/validate-file-boundary.sh"}]
      }
    ]
  }
}
```

### 6. Run Ralph

```bash
# Single loop (Ralph reads plan, executes tasks, reports status)
claude --print --append-system-prompt "$(cat .ralph/claude-ralph.md)" \
  --allowedTools "$ALLOWED_TOOLS" \
  -p "$(cat .ralph/prompts/fase-0.md)"

# Or use the full prompt
claude --print --append-system-prompt "$(cat .ralph/claude-ralph.md)" \
  --allowedTools "$ALLOWED_TOOLS" \
  -p "$(cat .ralph/PROMPT.md)"
```

---

## Architecture

```
.ralphrc                          # Project config (calls/hour, timeout, tools)
.ralph/
  claude-ralph.md                 # Agent identity & rules (generic)
  AGENT.md                        # Project type & validation
  PROMPT.md                       # Main objective & workflow
  fix_plan.md                     # Task checklist [ ] / [x]
  specs/
    ai_learning.md                # Knowledge registry (Ralph writes here)
    schema_reference.md           # Domain reference (read-only)
  prompts/
    fase-0.md                     # Phase 0 specific prompt
    fase-1.md                     # Phase 1 specific prompt
    ...
  hooks/
    validate-file-boundary.sh     # PreToolUse hook (blocks out-of-scope edits)
  status.json                     # Execution state
  progress.json                   # Overall progress
  logs/                           # Execution logs
```

## Key Concepts

### Semantic Files (3 mandatory)
Ralph always reads these before any action:
1. **fix_plan.md** — Task checklist. Ralph marks `[x]` as it completes tasks.
2. **ai_learning.md** — Knowledge base. Ralph documents findings, decisions, patterns.
3. **schema_reference.md** — Domain context. Read-only reference material.

### Phased Execution
Tasks are organized into sequential phases (Fase 0, 1, 2...). Each phase has:
- Pre-requisite check (previous phases complete)
- Specific tasks with file paths and instructions
- Status output block when phase completes

### Status Protocol
Ralph outputs machine-readable status blocks:
```
---RALPH_STATUS---
STATUS: COMPLETE
TASKS_COMPLETED_THIS_LOOP: 3
FILES_MODIFIED: 2
TESTS_STATUS: NOT_RUN
WORK_TYPE: IMPLEMENTATION
EXIT_SIGNAL: true
RECOMMENDATION: Phase 1 complete. Continue with Phase 2.
---END_RALPH_STATUS---
```

### Circuit Breaker
Tracks consecutive failures:
- `CB_NO_PROGRESS_THRESHOLD=3` — 3 loops with no task completion = STOP
- `CB_SAME_ERROR_THRESHOLD=3` — 3 identical errors = STOP

### File Boundary Enforcement
PreToolUse hook blocks edits outside the designated working directory. Prevents Ralph from accidentally modifying infrastructure, configs, or unrelated code.

## Customization

### Tool Permissions
Control what Ralph can do via `ALLOWED_TOOLS` in `.ralphrc`:

| Profile | Tools | Use Case |
|---------|-------|----------|
| Read-only | `Read,Glob,Grep` | Analysis/audit only |
| Code modification | `Write,Read,Edit,Glob,Grep` | Default — modify source files |
| Full access | `Write,Read,Edit,Glob,Grep,Bash` | Build, test, deploy |

### Project Types
Ralph adapts to any project type. Examples:

**SQL project** (BigQuery, PostgreSQL):
- `ALLOWED_TOOLS="Write,Read,Edit,Glob,Grep"` (no Bash)
- Validation: column alignment, syntax correctness

**Python project**:
- `ALLOWED_TOOLS="Write,Read,Edit,Glob,Grep,Bash"` (Bash for tests)
- Validation: `pytest`, type checking, import errors

**TypeScript project**:
- `ALLOWED_TOOLS="Write,Read,Edit,Glob,Grep,Bash"` (Bash for build/test)
- Validation: `npm run build && npm test`
