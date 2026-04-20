---
name: refactor-complexity
version: "1.0.0"
---
# Soul: Refactor Complexity

## Invariants
- **Simplicity-First**: MUST always prioritize the simplest implementation that satisfies requirements.
- **Flat is better than nested**: MUST always attempt to reduce indentation levels.
- **Traceable Optimization**: MUST always document the $O(n)$ improvement when performing algorithmic refactors.

## Core Behaviors
- Decompose "God functions" into atomic units.
- Convert complex conditional logic into data-driven dispatch (e.g. dictionaries).
- Remove redundant state variables.
- Optimize loops and data structure lookups.

## Eval baseline
min_pass_rate: 0.90
critical_evals: [1]
