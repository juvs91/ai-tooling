---
name: learning-protocol
description: Use when an agent discovers a new reusable pattern, solves a recurring issue, learns a new domain concept, or creates a new sub-agent. Always invoke to persist knowledge — never let learnings die with the session.
version: "1.0.0"
---
# Learning Protocol

---

## Identity

This is the **Learning Protocol**, a mandatory operating procedure for all agents working within this repository.

As an autonomous agent, your execution context is ephemeral, but your findings are permanent. When you learn a new domain concept, solve a recurring issue, or create a new specialized sub-agent to handle a specific domain, you MUST persist this knowledge into the repository itself.

**CRITICAL MANDATE:** All knowledge registered must be a direct refinement of how the agents behave, with the ultimate goal of making the Software Archeologist work better, faster, and smoother for the final users. Every abstraction, pattern, or sub-agent created must serve the purpose of improving the archeology and backtracking processes. Furthermore, **all of these learnings must be synthesized to be "general enough to work in any project."** Never hardcode project-specific names, paths, or proprietary logic into a learning or a general agent.

**TECHNICAL CONSTRAINTS:**
- If a new script is needed to support a learning or agent, **prefer Python** for maximum cross-platform compatibility.
- **Avoid system-specific commands** (e.g., direct PowerShell or Bash idioms) inside tool skills or instruction sets. Use cross-platform abstractions wherever possible.
- If a new tool or script must be written to support a new language or domain (e.g., a parser, analyzer, or extractor), **design it to be as generalizable as possible**. Avoid hardcoding assumptions about the specific codebase; instead, use parameterized inputs (extensions, patterns, command-line arguments) so the tool can be reused across any project utilizing that language or domain.

This ensures that the cognitive baseline expands over time to deliver higher-quality insights to the user across any codebase and any operating system.

---

## The Protocol

### 0. Circuit Breaker Protocol (Preventing Infinite Loops)
Agents communicating with each other (e.g., Tool Writer negotiating with the Architect) can get stuck in endless bureaucratic loops.
**RULE:** If a negotiation, design review, or tool-creation process requires more than 3 iterations between agents without producing a final accepted artifact, you MUST HALT the automated loop. Use the `ask_user` tool or simply pause and explicitly ask the human user for a tie-breaker decision. Never loop indefinitely.

### 0.5 Look Before You Create (Deduplication & Merging)
Before initiating the creation of any new sub-agent or tool:
1. **Check AGENTS.md**: Review the central agent registry to ensure a similar specialized agent doesn't already exist.
2. **Check Tool Index**: Review `docs/tools/index.md` to ensure a tool for the target language or domain hasn't already been written.
**If a capability exists, REUSE and REFINE it instead of creating a new one.**
**If two tools or agents are too similar, MERGE them into a single, more capable version. When merging, you MUST keep all existing contracts (command-line arguments, input/output formats) strictly compatible so that no existing agent workflows are broken.**
Only proceed to the steps below if you are filling a genuine gap in the repository's collective intelligence.

### 1. For New Domain Knowledge or Reusable Patterns (Project-Specific)
When you figure out how a complex subsystem works, discover an undocumented API quirk, or establish a convention that other agents should follow **within this specific project**, follow the canonical knowledge flow exactly:

1. **Capture the raw finding** — Append to `output/findings/FINDINGS.md` using the F-XXX format (sequential, zero-padded). Include: date, source file/procedure, what was found, impact, and next step. This is the **first and mandatory capture point** — never skip it.
2. **Promote confirmed facts** — Once independently verified (cross-referenced or confirmed by a tool run), add a concise entry to `context/run_context.md` under "Confirmed Architecture Facts". Speculation stays in FINDINGS only.
3. **Canonicalize reusable knowledge** — If the finding represents a general pattern or reusable reference, create or update a file in `knowledge/` (e.g., `knowledge/sql-patterns.md`). This knowledge is project-bound and must never be hardcoded into general tools.
4. **Open an ADR** — Only if the finding implies an architectural decision (new component, changed boundary, adopted pattern). Use the `adr-writer` skill.
5. **Commit the learning** — Stage and commit all touched files immediately: `docs(learning): <short title>`.

### 2. For Creating New Sub-Agents

When you encounter a problem space so specific or repetitive that it requires a dedicated expert (e.g., `usb-hid-specialist`, `legacy-parser-agent`), **do not write a SKILL.md manually**. You must use the **Skill Creator workflow** to produce a tested, well-formed skill:

#### 2.1 Use the Skill Creator

Invoke `document-skills:skill-creator` (or read its SKILL.md at `.agents/skills/` if the plugin is unavailable). The Skill Creator will guide you through:

1. **Capture Intent** — define what the skill should do, when it triggers, and what it outputs.
2. **Write the SKILL.md** — every skill MUST have YAML frontmatter with `name` and `description`. The description is the primary trigger mechanism; make it specific and slightly "pushy" to avoid undertriggering.
   ```yaml
   ---
   name: my-specialist
   description: Use when <specific signals> are detected. Always invoke for <domain> work, even if the user doesn't explicitly ask.
   ---
   ```
3. **Create Evals** — save test prompts to `evals/evals.json` next to the SKILL.md (see structure below). Evals ensure the skill works before it ships.
4. **Iterate** — run the test prompts, review outputs, revise the skill. Repeat until outputs are correct.

#### 2.2 Evals Structure

Every new skill must ship with evals at `<skill-dir>/evals/evals.json`:

```json
{
  "skill_name": "<name>",
  "evals": [
    {
      "id": 1,
      "prompt": "Realistic user prompt that should trigger and exercise the skill",
      "expected_output": "Description of what a correct response looks like",
      "files": [],
      "assertions": [
        {
          "text": "Output contains a structured findings table",
          "passed": null,
          "evidence": ""
        }
      ]
    }
  ]
}
```

Write at least 2 evals: one for the core happy path, one for an edge case or a near-miss that should NOT trigger the skill.

#### 2.3 Register and Commit

After the skill passes its evals:
1. **Register**: Update `AGENTS.md` with the new skill, its trigger conditions, and its expected outputs.
2. **Commit**: `feat(agents): create [name] skill with evals for [reason]`

### 3. For Creating New Tools & Scripts (The "New Stack" Protocol)
When you encounter a **new stack**, language, framework, or domain that the system does not currently understand, **you MUST NOT try to parse or understand it manually**.
Instead, you must immediately create a generalizable set of tools to understand that stack. Delegate the task by invoking the **Tool Writer Agent**. Pass your requirements, constraints, and the desired generalizability parameters to the Tool Writer. The Tool Writer will handle:
1. Writing or merging the tool defensively and cross-platform (e.g., AST parsers, extractors).
2. Updating `docs/tools/index.md` with usage instructions and constraints.
3. Committing and pushing the general tool to the upstream `deagentic` repository.

**Iterative Improvement:** Once the initial toolset is created, you must USE those tools to explore the new stack. As you experiment with the new code and find edge cases or missing features, iteratively invoke the Tool Writer to improve the tools. The tools grow alongside your understanding of the stack.

### 4. Upstream Knowledge Sharing (Deagentic Auto-Push)
If the learned pattern, architectural decision, or new sub-agent is generic enough to benefit other projects (a "general agent" or "general knowledge"):
1. **Document for Upstream**: Document the generic version of the agent or finding in `docs/upstream_contributions/`.
2. **Auto-Push to Deagentic**: Any updates to general agents MUST be automatically committed and pushed to the `deagentic` repository.
   - You are required to run the necessary shell commands to pull, update, commit (`feat(agents): update general agent [name]`), and push the generalized .agents/skills/agents to the central `deagentic` git repository so they are immediately available globally.

---

## Execution Steps & Output Format

Whenever the Learning Protocol is invoked, you must append a log entry to `docs/knowledge/learning_log.md` (create it if it doesn't exist):

```markdown
## [YYYY-MM-DD] Learning: [Short Title]
- **Trigger**: [What prompted this learning?]
- **Action Taken**: [Created new agent `.agents/skills/xyz.md` | Updated `docs/knowledge/abc.md`]
- **Impact**: [How this helps future tasks or other agents]
```

**Never keep useful abstractions or instructions in your temporary context window. If it is useful, protocolize it, write it to the repository, and commit it.**

## Collaboration & Learning Mandate

You are part of a unified, evolving agent team operating inside the Cornerstone
repository. You **MUST** follow these principles in every session:

1. **Share the Knowledge:** When you learn a domain quirk, solve a recurring
   issue, or find a reusable workaround, update the `learning-protocol` or your
   own `SKILL.md`. Knowledge hoarding is an anti-pattern.
2. **Domain Specialization:** Do not hallucinate skills outside your domain.
   If a task falls outside your expertise, delegate to the appropriate
   specialist agent — do not attempt it yourself.
3. **Use and Improve:** Before solving a problem, check whether another agent's
   `SKILL.md` already covers it. If an existing skill is flawed or incomplete,
   **refactor and improve that `SKILL.md`** rather than bypassing it.
4. **Just-In-Time Instantiation:** Be invoked exactly when your specific domain
   context is needed. Avoid accumulating massive monolithic contexts.

> Authority: `AGENTS.md § 1b — Collaborative Agentic Philosophy`.
> These rules apply to every agent, every session, no exceptions.
