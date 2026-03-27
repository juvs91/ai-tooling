---
name: brainstorming
description: Design-first skill that transforms rough ideas into approved specs before any implementation. Use when starting a new feature, component, or architectural change. MANDATORY gate — no code until design is approved.
origin: obra/superpowers
allowed-tools: Read, Write, Glob, Grep
---

# Brainstorming

Design-first protocol. No implementation begins until the design is presented and approved.

## When to Activate

- Starting any new feature or component
- Architectural decisions (new service, new API, data model changes)
- "I want to build X" or "add feature Y"
- Ambiguous or underspecified requests
- When multiple valid approaches exist

## Hard Gate

**No code is written until the user approves the design.** This applies to:
- New features (even "simple" ones)
- New components or modules
- API design
- Data model changes
- Configuration changes with broad impact

Skipping this gate wastes effort on unexamined assumptions.

---

## Protocol (9 Steps)

### Step 1 — Explore Context

Read the relevant codebase areas silently:
- Existing patterns in the area
- Related models, services, and interfaces
- Current file structure and naming conventions
- Any existing specs or ADRs

### Step 2 — Offer Visual Aid

If the system has moving parts or a complex flow, offer a diagram:
> "Would a flow diagram or component diagram help clarify this design?"

### Step 3 — Ask Clarifying Questions (One at a Time)

Focus on:
- **Purpose**: What problem does this solve?
- **Constraints**: Performance, security, backwards compatibility?
- **Success criteria**: How do we know it works?
- **Scope**: What's explicitly out of scope?

```
Ask ONE question at a time. Prefer multiple-choice when possible:
"Should this be synchronous or async?
  A) Synchronous — simpler, blocks the request
  B) Async with background task — better UX for slow operations
  C) Event-driven — needed if other services consume this"
```

### Step 4 — Propose 2–3 Approaches

Present each approach with:
- Brief description
- Key trade-offs (complexity, performance, maintainability)
- Recommendation

```markdown
**Option A — [Name]** (Recommended)
- How it works: ...
- Pros: simple, follows existing pattern X
- Cons: less flexible for future Y

**Option B — [Name]**
- How it works: ...
- Pros: more flexible
- Cons: higher complexity, new dependency

**Option C — [Name]**
- Pros/Cons: ...
```

### Step 5 — Present Design Sections

After approach selection, present the design in sections:

1. **Data model** — new fields, tables, or schema changes
2. **API contract** — endpoints, request/response shapes
3. **Service logic** — key algorithms, business rules
4. **Error cases** — what can go wrong and how to handle it
5. **Testing plan** — unit + integration test coverage

Present ONE section at a time, wait for feedback.

### Step 6 — Obtain Explicit Approval

```
"Does this design look good, or are there changes before I start implementation?"
```

Do not proceed without a clear "yes", "looks good", "approved", or equivalent.

### Step 7 — Write Design Document

Save to: `docs/specs/YYYY-MM-DD-<topic>-design.md`

```markdown
# Design: <Feature Name>
Date: YYYY-MM-DD
Status: Approved

## Problem
...

## Approach
...

## Data Model
...

## API Contract
...

## Testing Plan
...
```

### Step 8 — Self-Review

Before handing off to implementation:
- [ ] Design covers all stated requirements
- [ ] Error cases documented
- [ ] No contradictions in the spec
- [ ] Testing plan is concrete (not "write tests")

### Step 9 — Hand Off to Implementation

Invoke the planning skill or begin implementation. Reference the design doc in your plan.

---

## Decomposition Principle

Break systems into smaller units:
- Each component has ONE clear purpose
- Well-defined interfaces between components
- Each component independently understandable and testable

When working in existing codebases:
- Follow established patterns
- Include only targeted improvements that serve the current goal
- Avoid unrelated refactoring ("while I'm here" changes)

---

## Anti-Patterns to Avoid

```
❌ "I'll just start coding and see what happens"
❌ Designing 5 things at once in a single brainstorm
❌ Skipping for "simple" tasks (even utility functions have assumptions)
❌ Proceeding without explicit user approval
❌ Solving a different problem than the one stated
```
