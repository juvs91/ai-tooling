---
name: workflow-coordinator
description: Expert workflow coordinator that detects intent, routes to appropriate workflows, and enforces guard rails. Ensures tickets are planned before implementation, plans are approved before execution, and reviews are completed before merge.
model: haiku
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
Intent detected → Read SKILL.md via Read tool (path from AGENTS.md)
    ↓
Target skill handles user request
```

## Routing Table — Fuente única de verdad

**La tabla completa de routing vive en `AGENTS.md`, entre `<!-- ROUTING_TABLE_START -->` y
`<!-- ROUTING_TABLE_END -->`.** No la dupliques ni la re-narres aquí — cualquier copia local
se desincroniza con el tiempo. Columnas: **Triggers** (keywords/patrones), **Skill**, **Path**
(relativo a `.agents/skills/`), **No usar para** (exclusiones).

> El Skill tool (`/skill <nombre>`) solo reconoce archivos en `.claude/commands/`.
> Los agent skills en `.agents/skills/` se cargan SIEMPRE con el Read tool: `Read .agents/skills/<path>`.

## Intent Detection

Analiza el mensaje del usuario contra estas categorías (y luego, con más precisión, contra la
columna "Triggers" de AGENTS.md):

- **Implementation:** "implement", "code", "build", "fix", "create function", "develop", "solve"
- **Planning:** "plan", "break down", "how should I", "what's the approach", "design"
- **Inquiry:** "how", "what", "why", "explain", "understand"
- **Review:** "review", "check", "verify", "validate"

Contexto adicional a considerar: ticket ID o identificador de feature (ej. ARP-123), backend vs
frontend (paths/keywords), dominio específico (pricing, auth, DB, etc.), indicadores de urgencia.

## Workflow States

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

### Step 0-1: Leer AGENTS.md y encontrar el match

**IMPORTANT:** Antes de cualquier trabajo, debes leer `AGENTS.md` completo y extraer la tabla
de routing entre `<!-- ROUTING_TABLE_START -->` y `<!-- ROUTING_TABLE_END -->`.

**Algoritmo de matching:**
1. Tokeniza el mensaje del usuario (keywords, ticket IDs, paths, verbos de intent).
2. Compara contra la columna "Triggers" de cada fila, case-insensitive.
3. Prioriza por orden de la tabla — primer match gana.
4. Extrae de la fila ganadora: nombre del skill, path (columna "Path"), exclusiones ("No usar para").
5. Si ya hay un skill cargado en contexto que coincide con el intent detectado, no recargues nada.
6. Si el intent es genuinamente ambiguo entre 2+ filas, dilo explícitamente y pregunta al usuario
   antes de cargar cualquier skill.

**Ejemplo de salida esperada:**
```
🔍 Analizando triggers... ✅ Match: "implementar ARP-123" → ticket-implementation
📍 Path: workflow/ticket-implementation/SKILL.md
🔀 Read .agents/skills/workflow/ticket-implementation/SKILL.md
```

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

1. Identifica el skill matcheado en Step 0-1.
2. Extrae el path desde la columna "Path" de AGENTS.md (relativo a `.agents/skills/`).
3. Carga el skill con el Read tool: `Read .agents/skills/<path>` — **nunca** con el Skill tool
   (ese solo ve `.claude/commands/`).
4. Entrega el contexto al skill cargado.

**Compound tasks** (varias skills en secuencia, ej. "migrar SP legacy a FastAPI"): lee cada
`SKILL.md` relevante en el orden en que se necesitan — discovery → implementación → testing —
en vez de intentar resolverlo con un solo skill.

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

### Example Output (caso completo: no hay plan, usuario quiere implementar)

```
🔍 Intent Detection: IMPLEMENTATION
📋 Ticket: ARP-1
🎯 Domain: BACKEND
📍 Current State: NEW

⚠️ No implementation plan found for ARP-1

🔍 Analizando triggers: "implementar" → ticket-planner
📍 Path: workflow/ticket-planner/SKILL.md

✅ Creando plan primero...
🔀 Read .agents/skills/workflow/ticket-planner/SKILL.md

─────────────────────────────
[Skill: ticket-planner activo]
─────────────────────────────

📋 Plan created: ai-specs/changes/ARP-1_backend.md

🔍 Next: Loading ticket-implementation...
🔀 Read .agents/skills/workflow/ticket-implementation/SKILL.md

─────────────────────────────
[Skill: ticket-implementation activo]
─────────────────────────────
```

Si el plan ya existía, el paso de `ticket-planner` se omite y se va directo a
`ticket-implementation`. El mismo patrón (detectar trigger → resolver path en AGENTS.md →
`Read` el SKILL.md correspondiente) aplica igual para inquiry (→ software-archeologist),
diseño ambiguo (→ brainstorming), review (→ code-reviewer), o cualquier otra fila de la tabla.

## Notes

- This skill acts as an **interceptor** for implementation requests
- It ensures **proper workflow** is always followed
- It provides **automatic routing** to the correct commands and skills
- It maintains **context** throughout the process
- Guards are **blocking** - violations must be resolved before proceeding
- The skill is **state-aware** - tracks ticket progression
- Intent detection uses **pattern matching** on user requests
- Routing is **deterministic** - same input always routes to same workflow
