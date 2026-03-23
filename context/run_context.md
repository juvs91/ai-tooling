# Run Context — ai-tooling

Confirmed facts about this project. Updated by agents as new facts are verified.
**Rule**: Only write here what has been directly observed or tested — no inference.

---

## Project Identity

- **Name**: ai-tooling
- **Purpose**: Anthropic→OpenAI proxy + agentic CI tooling for Claude Code
- **Primary language**: Python
- **Package manager**: uv (vendor/claude-code-proxy)
- **Container runtime**: Docker Compose

## Key Paths

| Path | Description |
|------|-------------|
| `vendor/claude-code-proxy/` | Main proxy code (hot-reload via bind mount) |
| `vendor/ralph/` | Ralph agent execution harness |
| `scripts/` | Workflow CLI tools |
| `bin/` | Standalone utilities |
| `profile-envs/` | Per-provider environment configs |
| `cloud-provider-ymls/` | Docker Compose overrides per provider |
| `templates/` | GUARDRAILS, AI_PLAN, AI_LEARNING, AI_CONTEXT templates |
| `ai-notes/` | Session artifacts (learnings, analyses) |
| `.agents/skills/` | Agent skill personas (installed 2026-03-22) |
| `docs/adr/` | Architecture Decision Records |
| `docs/findings/` | Discovery findings ledger |
| `docs/knowledge/` | Canonical reference docs |

## Runtime

- **Proxy port**: 8083
- **Container**: `ai-tooling-proxy_cloud-1`
- **Health**: `curl http://127.0.0.1:8083/health | jq .`
- **Logs**: `curl "http://127.0.0.1:8083/api/logs?n=20" | jq .`
- **Stats**: `curl http://127.0.0.1:8083/api/stats | jq .`

## MCP Servers

| Server | Tool prefix | Notes |
|--------|-------------|-------|
| alloydb | `mcp__alloydb__` | AlloyDB pricing queries |
| atlassian | `mcp__atlassian__` | Jira + Confluence |
| bitbucket | `mcp__bitbucket__` | Bitbucket PRs |
| squit | `mcp__squit__` | Legacy SP semantic search |
| cloudsql | `mcp__cloudsql__` | CloudSQL (env-switched) |

## ADR Sequence

- ADR-0001: Adopt Agentic CI skill system (2026-03-22)

---
*Last updated: 2026-03-22*
