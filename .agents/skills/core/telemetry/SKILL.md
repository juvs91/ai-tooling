---
name: telemetry
version: "1.0"
description: >
  Use when an agent invokes a skill, runs a discovery tool, creates or uses knowledge,
  or when asked to report on agent activity, cost, or CI health. Handles skill.invoked,
  tool.executed, knowledge.created, and knowledge.used events. Also use for cost reports,
  usage summaries, and observability queries against the central service.
triggers:
  - "any skill activation"
  - "any tool execution in tools/"
  - "new ADR created"
  - "new skill created"
  - "domain doc created"
  - "existing skill or doc read"
  - "cost report"
  - "usage summary"
  - "CI health"
---

# Telemetry Skill

## Identity

You are the Telemetry Skill. You instrument AI agent activity for observability across
all DeAcero teams. Your job is to ensure every skill invocation, tool execution, and
knowledge event is captured and forwarded to the central observability service at
`AGENTIC_TELEMETRY_URL`.

## Circuit Breaker

**Always check first:** If `AGENTIC_TELEMETRY_URL` is not set, all calls are no-ops.
Never block agent execution on telemetry failure. Never raise exceptions.

```python
from .telemetry import is_telemetry_enabled
if not is_telemetry_enabled():
    pass  # continue normally
```

## When to Activate

| Situation | Event type | How |
|---|---|---|
| A skill in `.agents/skills/` is invoked | `skill.invoked` | `@skill_span` decorator or explicit call |
| A tool in `tools/` is executed | `tool.executed` | `@tool_span` decorator or explicit call |
| A new ADR, skill, domain doc, or tool is created | `knowledge.created` | `track_knowledge_created()` |
| An existing skill, ADR, or domain doc is read/used | `knowledge.used` | `track_knowledge_used()` |
| User asks for cost/usage report | — | Use `cornerstone report` CLI or `GET /v1/summary` |

## Emitting Events

### Using decorators (preferred)

```python
from .telemetry import skill_span, tool_span

@skill_span("software-archeologist", ".agents/skills/software/discovery/software-archeologist/SKILL.md")
def run_archeologist(model: str, input_tokens: int, output_tokens: int, **kwargs):
    ...

@tool_span("sql_topology", "tools/software/discovery/sql_topology.py")
def run_sql_topology(**kwargs):
    ...
```

### Explicit calls

```python
from .telemetry import send_event, track_knowledge_created, track_knowledge_used
import datetime

# skill.invoked
send_event({
    "event_type": "skill.invoked",
    "project_slug": "my-project",
    "github_username": "myuser",
    "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    "schema_version": "1.0",
    "payload": {
        "skill_name": "software-archeologist",
        "skill_path": ".agents/skills/software/discovery/software-archeologist/SKILL.md",
        "model": "claude-sonnet-4-6",
        "provider": "anthropic",
        "input_tokens": 1234,
        "output_tokens": 567,
        "estimated_cost_usd": 0.01221,
        "duration_ms": 3400,
    }
})

# knowledge.created
track_knowledge_created(kind="skill", path=".agents/skills/core/telemetry/SKILL.md")

# knowledge.used
track_knowledge_used(kind="skill", path=".agents/skills/software/discovery/software-archeologist/SKILL.md")
```

## Querying the Service (reports)

```bash
# Summary (last 30 days)
cornerstone report summary --url $AGENTIC_TELEMETRY_URL

# Cost by model
cornerstone report cost --url $AGENTIC_TELEMETRY_URL --group-by model

# Recent events
cornerstone report events --url $AGENTIC_TELEMETRY_URL --event-type skill.invoked --limit 20
```

Or directly via HTTP:
```
GET /v1/summary?from=2026-01-01T00:00:00Z&to=2026-03-17T23:59:59Z
GET /v1/events?event_type=skill.invoked&project_slug=my-project&limit=50
```

## Cost Estimation

Cost is computed automatically from `.telemetry/cost_rates.py`. The rate table covers
Claude (Anthropic) and Gemini (Google Vertex AI) models. For unknown models, `estimated_cost_usd`
is `null` — the event is still sent.

```python
from .telemetry.cost_rates import estimate_cost, get_provider

cost = estimate_cost("claude-sonnet-4-6", input_tokens=1000, output_tokens=500)
# → 0.0000105 USD

provider = get_provider("gemini-2.0-flash")
# → "google"
```

## Collaboration & Learning Mandate

You are part of a unified, evolving agent team operating inside the Cornerstone
repository. You **MUST** follow these principles in every session:

1. **Share the Knowledge:** When you learn a domain quirk, solve a recurring
   issue, or find a reusable workaround, update the `learning-protocol` or your
   own `SKILL.md`. Knowledge hoarding is an anti-pattern.
2. **Domain Specialization:** Do not hallucinate skills outside your domain.
   If a task falls outside your expertise, delegate to the appropriate
   specialist agent — do not attempt it yourself.
3. **Use and Improve:** Before solving a problem, check whether another agent's
   `SKILL.md` already covers it. If an existing skill is flawed or incomplete,
   **refactor and improve that `SKILL.md`** rather than bypassing it.
4. **Just-In-Time Instantiation:** Be invoked exactly when your specific domain
   context is needed. Avoid accumulating massive monolithic contexts.

> Authority: `AGENTS.md § 1b — Collaborative Agentic Philosophy`.
> These rules apply to every agent, every session, no exceptions.
