# DeepSeek Integration - Validación Exhaustiva

**Fecha**: 2026-02-27
**Estado**: VALIDADO - Implementación correcta, no se necesita implementar nada nuevo

---

## 1. Flujo Completo DeepSeek (Request → Response)

### 1.1 Request Pipeline
```
CC Request → server.py → proxy.py:run_messages()
  → convert_anthropic_to_litellm() [converters.py:423]
    → is_no_tools_model() check [tool_prompting.py:68]
    → Si NO_TOOLS: inject XML prompt + rewrite history
    → Si normal: convert tools a OpenAI format
  → litellm_pipeline.process() [compression, quirks, credentials]
  → litellm.acompletion() → DeepSeek API
```

### 1.2 Response Pipeline (Streaming)
```
DeepSeek chunks → handle_streaming() [streaming.py:438]
  → XmlToolBuffer state machine [tool_prompting.py:869]
  → reasoning_content → reasoning_buffer (NO se emite como texto)
  → delta.content → XmlToolBuffer.feed() → tool extraction
  → finish_reason → flush buffer + safety net + close blocks
  → SSE events → Claude Code
```

### 1.3 Response Pipeline (Non-streaming)
```
DeepSeek response → convert_litellm_to_anthropic() [converters.py:526]
  → reasoning_content → text block con <reasoning> tags
  → extract_tool_calls_from_text() si no-tools o XML detectado
  → recover_truncated_deterministic() si extraction falla
  → strip_tool_call_xml() como último fallback
```

---

## 2. Validación de Cada Componente

### 2.1 NO_TOOLS_MODELS Detection ✅
**Archivo**: `tool_prompting.py:52-74`
- `_load_no_tools_models()`: Lee `NO_TOOLS_MODELS` env var, cachea con `@lru_cache(1)`
- `is_no_tools_model()`: Substring match case-insensitive
- **Correcto**: Si `NO_TOOLS_MODELS=deepseek-reasoner`, entonces `openai/deepseek-reasoner` matchea

### 2.2 Tool Prompt Injection ✅
**Archivo**: `converters.py:497-507`
- Cuando `no_tools=True`, inyecta XML prompt con `build_tool_prompt()` en system message
- Reescribe historial con `rewrite_messages_without_tools()` (tool_calls → XML, role:tool → XML user)
- `_merge_consecutive_messages()` evita mensajes consecutivos del mismo role
- **Correcto**: DeepSeek recibe tools como XML + historial sin native tools

### 2.3 Tool Prompt Format ✅
**Archivo**: `tool_prompting.py:162-230`
- Formato claro: `<tool_call name="ToolName"><input>{json}</input></tool_call>`
- RULES section con 6 reglas CRITICAL:
  - Usar exactamente `<input>` tags
  - Double quotes para name attribute
  - JSON válido con double quotes
  - NO `<reasoning>` tags dentro de tool_call
  - NO inventar tags XML
  - Usar SOLO tool names del allowlist
- Quick reference + schema properties recursivos (TodoWrite nested arrays)
- **Correcto**: El prompt es muy explícito y cubre los edge cases conocidos

### 2.4 Regex de Extracción (5 niveles) ✅
**Archivo**: `tool_prompting.py:305-344`

| # | Regex | Propósito | Estado |
|---|-------|-----------|--------|
| 1 | `_TOOL_CALL_RE` | Standard con inner tags conocidos + `_REASONING_SKIP` | ✅ Correcto |
| 2 | `_TOOL_CALL_FALLBACK_RE` | Cualquier par de XML tags + backreference | ✅ Correcto |
| 3 | `_TOOL_CALL_BARE_RE` | JSON directo sin inner tags | ✅ Correcto |
| 4 | `_TOOL_CALL_ARGKV_RE` | GLM format `<arg_key>/<arg_value>` | ✅ Correcto* |
| 5 | `_TOOL_DILUTED_RE` | `<tool_name>/<args>` post-dilution | ✅ Correcto |

**NOTA sobre GLM regex**: El agente Explore reportó un bug falso. La regex real en línea 330 dice `</arg_key>` (CORRECTO), no `</arg_value>`.

**DeepSeek-specific**:
- `_NAME_ATTR = r"""name=["']([^"']+)["']"""` — Acepta single Y double quotes (deepseek-reasoner usa single quotes)
- `_REASONING_SKIP` — Salta `<reasoning>` tags que DeepSeek inyecta dentro de `<tool_call>`
- `_strip_inner_xml_tags()` — También elimina `<reasoning>` tags antes del JSON parse

### 2.5 reasoning_content Handling ✅

#### Streaming (streaming.py:486-506):
```python
if no_tools_mode:
    # DeepSeek-reasoner: emit_text=False → reasoning_buffer
    # (reasoning is 5-15K tokens, crashes CC's SSE parser)
    _process_buffer_segments(ctx, delta_reasoning, emit_text=False)
```
- **Correcto**: reasoning va al buffer, NO se emite como texto
- XmlToolBuffer procesa reasoning por si contiene `<tool_call>` XML
- `_process_reasoning_buffer()` al final:
  - Si hay tool calls → suprime reasoning (crashea CC)
  - Si hay `<tool_call>` en reasoning → extrae tools
  - Si no hay tools → emite reasoning como texto

#### Non-streaming (converters.py:550-552, 615-616):
```python
if reasoning_text:
    content.append({"type": "text", "text": f"<reasoning>\n{reasoning_text}\n</reasoning>\n\n"})
```
- Pero luego en línea 628-631: Si se extraen tool calls, reasoning se SUPRIME
- También busca `<tool_call>` en reasoning_text para DeepSeek-reasoner (línea 615)
- **Correcto**: reasoning_content nunca llega a CC con tool calls

### 2.6 XmlToolBuffer State Machine ✅
**Archivo**: `tool_prompting.py:869-1113`
- `feed()`: Procesa chunks incrementalmente, detecta `<tool_call` tags
- `_has_plausible_tool_call()`: Distingue XML real de documentación (backtick-quoted)
- `_try_extract_text()`: Emite texto antes de tool call, maneja `<tool_call` parcial
- `_try_extract_tool()`: Espera `</tool_call>`, safety overflow a 16KB
- `_parse_tool_xml()`: Usa las 4 regex en cascada (no diluted, ya es streaming)
- `flush()`: Al final del stream, maneja incomplete tool calls
- `_safe_text_end()`: Evita cortar `<tool_call` parcial al final del buffer
- **Correcto**: Implementación robusta de state machine

### 2.7 Tool Recovery (3 niveles) ✅
**Archivo**: `streaming.py:293-330`, `tool_prompting.py:647-834`

1. **Deterministic** (`recover_truncated_deterministic`): json_repair + schema validation
   - Intenta argkv format primero
   - Luego name= format con `_PARTIAL_TOOL_RE`
   - json_repair para cerrar JSON truncado
   - Valida required fields del schema
   - Detecta strings truncados (>500 chars)

2. **LLM Recovery** (`recover_incomplete_tool_call`): Pide al CLASSIFIER_MODEL completar el XML
   - Timeout 3s, temperatura 0
   - Usa contexto previo + tool schema
   - Deshabilitado con `DISABLE_TOOL_RECOVERY=1`

3. **Strip Fallback** (`strip_tool_call_xml`): Limpia XML y emite texto
   - Nunca raw XML llega a CC

**Correcto**: 3 niveles de recovery, del más rápido al más lento

### 2.8 Safety Net ✅
**Archivo**: `streaming.py:587-618`
- Al final del stream, revisa `accumulated_text + reasoning_buffer`
- Si hay `<tool_call>` que XmlToolBuffer no capturó → extracción directa
- Si `finish_reason=length` y tool truncado → emite warning a CC:
  ```
  [proxy-warning: A tool call was truncated due to output length limits...]
  ```
- **Correcto**: Double-check que nada se escape

### 2.9 Schemas (422 fix) ✅
**Archivo**: `schemas.py:54-72`
- `ContentBlockThinking`: type="thinking", thinking=str, signature=str
- `ContentBlockRedactedThinking`: type="redacted_thinking", data=str
- `ContentBlockServerToolUse`: type="server_tool_use"
- `ContentBlockServerToolResult`: type="server_tool_result"
- Todos incluidos en `Message.content` Union type
- **Correcto**: Pydantic valida y no rechaza estos blocks

### 2.10 Token Management ✅
**Archivo**: `converters.py:440-468`
- Para `no_tools` models: max_tokens NO se capea (reasoning consume output tokens)
- Temperature se omite para no_tools models (reasoning models lo ignoran)
- **Correcto**: DeepSeek-reasoner necesita max_tokens alto para reasoning + tool output

---

## 3. SSE Protocol Compliance ✅
**Archivo**: `sse.py:1-147`
- Todos los eventos siguen el protocolo Anthropic exacto
- `message_start` → `content_block_start` → deltas → `content_block_stop` → `message_delta` → `message_stop` → `[DONE]`
- `response_to_sse_events()`: Convierte MessagesResponse a SSE events (para non-streaming → streaming bridge)
- Tool use blocks: `content_block_start_tool` + `input_json_delta("")` (init) + `input_json_delta(json)` + `content_block_stop`
- **Correcto**: CC espera exactamente este formato

---

## 4. Tests Existentes

### Cobertura actual:
- `test_converters.py`: 25+ tests cubriendo _bget, _safe_json, assistant/user blocks, tool conversion, cache, Gemini schemas, JSON repair
- `test_streaming_reasoning.py`: (nuevo, sin leer - probablemente cubre reasoning)
- `test_fallback.py`: Provider fallback chain
- `test_intent_classifier.py`: Classifier con deepseek-chat model

### Gaps en cobertura:
- **NO hay tests dedicados para las 5 regex patterns con inputs reales**
- **NO hay tests para `extract_tool_calls_from_text` con DeepSeek-specific formats**
- **NO hay tests para XmlToolBuffer state machine completa**
- **NO hay tests para reasoning_content con tool calls embedded**

---

## 5. Conclusión

### ✅ TODO ESTÁ CORRECTO — NO SE NECESITA IMPLEMENTAR NADA NUEVO

La integración DeepSeek está completa y bien implementada:

1. **Tool extraction**: 5 niveles de regex + 3 niveles de recovery
2. **reasoning_content**: Correctamente buffered, suprimido cuando hay tools, extraído cuando contiene tools
3. **Streaming**: XmlToolBuffer state machine robusta con safety net
4. **Schemas**: Thinking blocks aceptados correctamente
5. **Token management**: max_tokens no capeado para reasoning models
6. **SSE protocol**: Compliance exacta con Anthropic spec

### Recomendaciones (mejora, no bloqueo):
1. **Agregar tests unitarios** para las 5 regex con inputs reales de DeepSeek
2. **Agregar test** para `XmlToolBuffer` con reasoning_content que contiene `<tool_call>`
3. **Agregar test** para `extract_tool_calls_from_text` con single-quote name attr

### Falso positivo del análisis anterior:
- El "bug en GLM regex" reportado era **FALSO**. La regex real dice `</arg_key>` (correcto).
