# ADR-0019: Compressor — Aislar Budget Exhaustion del Circuit Breaker

**Status:** Accepted  
**Date:** 2026-07-14  
**Refs:** `llm/compressor.py`, ADR-0017, `ai-notes/AI_LEARNING.md`

---

## Context

`llm/compressor.py` implementa dos mecanismos de resiliencia independientes:

1. **Internal token budget** (`_COMPRESSION_TOKEN_BUDGET = 50_000`): límite auto-impuesto de tokens  
   que el compressor puede gastar por sesión en llamadas a DeepSeek.

2. **Circuit breaker**: abre tras N fallas consecutivas para evitar hammear una API caída.

El bug: cuando el budget interno se agota, `_llm_compress_single` retorna `None` (línea 829).  
Ese `None` llega a `_llm_compress()` que lo interpreta como falla de API → incrementa  
`_consecutive_failures` → tras 5 fallas (threshold) el circuit breaker se abre.

**Consecuencia observada en logs (sesión 2026-07-14):**
```
[compress] Token budget exceeded for model openai/deepseek-chat, using simple trimming
[compress] Circuit breaker OPENED after 13/14 consecutive failures
[compress] LLM compression failed, falling back to aggressive trimming (keeping 25 of 81 messages)
```

En sesiones con 80+ mensajes (~90k tokens de historial), el budget de 50k se agota en 3-4 llamadas  
exitosas. Cada agotamiento posterior incrementa el contador. El circuit breaker abre → trimming  
agresivo → Kimi pierde ~70% del contexto → sesión "fatalmente" degradada.

**Distinción semántica clave:**  
- "Budget agotado" = decisión de diseño interna (no intentar más compresión cara esta sesión → OK)  
- "Error de API" = falla externa real (red, auth, rate limit → debe abrir circuit)

Mezclar ambos causa que el circuit breaker abra por razones internas, multiplicando el impacto.

---

## Decision

### 1. Aislar budget check del circuit breaker en `_llm_compress()`

Mover la verificación del budget al inicio de `_llm_compress()`, ANTES de intentar cualquier  
llamada. Si el budget está agotado, retornar `None` **sin** incrementar `_consecutive_failures`.

```python
# Budget exhausted: log clearly and return without counting as a failure
if session_budget > _COMPRESSION_TOKEN_BUDGET:
    print(f"[compress] session budget exhausted — trimming only (no circuit error)")
    return None
```

Este bloque se ejecuta ANTES de `_llm_compress_single()`, por lo que nunca llega a incrementar failures.

### 2. Externalizar `_COMPRESSION_TOKEN_BUDGET` via env var

```python
_COMPRESSION_TOKEN_BUDGET = int(os.getenv("COMPRESSION_TOKEN_BUDGET", "200000"))
```

Default aumentado de 50k → 200k. Configurable por perfil de provider en `.env`.

### 3. Agregar `COMPRESSION_TOKEN_BUDGET` a `profile-envs/cloud.kimi-coding.env`

Explícito en el profile para que sea auditable:
```
COMPRESSION_TOKEN_BUDGET=200000
```

---

## Files Changed

1. `vendor/claude-code-proxy/llm/compressor.py` — budget check en `_llm_compress()`, externalización var
2. `profile-envs/cloud.kimi-coding.env` — nueva var `COMPRESSION_TOKEN_BUDGET=200000`

---

## Consequences

**Positive:**
- Circuit breaker solo se abre por errores reales de API (4xx/5xx/timeout)
- Sesiones largas usan trimming limpiamente sin spam de "circuit breaker opened"
- Budget configurable por perfil de provider (Kimi puede usar más que otros providers)
- Hot-reload aplica automáticamente

**Negative:**
- Con budget 200k, una sesión muy larga podría gastar más en DeepSeek para compresión
  (mitigado: la compresión tiene su propio `max_tokens=2048`, el costo marginal es bajo)

**Risk mitigated:**
- No afecta la lógica de circuit breaker para errores reales — esos siguen abriendo el circuit
