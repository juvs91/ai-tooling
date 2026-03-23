# ADR-0001: Adopt Agentic CI Skill System

- **Date**: 2026-03-22
- **Status**: Accepted
- **Deciders**: Jorge Guzman

---

## Context

The `ai-tooling` project had no structured agent routing system. `CLAUDE.md` referenced a
`docs/` directory that did not exist, and all agent instructions were inline in a single file.
There was no explicit skill delegation, no ADR trail, and no canonical knowledge store.

The `cookiecutter-agentic-ci` template provides a proven pattern for:
- Routing agent tasks to specialized personas via `AGENTS.md` + `.agents/skills/`
- Enforcing an ADR-first mandate for architectural changes
- Maintaining a structured knowledge pipeline: findings → context → knowledge → ADR

---

## Decision

Install the Agentic CI skill system from the local template at:
`/Users/jeguzman/Documents/deacero/cookiecutter-agentic-ci/{{cookiecutter.project_slug}}/`

**Skills installed** (13 total):

| Category | Skills |
|----------|--------|
| Core | `learning-protocol`, `tool-writer` |
| Architecture | `architect`, `adr-writer`, `decision-logger` |
| Discovery | `software-archeologist`, `retro-engineer`, `unknown-domain-protocol` |
| Quality | `bdd-writer`, `code-reviewer` |
| Infrastructure | `database-expert`, `gitops-expert` |
| Security | `security-expert` |

**Skills excluded** (per project requirements):
- `hardware/` — all hardware specialists (NFC, USB, Bluetooth, serial, embedded, smart-card)
- `software/discovery/cobol-analyst` — no COBOL in this project
- `design/ux-expert` — CLI/proxy, no UI surface

**Files created/modified:**
- `AGENTS.md` — created (project-specific routing, 10 directives)
- `CLAUDE.md` — appended "Agent Skills" block
- `.agents/skills/` — created and populated
- `docs/adr/` — created (this file is the first entry)
- `docs/findings/FINDINGS.md` — created (empty ledger)
- `docs/knowledge/` — created (empty, populated as agents work)
- `context/run_context.md` — created (seed with known project facts)

---

## Consequences

**Positive:**
- Every session, Claude reads `AGENTS.md` and knows exactly which skill persona to activate
- Architectural decisions leave a traceable ADR trail
- Findings are recorded before being promoted to canonical knowledge
- `ai-notes/` continues to serve as session-scoped artifacts; `docs/knowledge/` holds confirmed canonical facts

**Negative / trade-offs:**
- Agents must remember to open `FINDINGS.md` before writing to `knowledge/` — requires discipline
- `docs/tools/index.md` does not yet exist; deduplication checks will find nothing until populated
- Hardware skills not installed — if hardware analysis is ever needed, install from template

---

## References

- Source template: `github.com/deagentic/cookiecutter-agentic-ci`
- Analysis plan: `/Users/jeguzman/.claude/plans/majestic-noodling-narwhal.md`
- Installation plan: `/Users/jeguzman/.claude/plans/peppy-scribbling-yeti.md`
