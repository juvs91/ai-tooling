# Análisis Exhaustivo: Fallo de DeepSeek API vía Proxy

**Fecha:** 2026-02-13
**Contexto:** Claude Code usando proxy → DeepSeek (deepseek-chat / deepseek-reasoner)
**Síntoma:** API falla intermitentemente, especialmente en sesiones largas con tool calls

---

## 1. DIAGNÓSTICO: ¿Qué Falló?

### 1.1 Evidencia de los Logs

Los logs muestran **DOS problemas críticos** en las últimas requests:

```
stop_reason=max_tokens finish_reason=length tool_index=0 has_xml=False no_tools=False
```

Esto ocurrió **dos veces consecutivas** con `claude-opus-4-6` → `openai/deepseek-chat`:
- `approx_tokens= 24829` con 17 tools
- `approx_tokens= 24976` con 17 tools

**Interpretación:**
- `finish_reason=length` = DeepSeek cortó la respuesta porque se quedó sin `max_completion_tokens`
- `tool_index=0` = **Estaba generando un tool_call cuando se cortó**
- Esto produce un **tool_call JSON truncado** → Claude Code recibe un tool_use incompleto → **CRASH**

### 1.2 Cadena de Fallo Completa

```
Claude Code envía request (24K+ tokens, 17 tools)
  → Proxy mapea: claude-opus-4-6 → openai/deepseek-chat
  → deepseek-chat genera tool_call
  → Se alcanza max_completion_tokens (16384 cap)
  → finish_reason=length
  → Proxy convierte a Anthropic: stop_reason=max_tokens
  → Claude Code recibe tool_use block con JSON TRUNCADO
  → Claude Code no puede parsear el input → ERROR/CRASH
```

---

## 2. PROBLEMAS IDENTIFICADOS (Exhaustivo)

### PROBLEMA 1: `max_tokens` Cap demasiado bajo para deepseek-chat con tools

**Archivo:** `llm/converters.py:446-451`
```python
if (
    isinstance(anthropic_request.model, str)
    and anthropic_request.model.startswith("openai/")
    and not no_tools
):
    max_tokens = min(max_tokens, 16384)
```

**El problema:** Claude Code envía `max_tokens=32000` (su default para opus). El proxy lo capea a 16384 para modelos `openai/` que no son reasoning. Pero cuando DeepSeek genera tool_calls con argumentos JSON largos (ej: un `Write` tool con contenido de archivo), 16384 tokens puede no ser suficiente y se trunca.

**deepseek-chat soporta 8K output tokens por defecto y hasta 16K con ciertas configuraciones.** El cap de 16384 ya está en el límite. Con contextos de 24K+ tokens de input + guardrails + tool definitions, el espacio para output se reduce aún más.

### PROBLEMA 2: Sin compresión efectiva para deepseek-chat

**Archivo:** `proxy/proxy.py:380-399`
**Archivo:** `llm/compressor.py`

**La compresión está configurada pero puede no activarse a tiempo:**
- `MODEL_CONTEXT_WINDOW=64000` → threshold = `0.85 * 64000 = 54400`
- Estimación de tokens = `chars / 4` (heurística del compressor)
- Pero `approx_tokens_from_bytes` usa `len(bytes) // 6`

**El conflicto:** Hay DOS estimaciones diferentes de tokens:
1. `approx_tokens_from_bytes(raw_body)` en `apply_policy_and_routing` = `bytes / 6` → para caps
2. `_estimate_message_tokens(messages)` en `compressor.py` = `chars / 4` → para trigger

Los logs muestran requests de **24-27K approx_tokens** (estimados como bytes/6). Pero la compresión opera sobre mensajes **ya convertidos** a OpenAI format. La realidad es:
- El raw body incluye tool schemas (muy repetitivos en cada request)
- Los 17 tools + schemas pueden ser 10K+ tokens solo en definiciones
- El contenido real de la conversación crece con cada tool_result

**Resultado:** La compresión no se activa porque `estimated_tokens < 54400`, pero DeepSeek con 64K context se ahoga cuando:
- 24K input tokens (estimación conservadora)
- +10K+ de tool definitions
- +system prompt con guardrails
- +tool enforcement prompt
- = **35-40K tokens reales de input**
- Dejando solo **24-29K para output** (y DeepSeek capea output a 8K/16K)

### PROBLEMA 3: `tool_index=0` con `finish_reason=length` = Tool Call Truncado

**Archivo:** `llm/streaming.py:266-271`

Cuando `finish_reason=length` ocurre **durante** una tool_call (tool_index != None):
```python
# close tool blocks (with JSON repair attempt)
if tool_index is not None:
    for i in range(1, last_tool_index + 1):
        suffix = _compute_repair_suffix(tool_args_buffer.get(i, ""), i)
        if suffix:
            yield ...
        yield content_block_stop
```

El proxy intenta reparar el JSON con `json_repair`, pero:
- Si el JSON está muy truncado (cortado a mitad de una cadena larga), la reparación puede ser incorrecta
- Claude Code recibe `stop_reason=max_tokens` con un tool_use block → puede re-intentar pero con un tool_call corrupto en el historial
- El **re-intento** incluye el tool_call corrupto en el historial → DeepSeek se confunde

### PROBLEMA 4: Sin Context Window Scaling para Token Count

**Archivo:** `utils/utils.py:36-44`
```python
def scale_tokens(raw_count: int, model_context_window: int) -> int:
    if model_context_window <= 0 or model_context_window >= _CLAUDE_ASSUMED_CONTEXT:
        return raw_count
    return int(raw_count * (_CLAUDE_ASSUMED_CONTEXT / model_context_window))
```

**Esto SÍ está implementado** y con `MODEL_CONTEXT_WINDOW=64000`:
- Factor de escala: `200000 / 64000 = 3.125x`
- Token count de 20K → reportado como 62.5K a Claude Code
- Esto hace que CC crea que está "cerca del límite" y active su propia compresión

**PERO:** El escalado se aplica al **output** (token counts que reportamos a CC), NO al input que enviamos a DeepSeek. Así que CC puede creer que tiene espacio, pero DeepSeek no.

### PROBLEMA 5: Routing incorrecto de opus → deepseek-chat (no reasoner)

**Los logs muestran:**
```
model_in= claude-opus-4-6 model_out= openai/deepseek-chat tools_in= 17 (intent=CHAT)
```

Cuando CC envía `claude-opus-4-6`, el mapping es:
1. `map_claude_alias_to_target`: "opus" → `big_model` = `deepseek-reasoner`
2. Pero luego el intent routing: `CHAT` → `small_model` = `deepseek-chat`

**El problema:** Claude Code usa opus para su conversación principal (Task tool, complex operations). El clasificador a veces marca como "CHAT" algo que debería ser "BUILDING" o "PLANNING", y el modelo se downgrade a deepseek-chat que es MUCHO menos capaz con 17 tools.

### PROBLEMA 6: La compresión se ejecuta DESPUÉS de la conversión pero ANTES del envío

**Archivo:** `proxy/proxy.py:392-399`

```python
litellm_request["messages"], was_compressed = await compress_messages_if_needed(
    messages=litellm_request["messages"],
    model_context_window=model_ctx,
    ...
)
```

La compresión usa `_estimate_message_tokens(messages)` = `chars / 4` sobre mensajes ya convertidos. Pero **NO incluye:**
- El system prompt (ya insertado como messages[0])
- Las tool definitions (que se pasan como `litellm_request["tools"]`, no como messages)
- El max_completion_tokens reservado

**Resultado:** La estimación subestima el total real. Un request con:
- 40K chars de mensajes → ~10K tokens estimados (con chars/4)
- +8K tokens de tool schemas (NO contados)
- +2K tokens de system prompt (NO contados separadamente)
- +16K reservados para output
- = **36K tokens reales** vs 64K window, pero solo 10K estimados → no comprime

---

## 3. FORMATO DE TOOL CALLS: Lo que Claude Code Espera

### 3.1 Formato Anthropic SSE para tool_use (lo que CC espera)

```
event: content_block_start
data: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"toolu_abc123","name":"Read","input":{}}}

event: content_block_delta
data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\"file_path\":\"/path/to/file\"}"}}

event: content_block_stop
data: {"type":"content_block_stop","index":1}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"tool_use","stop_sequence":null},"usage":{"output_tokens":50}}
```

**Requisitos CRÍTICOS de Claude Code:**
1. `id` DEBE empezar con `toolu_` (el proxy ya lo maneja)
2. `partial_json` vacío como primer delta (el proxy ya lo emite)
3. `stop_reason` DEBE ser `"tool_use"` cuando hay tool_use blocks
4. El JSON de `input` DEBE ser válido y parseble
5. Los `index` deben ser secuenciales y no repetirse
6. `content_block_stop` es obligatorio para cada bloque

### 3.2 Lo que el Proxy Envía (análisis del flujo)

El proxy maneja correctamente:
- ✅ IDs con formato `toolu_*`
- ✅ Empty `partial_json` inicial
- ✅ `stop_reason: tool_use` cuando hay tool_calls
- ✅ Secuencia de indices correcta
- ✅ `content_block_stop` para cada bloque

**PERO falla en:**
- ❌ JSON de tool input puede estar truncado cuando `finish_reason=length`
- ❌ `json_repair` puede producir JSON "reparado" que no tiene sentido semántico
- ❌ No valida que el JSON reparado tenga las keys requeridas del schema

### 3.3 Formato No-Streaming (para referencia)

```json
{
  "id": "msg_abc123",
  "type": "message",
  "role": "assistant",
  "content": [
    {"type": "text", "text": "Let me read that file."},
    {"type": "tool_use", "id": "toolu_def456", "name": "Read", "input": {"file_path": "/path"}}
  ],
  "stop_reason": "tool_use",
  "usage": {"input_tokens": 100, "output_tokens": 50}
}
```

---

## 4. FIXES RECOMENDADOS (Priorizados)

### FIX 1: Compresión más agresiva (CRÍTICO)

**Problema:** La compresión no cuenta tool definitions ni system prompt.

**Fix en `proxy/proxy.py`:** Cambiar el trigger de compresión para incluir el overhead real.

```python
# ANTES de compress_messages_if_needed, calcular overhead real
tool_overhead = 0
if "tools" in litellm_request and litellm_request["tools"]:
    tool_overhead = len(json.dumps(litellm_request["tools"])) // 4  # rough estimate

system_overhead = len(litellm_request["messages"][0].get("content", "")) // 4 if litellm_request["messages"] else 0

# Pasar como effective_window al compressor
effective_window = model_ctx - tool_overhead - (max_tokens // 2)
```

O alternativamente, **bajar el `trigger_ratio`** de 0.85 a **0.60** para DeepSeek:

```env
# En cloud.deepseek.env
COMPRESSOR_TRIGGER_RATIO=0.60
```

### FIX 2: Manejar tool_call truncado gracefully (CRÍTICO)

**Problema:** Cuando `finish_reason=length` durante un tool_call, el JSON queda corrupto.

**Fix en `llm/streaming.py`:** Cuando detectemos `finish_reason=length` con `tool_index is not None`:
1. Intentar reparar JSON
2. Si la reparación falla o el resultado no tiene las keys requeridas → **descartar el tool_use block**
3. Emitir solo el texto acumulado + `stop_reason=max_tokens` (sin el tool_use corrupto)

```python
# En el bloque finish de streaming.py
if finish_reason == "length" and tool_index is not None:
    # Tool call was truncated - validate repair
    for i in range(1, last_tool_index + 1):
        accumulated = tool_args_buffer.get(i, "")
        try:
            json.loads(accumulated)  # Valid?
        except:
            repaired = repair_json(accumulated)
            try:
                parsed = json.loads(repaired)
                if not isinstance(parsed, dict):
                    raise ValueError("Not a dict")
            except:
                # DISCARD this tool_use block entirely
                # Re-emit as text instead
                print(f"[streaming] DISCARDING truncated tool_call at index {i}")
                # ... emit text fallback
```

### FIX 3: max_tokens dinámico basado en contexto (ALTO)

**Problema:** Cap fijo de 16384 no es suficiente cuando hay tool_calls largos.

**Fix en `llm/converters.py`:**
```python
# En vez de cap fijo, calcular basado en context window
if model.startswith("openai/") and not no_tools:
    model_ctx = int(os.environ.get("MODEL_CONTEXT_WINDOW", "0"))
    if model_ctx > 0:
        # Reservar al menos 25% del window para output
        estimated_input = sum(len(m.get("content", "")) for m in messages) // 4
        available_for_output = model_ctx - estimated_input
        max_tokens = min(max_tokens, max(4096, available_for_output))
    else:
        max_tokens = min(max_tokens, 16384)
```

### FIX 4: Mejorar estimación de tokens en compressor (MEDIO)

**Problema:** `chars / 4` subestima tokens en JSON/código.

**Fix en `llm/compressor.py`:**
```python
def _estimate_message_tokens(messages: list[dict]) -> int:
    total = 0
    for msg in messages:
        content = msg.get("content", "") or ""
        # JSON/code has more tokens per char than prose
        if any(c in content for c in ['{', '[', '"', ':', ',']):
            total += len(content) // 3  # JSON/code ratio
        else:
            total += len(content) // 4  # prose ratio
    return max(1, total)
```

### FIX 5: Intent classifier más preciso para opus requests (MEDIO)

**Problema:** Requests de opus con 17 tools se clasifican como CHAT → downgrade a deepseek-chat.

**Fix:** Cuando el request tiene muchas tools (>10), el intent debería pesar más hacia BUILDING/PLANNING:
```python
# En apply_policy_and_routing, después de intent classification
tools_count = len(getattr(request_obj, "tools", None) or [])
if tools_count > 10 and intent == "CHAT":
    intent = "BUILDING"  # Many tools = likely an agent, not chat
    print(f"[route] Upgraded intent CHAT→BUILDING (tools_count={tools_count})")
```

### FIX 6: Compresión debe contar tools+system overhead (MEDIO)

**Cambiar `compress_messages_if_needed` para aceptar overhead adicional:**

```python
async def compress_messages_if_needed(
    messages: list[dict],
    model_context_window: int,
    ...,
    additional_token_overhead: int = 0,  # NEW: para tools, system, etc.
) -> tuple[list[dict], bool]:
    estimated_tokens = _estimate_message_tokens(messages) + additional_token_overhead
    threshold = int(trigger_ratio * model_context_window)
    ...
```

---

## 5. RESUMEN EJECUTIVO

| # | Problema | Severidad | Estado | Fix |
|---|---------|-----------|--------|-----|
| 1 | Compresión no cuenta tool definitions overhead | CRÍTICO | Pendiente | Bajar trigger_ratio o contar overhead |
| 2 | Tool call truncado por max_tokens → JSON corrupto | CRÍTICO | Pendiente | Descartar tool_use corrupto |
| 3 | max_tokens cap fijo (16384) insuficiente | ALTO | Pendiente | Cap dinámico basado en context |
| 4 | Estimación chars/4 subestima tokens JSON | MEDIO | Pendiente | Ratio diferenciado para JSON |
| 5 | opus+17tools clasificado como CHAT → downgrade | MEDIO | Pendiente | Upgrade intent con muchas tools |
| 6 | Compressor no recibe overhead de tools | MEDIO | Pendiente | Pasar overhead como parámetro |

### Lo que SÍ funciona correctamente:
- ✅ Formato SSE Anthropic (IDs, indices, partial_json, stop_reason)
- ✅ XML tool simulation para deepseek-reasoner
- ✅ Token scaling (3.125x para 64K window)
- ✅ Retry con backoff exponencial
- ✅ Reasoning content suppression cuando hay tool_calls
- ✅ JSON repair para casos simples
- ✅ Compresión con Z.AI fallback

### La causa raíz más probable del fallo:
**`finish_reason=length` durante tool_call generation** → JSON truncado → CC recibe tool_use con input inválido → CC crashea o retries con historial corrupto → loop de fallos.

Confirmado en logs: dos requests consecutivas terminaron con `stop_reason=max_tokens finish_reason=length tool_index=0`.
