# ADR-0020: EnterWorktree oneOf — Negative Examples en Tool Description

**Status:** Accepted  
**Date:** 2026-07-14  
**Refs:** `llm/transformers/deferred_tools.py`, ADR-0009, `ai-notes/AI_LEARNING.md` (K2-001)

---

## Context

Kimi K2 llama `EnterWorktree` con AMBOS parámetros `path` y `name` simultáneamente, violando la  
constraint `oneOf` del schema (solo uno de los dos está permitido por turno):

```json
{"path": "/foo/bar", "name": "my-worktree"}   ← WRONG — oneOf violation
```

Claude Code SDK rechaza el call. Kimi, sin poder entrar al worktree, cae a su fallback:  
ejecutar `sed -i` o ediciones directas **sin aislamiento de worktree**. Esto produce drift  
de archivos en el working tree principal.

**Causa raíz:** Los modelos no-Claude no entienden constraints `oneOf` complejos a menos que  
reciban ejemplos explícitos de qué NO hacer. La descripción actual de `EnterWorktree` en  
`_CC_TOOL_DESCRIPTIONS` solo muestra el formato correcto, sin casos negativos.

---

## Decision

Agregar ejemplos negativos a `_CC_TOOL_DESCRIPTIONS["EnterWorktree"]` en `deferred_tools.py`.

El formato:
```
WRONG: use ONLY ONE of 'path' OR 'name', never both.
  ✗  {"path": "/repo/project", "name": "my-session"}
RIGHT:
  ✓  {"path": "/repo/project"}    ← create/reuse worktree at path
  ✓  {"name": "my-session"}       ← create/reuse worktree by name (auto path)
```

Este patrón de "WRONG/RIGHT" en la tool description es el mismo que funciona para  
AskUserQuestion (corrección de `question` → `questions[]`) — ya probado con Kimi K2.

---

## Files Changed

1. `vendor/claude-code-proxy/llm/transformers/deferred_tools.py` — `_CC_TOOL_DESCRIPTIONS["EnterWorktree"]`

---

## Consequences

**Positive:**
- Kimi K2 ve ejemplos explícitos negativos + positivos en el schema
- Elimina el fallback destructivo a `sed -i` sin aislamiento

**Negative:**
- Descripción más larga → +30-50 tokens de overhead para `EnterWorktree`

**Trade-off aceptable:** El worktree isolation es crítico para la integridad del repo.
