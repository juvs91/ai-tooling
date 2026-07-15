# ADR-0017: Circuit Breaker para el Intent Classifier

**Fecha:** 2026-07-13  
**Estado:** Accepted  
**Decisores:** jeguzman  
**Refs:** ai-notes/AI_LEARNING.md, cloud.kimi-coding.env

---

## Contexto

El intent classifier usa `CLASSIFIER_API_KEY` de DeepSeek para clasificar el intent de cada mensaje. Esta misma clave se usa simultáneamente en Compressor, Fallback y OpenAI catch-all. En sesiones largas, el balance se agota → `BadRequestError: Insufficient Balance` → `classify_intent()` cae a regex para todos los turns restantes.

El resultado: el classifier LLM funciona correctamente cuando tiene balance, pero ante cualquier excepción hace 100% fallback silencioso a regex. No hay backoff, no hay alerta, no hay circuit breaker.

**Síntomas observados:**
- `classifier.regex_fallback: 30` de 110 requests en una sola sesión
- `outcome_accuracy_pct: 29.4%` cuando el regex maneja la clasificación
- Ciclo recurrente: recargar balance → funciona → nueva sesión pesada → falla de nuevo

## Decisión

Implementar un circuit breaker en `classify_intent()` con tres estados:

| Estado | Condición | Comportamiento |
|--------|-----------|----------------|
| CLOSED | Normal | Llama al LLM classifier normalmente |
| OPEN | ≥ N errores consecutivos | Usa regex directamente sin intentar LLM (ahorra latencia y evita error spam) |
| HALF-OPEN | Después de T segundos en OPEN | Intenta una llamada LLM; si falla → OPEN, si pasa → CLOSED |

**Variables de configuración (nuevas):**
- `CLASSIFIER_MAX_CONSECUTIVE_ERRORS` (default: 3) — errores para abrir el circuit
- `CLASSIFIER_CIRCUIT_RESET_SECONDS` (default: 60) — tiempo antes de intentar HALF-OPEN

**Scope del cambio:**
- `vendor/claude-code-proxy/llm/intent_classifier.py` — clase `ClassifierCircuitBreaker` + integración en `classify_intent()`
- `vendor/claude-code-proxy/config.py` — campos `classifier_max_consecutive_errors`, `classifier_circuit_reset_seconds`
- **No se cambia** `llm_router.py` — el circuit breaker vive en el classifier

## Consecuencias

**Positivas:**
- Elimina el spam de errores cuando la key está agotada (N intentos → OPEN, sin más)
- Ruta de recuperación automática (HALF-OPEN → CLOSED sin intervención del usuario)
- Log explícito cuando el circuit se abre → observable sin revisar cada request

**Negativas:**
- Cuando el circuit está OPEN, todas las clasificaciones van a regex (mismo resultado que hoy, pero ahora es explícito y transitorio)
- El estado del circuit breaker es in-memory (se reinicia al reiniciar el proxy)

**Riesgo mitigado:**
- El fix real (key dedicada para el classifier) requiere que el usuario provea una nueva API key. El circuit breaker es una mejora arquitectónica que funciona incluso con key compartida.
