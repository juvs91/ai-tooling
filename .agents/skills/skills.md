# Agent Skills — Full Index

> **Routing primario:** `AGENTS.md` (tabla de dispatch con triggers y descripción de capacidad).
> Este archivo es para discovery mid-session cuando necesitas encontrar un skill por keyword.

| Skill | Path | Capacidad | Triggers clave |
|---|---|---|---|
| **brainstorming** | `core/brainstorming/SKILL.md` | Gate de diseño obligatorio: transforma ideas en specs aprobados antes de escribir código | nueva feature, build, add, diseñar, "quiero hacer X", ambiguous request |
| **learning-protocol** | `core/learning-protocol/SKILL.md` | Persiste conocimiento en ai-notes/ para que no muera con la sesión | aprendí algo, nuevo patrón, problema recurrente, sub-agente nuevo |
| **tool-writer** | `core/tool-writer/SKILL.md` | Crea herramientas reutilizables cross-platform con deduplication check | nuevo script, herramienta, automation, utility, parser, analyzer |
| **orchestrator** | `core/orchestrator/SKILL.md` | Descompone goals complejos en pipelines multi-agente y coordina especialistas | orquestar, multi-agent, descomponer goal, delegar, pipelines |
| **swarm-anti-drift** | `core/swarm-anti-drift/SKILL.md` | Anti-drift para trabajo paralelo de 2+ agentes, previene diseños contradictorios | swarm, agent drift, coordinación paralela, consistencia multi-agent |
| **telemetry** | `core/telemetry/SKILL.md` | Instrumenta actividad de agentes: skill.invoked, tool.executed, knowledge.created | telemetría, observabilidad agente, métricas, cost report |
| **architect** | `software/architecture/architect/SKILL.md` | Diseño de sistemas, boundaries, trade-offs. Produce ADR antes de código | diseño de sistema, componentes, boundaries, trade-offs, integración |
| **adr-writer** | `software/architecture/adr-writer/SKILL.md` | Captura decisiones en formato MADR. Inmutables, se superseden, nunca editan | ADR, architecture decision record, documentar decisión técnica |
| **decision-logger** | `software/architecture/decision-logger/SKILL.md` | Extrae decisiones implícitas del código y las convierte en ADRs | por qué hardcodeado, extraer decisión, decision logger |
| **software-archeologist** | `software/discovery/software-archeologist/SKILL.md` | Ingeniería inversa: execution graph, call trees, API inventory, findings ledger | reverse engineer, analizar codebase, execution graph, call tree, mapear |
| **retro-engineer** | `software/discovery/retro-engineer/SKILL.md` | Análisis estructural automatizado. Produce retro-report.md | backtrack, trazar comportamiento, de dónde viene X, trazar ejecución |
| **unknown-domain-protocol** | `software/discovery/unknown-domain-protocol/SKILL.md` | Protocolo para dominar sistemas desconocidos sin documentación | sistema desconocido, código nunca visto, unfamiliar codebase, primer contacto |
| **tdd-workflow** | `software/quality/tdd-workflow/SKILL.md` | TDD: tests ANTES del código, 80%+ coverage (unit + integration + E2E) | escribir tests, TDD, test-first, agregar cobertura, fix bug, nueva feature |
| **bdd-writer** | `software/quality/bdd-writer/SKILL.md` | Gherkin ejecutable (pytest-bdd): puente entre "qué hace" y "qué debería hacer" | Gherkin, BDD, feature file, given/when/then, behavioral spec |
| **code-review** | `software/quality/code-review/SKILL.md` | Review de PR/diff: calidad, seguridad, correctitud, cobertura de ADRs | review PR, revisar código, /code-review, revisar PR, revisar diff |
| **code-reviewer** | `software/quality/code-reviewer/SKILL.md` | Quality/security/correctness reviewer para código general antes de merge | audit diff, revisar calidad general, code reviewer, antes de merge |
| **coding-standards** | `software/quality/coding-standards/SKILL.md` | Estilo consistente por lenguaje: ruff para Python, eslint para TS | linting, formateo, coding standards, ruff, eslint, estilo |
| **verification-loop** | `software/quality/verification-loop/SKILL.md` | Loop post-implementación: tests, cobertura, verificación de reqs antes de PR | verificar impl, pre-PR, quality gate, terminé de implementar |
| **eval-harness** | `software/quality/eval-harness/SKILL.md` | Framework de evaluación para outputs de LLM. Produce scores y comparaciones | eval harness, evaluar output modelo, A/B prompt, EDD, scoring |
| **characterization-tester** | `software/quality/characterization-tester/SKILL.md` | Captura comportamiento legacy para refactors seguros. Tests que documentan el estado actual | caracterización, legacy behavior capture, tests de caracterización |
| **refactor-complexity** | `software/quality/refactor-complexity/SKILL.md` | Reduce complejidad ciclomática y cognitiva sin cambiar behavior | refactor complejidad, reducir cognitive load, simplificar código complejo |
| **technical-writer** | `software/quality/technical-writer/SKILL.md` | Governance de docs: MkDocs, Diataxis, docstrings, static sites | documentación, MkDocs, diataxis, docstrings, README, docs site |
| **playwright-pro** | `software/quality/playwright-pro/SKILL.md` | Browser testing: page objects, locators, intercepts, fixtures, CI | playwright, E2E test, browser test, test flaky, Cypress migration |
| **senior-frontend** | `software/frontend/senior-frontend/SKILL.md` | React/Next.js: component optimization, bundle, accessibility, TypeScript, Tailwind | React, Next.js, TypeScript, Tailwind, componente, hook, props |
| **nextjs** | `software/frontend/nextjs/SKILL.md` | App Router: RSC vs client, data fetching, layouts, middleware, Vercel | App Router, server component, RSC, client component, Vercel |
| **ux-expert** | `design/ux-expert/SKILL.md` | UX advisor: user goals, mental models, friction, Nielsen's heuristics, accessibility | UI design, UX, user flow, wireframe, interfaz, accesibilidad |
| **senior-backend** | `software/backend/senior-backend/SKILL.md` | Python/FastAPI: SOLID, DRY, N+1, async, JWT/OAuth, rate limiting, caching | FastAPI, Python service, Pydantic, async Python, SOLID, endpoint |
| **python-testing** | `software/backend/python-testing/SKILL.md` | pytest: fixtures, async tests, mocking, coverage, property-based testing | pytest, fixtures, mock, async test, conftest, parametrize |
| **golang-patterns** | `software/language/go/golang-patterns/SKILL.md` | Go idioms: interfaces, goroutines, channels, context, error wrapping, DI | Go, Gin, goroutine, interface, idiomatic Go, concurrencia, channels |
| **golang-testing** | `software/language/go/golang-testing/SKILL.md` | Testing Go: table-driven, subtests, mocks con interfaces, benchmarks, fuzzing | go test, table-driven, benchmark, fuzzing, testify, Go coverage |
| **database-expert** | `infrastructure/database-expert/SKILL.md` | Data modeling, SQL/NoSQL, query optimization, migrations. PostgreSQL, BigQuery, AlloyDB | base de datos, SQL, schema, migración, AlloyDB, ORM, indexing |
| **gitops-expert** | `infrastructure/gitops-expert/SKILL.md` | CI/CD, deployment, IaC, secrets management, branching strategy | CI/CD, pipeline, Docker, deploy, GitHub Actions, Terraform, GitOps |
| **claude-api** | `integrations/claude-api/SKILL.md` | Anthropic API: streaming, tool use, vision, batches, prompt caching, Agent SDK | Claude API, Anthropic SDK, streaming, tool use, prompt caching |
| **deep-research** | `integrations/deep-research/SKILL.md` | Web research multi-source con síntesis. Produce research report verificado | investigar, web research, multi-fuente, buscar información, fact-check |
| **documentation-lookup** | `integrations/documentation-lookup/SKILL.md` | Documentación actualizada vía Context7/web para libs que cambian frecuente | docs de librería, framework docs, how does X work, look up API |
| **security-expert** | `security/security-expert/SKILL.md` | Threat modeler y crypto reviewer: OWASP, key management, auth, timing attacks | seguridad, auth, crypto, secrets, JWT, OAuth, threat model, OWASP |
| **security-review** | `security/security-review/SKILL.md` | Security code review de diffs. Produce findings con severity rating | security review, vulnerabilidades, revisar auth code, pen test |
| **api-design** | `software/api/api-design/SKILL.md` | REST API: resource naming, status codes, pagination, filtering, versioning | REST API, endpoint, OpenAPI, paginación, error codes, versioning |
| **backend-patterns** | `software/api/backend-patterns/SKILL.md` | Arquitectura backend: service/repository, error propagation, retry/circuit breaker | backend patterns, service layer, error handling, arquitectura servicio |
| **mcp-server-patterns** | `software/api/mcp-server-patterns/SKILL.md` | Design MCP servers: stdio vs Streamable HTTP, tools vs resources | MCP server, build MCP tool, stdio vs HTTP, MCP resource, MCP prompt |
| **workflow-coordinator** | `workflow/workflow-coordinator/SKILL.md` | Detecta intent del usuario, verifica estado del workflow, enruta al skill apropiado | ¿qué hago?, ambiguous intent, routing, workflow gate |
| **ticket-planner** | `workflow/ticket-planner/SKILL.md` | Planificación Jira: 11-fuentes context, grokking refinement, pasos atómicos | planear ticket, desglosar story, Jira ticket, quiero implementar X |
| **ticket-implementation** | `workflow/ticket-implementation/SKILL.md` | 7-hop multihop grounding: ejecución atómica con verificación iterativa | implementar ticket, ejecutar plan, implementa X, codifica Y |
| **squit** | `archaeology/squit/SKILL.md` | Búsqueda semántica de SPs legacy de Deacero (5.7M objetos SQL) | buscar stored procedure, SP SQL Server, legacy business logic |
| **problem-intake** | `software/discovery/problem-intake/SKILL.md` | Inicia arqueología en codebase legacy cuando el input es vago u orientado a negocio | cómo funciona facturación, rastrear bug, entender proceso legacy, input vago |
| **cobol-analyst** | `software/discovery/cobol-analyst/SKILL.md` | Parsea y entiende archivos copybook COBOL en sistemas legacy | analizar COBOL, entender copybook, interpretar COBOL, legacy COBOL |
| **tdd-developer** | `software/quality/tdd-developer/SKILL.md` | Ciclo Red→Green→Refactor estricto. NUNCA escribe implementación sin test fallante previo | implementar feature TDD, test primero, ciclo rojo-verde-refactor |
| **qa-validator** | `software/quality/qa-validator/SKILL.md` | Valida BDD post-implementación, testing exploratorio. Gate final antes de merge | validar BDD, testing exploratorio, verificar criterios aceptación, gate pre-merge |
| **sonarqube-mgmt** | `software/quality/sonarqube-mgmt/SKILL.md` | Administra issues SonarQube, vulnerabilidades, automatiza mejoras de calidad | revisar SonarQube, fijar vulnerabilidad sonar, score calidad, métricas sonar |
| **tech-lead** | `software/quality/tech-lead/SKILL.md` | Valida criterios de aceptación, descompone en task graph, requiere DoD al inicio de feature | planificar feature, validar criterios aceptación, descomponer story, DoD |
| **react-components** | `frontend/react-components/SKILL.md` | Convierte diseños Stitch en componentes modulares Vite/React con validación AST | generar componentes React, convertir diseño a código, componente modular Vite |
| **shadcn-ui** | `frontend/shadcn-ui/SKILL.md` | shadcn/ui: instalación, customización, temas, variantes, mejores prácticas | integrar shadcn-ui, customizar componente shadcn, instalar shadcn |
| **stitch-design** | `frontend/stitch-design/SKILL.md` | Punto entrada unificado Stitch: enhancement de prompts, design system, generación de pantallas | diseñar interfaz Stitch, crear design system, generar pantalla alta fidelidad |
| **stitch-loop** | `frontend/stitch-loop/SKILL.md` | Construye websites iterativamente con Stitch usando patrón autónomo de baton-passing | construir sitio web Stitch, iterar diseño, crear aplicación Stitch |
| **enhance-prompt** | `frontend/enhance-prompt/SKILL.md` | Transforma ideas vagas de UI en prompts pulidos para Stitch | mejorar prompt diseño, optimizar descripción UI, enriquecer prompt Stitch |
| **design-md** | `frontend/design-md/SKILL.md` | Analiza proyectos Stitch y sintetiza sistema de diseño semántico en DESIGN.md | crear DESIGN.md, sintetizar sistema diseño, documentar design system |
| **remotion** | `frontend/remotion/SKILL.md` | Genera videos walkthrough desde proyectos Stitch con Remotion | crear video walkthrough, generar demostración visual, producir video Remotion |
| **python-senior-backend** | `backend/python-senior-backend/SKILL.md` | Python/FastAPI con DDD: entidades, servicios, repositorios, async, seguridad, testing | diseñar API FastAPI DDD, optimizar query async, seguridad backend Python |
| **distribution-expert** | `software/cli/distribution-expert/SKILL.md` | Prepara CLIs Python para PyPI: entry points, versioning, instalación, debugging | empaquetar Python, preparar PyPI, configurar distribución, pyproject.toml |
| **cli-ux-expert** | `software/cli/cli-ux-expert/SKILL.md` | Diseña y revisa UX de CLIs: parsing, help text, output format, inputs interactivos | mejorar CLI, revisar comandos CLI, diseñar flags, Typer, Click, argparse |
| **sparc-methodology** | `software/sparc/SKILL.md` | SPARC workflow (Specification, Pseudocode, Architecture, Refinement, Completion) para features complejas | implementar feature complejo, arquitectura multi-archivo, SPARC |
| **backend-developer** | `project/backend-developer/SKILL.md` | Desarrollo TypeScript backend con DDD: entidades, servicios, repositorios, Express | implementar servicio backend TS, DDD, entidad dominio, repositorio Prisma |
| **frontend-developer** | `project/frontend-developer/SKILL.md` | Features React con arquitectura componentes, servicios, routing y estado | implementar feature React proyecto, revisar componente, refactorizar frontend |
