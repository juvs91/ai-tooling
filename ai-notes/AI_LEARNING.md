# AI_LEARNING
> Aprendizajes iterativos del proyecto. Actualizar despues de cada sesion.

## Ultima actualizacion
- Fecha: 2025-02-07
- Por: claude-opus-4-6 + jeguzman

---

## Patrones que funcionan

### Arquitectura
- Proxy como abstraccion total: Claude Code no sabe que habla con GLM-4.7. Un `.env` cambia todo el backend.
- Separar clasificador de provider: CLASSIFIER_MODEL es independiente de PREFERRED_PROVIDER (inversion de dependencias)
- Hot-reload con bind mount + uvicorn --reload: cambios en `vendor/claude-code-proxy/` aplican sin rebuild

### Codigo
- Pydantic Union types para content blocks: agregar nuevos tipos es solo agregar al union en `Message.content`
- Stripear bloques no soportados en converter (thinking, redacted_thinking) en vez de rechazar el request

### Herramientas
- `cc-scan` + `cc-plan` (local, sin tools) para analisis barato antes de ejecutar
- Docker bind mount de vendor/ para desarrollo sin rebuild

---

## Anti-patrones / Errores comunes

### Codigo
- Schemas Pydantic incompletos rompen con 422 antes de llegar al converter. Siempre validar que TODOS los content block types de Anthropic API esten cubiertos
- `type: "thinking"` blocks causan 422 si no hay ContentBlockThinking en el schema

### Configuracion
- `CLASSIFIER_MODEL` vacio = sin costo extra (regex fallback). No olvidar que sin esta var el intent siempre era "CHAT" (bug corregido)
- Z.AI tiene DOS endpoints: `/api/paas/v4` (OpenAI) y `/api/anthropic` (nativo). El nativo evita conversion pero pierde el routing del proxy

### Proceso
- Regex para intent detection es fragil: "implement a login endpoint" matchea BUILDING pero mensajes en español no matchean nada
- Token approximation (bytes/6) tiene ~15-20% error vs tiktoken real

---

## Decisiones tecnicas tomadas

| Fecha | Decision | Contexto | Alternativas descartadas |
|-------|----------|----------|-------------------------|
| 2025-02-07 | Agregar 4 content block types (thinking, redacted_thinking, server_tool_use, server_tool_result) | Claude Code extended thinking causa 422 | Rechazar requests con thinking (romperia CC) |
| 2025-02-07 | LLM classifier con DeepSeek como modelo dedicado | Regex es fragil para intent, DeepSeek es ultra barato | Usar el mismo SMALL_MODEL (mas caro, misma latencia) |
| 2025-02-07 | Cloud downgrade: CHAT intent -> SMALL_MODEL | Optimizacion de costos para mensajes simples | Siempre usar BIG_MODEL (mas caro sin beneficio) |
| 2025-02-07 | Env vars del clasificador por provider (profile-envs/) no globales | Cada provider puede tener diferente config de clasificador | Global en .env (menos flexible) |

---

## Dependencias y versiones estables

| Paquete | Version | Notas |
|---------|---------|-------|
| claude-code-proxy | ghcr.io/1rgs/claude-code-proxy:main | Base image, vendor code mounted over it |
| litellm | (bundled in image) | Unified LLM interface |
| Z.AI GLM-4.7 | API | Big model for cloud |
| Z.AI GLM-4.7-flash | API | Small model for cloud |
| DeepSeek | API (pendiente) | Clasificador de intents ($15/mes) |

---

## Comandos utiles del proyecto

```bash
# Levantar proxy cloud con Z.AI
cd /Users/jeguzman/ai-tooling && docker compose up proxy_cloud -d

# Ver logs del proxy
docker logs ai-tooling-proxy_cloud-1 --tail 30 -f

# Health check
curl http://127.0.0.1:8083/health | jq .

# Test rapido del proxy
curl -X POST http://127.0.0.1:8083/v1/messages \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4-5-20250929","max_tokens":30,"messages":[{"role":"user","content":"hi"}]}'

# Smoke test con thinking blocks
python3 -c "
import json, urllib.request
payload = json.dumps({'model': 'claude-sonnet-4-5-20250929', 'max_tokens': 30,
  'messages': [{'role': 'user', 'content': 'hi'},
    {'role': 'assistant', 'content': [{'type': 'thinking', 'thinking': 'test', 'signature': 's1'}, {'type': 'text', 'text': 'hello'}]},
    {'role': 'user', 'content': 'bye'}]}).encode()
req = urllib.request.Request('http://127.0.0.1:8083/v1/messages', data=payload,
  headers={'Content-Type': 'application/json', 'x-api-key': 'test', 'anthropic-version': '2023-06-01'})
print(urllib.request.urlopen(req, timeout=30).read().decode()[:200])
"
```

---

## Notas de sesiones anteriores

### Sesion 2025-02-07
**Objetivo:** Fix 422 errors del proxy con Z.AI + implementar intent classifier
**Resultado:**
- Fix aplicado: 4 nuevos content block types en schemas.py
- Thinking blocks se stripean en converter para providers OpenAI-compatible
- Intent classifier implementado con LLM (DeepSeek) + regex fallback
- Cloud routing: CHAT -> downgrades a SMALL_MODEL automaticamente
**Aprendizaje:** Los schemas Pydantic deben cubrir TODOS los tipos de la Anthropic API. Extended thinking es un feature que Claude Code usa activamente y los proxies deben manejarlo.
**Bloqueadores encontrados:** Z.AI timeout intermitente (~30s+ en algunos requests)

---

## Grokking checkpoints
> Momentos donde el modelo "entendio" algo fundamental del proyecto

1. **2025-02-07**: El proxy es una capa de abstraccion Anthropic->OpenAI, no un simple forwarder. Cada content block type nuevo de Anthropic requiere: schema + converter + stripper
2. **2025-02-07**: Intent classification es un "hop" en el multi-hop grounding. Separar el clasificador del provider principal permite optimizar costo vs precision independientemente
