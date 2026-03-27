# AGENTS.md — ai-tooling
**READ THIS FIRST. Every agent, every session, no exceptions.**

## 1. Project Mandate

* **Repository:** `github.com/deagentic/ai-tooling`
* **Description:** Anthropic→OpenAI proxy + agentic CI infrastructure tooling (Claude Code harness, provider routing, Docker-based).
* **Core Objective:** Evolve, document, and improve the proxy pipeline and agent skills with full traceability.

## 2. Core Routing Directives

Depending on your platform (Claude Code, Cursor, Gemini), either use the `/activate_skill` tool OR read the corresponding `SKILL.md` file before proceeding:

1. **Reverse Engineering & Analysis**: If asked to reverse engineer, analyze, backtrack, or generate an executions graph, you MUST read `.agents/skills/software/discovery/software-archeologist/SKILL.md`.
2. **Tool Creation**: If you determine a new tool or script is needed, or if instructed to create one, DO NOT write it yourself. You MUST read `.agents/skills/core/tool-writer/SKILL.md` and delegate the task.
3. **Architecture**: If asked to design a system, review component boundaries, or make structural trade-offs, you MUST read `.agents/skills/software/architecture/architect/SKILL.md`.
4. **Behavior Driven Development**: If asked to write Gherkin specs or backtrack to BDD feature files, you MUST read `.agents/skills/software/quality/bdd-writer/SKILL.md`.
5. **Decision Logging**: If analyzing code to extract why a hardcoded value or architectural choice was made, read `.agents/skills/software/architecture/decision-logger/SKILL.md`.
6. **Architecture Decision Records**: If an architectural decision is made or confirmed, read `.agents/skills/software/architecture/adr-writer/SKILL.md` to document it.
7. **The Learning Protocol**: If you learn a new domain concept, solve a recurring issue, discover a reusable pattern, or create a new generalized sub-agent, you MUST read `.agents/skills/core/learning-protocol/SKILL.md` and persist the knowledge to the repository.
8. **Security Threat Modeling**: If performing threat modeling, vulnerability analysis, or security hardening, read `.agents/skills/security/security-expert/SKILL.md` before merging.
9. **Database / AlloyDB / SQL**: If working with database queries, schema design, AlloyDB, or MCP data tools, read `.agents/skills/infrastructure/database-expert/SKILL.md`.
10. **CI/CD / Docker / GitOps**: If working with Docker Compose, GitHub Actions, deployment pipelines, or infra config, read `.agents/skills/infrastructure/gitops-expert/SKILL.md`.
11. **Claude API / Anthropic SDK**: If working with the Anthropic SDK, Claude API, streaming, tool use, prompt caching, or the Agent SDK, read `.agents/skills/integrations/claude-api/SKILL.md`.
12. **MCP Server Development**: If asked to build or modify an MCP server (tools, resources, stdio vs HTTP), read `.agents/skills/software/api/mcp-server-patterns/SKILL.md`.
13. **TDD / Test-First**: If writing new features, fixing bugs, or refactoring — use test-driven development. Read `.agents/skills/software/quality/tdd-workflow/SKILL.md`.
14. **Live Library/Framework Docs**: If looking up current API docs for Python, FastAPI, Docker, uvicorn, or any library, read `.agents/skills/integrations/documentation-lookup/SKILL.md` (uses Context7 MCP).
15. **Go Code / Go Project**: If working with Go code, idiomatic patterns, or Go tests, read `.agents/skills/software/language/go/golang-patterns/SKILL.md`.
16. **Code Review**: If reviewing a PR, checking code quality, or auditing a diff, read `.agents/skills/software/quality/code-reviewer/SKILL.md`.
16b. **Bitbucket PR Code Review**: If asked to do a code review, review a PR, "revisar PR", "hacer code review", or `/code-review`, you MUST read `.agents/skills/software/quality/code-review/SKILL.md`.
27. **Brainstorming / Design First**: If asked to build a new feature, add functionality, or design something new, you MUST read `.agents/skills/core/brainstorming/SKILL.md` BEFORE writing any code.
28. **Frontend Development**: If working with React, Next.js, TypeScript, Tailwind, or Vue, read `.agents/skills/software/frontend/senior-frontend/SKILL.md`.
29. **Next.js Specifics**: If working with Next.js App Router, server/client components, dynamic routes, or Vercel deployment, read `.agents/skills/software/frontend/nextjs/SKILL.md`.
30. **Backend Development (Python)**: If implementing Python services, FastAPI endpoints, SOLID principles, or DRY patterns, read `.agents/skills/software/backend/senior-backend/SKILL.md`.
31. **Python Testing**: If writing Python tests, pytest fixtures, mocks, or async tests, read `.agents/skills/software/backend/python-testing/SKILL.md`.
32. **E2E / Playwright Testing**: If writing, fixing, or reviewing Playwright tests, read `.agents/skills/software/quality/playwright-pro/SKILL.md`.
17. **Security Code Review**: If code changes touch auth, crypto, inputs, or secrets, read `.agents/skills/security/security-review/SKILL.md`.
18. **Deep Multi-Source Research**: If a task requires searching across multiple sources (web, docs, repos), read `.agents/skills/integrations/deep-research/SKILL.md`.
19. **REST API Design**: If designing or reviewing API endpoints, request/response shapes, or OpenAPI specs, read `.agents/skills/software/api/api-design/SKILL.md`.
20. **Backend Implementation Patterns**: If implementing services, error handling, pagination, or rate limiting, read `.agents/skills/software/api/backend-patterns/SKILL.md`.
21. **Go Testing**: If writing Go tests, benchmarks, or table-driven tests, read `.agents/skills/software/language/go/golang-testing/SKILL.md`.
22. **Coding Standards**: If writing Python, JS/TS, or any code that must pass linting/formatting gates, read `.agents/skills/software/quality/coding-standards/SKILL.md`.
23. **Formal Evals**: If building an eval harness, scoring model outputs, or running A/B prompt experiments, read `.agents/skills/software/quality/eval-harness/SKILL.md`.
24. **Verification Loop**: If completing an implementation and need to verify correctness end-to-end, read `.agents/skills/software/quality/verification-loop/SKILL.md`.
25. **Unknown Domain**: If encountering a completely unfamiliar codebase or domain, read `.agents/skills/software/discovery/unknown-domain-protocol/SKILL.md`.
26. **Behavior Backtracking**: If tracing a specific runtime behavior back to its code entry point, read `.agents/skills/software/discovery/retro-engineer/SKILL.md`.

## 3. DEDUPLICATION MANDATE
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

Full skill index: `.agents/skills/skills.md`

## 7. Self-Repair Mandate

If you detect any discrepancies in agent documentation or configurations (e.g., outdated file paths, broken skill references), self-repair them by fixing references across the workspace. If the issue stems from an upstream template, submit a PR to fix it at the source.

## 8. Project-Specific Context

- **Proxy container**: `ai-tooling-proxy_cloud-1` on port `8083`
- **Hot-reload**: Uvicorn `--reload` — file changes in `vendor/claude-code-proxy/` apply immediately
- **Health check**: `curl http://127.0.0.1:8083/health | jq .`
- **Logs**: `curl "http://127.0.0.1:8083/api/logs?n=20" | jq .`
- **Provider configs**: `profile-envs/` + `cloud-provider-ymls/`
- **MCP servers**: alloydb, atlassian, squit, cloudsql, context7, serper, playwright, sequential-thinking, memory (see `CLAUDE.md`)
- **Session artifacts**: `ai-notes/` (learnings, analyses — shared with team and future agents)
