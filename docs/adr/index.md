# Architecture Decision Records

| ID | Title | Status | Date | Domain |
|----|-------|--------|------|--------|
| [ADR-0001](ADR-0001-adopt-agentic-ci-skill-system.md) | Adopt Agentic CI Skill System | Accepted | 2026-04-28 | Agent Infrastructure |
| [ADR-0002](ADR-0002-proxy-multi-model-agentic-enhancements.md) | Proxy Multi-Model Agentic Enhancements | Accepted | 2026-04-28 | Proxy |
| [ADR-0003](ADR-0003-proxy-code-organization-refactoring.md) | Proxy Code Organization Refactoring | Accepted | 2026-04-28 | Proxy |
| [ADR-0004](ADR-0004-long-session-reliability-multi-provider-proxy.md) | Long-Session Reliability Architecture for Multi-Provider Proxy | Accepted | 2026-04-30 | Proxy / Reliability |
| [ADR-0005](ADR-0005-configurable-anthropic-endpoint-path.md) | Configurable Anthropic Passthrough Endpoint Path | Accepted | 2026-05-26 | Proxy |
| [ADR-0008](ADR-0008-plan-lock-implicit-exit-signal4.md) | P0 PLAN_LOCK — Signal 4 Implicit Exit vía CC UI Mode Change | Accepted | 2026-06-25 | Proxy |
| [ADR-0029](ADR-0029-strip-server-tools-for-non-anthropic-providers.md) | Strip Anthropic Server-Side Tools for Non-Anthropic Providers | Accepted | 2026-07-21 | Proxy |
| [ADR-0030](ADR-0030-plan-mode-session-id-fallback.md) | Deterministic Session ID Fallback for Plan-Mode Persistence | Accepted | 2026-07-22 | Proxy |
| [ADR-0031](ADR-0031-completion-claim-grounding.md) | Detect Ungrounded Completion Claims | Accepted | 2026-07-27 | Proxy |
| [ADR-0032](ADR-0032-decompose-compressor-into-session-package.md) | Decompose `compressor.py` into a `session/` Package | Accepted | 2026-07-27 | Proxy |
| [ADR-0033](ADR-0033-generality-claim-grounding.md) | Detect Ungrounded Generality Claims | Accepted | 2026-07-27 | Proxy |
| [ADR-0034](ADR-0034-manifiesto-fuente-de-verdad-version.md) | El manifiesto de cada proyecto es la fuente de verdad de la versión | Accepted | 2026-08-02 | GitOps |
| [ADR-0035](ADR-0035-unificacion-fuente-verdad-adr-gate.md) | Cerrar las fuentes de verdad restantes del ADR gate en el bootstrap | Accepted | 2026-08-02 | GitOps |
| [ADR-0036](ADR-0036-state-assertion-verification-framework.md) | State-Assertion Verification Framework | Accepted | 2026-08-03 | Proxy |
| [ADR-0037](ADR-0037-eager-plan-mode-tools-fragile-models.md) | Eager Plan-Mode Tool Loading for Fragile Orchestration Models | Accepted | 2026-08-02 | Proxy |
| [ADR-0038](ADR-0038-deferred-denial-rule.md) | Deferred-Tool Denial Rule | Accepted | 2026-08-03 | Proxy |
| [ADR-0039](ADR-0039-no-progress-rule.md) | No-Progress Rule (Failure-Driven, Reusing Existing Guardrails) | Accepted | 2026-08-03 | Proxy |
| [ADR-0040](ADR-0040-exploration-grounding-rule.md) | Exploration-Grounding Rule (and Why No New Orientation-Checkpoint Rule Ships) | Accepted | 2026-08-03 | Proxy |
| [ADR-0041](ADR-0041-task-scope-file-per-session.md) | Task-Scope File Per Session, Not Per Project | Accepted | 2026-08-03 | Agent Infrastructure |
| [ADR-0042](ADR-0042-autoload-workflow-coordinator-skill-vs-agent-tool.md) | Autoload de workflow-coordinator vía Skill tool, no Agent tool | Accepted | 2026-08-03 | Agent Infrastructure |
| [ADR-0043](ADR-0043-skills-de-dominio-via-skill-tool-nativo.md) | Skills de dominio se exponen vía el tool Skill nativo, no vía Agent-subagent | Accepted | 2026-08-03 | Agent Infrastructure |
| [ADR-0044](ADR-0044-worktree-gitops-integration.md) | Integración de Git Worktree al GitOps Monorepo (renumerado de ADR-0008, duplicado) | Aceptado | 2026-07-02 | GitOps |
| [ADR-0045](ADR-0045-check-skill-frontmatter-tool.md) | Nuevo checker `tools/check_skill_frontmatter.py` | Accepted | 2026-08-03 | Agent Infrastructure |
| [ADR-0046](ADR-0046-check-adr-sections-tool.md) | Nuevo checker `tools/check_adr_sections.py` | Accepted | 2026-08-03 | Agent Infrastructure |
| [ADR-0047](ADR-0047-auto-sync-daily-hook.md) | Hook `SessionStart` para sincronización diaria de skills y hooks en proyectos hijos | Accepted | 2026-08-04 | Agent Infrastructure |
| [ADR-0048](ADR-0048-homologacion-gitops-monorepo-commons.md) | Homologación GitOps con `commons` — puerto bidireccional de mejoras validadas en producción | Aceptado | 2026-08-04 | GitOps |
