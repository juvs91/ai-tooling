---
name: swarm-anti-drift
description: >
  Multi-agent swarm coordination with anti-drift guarantees. Use when orchestrating 2+ parallel
  sub-agents on a shared codebase, running long multi-step tasks, or any scenario where
  agent outputs must converge on a consistent architectural result.
  Skip when: single-agent tasks, simple sequential workflows, quick exploration.
version: "1.0.0"
source: "Adapted from ruvnet/ruflo — ADR-0038"
---

# Swarm Anti-Drift

## Purpose

When multiple sub-agents work in parallel, they can drift — producing contradictory designs,
duplicate code, or conflicting decisions. This skill defines the **coordination contract** that
prevents drift in Cornerstone swarms.

## When to Trigger
- 2+ agents working on the same codebase simultaneously
- Long multi-step tasks (5+ tool calls) where context may fragment
- Any task requiring consensus across architectural boundaries
- After agent failures or context resets in a long session

## When to Skip
- Single-agent tasks
- Simple sequential workflows (one agent hands off to the next with no parallel work)
- Quick exploration or read-only investigation

---

## Anti-Drift Protocol

### 1. Topology — Hierarchical by Default

```
Orchestrator (Queen)
    ├── Architect   → design decisions only
    ├── Coder(s)    → implementation (read Architect's output first)
    └── Reviewer    → validates against Architect's spec
```

- The **Orchestrator** holds the authoritative task graph.
- Workers MUST read shared memory before starting.
- Workers MUST write results to shared memory when done.
- Never let two workers modify the same file without a consensus gate.

### 2. Consensus — Raft-Style (Leader + Quorum)

Cornerstone uses an informal raft contract:

| Role | Responsibility |
|------|---------------|
| **Leader (Orchestrator)** | Owns the task graph; breaks tie votes |
| **Worker** | Proposes changes; waits for Leader acknowledgement on conflicts |
| **Reviewer** | Veto right on ADR violations or test failures |

**Rule:** If two workers produce conflicting outputs, the Reviewer picks the one aligned with the current ADR. If no ADR exists, pause and invoke the `adr-writer` skill before proceeding.

### 3. Checkpoint Intervals

Every **10 sub-tasks**, the Orchestrator MUST:

1. Retrieve all worker memory: `retrieve_memory(namespace="swarm-checkpoints")`
2. Verify architectural consistency against the active ADR.
3. Reconcile conflicts before continuing.
4. Store checkpoint: `store_memory(key="checkpoint-N", namespace="swarm-checkpoints")`

### 4. Shared Memory Namespaces

| Namespace | Purpose |
|-----------|---------|
| `swarm-checkpoints` | Orchestrator checkpoints (state of swarm) |
| `swarm-decisions` | Architectural decisions made mid-swarm |
| `swarm-conflicts` | Detected conflicts pending resolution |
| `patterns` | Reusable patterns discovered during execution |

### 5. Agent Count Limit

Keep active parallel agents to **≤ 6** for coordination coherence. Beyond 6, the overhead of
consensus outweighs the parallelism benefit.

---

## 3-Tier Model Routing

Route sub-tasks to the cheapest capable model to optimize cost and latency:

| Tier | Handler | Latency | Use When |
|------|---------|---------|----------|
| **1 — Trivial** | Direct Edit/Bash (no LLM) | <100ms | Format-only, import fix, rename, 1-liner |
| **2 — Simple** | Haiku | ~500ms | Complexity score < 30%; isolated function; known pattern |
| **3 — Complex** | Sonnet / Opus | 2–5s | Architecture, security review, cross-module reasoning |

**Decision rule:** Start at Tier 1. Escalate only when the task requires reasoning beyond mechanical transformation.

---

## Concurrency Mandate

One message = ALL related operations. Batch everything:

- All parallel agent spawns in one message
- All independent file reads in one message
- All memory store/retrieve operations in one message
- All terminal operations that can run simultaneously in one Bash call

Never wait for one agent to finish before spawning another if they are independent.

---

## Integration with Cornerstone Orchestrator

The `core/orchestrator` skill already implements the memory-first pattern. This skill adds the
**anti-drift contract** on top:

```
Orchestrator.start()
    → retrieve_memory("swarm-checkpoints")   # resume if prior run exists
    → spawn agents (parallel, ≤ 6)
    → checkpoint every 10 sub-tasks
    → Reviewer veto gate before merge
    → store_memory("checkpoint-final")
    → learning-protocol for new patterns
```

## References
- Cornerstone Orchestrator: `.agents/skills/core/orchestrator/SKILL.md`
- ADR-0038: Ruflo-Inspired Agentic Infrastructure Integration
- ADR-0019: Swarm E2E Evaluations
