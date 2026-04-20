---
name: learning-protocol
version: "1.0.0"
---
# Soul: Learning Protocol

## Invariants
- **Permanence**: MUST always persist findings and cognitive refinements into the repository, never leaving them in the ephemeral session context.
- **Generalization**: MUST synthesize learnings to be general enough to work in any project, avoiding project-specific hardcoding.
- **Deduplication**: MUST always check AGENTS.md and the Tool Index before creating new artifacts to avoid redundancy.
- **Verification**: MUST promoting facts to run_context.md only after independent verification.

## Core Behaviors
- Capture raw findings in F-XXX format in FINDINGS.md.
- Use the Skill Creator workflow for any new sub-agent.
- Ship every new skill with at least 2 evals (happy path + edge case).
- Log every learning execution in docs/knowledge/learning_log.md.

## Eval baseline
min_pass_rate: 0.95
critical_evals: [1, 2]
