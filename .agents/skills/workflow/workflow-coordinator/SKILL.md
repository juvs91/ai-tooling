---
name: workflow-coordinator
description: Expert workflow coordinator that detects intent, routes to appropriate workflows, and enforces guard rails. Ensures tickets are planned before implementation, plans are approved before execution, and reviews are completed before merge.
allowed-tools:
  - Read
  - Write
  - Glob
  - Grep
  - mcp__atlassian__jira_get_issue
  - mcp__atlassian__jira_search
  - mcp__atlassian__jira_add_comment
  - mcp__bitbucket__bb_get
  - mcp__bitbucket__bb_post
  - mcp__squit-remote__squit_search
  - mcp__memory__create_entities
  - mcp__memory__create_relations
  - mcp__memory__search_nodes
  - mcp__memory__open_nodes
---

# Workflow Coordinator Skill

## Autoload Mode

**IMPORTANT:** When loaded at session start (via skill-autoload.sh), this skill operates in **Autoload Mode** with special behavior:

### Autoload Behavior

1. **First Message Detection:** This skill analyzes the user's first message to determine intent
2. **Automatic Routing:** Route to the appropriate skill based on AGENTS.md routing table
3. **Self-Unload:** After target skill is loaded, workflow-coordinator is no longer needed

### Behavior Flow

```
Session Start (no skill loaded)
    ↓
skill-autoload.sh → "Load workflow-coordinator"
    ↓
Skill tool loads workflow-coordinator
    ↓
workflow-coordinator analyzes user's first message
    ↓
Intent detected → Route to appropriate skill via Skill tool
    ↓
Target skill handles user request
```

### Intent → Skill Mapping (from AGENTS.md)

**CRITICAL:** This skill MUST read `AGENTS.md` and extract the routing table between `<!-- ROUTING_TABLE_START -->` and `<!-- ROUTING_TABLE_END -->` (lines 22-66).

**The routing table in AGENTS.md has 45+ skills with specific triggers. Use that table, not this simplified summary.**

**Simplified Reference (full table in AGENTS.md):**

| Intent Category | Example Skills | AGENTS.md Triggers |
|-----------------|---------------|-------------------|
| **New Feature** | brainstorming, architect | "nueva feature", "diseñar algo", ambiguous |
| **Backend Code** | senior-backend, python-testing | "FastAPI", "Python", "endpoint", "pytest" |
| **Frontend Code** | senior-frontend, nextjs | "React", "Next.js", "componente", "TypeScript" |
| **Discovery** | software-archeologist, retro-engineer | "reverse engineer", "analizar codebase", "trazar" |
| **Planning** | ticket-planner | "planear ticket", "desglosar story", "Jira ticket" |
| **Implementation** | ticket-implementation | "implementar ticket", "codifica", "ejecutar plan" |
| **Review** | code-reviewer, security-review | "review PR", "revisar código", "code review" |
| **Documentation** | documentation-lookup, adr-writer | "docs de librería", "ADR", "documentar decisión" |

**How to Read AGENTS.md Routing Table:**

1. **Parse the table structure:**
   ```markdown
   | Triggers | Skill — Capacidad que activa | Path | No usar para |
   ```

2. **Match user message against "Triggers" column:**
   - Check for keyword matches (e.g., "implementar" → ticket-implementation)
   - Check for domain keywords (e.g., "FastAPI" → senior-backend)
   - Check for intent patterns (e.g., "¿cómo funciona?" → inquiry)

3. **Extract skill path:**
   - Use the "Path" column to locate SKILL.md
   - Example: `workflow/ticket-planner/SKILL.md`

4. **Load skill via Skill tool:**
   ```bash
   /skill <skill-name-from-table>
   ```

**Note:** If intent is ambiguous, workflow-coordinator remains loaded to assist with routing.

### Detection from User Message

Analyze the first message for patterns:

- **Implementation:** "implement", "code", "build", "fix", "create function"
- **Planning:** "plan", "break down", "how should I", "design"
- **Inquiry:** "how", "what", "why", "explain", "understand"
- **Review:** "review", "check", "verify", "validate"

---

## Core Expertise

### Intent Detection

Analyze user's request to determine their intent:

**Implementation Patterns:**
- Keywords: "implement", "code", "develop", "build", "fix", "solve"
- Directives: "Create a function", "Add a feature", "Fix the bug in..."
- Context: References to specific files, components, or functionality

**Planning Patterns:**
- Keywords: "plan", "break down", "how should I", "what's the approach"
- Directives: "Create a plan for...", "Break down this ticket..."
- Context: Ticket ID without implementation details

**Inquiry Patterns:**
- Keywords: "how", "what", "why", "explain", "understand"
- Directives: "How does this work?", "What does this function do?"
- Context: Questions about existing code or architecture

**Review Patterns:**
- Keywords: "review", "check", "verify", "validate"
- Directives: "Review my changes", "Check this implementation"
- Context: Requests for code review or quality checks

### Workflow States

A ticket progresses through these states:

```
New → Planned → In Progress → Implemented → Reviewed → Merged
```

**State Descriptions:**
- **New**: No plan exists, no work started
- **Planned**: Implementation plan exists at `ai-specs/changes/[ticket-id]_[backend|frontend].md`
- **In Progress**: Branch exists, implementation started
- **Implemented**: All implementation steps complete, tests passing
- **Reviewed**: Code review completed, issues addressed
- **Merged**: Changes merged to main branch

### Guards by State

**New → Planned:**
- ✅ Allowed: Create implementation plan
- ❌ Blocked: Start implementation without plan

**Planned → In Progress:**
- ✅ Allowed: Start implementation (plan exists)
- ❌ Blocked: Create new plan (override existing)

**In Progress → Implemented:**
- ✅ Allowed: Continue implementation, run tests
- ❌ Blocked: Create new plan for same ticket

**Implemented → Reviewed:**
- ✅ Allowed: Request review, self-review
- ❌ Blocked: Merge without review

**Reviewed → Merged:**
- ✅ Allowed: Merge after review approval
- ❌ Blocked: Merge without review

## When to Use

Invoke this skill when:
- User makes an implementation request without a clear workflow
- User provides a ticket ID and asks to start work
- User's intent is unclear (planning vs implementation vs inquiry)
- Need to ensure proper workflow is followed
- Need to route user to appropriate command or skill

## Workflow

### Step 0: Verify Skills Loaded & Read Routing Table

**IMPORTANT:** Before any work, you MUST read AGENTS.md and extract the routing table.

**Action:**
1. **Read AGENTS.md** (entire file, lines 1-250)
2. **Extract routing table** from lines 22-66 (between `<!-- ROUTING_TABLE_START -->` and `<!-- ROUTING_TABLE_END -->`)
3. **Parse table structure:**
   - Column 1: **Triggers** (keywords/patterns that activate the skill)
   - Column 2: **Skill** (name and description)
   - Column 3: **Path** (location of SKILL.md relative to `.agents/skills/`)
   - Column 4: **No usar para** (when NOT to use this skill)

**Check if skill already loaded:**
- Verify if any skill is already loaded in context
- If yes, check if it matches the detected intent
- If no, proceed to load appropriate skill

**How to Parse Routing Table:**

```
| Triggers | Skill — Capacidad que activa | Path | No usar para |
|---|---|---|---|
| nueva feature, "quiero hacer X" | **brainstorming** | `core/brainstorming/SKILL.md` | Cambios mid-impl |
| FastAPI, Python service | **senior-backend** | `software/backend/senior-backend/SKILL.md` | Go code |
| planear ticket, Jira ticket | **ticket-planner** | `workflow/ticket-planner/SKILL.md` | Tareas mid-impl |
| implementar ticket, ejecutar | **ticket-implementation** | `workflow/ticket-implementation/SKILL.md` | Sin plan previo |
```

**Matching Algorithm:**
1. Extract user message keywords and intent
2. Compare against "Triggers" column (case-insensitive)
3. Find first match (table is ordered by priority)
4. Extract skill name and path from matched row
5. Load skill using: `/skill <skill-name>`

**Example output after reading AGENTS.md:**
```
📚 Routing Table Loaded: 45 skills from AGENTS.md (lines 22-66)

🔍 Analyzing user message against triggers...
✅ Match found: "implementar ARP-123" → ticket-implementation

📋 Skill: ticket-implementation
📍 Path: workflow/ticket-implementation/SKILL.md
🔀 Loading: /skill ticket-implementation
```

### Step 1: Detect Intent Using AGENTS.md Triggers

**IMPORTANT:** Intent detection MUST use the triggers from AGENTS.md routing table (lines 22-66).

**Detection Process:**

1. **Extract user message keywords:**
   - Tokenize user message into words/phrases
   - Identify domain-specific terms (ticket IDs, file paths, technical terms)
   - Identify intent verbs (implementar, planear, revisar, analizar, etc.)

2. **Match against AGENTS.md "Triggers" column:**
   - Compare user keywords against each row's triggers
   - Use case-insensitive matching
   - Prioritize by table order (first match wins)
   - Consider context (backend vs frontend, code vs docs)

3. **Extract matched skill information:**
   - Skill name (e.g., "ticket-implementation")
   - Skill path (e.g., "workflow/ticket-implementation/SKILL.md")
   - Exclusion criteria ("No usar para" column)

**Trigger Categories from AGENTS.md:**

| Category | Triggers | Skill |
|----------|----------|-------|
| **Feature Design** | "nueva feature", "quiero hacer X", "diseñar algo", "ambiguous request" | brainstorming |
| **Tools** | "nuevo script", "herramienta", "automation", "utility", "parser" | tool-writer |
| **Learning** | "aprendí algo", "nuevo patrón", "problema recurrente" | learning-protocol |
| **Architecture** | "diseño de sistema", "componentes", "boundaries", "trade-offs" | architect |
| **ADR** | "ADR", "architecture decision record", "documentar decisión" | adr-writer |
| **Reverse Eng** | "reverse engineer", "analizar codebase", "execution graph", "call tree" | software-archeologist |
| **Backtrack** | "backtrack", "trazar comportamiento", "de dónde viene X" | retro-engineer |
| **Unknown Domain** | "sistema desconocido", "código nunca visto", "unfamiliar codebase" | unknown-domain-protocol |
| **Backend** | "FastAPI", "Python service", "Pydantic", "async Python", "endpoint", "router" | senior-backend |
| **Python Tests** | "pytest", "fixtures", "mock", "async test", "conftest", "parametrize" | python-testing |
| **TDD** | "escribir tests", "TDD", "test-first", "agregar cobertura", "fix bug", "nueva feature" | tdd-workflow |
| **BDD** | "Gherkin", "BDD", "feature file", "given/when/then", "behavioral spec" | bdd-writer |
| **Code Review** | "review PR", "revisar código", "/code-review", "revisar PR", "code review" | code-review |
| **Quality Review** | "audit diff", "revisar calidad", "code reviewer", "antes de merge" | code-reviewer |
| **Verification** | "verificar implementación", "pre-PR", "quality gate", "terminé de implementar" | verification-loop |
| **Standards** | "linting", "formateo", "coding standards", "ruff", "estilo de código" | coding-standards |
| **Frontend** | "React", "Next.js", "TypeScript", "Tailwind", "componente", "hook", "props" | senior-frontend |
| **Next.js** | "App Router", "server component", "RSC", "client component", "Next.js routing" | nextjs |
| **Database** | "base de datos", "SQL", "schema", "migración", "AlloyDB", "ORM", "Alembic" | database-expert |
| **CI/CD** | "CI/CD", "pipeline", "Docker", "deploy", "Bitbucket Pipelines", "Cloud Run" | gitops-expert |
| **Planning** | "planear ticket", "desglosar story", "Jira ticket", "quiero implementar X" | ticket-planner |
| **Implementation** | "implementar ticket", "ejecutar plan", "implementa X", "codifica Y" | ticket-implementation |
| **Routing** | "¿qué hago?", "ambiguous intent", "routing", "workflow gate" | workflow-coordinator |

**Detection Examples:**

```
User message: "Implementar ARP-123 con FastAPI"
Keywords: ["implementar", "ARP-123", "FastAPI"]
Matches:
  - "implementar" → ticket-implementation (row 62)
  - "FastAPI" → senior-backend (row 39)
First match: ticket-implementation
Action: Load /skill ticket-implementation
```

```
User message: "¿Cómo funciona el cálculo de precios?"
Keywords: ["¿cómo", "funciona", "cálculo", "precios"]
Matches:
  - "¿cómo funciona" → inquiry pattern (no direct trigger)
  - Domain: pricing system
Intent: Inquiry → software-archeologist (for codebase analysis)
Action: Load /skill software-archeologist
```

```
User message: "Diseñar nueva API de precios"
Keywords: ["diseñar", "nueva", "API", "precios"]
Matches:
  - "diseñar algo" → brainstorming (row 30)
  - "REST API" → api-design (row 58)
First match: brainstorming
Action: Load /skill brainstorming
```

**Primary Intent Classification:**

Based on AGENTS.md triggers, classify into:

1. **Design/Planning** → brainstorming, ticket-planner, architect
2. **Implementation** → ticket-implementation, senior-backend, senior-frontend
3. **Discovery/Inquiry** → software-archeologist, documentation-lookup
4. **Review/Quality** → code-reviewer, verification-loop, security-review
5. **Infrastructure** → database-expert, gitops-expert
6. **Process/Workflow** → workflow-coordinator, learning-protocol

**Secondary Detection:**
- Ticket ID or feature identifier (e.g., ARP-123)
- Backend vs frontend (analyze file paths, keywords)
- Specific domain (pricing, auth, database, etc.)
- Urgency or priority indicators

### Step 2: Check Prerequisites

**Check 1: Plan Existence**
- Check if plan exists at:
  - `ai-specs/changes/[ticket-id]_backend.md` (for backend)
  - `ai-specs/changes/[ticket-id]_frontend.md` (for frontend)

**Check 2: Context Availability**
- Verify ticket details are accessible (via MCP or local files)
- Check if related files and context are available
- Verify working directory is correct

**Check 3: Workflow State**
- Determine current state of the ticket
- Identify which guards apply
- Verify that the request doesn't violate any guards

### Step 3: Route to Appropriate Skill Using AGENTS.md

**IMPORTANT:** Route directly to skills from AGENTS.md routing table, NOT enhanced commands.

**Routing Algorithm:**

1. **Identify matched skill from Step 1**
2. **Extract skill path from AGENTS.md** (column "Path")
3. **Load skill using Skill tool:** `/skill <skill-name>`
4. **Hand off context** to loaded skill

**Common Routing Scenarios:**

**Scenario A: New Feature (No Code Yet)**
```
User: "Quiero agregar un endpoint de precios"
Triggers: "nueva feature", "quiero hacer X", "endpoint"
Match: brainstorming (row 30)
Action: /skill brainstorming

Brainstorming will:
  - Gate de diseño obligatorio
  - Transformar idea en spec aprobada
  - CERO código hasta aprobación
```

**Scenario B: Jira Ticket Implementation (No Plan)**
```
User: "Implementar ARP-123"
Triggers: "implementar ticket" (row 62)
Match: ticket-planner (row 61)
Action: /skill ticket-planner

ticket-planner will:
  - Planificar Jira con 11-fuentes context
  - Crear plan en ai-specs/changes/[ticket-id]_[backend|frontend].md
  - Luego cargar ticket-implementation
```

**Scenario C: Jira Ticket Implementation (Plan Exists)**
```
User: "Implementar ARP-123"
Check: ai-specs/changes/ARP-123_backend.md exists?
  - YES → Skip planning, go to implementation
  - NO → Go to Scenario B

Action: /skill ticket-implementation

ticket-implementation will:
  - Ejecutar plan via 7-hop multihop grounding
  - Verificación iterativa
  - Tests + commit
```

**Scenario D: Codebase Analysis**
```
User: "¿Cómo funciona el cálculo de precios?"
Triggers: "reverse engineer", "analizar codebase" (row 36)
Match: software-archeologist (row 36)
Action: /skill software-archeologist

software-archeologist will:
  - Ingeniería inversa: execution graph
  - Call trees, API inventory
  - Findings ledger
```

**Scenario E: Backend Implementation**
```
User: "Crear endpoint FastAPI para precios"
Triggers: "FastAPI", "endpoint" (row 39)
Match: senior-backend (row 39)
Action: /skill senior-backend

senior-backend will:
  - SOLID, DRY, async patterns
  - JWT/OAuth, rate limiting
  - Caching, middleware
```

**Scenario F: Frontend Implementation**
```
User: "Crear componente React para precios"
Triggers: "React", "componente" (row 49)
Match: senior-frontend (row 49)
Action: /skill senior-frontend

senior-frontend will:
  - Component optimization
  - Bundle, accessibility
  - TypeScript patterns
```

**Scenario G: Code Review**
```
User: "Review mis cambios en feature/ARP-123"
Triggers: "review PR", "revisar código" (row 43)
Match: code-reviewer (row 44)
Action: /skill code-reviewer

code-reviewer will:
  - Quality/security review
  - ADR coverage check
  - Pre-merge validation
```

**Scenario H: Inquiry About Documentation**
```
User: "¿Cómo se usa Alembic?"
Triggers: "docs de librería", "look up API" (row 55)
Match: documentation-lookup (row 55)
Action: /skill documentation-lookup

documentation-lookup will:
  - Buscar docs actualizadas vía Context7
  - Proporcionar ejemplos
```

**Scenario I: Architecture Question**
```
User: "¿Qué patrón usar para precios?"
Triggers: "diseño de sistema", "componentes", "boundaries" (row 33)
Match: architect (row 33)
Action: /skill architect

architect will:
  - Evaluar trade-offs
  - Escribir ADR si es decisión nueva
  - Revisar diseño ANTES de codificar
```

**Scenario J: ADR Required**
```
User: "Documentar decisión de arquitectura"
Triggers: "ADR", "architecture decision record" (row 34)
Match: adr-writer (row 34)
Action: /skill adr-writer

adr-writer will:
  - Capturar decisión en formato MADR
  - Inmutables (se superseden, nunca editan)
```

**Compound Tasks (Multiple Skills):**

From AGENTS.md lines 68-76, when multiple skills apply:

```
Example: "Migrar stored procedure de precios a FastAPI"

Order of loading:
1. /skill brainstorming (feature nueva)
2. /skill software-archeologist (análisis legacy)
3. /skill senior-backend (FastAPI implementation)
4. /skill python-testing (tests)
5. /skill tdd-workflow (TDD approach)
6. /skill verification-loop (pre-PR)
7. /skill learning-protocol (closing session)
```

**Domain-Specific Routing:**

| Domain | Keywords | Skills |
|--------|----------|--------|
| **Pricing** | "precios", "cascade", "descuento" | database-expert, senior-backend |
| **Auth/Security** | "auth", "JWT", "OAuth", "seguridad" | security-expert, senior-backend |
| **Database** | "SQL", "query", "migración", "AlloyDB" | database-expert |
| **API** | "endpoint", "REST", "OpenAPI" | api-design, senior-backend |
| **Frontend** | "React", "Next.js", "UI", "componente" | senior-frontend, nextjs |
| **Testing** | "test", "pytest", "coverage" | python-testing, tdd-workflow |
| **CI/CD** | "deploy", "pipeline", "Docker" | gitops-expert |
| **Legacy SQL** | "stored procedure", "SQL Server" | software-archeologist, squit-remote |

**Skill Loading Command:**

Always use the Skill tool with the skill name from AGENTS.md:

```bash
/skill <skill-name>
```

**Examples:**
- `/skill ticket-planner`
- `/skill senior-backend`
- `/skill software-archeologist`
- `/skill code-reviewer`

**NEVER use enhanced commands directly** - let skills orchestrate themselves.

### Step 4: Execute with Validation

**Before executing any workflow:**
1. Confirm user's intent
2. Verify prerequisites
3. Inform user of the workflow being followed
4. Execute the workflow
5. Validate results
6. Report completion

## Integration Points

### With ticket-planner Skill
- **Trigger**: No plan exists + user wants to implement or plan
- **Action**: Invoke ticket-planner skill with ticket ID
- **Output**: Implementation plan at `ai-specs/changes/[ticket-id]_[backend|frontend].md`

### With ticket-implementation Skill
- **Trigger**: Plan exists + user wants to implement
- **Action**: Invoke ticket-implementation skill with plan path
- **Output**: Implemented feature, tests passing, commit created

### With code-reviewer Skill
- **Trigger**: Implementation complete + user wants review
- **Action**: Invoke code-reviewer skill with branch or diff
- **Output**: Review report, issues identified (if any)

### With architect Skill
- **Trigger**: Architecture question or architectural changes needed
- **Action**: Invoke architect skill with context
- **Output**: Architecture guidance or ADR

### With MCP Tools
- **Jira**: Get ticket details, search tickets, add comments
- **Bitbucket**: Get branch info, create PRs, review code
- **squit-remote**: Search legacy SQL, find dependencies

## Output

### Routing Decision

**Format:**
```
🔍 Intent Detection: [IMPLEMENTATION|PLANNING|INQUIRY|REVIEW]
📋 Ticket: [TICKET-ID]
🎯 Domain: [BACKEND|FRONTEND]
📍 Current State: [NEW|PLANNED|IN_PROGRESS|IMPLEMENTED|REVIEWED]

✅ Workflow: [workflow name]
🔀 Routing to: [command or skill]
```

### Guard Violation

**Format:**
```
⛔ Guard Violation: [guard description]

Current State: [state]
Requested Action: [action]
Blocking Rule: [rule]

❌ Cannot proceed because: [reason]

✅ To continue:
1. [Step 1]
2. [Step 2]
```

### Example Outputs (Using AGENTS.md Routing)

**Scenario 1: No plan, wants to implement**
```
📚 Routing Table Loaded: 45 skills from AGENTS.md (lines 22-66)

🔍 Intent Detection: IMPLEMENTATION
📋 Ticket: ARP-1
🎯 Domain: BACKEND
📍 Current State: NEW

⚠️ No implementation plan found for ARP-1

🔍 Analyzing triggers: "implementar" → ticket-planner (row 61)
📍 Path: workflow/ticket-planner/SKILL.md

✅ Creating plan first...
🔀 Loading skill: /skill ticket-planner

─────────────────────────────
[Skill: ticket-planner loads]
─────────────────────────────

📋 Plan created: ai-specs/changes/ARP-1_backend.md

🔍 Next: Loading ticket-implementation...
🔀 Loading skill: /skill ticket-implementation

─────────────────────────────
[Skill: ticket-implementation loads]
─────────────────────────────
```

**Scenario 2: Plan exists, wants to implement**
```
📚 Routing Table Loaded: 45 skills from AGENTS.md (lines 22-66)

🔍 Intent Detection: IMPLEMENTATION
📋 Ticket: ARP-1
🎯 Domain: BACKEND
📍 Current State: PLANNED

✅ Found plan: ai-specs/changes/ARP-1_backend.md

🔍 Analyzing triggers: "implementar" → ticket-implementation (row 62)
📍 Path: workflow/ticket-implementation/SKILL.md

🔀 Loading skill: /skill ticket-implementation

─────────────────────────────
[Skill: ticket-implementation loads]
─────────────────────────────
```

**Scenario 3: Inquiry about codebase**
```
📚 Routing Table Loaded: 45 skills from AGENTS.md (lines 22-66)

🔍 Intent Detection: INQUIRY
❓ Question: "¿Cómo funciona el cálculo de precios?"

🔍 Analyzing triggers:
  - "¿cómo funciona" → inquiry pattern
  - "cálculo de precios" → domain-specific
  - Match: software-archeologist (row 36)
  - Triggers: "reverse engineer", "analizar codebase"
  - Path: software/discovery/software-archeologist/SKILL.md

🔀 Loading skill: /skill software-archeologist

─────────────────────────────
[Skill: software-archeologist loads]
─────────────────────────────

📊 Gathering context...
- Execution graph analysis
- Call tree mapping
- API inventory
- Findings ledger: docs/findings/FINDINGS.md

✅ Analysis complete: [detailed findings]
```

**Scenario 4: New feature (ambiguous)**
```
📚 Routing Table Loaded: 45 skills from AGENTS.md (lines 22-66)

🔍 Intent Detection: DESIGN
❓ Request: "Quiero agregar una funcionalidad de precios"

🔍 Analyzing triggers:
  - "quiero hacer X" → brainstorming (row 30)
  - "nueva feature" → brainstorming (row 30)
  - Match: brainstorming
  - Path: core/brainstorming/SKILL.md

🔀 Loading skill: /skill brainstorming

─────────────────────────────
[Skill: brainstorming loads]
─────────────────────────────

⚠️ DESIGN GATE ACTIVE
─────────────────────

📌 Brainstorming mode: NO code until design is approved

1. ✅ Exploring requirements...
2. ✅ Analyzing constraints...
3. ✅ Proposing solutions...
4. ⏳ Awaiting user approval...

🚫 CODE BLOCKED: Design must be approved first
```

**Scenario 5: Backend FastAPI implementation**
```
📚 Routing Table Loaded: 45 skills from AGENTS.md (lines 22-66)

🔍 Intent Detection: IMPLEMENTATION
💻 Technology: FastAPI / Python
🎯 Domain: BACKEND

🔍 Analyzing triggers:
  - "FastAPI" → senior-backend (row 39)
  - "Python service" → senior-backend (row 39)
  - Match: senior-backend
  - Path: software/backend/senior-backend/SKILL.md

🔀 Loading skill: /skill senior-backend

─────────────────────────────
[Skill: senior-backend loads]
─────────────────────────────

🐍 Python/FastAPI Expert Mode Active
─────────────────────────────

✓ SOLID principles
✓ Async patterns
✓ JWT/OAuth
✓ Rate limiting
✓ Caching strategies
✓ Middleware design
```

**Scenario 6: Code review request**
```
📚 Routing Table Loaded: 45 skills from AGENTS.md (lines 22-66)

🔍 Intent Detection: REVIEW
📝 Scope: PR / code changes

🔍 Analyzing triggers:
  - "review PR" → code-review (row 43)
  - "antes de merge" → code-reviewer (row 44)
  - Match: code-reviewer (quality/security focused)
  - Path: software/quality/code-reviewer/SKILL.md

🔀 Loading skill: /skill code-reviewer

─────────────────────────────
[Skill: code-reviewer loads]
─────────────────────────────

🔍 Code Review Active
─────────────────────

✓ Quality check
✓ Security analysis
✓ ADR coverage verification
✓ Correctness validation
✓ Performance review

📊 Review Report: [findings and recommendations]
```

## Notes

- This skill acts as an **interceptor** for implementation requests
- It ensures **proper workflow** is always followed
- It provides **automatic routing** to the correct commands and skills
- It maintains **context** throughout the process
- Guards are **blocking** - violations must be resolved before proceeding
- The skill is **state-aware** - tracks ticket progression
- Intent detection uses **pattern matching** on user requests
- Routing is **deterministic** - same input always routes to same workflow

## Enhanced Commands vs Skills

### Why Route to Skills Directly?

This workflow-coordinator routes to `/skill <name>` instead of enhanced commands (e.g., `/plan-backend-ticket-enhanced`) for three key reasons:

**1. Composability**
- Skills are atomic, reusable capabilities
- Enhanced commands are thin orchestrators that combine skills
- Direct skill routing enables flexible composition

**2. Transparency**
- Skills contain actual expertise and instructions
- Enhanced commands are just wrappers around skills
- Routing directly to skills makes flow explicit

**3. Flexibility**
- Skills can be combined dynamically
- Enhanced commands have fixed orchestration patterns
- workflow-coordinator can make routing decisions based on AGENTS.md

### Enhanced Commands as Thin Orchestrators

Enhanced commands in `code_standards/ai-specs/.commands/` work like this:

```
/plan-backend-ticket-enhanced
    ↓ (orchestrates)
ticket-planner + backend-developer skills
    ↓ (create)
Implementation plan + technical design
```

workflow-coordinator bypasses the orchestrator and routes directly:

```
User request → workflow-coordinator → /skill <name>
```

### Mapping: Enhanced Commands → Skills

| Enhanced Command | Skills Used | Direct Route |
|-----------------|-------------|--------------|
| `plan-backend-ticket-enhanced` | ticket-planner + backend-developer | `/skill ticket-planner` or `/skill backend-developer` |
| `develop-backend-enhanced` | ticket-implementation + python-senior-backend | `/skill ticket-implementation` |
| `plan-frontend-ticket-enhanced` | ticket-planner + frontend-developer | `/skill ticket-planner` or `/skill frontend-developer` |
| `develop-frontend-enhanced` | ticket-implementation + senior-frontend | `/skill ticket-implementation` |
| `enforce-workflow` | workflow-coordinator | `/skill workflow-coordinator` |

**When to use enhanced commands:**
- Complex workflows that require multiple skills in sequence
- Pre-defined orchestration patterns
- When you need the enhanced command's specific workflow

**When workflow-coordinator routes directly:**
- Simple skill activation based on intent
- When AGENTS.md routing table directly maps to a skill
- User requests focused on single domain expertise
