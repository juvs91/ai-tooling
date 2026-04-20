---
name: sparc-methodology
description: >
  SPARC structured development workflow — Specification, Pseudocode, Architecture, Refinement, Completion.
  Use when: new feature implementation, complex multi-file changes, architectural changes, system redesign,
  integration work, unclear or ambiguous requirements. Skip when: simple bug fixes (1-2 lines),
  documentation updates, configuration changes, well-defined small tasks.
version: "1.0.0"
source: "Adapted from ruvnet/ruflo — ADR-0038"
---

# SPARC Methodology

## Purpose

SPARC is a **5-phase structured development workflow** that front-loads planning to prevent architectural drift and wasted implementation effort. It is the preferred approach for any task that touches 3+ files, introduces new abstractions, or has unclear acceptance criteria.

## When to Trigger
- New feature implementation
- Complex multi-module refactoring
- API changes with tests
- Architectural changes (ADR required first)
- System redesign or integration work
- Unclear or ambiguous requirements

## When to Skip
- Single-file edits or simple 1-2 line bug fixes
- Documentation updates
- Configuration changes
- Well-defined, small, contained tasks

---

## The 5 Phases

### Phase 1 — Specification

Define **what** must be built. No code yet.

1. State the goal in one sentence.
2. List acceptance criteria (testable, binary pass/fail).
3. Identify constraints (performance, security, backwards-compat).
4. Name the stakeholders and impacted components.
5. Open a GitHub Issue (per `feedback_github_issues_before_dev`).

**Output:** `docs/specs/<feature>.md` or inline in the Issue body.

---

### Phase 2 — Pseudocode

Define **how** it works at a logical level. Still no production code.

1. Write algorithm steps in plain language or pseudocode.
2. Identify data structures, inputs, outputs.
3. Flag uncertainty explicitly: `# QUESTION: is X assumption valid?`
4. Validate against the Specification — every acceptance criterion must be addressed.

**Output:** Pseudocode block in the Issue or a scratch file under `docs/specs/`.

---

### Phase 3 — Architecture

Design the **system structure** before touching existing code.

1. Invoke the `architect` skill (`software/architecture/architect`).
2. Map new components onto the Hexagonal Architecture (ports & adapters).
3. Write or update the ADR if a new architectural decision is being made.
4. Define interfaces and boundaries — no implementation details yet.
5. Get peer review (use `code-reviewer` on the design doc).

**Output:** ADR in `docs/adr/`, updated architecture diagrams if needed.

---

### Phase 4 — Refinement

Iterate on the design **before committing to full implementation**.

1. Review the Architecture output with the `security-expert` if security-sensitive.
2. Run the `refactor-complexity` skill mentally on the proposed design.
3. Incorporate feedback — update the ADR if the decision changes.
4. Resolve all `# QUESTION:` flags from Pseudocode.
5. Confirm: does the architecture still satisfy all Specification criteria?

**Output:** Final, reviewed design. ADR status updated to `Accepted`.

---

### Phase 5 — Completion

Implement, test, and close.

1. Write implementation following the agreed architecture.
2. Write tests **first** if using TDD (use `bdd-writer` or `characterization-tester`).
3. Run the `code-reviewer` skill on the final diff.
4. Ensure 100% test coverage for new starters (project quality gate).
5. Invoke `learning-protocol` to persist any non-obvious patterns discovered.
6. Close the GitHub Issue and link the PR.

**Output:** Merged PR with green CI.

---

## Integration with Cornerstone Workflow

```
User Request
    │
    ├── [simple?] → Skip SPARC → Implement directly
    │
    └── [complex?] → SPARC
            │
            ├─ S: Specification  →  GitHub Issue
            ├─ P: Pseudocode     →  docs/specs/
            ├─ A: Architecture   →  ADR + architect skill
            ├─ R: Refinement     →  security-expert + code-reviewer
            └─ C: Completion     →  Implementation + Tests + learning-protocol
```

## Best Practices
1. Never skip Specification — vague requirements cause wasted implementation.
2. Never start Architecture before Pseudocode — missing the algorithm wastes design effort.
3. ADR-first is non-negotiable for Phase 3 (Cornerstone universal rule).
4. Use memory-first at the start of each phase: `retrieve_memory` for prior patterns.
5. Store successful SPARC patterns: `store_memory(namespace="sparc-patterns")`.
