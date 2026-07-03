# AGENTS.md — ai-tooling
**READ THIS FIRST. Every agent, every session, no exceptions.**

## 1. Project Mandate

* **Repository:** `github.com/deagentic/ai-tooling`
* **Description:** Anthropic→OpenAI proxy + agentic CI infrastructure tooling (Claude Code harness, provider routing, Docker-based).
* **Core Objective:** Evolve, document, and improve the proxy pipeline and agent skills with full traceability.

<!-- ROUTING_TABLE_START -->
## 2. Skill Routing — Tabla de Dispatch

**Lee esta tabla al inicio de cada sesión y ante cada nueva subtarea.**
Primera fila que hace match con el intent del usuario → lee ese SKILL.md → aplica protocolo.
Paths son relativos a `.agents/skills/`.

| Triggers | Skill — Capacidad que activa | Path | No usar para |
|---|---|---|---|
| nueva feature, "quiero hacer X", add, build, diseñar algo, ambiguous request | **brainstorming** — Gate de diseño obligatorio. Transforma ideas en specs aprobados. Hard gate: CERO código hasta que el usuario aprueba el diseño. | `core/brainstorming/SKILL.md` | Cambios mid-impl, bugfixes pequeños |
| nuevo script, herramienta, automation, utility, parser, analyzer | **tool-writer** — Crea herramientas reutilizables y cross-platform. Consulta AGENTS.md y docs/tools/ antes de crear para evitar duplicados. Produce script ejecutable con tests. | `core/tool-writer/SKILL.md` | Scripts one-shot desechables |
| aprendí algo, nuevo patrón reutilizable, problema recurrente resuelto, crear sub-agente | **learning-protocol** — Persiste conocimiento en ai-notes/ para que no muera con la sesión. Invocar al DESCUBRIR algo nuevo, no al inicio. | `core/learning-protocol/SKILL.md` | Inicio de sesión |
| orquestar, multi-agent pipeline, descomponer goal, delegar a especialistas | **orchestrator** — Descompone goals en 4 Core Pipelines (Archaeology, Docs, Architecture, Re-impl). Coordina agentes especialistas. The Queen del swarm. | `core/orchestrator/SKILL.md` | Single-agent tasks |
| agent drift, swarm, coordinación paralela, 2+ agentes simultáneos, consistencia | **swarm-anti-drift** — Coordinación multi-agente con anti-drift guarantees. Previene diseños contradictorios en trabajo paralelo. | `core/swarm-anti-drift/SKILL.md` | Single-agent |
| telemetría, observabilidad agente, métricas, trace agent, cost report | **telemetry** — Instrumenta actividad de agentes (skill.invoked, tool.executed, knowledge.created). Forward a servicio de observabilidad. | `core/telemetry/SKILL.md` | — |
| diseño de sistema, componentes, boundaries, trade-offs estructurales, integración | **architect** — Piensa en sistemas y límites. Evalúa trade-offs, diagnstica problemas estructurales, revisa diseños ANTES de codificar. Primer step: escribe ADR. | `software/architecture/architect/SKILL.md` | Implementación local |
| ADR, architecture decision record, documentar decisión técnica | **adr-writer** — Captura decisiones arquitectónicas en formato MADR. Inmutables — nunca se editan, se superseden. | `software/architecture/adr-writer/SKILL.md` | Decisiones de impl local |
| por qué está hardcodeado, extraer decisión del código, decision logger | **decision-logger** — Extrae decisiones implícitas en el código y las convierte en ADRs. | `software/architecture/decision-logger/SKILL.md` | — |
| reverse engineer, analizar codebase, execution graph, call tree, mapear sistema | **software-archeologist** — Ingeniería inversa del codebase: genera execution graph, mapea call trees, extrae API inventory, construye findings ledger. Entry point para análisis profundo. | `software/discovery/software-archeologist/SKILL.md` | — |
| backtrack, trazar comportamiento, de dónde viene X, trazar ejecución | **retro-engineer** — Análisis estructural automatizado. Produce retro-report.md. Complementa software-archeologist. | `software/discovery/retro-engineer/SKILL.md` | — |
| sistema desconocido, código nunca visto, unfamiliar codebase, primer contacto | **unknown-domain-protocol** — Protocolo para dominar sistemas desconocidos sin documentación. Produce mapa de dominio y plan de exploración. | `software/discovery/unknown-domain-protocol/SKILL.md` | — |
| cómo funciona facturación, rastrear bug descuento, entender proceso legacy, input vago o de negocio | **problem-intake** — Inicia sesión de arqueología en codebase legacy cuando el input es ambiguo u orientado a negocio. Produce mapa de dominio. | `software/discovery/problem-intake/SKILL.md` | — |
| analizar COBOL, entender copybook, interpretar código COBOL, legacy COBOL | **cobol-analyst** — Parsea y entiende archivos copybook COBOL en sistemas legacy. | `software/discovery/cobol-analyst/SKILL.md` | — |
| escribir tests, TDD, test-first, agregar cobertura, fix bug, nueva feature con tests | **tdd-workflow** — Enforces TDD: tests ANTES del código. Requiere 80%+ coverage (unit + integration + E2E). Aplica para features, bugfixes, y refactors. | `software/quality/tdd-workflow/SKILL.md` | Arreglar tests existentes únicamente |
| Gherkin, BDD, feature file, given/when/then, behavioral spec | **bdd-writer** — Traduce comportamiento del código en specs Gherkin ejecutables (pytest-bdd). Puente entre "qué hace el código" y "qué debería hacer". | `software/quality/bdd-writer/SKILL.md` | — |
| review PR, revisar código, code review, revisar diff, /code-review, revisar PR | **code-review** — Revisión de calidad, seguridad, correctitud y cobertura de ADRs sobre un diff o PR. Encuentra bugs, security holes, race conditions. | `software/quality/code-review/SKILL.md` | — |
| audit diff, revisar calidad general, code reviewer, antes de merge | **code-reviewer** — Quality/security/correctness reviewer para código general. Usa después de escribir, antes de merge. | `software/quality/code-reviewer/SKILL.md` | — |
| verificar implementación, pre-PR, quality gate, terminé de implementar | **verification-loop** — Loop de verificación post-implementación. Corre tests, revisa cobertura, verifica reqs antes de PR. | `software/quality/verification-loop/SKILL.md` | Inicio de tarea |
| linting, formateo, coding standards, ruff, eslint, estilo de código | **coding-standards** — Enforces estilo consistente por lenguaje (ruff para Python, eslint para TS). | `software/quality/coding-standards/SKILL.md` | — |
| eval harness, evaluar output del modelo, A/B prompt, EDD, scoring | **eval-harness** — Framework de evaluación para outputs de LLM. Produce scores y comparaciones. | `software/quality/eval-harness/SKILL.md` | — |
| caracterización, legacy behavior capture, tests de caracterización | **characterization-tester** — Captura comportamiento de código legacy para refactors seguros. Genera tests que documentan el comportamiento actual. | `software/quality/characterization-tester/SKILL.md` | Código nuevo |
| refactor complejidad, reducir cognitive load, simplificar código complejo | **refactor-complexity** — Identifica y reduce complejidad ciclomática y cognitiva. Produce código más simple sin cambiar behavior. | `software/quality/refactor-complexity/SKILL.md` | Refactors de naming |
| documentación, MkDocs, diataxis, docstrings, README, docs site | **technical-writer** — Governance de documentación. MkDocs, Diataxis framework, docstrings, static sites. "Docs or it didn't happen." | `software/quality/technical-writer/SKILL.md` | Comentarios inline |
| playwright, E2E test, browser test, test flaky, Cypress migration | **playwright-pro** — Browser testing con Playwright: page objects, locators, intercepts, fixtures, CI integration. | `software/quality/playwright-pro/SKILL.md` | Unit tests |
| implementar feature con ciclo rojo-verde-refactor, test primero siempre, TDD estricto | **tdd-developer** — Enforces ciclo Red→Green→Refactor estricto. NUNCA escribe implementación sin test fallante previo. | `software/quality/tdd-developer/SKILL.md` | Arreglar tests ya existentes |
| validar escenarios BDD, testing exploratorio, verificar criterios aceptación, gate pre-merge | **qa-validator** — Valida BDD scenarios post-implementación, ejecuta testing exploratorio. Gate final de calidad antes de merge. | `software/quality/qa-validator/SKILL.md` | Generación de tests |
| revisar SonarQube, fijar vulnerabilidad sonar, score calidad código, métricas sonar | **sonarqube-mgmt** — Administra issues SonarQube, revisa vulnerabilidades, automatiza mejoras de calidad. | `software/quality/sonarqube-mgmt/SKILL.md` | — |
| planificar feature, validar criterios aceptación, descomponer story, definition of done | **tech-lead** — Invocar al START de feature/story. Valida criterios, descompone en task graph, requiere DoD clara. | `software/quality/tech-lead/SKILL.md` | Mid-implementación |
| React, Next.js, TypeScript, Tailwind, componente React, hook, props | **senior-frontend** — React/Next.js patterns: component optimization, bundle analysis, accessibility, TypeScript, Tailwind. | `software/frontend/senior-frontend/SKILL.md` | Backend puro |
| App Router, server component, RSC, client component, Vercel, Next.js routing | **nextjs** — Next.js App Router patterns: RSC vs client components, data fetching, layouts, middleware. | `software/frontend/nextjs/SKILL.md` | — |
| generar componentes React, convertir diseño a código, componente modular React, Vite | **react-components** — Convierte diseños Stitch en componentes modulares Vite/React con validación basada en AST. | `frontend/react-components/SKILL.md` | — |
| integrar shadcn-ui, customizar componente shadcn, instalar shadcn, shadcn/ui | **shadcn-ui** — Guía experta para shadcn/ui: instalación, customización, temas, variantes, mejores prácticas. | `frontend/shadcn-ui/SKILL.md` | — |
| diseñar interfaz Stitch, crear design system, generar pantalla alta fidelidad, editar diseño | **stitch-design** — Punto entrada unificado para trabajo de diseño Stitch: enhancement de prompts, síntesis design system, generación de pantallas. | `frontend/stitch-design/SKILL.md` | — |
| construir sitio web Stitch, iterar diseño Stitch, crear aplicación Stitch iterativamente | **stitch-loop** — Construye websites iterativamente con Stitch usando patrón autónomo de baton-passing. | `frontend/stitch-loop/SKILL.md` | — |
| mejorar prompt diseño, optimizar descripción UI, refinar spec interfaz, enriquecer prompt Stitch | **enhance-prompt** — Transforma ideas vagas de UI en prompts pulidos optimizados para Stitch. | `frontend/enhance-prompt/SKILL.md` | — |
| crear DESIGN.md, sintetizar sistema diseño, documentar design system, analizar proyecto Stitch | **design-md** — Analiza proyectos Stitch y sintetiza un sistema de diseño semántico en archivos DESIGN.md. | `frontend/design-md/SKILL.md` | — |
| crear video walkthrough, generar demostración visual, producir video Remotion, video tutorial | **remotion** — Genera videos de walkthrough desde proyectos Stitch usando Remotion con transiciones y overlays. | `frontend/remotion/SKILL.md` | — |
| UI design, UX, user flow, wireframe, interfaz de usuario, accesibilidad | **ux-expert** — User experience advisor. User goals, mental models, friction points. Nielsen's heuristics, accessibility. | `design/ux-expert/SKILL.md` | Backend puro |
| FastAPI, Python service, Pydantic, async Python, SOLID Python, endpoint | **senior-backend** — Backend engineering Python/FastAPI: SOLID, DRY, API design, N+1 queries, async patterns, JWT/OAuth, rate limiting, caching, middleware. | `software/backend/senior-backend/SKILL.md` | Go code, JS/TS |
| diseñar API FastAPI, optimizar query, implementar patrón async, seguridad backend Python, DDD | **python-senior-backend** — Ingeniería backend Python/FastAPI con DDD: entidades, servicios, repositorios, design de API, async, seguridad, testing. | `backend/python-senior-backend/SKILL.md` | Go code, JS/TS |
| pytest, fixtures, mock, async test, conftest, parametrize, Python test | **python-testing** — Testing patterns Python: pytest fixtures, async tests, mocking, coverage, property-based testing. | `software/backend/python-testing/SKILL.md` | Go tests, JS tests |
| Go, Gin, goroutine, interface, idiomatic Go, concurrencia, channels, context | **golang-patterns** — Go idioms: interfaces, concurrency (goroutines, channels, context cancellation), error wrapping, dependency injection, project layout. | `software/language/go/golang-patterns/SKILL.md` | Python/JS/TS |
| go test, table-driven tests, benchmark, fuzzing, testify, Go coverage | **golang-testing** — Testing en Go: table-driven, subtests, mocks con interfaces, benchmarks, fuzzing, integration tests. | `software/language/go/golang-testing/SKILL.md` | — |
| base de datos, SQL, schema, migración, AlloyDB, NoSQL, ORM, indexing, queries | **database-expert** — Data modeling, SQL/NoSQL, time-series, query optimization, migrations. PostgreSQL, BigQuery, AlloyDB, Redis. | `infrastructure/database-expert/SKILL.md` | — |
| CI/CD, pipeline, Docker, deploy, GitHub Actions, Terraform, IaC, GitOps, Cloud Run | **gitops-expert** — Source control, CI/CD, deployment, IaC, secrets management, branching strategy. | `infrastructure/gitops-expert/SKILL.md` | — |
| monorepo, trunk-based, sparse checkout, worktree, tag por proyecto, independencia de despliegue, release por proyecto, ramas compartidas contaminan ambientes, hotfix paralelo | **gitops-monorepo** — Estrategia GitOps Deacero-específica: tag=versión en prod, sparse checkout, worktrees (ADR-0008), hotfix protocol, exportable a otros proyectos. | `infrastructure/gitops-monorepo/SKILL.md` | — |
| empaquetar Python, preparar PyPI, configurar distribución, entry points, pyproject.toml | **distribution-expert** — Prepara CLIs Python para release en PyPI: entry points, versioning, instalación, debugging. | `software/cli/distribution-expert/SKILL.md` | — |
| mejorar experiencia CLI, revisar comandos CLI, diseñar flags, estructurar argparse, Typer, Click | **cli-ux-expert** — Diseña y revisa UX de interfaces CLI: parsing, help text, output format, inputs interactivos. | `software/cli/cli-ux-expert/SKILL.md` | — |
| Claude API, Anthropic SDK, streaming, tool use, prompt caching, Agent SDK, MCP client | **claude-api** — Patterns Python/TypeScript con Anthropic API: streaming, tool use, vision, batches, Agent SDK. | `integrations/claude-api/SKILL.md` | — |
| investigar, web research, multi-fuente, buscar información, fact-check | **deep-research** — Web research multi-source con síntesis. Produce research report con fuentes verificadas. | `integrations/deep-research/SKILL.md` | — |
| docs de librería, framework docs, how does X work, look up API, Context7 | **documentation-lookup** — Busca documentación actualizada vía Context7/web. Usar cuando docs pueden haber cambiado desde el training. | `integrations/documentation-lookup/SKILL.md` | — |
| seguridad, auth, crypto, secrets, access control, JWT, OAuth, threat model, OWASP | **security-expert** — Threat modeler y cryptography reviewer. OWASP Top 10, key management, auth patterns, timing attacks, protocol security. | `security/security-expert/SKILL.md` | — |
| security review, vulnerabilidades en diff, revisar auth code, pen test | **security-review** — Security code review de diffs específicos. Produce findings con severity rating. | `security/security-review/SKILL.md` | Review general de calidad |
| REST API, endpoint, OpenAPI, paginación, error codes, versioning, rate limiting | **api-design** — REST API design: resource naming, status codes, pagination, filtering, error responses, versioning. | `software/api/api-design/SKILL.md` | — |
| backend patterns, service layer, error handling, arquitectura de servicio | **backend-patterns** — Arquitectura backend: service/repository, error propagation, retry/circuit breaker. | `software/api/backend-patterns/SKILL.md` | — |
| MCP server, build MCP tool, stdio vs HTTP, MCP resource, MCP prompt | **mcp-server-patterns** — Design e implementación de MCP servers. stdio vs Streamable HTTP, tools vs resources. | `software/api/mcp-server-patterns/SKILL.md` | — |
| implementar feature complejo, arquitectura multi-archivo, cambio arquitectónico, SPARC | **sparc-methodology** — Workflow SPARC estructurado (Specification, Pseudocode, Architecture, Refinement, Completion) para features complejas. | `software/sparc/SKILL.md` | Features simples |
| implementar servicio backend TypeScript, DDD, entidad de dominio, repositorio Prisma, Express | **backend-developer** — Desarrolla y refactoriza código TypeScript backend con DDD: entidades, servicios, repositorios, controladores. | `project/backend-developer/SKILL.md` | Python/Go |
| implementar feature React proyecto, revisar componente, refactorizar módulo frontend | **frontend-developer** — Desarrolla features React siguiendo arquitectura basada en componentes, servicios, routing y estado. | `project/frontend-developer/SKILL.md` | Backend puro |
| planear ticket, desglosar story, Jira ticket, "quiero implementar X" | **ticket-planner** — Planificación Jira: 11-fuentes context, grokking refinement, pasos atómicos. | `workflow/ticket-planner/SKILL.md` | Tareas mid-impl |
| implementar ticket, ejecutar plan, "implementa X", "codifica Y" | **ticket-implementation** — 7-hop multihop grounding: ejecución atómica con verificación iterativa. | `workflow/ticket-implementation/SKILL.md` | Sin plan previo |
| ¿qué hago?, ambiguous intent, routing, workflow gate | **workflow-coordinator** — Detecta intent, verifica estado del workflow, enruta al command apropiado. | `workflow/workflow-coordinator/SKILL.md` | — |
| buscar stored procedure legacy, SP SQL Server, legacy business logic | **squit** — Búsqueda semántica de SPs legacy de Deacero (5.7M objetos SQL). | `archaeology/squit/SKILL.md` | — |
<!-- ROUTING_TABLE_END -->

### Compound Tasks — Skills que se apilan

Algunos requests disparan múltiples skills. Cargarlos en este orden:
1. **brainstorming** — siempre primero si es feature nueva o request ambiguo
2. Skill de dominio: `golang-patterns`, `senior-backend`, `senior-frontend`, etc.
3. **tdd-workflow** — si hay implementación de código
4. **security-expert** o **security-review** — si hay auth, crypto, o secrets
5. **verification-loop** — siempre al terminar implementación, antes de PR
6. **learning-protocol** — al cerrar sesión si hubo nuevo conocimiento reutilizable

Ejemplo: "agrega autenticación JWT a la API Python" →
`brainstorming` → `senior-backend` → `tdd-workflow` → `security-expert` → `verification-loop`

### Mid-Session Re-Trigger

Re-verificar la tabla de routing cuando:
- El dominio de la tarea cambia (ej: infra → API design, backend → frontend)
- El usuario dice "ahora también...", "y además...", "y agrega..."
- Cambias de tipo de archivo (`.py` → `.go`, servicio → test, código → docs)
- Empiezas una subtarea claramente diferente dentro de la misma sesión

## 3. MCP Tool Usage — Guardrail

**MCP tools solo bajo petición explícita del usuario.**
Nunca invoques herramientas MCP (Atlassian, AlloyDB, Squit, CloudSQL, Serper, Playwright, etc.) de forma autónoma. Solo úsalas cuando el usuario lo pida directamente. La presencia de un MCP en `.mcp.json` no es una invitación a usarlo proactivamente.

## 4. DEDUPLICATION MANDATE
Before writing any new tool, script, or proposing a new agent, you MUST consult this `AGENTS.md` and `docs/tools/index.md` (if it exists). Reuse and refine existing capabilities. If merging two similar tools, keep the CLI contract compatible.

## 4. Context & Knowledge Management

```
Raw discovery
    |
docs/findings/FINDINGS.md  <-  append F-XXX entry FIRST
    |
context/run_context.md        <-  update confirmed facts
    |
docs/knowledge/               <-  canonical reference docs
    |
docs/adr/                     <-  open ADR ONLY when a decision is made
```

## 5. ADR-First Mandate

**HARD STOP. No exceptions. No bypasses except trivial fixes.**
> **You must write an ADR before changing any proxy architecture or core agent skill.**

1. **Discovery** — identify the design decision or integration change needed
2. **Write the ADR** — create `docs/adr/ADR-NNNN-<decision-title>.md`
3. **Then write the code** — commit the ADR and the code change together

## 6. Agent Skills (`.agents/skills/`)

| Category | Skill | Purpose |
|----------|-------|---------|
| Workflow | `workflow-coordinator` | Route requests to appropriate workflows with guard enforcement |
| Workflow | `ticket-planner` | Plan Jira tickets with pre-planning bloat and grokking refinement |
| Workflow | `ticket-implementation` | Execute plans via 7-hop multihop grounding process |
| Core | `learning-protocol` | Persist new domain knowledge, patterns, and learnings |
| Core | `tool-writer` | Create new tools/scripts — always delegate, never ad-hoc |
| Architecture | `architect` | System design, component boundaries, ADR-first |
| Architecture | `adr-writer` | Write and maintain Architecture Decision Records |
| Architecture | `decision-logger` | Extract decisions embedded in code |
| Discovery | `software-archeologist` | Reverse-engineer codebases, build executions graph |
| Discovery | `retro-engineer` | Backtrack a specific behavior to its entry point |
| Discovery | `unknown-domain-protocol` | Protocol for encountering completely unknown domains |
| Quality | `bdd-writer` | Write Gherkin specs and BDD feature files |
| Quality | `code-reviewer` | Code quality, security, ADR coverage check |
| Quality | `tdd-workflow` | Test-driven development — write tests first, then code |
| Quality | `coding-standards` | Universal Python/JS/TS linting and formatting standards |
| Quality | `verification-loop` | End-to-end verification after implementation |
| Quality | `eval-harness` | Formal eval framework for scoring model outputs |
| Infrastructure | `database-expert` | AlloyDB, SQL, MCP data tools, schema design |
| Infrastructure | `gitops-expert` | Docker, CI/CD, GitHub Actions, deployment pipelines |
| Integrations | `claude-api` | Anthropic SDK, Claude API, streaming, tool use, caching |
| Integrations | `documentation-lookup` | Live docs via Context7 MCP for any library/framework |
| Integrations | `deep-research` | Multi-source research using serper + context7 |
| Security | `security-expert` | Threat modeling, vulnerability analysis, hardening |
| Security | `security-review` | Code-level security checks on diffs and PRs |
| API | `api-design` | REST endpoint design, OpenAPI specs, versioning |
| API | `backend-patterns` | Error handling, pagination, rate limiting, service patterns |
| API | `mcp-server-patterns` | Building MCP servers (stdio vs HTTP, tools, resources) |
| Go | `golang-patterns` | Idiomatic Go: interfaces, errors, concurrency |
| Go | `golang-testing` | Go tests, table-driven tests, benchmarks |
| Archaeology | `squit` | Semantic search of Deacero legacy SQL (5.7M objects) |

Full skill index: `.agents/skills/skills.md`

### Command Workflow Integration

| Trigger | Skill(s) Invoked | Purpose |
|---------|-----------------|---------|
| `enforce-workflow` | `workflow-coordinator` | Route user intent to appropriate workflow |
| ticket ID + plan | `ticket-planner` → `ticket-implementation` | Plan then execute (workflow-coordinator orchestrates) |

**Pattern:** workflow-coordinator enruta, skills implementan. Separation of concerns = reusabilidad.

## 7. Self-Repair Mandate

If you detect any discrepancies in agent documentation or configurations (e.g., outdated file paths, broken skill references), self-repair them by fixing references across the workspace. If the issue stems from an upstream template, submit a PR to fix it at the source.

## 8. Project-Specific Context

- **Proxy container**: `ai-tooling-proxy_cloud-1` on port `8083`
- **Hot-reload**: Uvicorn `--reload` — file changes in `vendor/claude-code-proxy/` apply immediately
- **Health check**: `curl http://127.0.0.1:8083/health | jq .`
- **Logs**: `curl "http://127.0.0.1:8083/api/logs?n=20" | jq .`
- **Provider configs**: `profile-envs/` + `cloud-provider-ymls/`
- **MCP servers**: alloydb, atlassian, bitbucket, squit, cloudsql, context7, serper, playwright, sequential-thinking, memory (see `CLAUDE.md`)
- **Session artifacts**: `ai-notes/` (learnings, analyses — shared with team and future agents)
