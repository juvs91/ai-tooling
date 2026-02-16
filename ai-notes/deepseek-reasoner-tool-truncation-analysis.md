# Análisis Exhaustivo: Compresión y Truncamiento de Tool Calls con DeepSeek Reasoner

**Fecha:** 2026-02-15
**Contexto:** Claude Code → proxy → DeepSeek Reasoner (XML tool simulation)
**Config actual:** BIG/SMALL/BUILDING = `deepseek-reasoner`, NO_TOOLS_MODELS=`deepseek-reasoner`

---

## PUNTO 5: ¿Por Qué la Compresión No Se Activa a Tiempo?

### El Problema: Tres Estimaciones de Tokens Desconectadas

El proxy tiene **tres** estimaciones diferentes de tokens que NO coinciden:

| Estimador | Ubicación | Fórmula | Para qué se usa |
|-----------|-----------|---------|------------------|
| `approx_tokens_from_bytes` | `utils.py:30` | `len(raw_body) / 6` | Logging, provider cap, hard cap |
| `_estimate_message_tokens` | `compressor.py:30` | `sum(len(content)) / 4` | **Trigger de compresión** |
| `estimate_tools_tokens` | `compressor.py:39` | `len(json_tools) / 4` | Overhead de tools (sumado al anterior) |

### Traza Exacta para DeepSeek Reasoner (Config Actual)

**Config:**
```
MODEL_CONTEXT_WINDOW=64000
COMPRESSOR_TRIGGER_RATIO=0.75
NO_TOOLS_MODELS=deepseek-reasoner
COMPRESSOR_MODEL=openai/glm-4.7-flash
MAX_OUTPUT_TOKENS=8192
```

**Threshold = 0.75 × 64000 = 48,000 tokens**

#### Paso 1: `convert_anthropic_to_litellm()` (converters.py:427)

```
is_no_tools_model("openai/deepseek-reasoner") → True

→ max_tokens cap block: `not no_tools` = False → SALTADO
→ max_tokens queda en lo que CC envió (ej: 16384)

→ Tools NO van a litellm_request["tools"]
→ Tools inyectados como XML prompt en messages[0]["content"] (system)
→ Historial reescrito con rewrite_messages_without_tools()
→ litellm_request["tools"] = NO EXISTE (key no seteada)
```

#### Paso 2: Compresión en `run_messages()` (proxy.py:395)

```python
tools_overhead = estimate_tools_tokens(litellm_request.get("tools"))
#                                      ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#                      litellm_request NO tiene "tools" (son XML en system)
#                      → .get("tools") = None → estimate_tools_tokens(None) = 0
```

**`tools_overhead = 0` siempre para deepseek-reasoner**

Esto es correcto en el sentido de que las tools ya están EN los mensajes (como XML en system).
Pero `_estimate_message_tokens` usa `chars / 4` que SUBESTIMA.

#### Paso 3: `compress_messages_if_needed()` (compressor.py:100)

```python
estimated_tokens = _estimate_message_tokens(messages) + 0  # tools_overhead=0
threshold = int(0.75 * 64000)  # = 48000
```

### Números Reales: Sesión Típica (10ª iteración, 30 mensajes)

**System message (con XML tool prompt inyectado):**
```
XML prompt header + rules + examples:      ~2,000 chars
17 tool definitions (name, desc, params):  ~5,000 chars
CC system prompt original:                 ~3,000 chars
Guardrails proxy:                          ~   300 chars
───────────────────────────────────────────────────────
Total system:                              ~10,300 chars
→ estimate_message_tokens: 10300 / 4 = 2,575
→ tokens REALES (tokenizer): ~3,500-4,000 (JSON/XML tokeniza peor)
```

**Conversación (30 mensajes reescritos sin tools):**
```
10 tool_call XMLs:    ~1,000 chars (Read/Bash calls son cortos)
10 tool_result XMLs:  ~30,000-50,000 chars (file reads pueden ser enormes)
10 mensajes texto:    ~5,000 chars
───────────────────────────────────────────────────────
Total conversación:   ~40,000-60,000 chars
→ estimate: 50000 / 4 = 12,500
→ tokens REALES: ~16,000-20,000 (JSON/code tokeniza a ~3 chars/token)
```

**Total estimado: 2,575 + 12,500 = 15,075 tokens**
**Total REAL: ~20,000-24,000 tokens**
**Threshold: 48,000**

**RESULTADO: 15,075 < 48,000 → COMPRESIÓN NO SE ACTIVA**

### Sesión Más Larga (20ª iteración, 60 mensajes)

```
System:        ~10,300 chars → 2,575 estimate
Conversación:  ~120,000 chars → 30,000 estimate
───────────────────────────────────────────────
Total estimado: 32,575
Total REAL:     ~45,000-50,000
Threshold:      48,000
```

**32,575 < 48,000 → COMPRESIÓN TODAVÍA NO SE ACTIVA**
**Pero tokens reales (~47,000) ya casi llenan la ventana de 64K**

### La Subestimación Sistemática: chars/4 vs Realidad

| Tipo de contenido | chars/token (heurística) | chars/token (real) | Error |
|---|---|---|---|
| Prosa en inglés | 4 | 4-5 | OK |
| Prosa en español | 4 | 3-4 | ~15% bajo |
| JSON/XML | 4 | **2.5-3** | **30-40% bajo** |
| Código Python | 4 | **3-3.5** | **15-30% bajo** |
| File paths | 4 | **2-3** | **30-50% bajo** |

Para deepseek-reasoner, **la mayoría del contenido es JSON/XML** (tool prompts, tool calls reescritos, tool results con código/paths). La heurística chars/4 subestima consistentemente por **30-40%**.

### ¿Por Qué con GLM (Z.AI) No Era Tan Grave?

Con Z.AI:
- `MODEL_CONTEXT_WINDOW=131072` (128K)
- Threshold = 0.85 × 131072 = **111,411 tokens**
- GLM-4.7 tiene 128K de context real
- Incluso subestimando 30%, una sesión de 80K chars: estimate=20K, real=28K
- Ambos muy lejos del threshold de 111K
- **Nunca necesita comprimir** porque 128K es enorme

Con DeepSeek:
- `MODEL_CONTEXT_WINDOW=64000` (64K)
- Threshold = 0.75 × 64000 = **48,000 tokens**
- El margen es MUCHO menor
- La subestimación de 30-40% es la diferencia entre "OK" y "overflow"

### ¿Puede DeepSeek Servir Como Compresor?

El compressor actual usa **Z.AI GLM-4.7-flash** (128K context, gratis). Se podría usar deepseek-chat:

| | GLM-4.7-flash (actual) | deepseek-chat (alternativa) |
|---|---|---|
| Context window | 128K | 64K |
| Output limit | ~4K | 8K |
| Costo | Gratis (Z.AI) | ~$0.001/call |
| Velocidad | ~2-3s | ~1-2s |
| Calidad resumen | Buena | Buena |

**Veredicto:** GLM es mejor como compresor porque tiene 128K de context (puede ingerir conversaciones más grandes para resumir). Deepseek-chat tiene solo 64K, que podría no alcanzar para resumir conversaciones largas.

**El problema NO es el compresor. El problema es que el TRIGGER nunca se activa.**

---

## PUNTO 3: ¿Qué Pasa Cuando un Tool Call se Trunca?

### Flujo Exacto para DeepSeek Reasoner (XML Mode)

DeepSeek Reasoner genera output en DOS fases:
```
Fase 1: reasoning_content (chain of thought) — tipicamente 2K-8K tokens
Fase 2: content (texto + XML tool calls) — limitado por max_tokens Y output budget
```

Cuando la sesión es larga:
```
Context window: 64K
Input tokens:    ~40K-50K (subestimados)
Output budget:   64K - input = 14K-24K
Reasoning:       -5K (variable, incontrolable)
Content budget:  9K-19K tokens para XML tool calls
```

Pero si el modelo intenta generar un Write con un archivo largo:
```
<tool_call name="Write">
<input>
{"file_path": "/path/to/file.py", "content": "import os\nimport sys\n...200 líneas..."}
</input>
</tool_call>
```

Un archivo de 200 líneas ≈ 4K-8K tokens solo en el JSON del content.
Si el budget es 9K y reasoning usó 5K, solo quedan 4K → **TRUNCAMIENTO**.

### Los 4 Escenarios de Truncamiento

#### Escenario A: UN tool call truncado (sin calls previos exitosos)

```
DeepSeek genera:
"Voy a leer el archivo.\n<tool_call name="Read"><input>{"file_path": "/very/lo...

finish_reason=length
```

**Flujo en streaming.py:**
1. `XmlToolBuffer.feed()`: texto "Voy a leer..." emitido como text_delta a CC
2. `XmlToolBuffer.feed()`: acumula `<tool_call name="Read"><input>{"file_path": "/very/lo...` en buffer
3. Stream termina → `XmlToolBuffer.flush()` retorna `[{"type": "text", "text": "<tool_call..."}]`
4. `"<tool_call" in segment["text"]` → **True**
5. `recover_incomplete_tool_call()` llamado:
   - model=`openai/deepseek-chat`, max_tokens=**512**, temp=0
   - Le pide completar el XML truncado
   - Para un Read simple: **probablemente ÉXITO** (solo necesita completar el path)
   - Para un Write largo: **FALLO** (512 tokens no alcanza para reconstruir contenido)

**Si recovery ÉXITO:**
- `has_xml_tool_calls = True`
- Tool emitido como `tool_use` block SSE → CC lo ejecuta
- `stop_reason = "tool_use"` → CC feliz

**Si recovery FALLO:**
- Texto parcial emitido como text_delta (text_block NO cerrado aún)
- `has_xml_tool_calls = False`
- `finish_reason = "length"` → `stop_reason = "max_tokens"`
- CC recibe: texto con XML parcial + stop_reason=max_tokens
- **CC interpreta max_tokens = "se acabó el espacio"** → puede reintentar o mostrar error

#### Escenario B: Múltiples tool calls, el ÚLTIMO se trunca

```
DeepSeek genera:
<tool_call name="Read"><input>{"file_path": "/a"}</input></tool_call>     ← COMPLETO
<tool_call name="Bash"><input>{"command": "ls"}</input></tool_call>       ← COMPLETO
<tool_call name="Write"><input>{"file_path": "/b", "content": "parti...   ← TRUNCADO

finish_reason=length
```

**Flujo en streaming.py:**
1. XmlToolBuffer procesa durante streaming:
   - Read tool_call completo → emitido como tool_use block (index=1)
   - Bash tool_call completo → emitido como tool_use block (index=2)
   - `has_xml_tool_calls = True`
2. Buffer al final: `<tool_call name="Write"><input>{"file_path": "/b", "content": "parti...`
3. flush() → recovery intenta completar el Write → **FALLA** (512 tokens insuficientes)
4. Texto fallback: `text_block_closed = True` (se cerró cuando emitimos Read)
5. **El texto del Write truncado se agrega a `accumulated_text` pero NUNCA SE ENVÍA** porque `text_block_closed = True`

**Decisión de stop_reason:**
```python
valid_tool_blocks = 0       # (solo cuenta native tools, XML no se cuentan aquí)
total_native_tools = 0      # (tool_index is None, no hay native tools)
# valid_tool_blocks(0) == total_native_tools(0) → True
# (tool_index is not None OR has_xml_tool_calls) → True
# finish_reason == "length" → True
→ stop_reason = "tool_use"
```

**CC recibe:**
1. tool_use block para Read → CC ejecuta
2. tool_use block para Bash → CC ejecuta
3. stop_reason = "tool_use"
4. **El Write DESAPARECE SILENCIOSAMENTE** — CC nunca lo ve

**Consecuencia:** El modelo quería hacer Read+Bash+Write, pero CC solo ejecuta Read+Bash. En el siguiente turno, el modelo puede o no recordar que quería hacer el Write. Esto causa comportamiento errático donde el modelo "olvida" acciones.

#### Escenario C: Tool call con reasoning gigante

```
DeepSeek genera:
reasoning_content: "Let me think about this... [5000 tokens de chain-of-thought]"
content: "<tool_call name="Read"><input>{"file_pa...  [TRUNCADO]

finish_reason=length
```

- reasoning_buffer tiene 5K tokens de reasoning (buffereado, no emitido aún)
- El Read tool call está truncado en el buffer
- Recovery intenta completar → puede funcionar para Read
- Si recovery ÉXITO:
  - `has_xml_tool_calls = True`
  - reasoning_buffer SUPRIMIDO (correcto, evita crash de CC)
  - CC recibe solo el tool_use → ejecuta
- Si recovery FALLO:
  - `has_xml_tool_calls = False`
  - reasoning emitido como texto (ya que no hay tool calls)
  - CC recibe 5K tokens de reasoning + partial XML como texto
  - `stop_reason = "max_tokens"`

#### Escenario D: El modelo NO genera tool calls (solo texto + reasoning)

Sin problemas. reasoning emitido como texto, stop_reason=end_turn.

### El Bug Más Sutil: Tool Calls Perdidos Silenciosamente (Escenario B)

En [streaming.py:241-248](vendor/claude-code-proxy/llm/streaming.py#L241-L248):

```python
else:
    # Recovery failed — emit as text
    seg_text = segment["text"]
    if seg_text.strip():
        accumulated_text += seg_text
        if not text_block_closed:         # ← SIEMPRE False si hubo tool calls antes
            text_sent = True
            yield text_delta event         # ← NUNCA SE EJECUTA
```

Cuando hay tool calls exitosos antes del truncado:
- `text_block_closed = True` (se cerró al emitir el primer tool_use)
- El texto del tool call truncado se agrega a `accumulated_text` pero **nunca se envía a CC**
- CC no sabe que hubo un tool call más que se perdió
- El modelo en el siguiente turno puede no reintentarlo

### Recovery: Limitaciones Fundamentales

`recover_incomplete_tool_call()` en [tool_prompting.py:321-388](vendor/claude-code-proxy/llm/tool_prompting.py#L321-L388):

```python
response = await asyncio.wait_for(
    litellm.acompletion(
        model=model,              # "openai/deepseek-chat"
        messages=[{"role": "user", "content": prompt}],
        max_tokens=512,           # ← SOLO 512 TOKENS
        temperature=0,
    ),
    timeout=timeout_s,            # 3 segundos
)
```

**Limitaciones:**

| Tipo de tool call | Tamaño típico | ¿Recovery funciona? |
|---|---|---|
| Read (file_path) | 50-200 chars | **SÍ** (path corto, 512 tokens alcanza) |
| Bash (command) | 50-500 chars | **Probablemente SÍ** |
| Glob (pattern) | 30-100 chars | **SÍ** |
| Grep (pattern, path) | 50-200 chars | **SÍ** |
| Write (file_path, content) | 500-50000 chars | **NO** (contenido largo, 512 tokens NO alcanza) |
| Edit (file_path, old, new) | 200-10000 chars | **NO** (diffs largos) |

**Los tool calls que MÁS se truncan (Write, Edit) son los que MÁS difíciles de recuperar.**

---

## DIAGNÓSTICO FINAL: ¿Qué Está Pasando Ahora?

Con la config actual (todo deepseek-reasoner):

### Cadena de fallo más probable:

```
1. CC envía request con 17 tools, conversación mediana-larga
2. Proxy: model → openai/deepseek-reasoner (XML tools mode)
3. XML tool prompt (5K chars) inyectado en system message
4. Historial reescrito (tool_call→XML, tool_result→XML)
5. Token estimate: ~15K-30K (chars/4)
6. Threshold: 48K → COMPRESIÓN NO SE ACTIVA
7. Tokens REALES enviados a DeepSeek: ~25K-45K
8. DeepSeek genera reasoning (2K-8K tokens, incontrolable)
9. Content budget restante: posiblemente solo 5K-15K tokens
10. Si el modelo intenta un Write/Edit → se trunca
11. XmlToolBuffer.flush() detecta XML parcial
12. Recovery (512 tokens via deepseek-chat) → FALLA para Write/Edit
13. Si había tool calls previos exitosos:
    → tool call truncado se pierde SILENCIOSAMENTE
    → CC ejecuta solo los calls exitosos
    → Modelo "olvida" la acción perdida
14. Si NO había calls previos:
    → CC recibe stop_reason=max_tokens
    → CC puede reintentar (pero el mismo problema se repite)
```

---

## FIXES PROPUESTOS

### FIX 1 (CRÍTICO): Arreglar la subestimación del trigger de compresión

**Problema:** `chars / 4` subestima 30-40% para JSON/XML.

**Opciones:**

**Opción A: Cambiar heurística a `chars / 3` (simple)**
```python
# compressor.py
def _estimate_message_tokens(messages: list[dict]) -> int:
    total = 0
    for msg in messages:
        content = msg.get("content", "") or ""
        total += len(content) // 3  # JSON/XML-heavy content
    return max(1, total)
```

Resultado: estimate sube ~33%. Sesión de 120K chars: 40K estimate (vs 30K actual). Más cercano a realidad.

**Opción B: Usar factor dinámico basado en contenido**
```python
def _estimate_message_tokens(messages: list[dict]) -> int:
    total = 0
    for msg in messages:
        content = msg.get("content", "") or ""
        # JSON/XML/code tiene más tokens por char
        if any(marker in content for marker in ['<tool_call', '<tool_result', '{"', '\\n']):
            total += len(content) // 3
        else:
            total += len(content) // 4
    return max(1, total)
```

**Opción C: Bajar COMPRESSOR_TRIGGER_RATIO a 0.50**
```env
COMPRESSOR_TRIGGER_RATIO=0.50
```
Threshold baja de 48K a 32K. La compresión se activa mucho antes.
Desventaja: comprime innecesariamente en sesiones cortas.

**Recomendación: Opción A + Opción C (ambas)**
- Cambiar a chars/3 para mejor estimación
- Bajar ratio a 0.55-0.60 como safety margin adicional
- Threshold = 0.55 × 64000 = 35,200 con chars/3 → compresión se activa con ~105K chars de conversación

### FIX 2 (CRÍTICO): Capear max_tokens para deepseek-reasoner

**Problema:** El bloque de max_tokens cap tiene `not no_tools` → se SALTA para reasoner.
Pero deepseek-reasoner tiene output limit real de ~8K content tokens.
Si CC envía max_tokens=16384, el modelo puede generar output que excede su budget.

**Fix en converters.py:**
```python
# Después del bloque existente de cap para openai/ non-reasoning:
if no_tools and model_context_window > 0:
    # Reasoning models: cap output to avoid exceeding context window
    # reasoning_content uses output budget too — reserve space
    input_estimate = sum(len(str(m.get("content", ""))) for m in messages) // 3
    remaining_for_output = model_context_window - input_estimate
    # Reserve 50% for reasoning chain-of-thought (not controllable)
    content_budget = max(2048, int(remaining_for_output * 0.5))
    provider_max = int(os.environ.get("MAX_OUTPUT_TOKENS", "8192"))
    max_tokens = min(max_tokens, content_budget, provider_max)
    print(f"[no-tools] input~{input_estimate} remaining~{remaining_for_output} "
          f"content_budget={content_budget} max_tokens={max_tokens}")
```

Esto asegura que no pedimos más output del que el modelo puede dar.

### FIX 3 (ALTO): No perder tool calls truncados silenciosamente

**Problema:** Escenario B — tool call truncado se pierde cuando text_block está cerrado.

**Fix en streaming.py:** Cuando recovery falla y text_block está cerrado, abrir un nuevo text block para informar a CC que hubo un tool call perdido:

```python
else:
    # Recovery failed
    seg_text = segment["text"]
    if seg_text.strip():
        if text_block_closed:
            # NUEVO: Re-open text block to inform CC about the lost tool call
            last_tool_index += 1
            warning = f"[proxy-warning] A tool call was truncated and could not be recovered. The model may need to retry this action."
            yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': last_tool_index, 'content_block': {'type': 'text', 'text': ''}})}\n\n"
            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': last_tool_index, 'delta': {'type': 'text_delta', 'text': warning}})}\n\n"
            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': last_tool_index})}\n\n"
        else:
            text_sent = True
            yield text_delta...
```

### FIX 4 (MEDIO): Aumentar max_tokens del recovery

**Problema:** 512 tokens no alcanza para reconstruir Write/Edit.

**Fix:** Escalar max_tokens del recovery según el tipo de tool:

```python
async def recover_incomplete_tool_call(..., max_recovery_tokens: int = 2048):
    # ...
    response = await litellm.acompletion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_recovery_tokens,  # ← antes era 512
    )
```

Pero incluso con 2048, un Write de 500 líneas no se puede reconstruir.
**Mejor opción:** Para Write/Edit, NO intentar recovery y en su lugar emitir warning a CC.

### FIX 5 (MEDIO): Usar deepseek-chat como compresor alternativo

Si Z.AI tiene downtime, deepseek-chat puede servir como fallback para compresión:

```env
# En cloud.deepseek.env, añadir fallback:
COMPRESSOR_MODEL=openai/glm-4.7-flash
COMPRESSOR_FALLBACK_MODEL=openai/deepseek-chat
COMPRESSOR_FALLBACK_API_KEY=${OPENAI_API_KEY}
COMPRESSOR_FALLBACK_BASE_URL=https://api.deepseek.com/v1
```

**Pero GLM es superior como compresor:**
- 128K context vs 64K
- Gratis vs pagado
- No compite por rate limits con el modelo principal

**Recomendación:** Mantener GLM como compresor principal. Solo usar deepseek-chat si Z.AI está caído.

---

## PRIORIZACIÓN

| # | Fix | Impacto | Esfuerzo | Riesgo |
|---|-----|---------|----------|--------|
| 1 | Heurística chars/3 + trigger_ratio=0.55 | **ALTO** — la compresión se activa cuando debe | Bajo | Bajo |
| 2 | Cap max_tokens para reasoning models | **ALTO** — evita pedir output que el modelo no puede dar | Medio | Medio |
| 3 | No perder tool calls silenciosamente | **ALTO** — CC sabe que faltó algo | Bajo | Bajo |
| 4 | Recovery tokens más grandes | **MEDIO** — ayuda para tool calls medianos | Bajo | Bajo |
| 5 | Compressor fallback | **BAJO** — solo para downtime de Z.AI | Medio | Bajo |

**Fix 1 + Fix 3 son los de mayor impacto con menor esfuerzo y riesgo.**
