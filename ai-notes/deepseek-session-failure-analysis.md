# Análisis Exhaustivo: Fallo de Sesión DeepSeek-Reasoner

**Fecha:** 2026-02-17
**Síntoma:** DeepSeek mandó dos XML como plain text → CC paró de ejecutar
**Modelo:** openai/deepseek-reasoner (no-tools mode, XML simulation)

---

## 1. CADENA DE FALLO (Root Cause)

### Causa raíz: Z.AI (compressor endpoint) con caída intermitente

```
57 errores consecutivos de compressor:
  "Compressor LLM failed: InternalServerError: OpenAIException - Connection error."
```

El compressor usa **Z.AI glm-4.7-flash** (`COMPRESSOR_MODEL`) para resumir mensajes viejos.
**NO es DeepSeek Chat** — el classifier usa DeepSeek, pero el compressor va a `api.z.ai/api/paas/v4`.

```bash
# Configuración real en cloud.deepseek.env:
COMPRESSOR_MODEL=openai/glm-4.7-flash
COMPRESSOR_BASE_URL=https://api.z.ai/api/paas/v4
```

Z.AI tuvo una **caída intermitente** de ~1 hora (19:02 - 20:09 UTC, Feb 17).
Validación posterior confirmó que el endpoint y modelo funcionan correctamente fuera de esos periodos.

### Efecto dominó

```
1. Compressor falla → fallback a "trimming" (corte bruto de mensajes)
2. Trimming corta agresivamente: 113K → 25K tokens (pierde ~78% del contexto)
3. DeepSeek-reasoner pierde el XML tool prompt inyectado al inicio
4. Sin contexto de cómo usar tools → genera TEXTO DESCRIPTIVO en vez de XML válido
5. Proxy detecta <tool_call> en texto pero NO matchea ningún regex
6. stop_reason=end_turn (texto plano) → CC recibe texto, no tool_use → para de ejecutar
```

---

## 2. LOS 8 FALLOS DE REGEX (cronológico)

### Fallo #1: Resumen de conversación como texto
```
"We are summarizing a conversation about debugging and fixing a Claude Code Proxy issue..."
```
**Causa:** Tras trimming, el modelo recibió un resumen previo y lo repitió como texto.

### Fallo #2: Modelo describe qué herramientas usaría
```
"Voy a analizar exhaustivamente el código del proxy... Primero, necesito explorar..."
```
**Causa:** Sin el XML prompt template, el modelo describe acciones en vez de ejecutarlas.

### Fallo #3: XML malformado sin name attribute
```xml
<tool_call>
<arguments>
{"text": "Complete the XML tool call extraction function."}
```
**Causa:** El modelo intentó llamar una tool pero el formato es incorrecto — falta `name=""`.

### Fallo #4: Modelo admite que no puede usar tools
```
"¡Vaya! Parece que estoy teniendo problemas con las herramientas.
Déjame cambiar mi enfoque y continuar con el análisis directamente en texto..."
```
**Causa:** Tras fallos repetidos, el modelo "se da por vencido" y cambia a modo texto.

### Fallo #5: Modelo reflexiona sobre su propio error
```
"El error anterior parece ser un problema con una herramienta que intenté usar incorrectamente
(probablemente por completar el XML que estaba describiendo)."
```
**Causa:** El modelo está consciente de que falló pero no sabe cómo arreglarlo.

### Fallo #6: XML vacío con formato inventado
```xml
<tool_call>
<tool_name>complete_xml</tool_name>
<args>
```
**Causa:** El modelo inventa tags (`<tool_name>`, `<args>`) que no existen en el prompt.

### Fallo #7: Texto narrativo con XML embebido (EL CRASH FINAL)
```
"Ahora necesito crear la guía de troubleshooting completa..."

<tool_call name="Read">
<tool_result>
<tool_use_id tool_name="Read">
<input>
{"file_path": "...", "offs
```
**Causa:** El modelo mezcla narrativa con XML, pero además inyecta `<tool_result>` DENTRO de `<tool_call>` — formato imposible. Ningún regex lo matchea.

### Fallo #8: CDATA wrap (ya corregido con nuestro fix)
```
Line 468-474:
<tool_call name="Read">
<![CDATA[{"file_path": "...", "limit": 50}]]>
</tool_call>
```
**Nota:** Este SÍ fue parseado por el BARE regex, pero producía el bug de `file_path?.split` que ya corregimos con el CDATA fix + type gate.

---

## 3. MÉTRICAS DE LA SESIÓN

| Métrica | Valor | Impacto |
|---------|-------|---------|
| **Compressor failures** | 57 | 100% de intentos fallaron |
| **Trim fallbacks** | 58 | Cortes brutos, pérdida masiva de contexto |
| **Regex failures** | 8 | 8 requests donde model no pudo usar tools |
| **Reasoning content tools** | 447 | Tools extraídos del campo reasoning (workaround) |
| **Quality warnings** | 6 | TodoWrite con campos vacíos (activeForm) |
| **Tokens antes de trim** | 69K → 113K | Crecimiento continuo por acumulación |
| **Tokens después de trim** | 18K → 32K | Contexto reducido al ~25% |

---

## 4. ANÁLISIS DE DEGRADACIÓN PROGRESIVA

### Fase 1: Funcionamiento normal (requests 1-3)
```
[compress] Success: 69979 → 18695 tokens (saved 51284)
[xml-buffer] PRIMARY match: name=Read keys=['file_path']
```
El compressor (Z.AI glm-4.7-flash) **funcionaba** al inicio. Z.AI se cayó a las 19:02 UTC.

### Fase 2: Compressor falla, trimming funciona (requests 4-25)
```
[compress] Compressor LLM failed: InternalServerError: OpenAIException - Connection error.
[compress] LLM compression failed, falling back to trimming
[compress] Trimmed: 80419 → 24587 tokens
```
Tools siguen funcionando porque el XML prompt aún está en los mensajes recientes.

### Fase 3: Tool calls migran a reasoning_content (requests 15-35)
```
[streaming] Found 1 tool call(s) in reasoning_content!
[streaming] XML tool_use from reasoning: name=Read index=1
```
DeepSeek-reasoner empieza a poner tool calls en `reasoning_content` en vez de `content`. El proxy los extrae correctamente (workaround implementado).

### Fase 4: Formatos degradados (requests 30+)
```
[no-tools] WARNING: Model used <filename> instead of <input> for tool 'Read'
[no-tools] BARE regex match for tool 'Read' (no inner tags, content=228 chars)
```
El modelo empieza a usar tags incorrectos. Los regexes fallback los capturan.

### Fase 5: Colapso total (requests finales)
```
[no-tools] WARNING: Found <tool_call> in text but ALL regexes failed.
```
El modelo pierde completamente la capacidad de generar XML válido.

---

## 5. ¿POR QUÉ EL TRIMMING PIERDE EL XML PROMPT?

El XML tool prompt se inyecta como **primer mensaje del sistema**:
```python
# converters.py:497-500
if messages and messages[0]["role"] == "system":
    messages[0]["content"] = tool_prompt + "\n\n" + messages[0]["content"]
```

Pero el trimming en el compressor **conserva los últimos N mensajes** y recorta los más viejos. El system message con el tool prompt está al INICIO, así que **debería conservarse**.

**Sin embargo**, tras ~20 trims consecutivos, el contexto acumulado es tan denso que:
1. El modelo recibe 25K tokens de tool results + conversation
2. El XML prompt (~4K tokens para 31 tools) se diluye en el ruido
3. DeepSeek-reasoner "olvida" el formato correcto

---

## 6. FIXES NECESARIOS

### Fix A: Compressor resilience — retry + circuit breaker + fallback (CRÍTICO)
**Problema:** Z.AI tiene caídas intermitentes (~1h observada el Feb 17). Sin compressor, el trimming bruto destruye el contexto.
**Fix:** Triple capa de resilencia en `compressor.py`:

```python
# 1. Retry con backoff exponencial (3 intentos, 1s/2s/4s)
# 2. Circuit breaker: tras 5 fallos consecutivos, skip por 60s
# 3. Fallback compressor: si el primario falla, usar DeepSeek Chat
```

```bash
# Configurar fallback compressor en profile-envs/cloud.deepseek.env:
COMPRESSOR_FALLBACK_MODEL=openai/deepseek-chat
COMPRESSOR_FALLBACK_API_KEY=sk-0c7292c4b19f463d8bb50e8b7e6a1c60
COMPRESSOR_FALLBACK_BASE_URL=https://api.deepseek.com/v1
```

### Fix B: Trimming que preserva el XML prompt (IMPORTANTE)
**Problema:** El trimming bruto puede eliminar contexto crítico del tool prompt.
**Fix:** Forzar que el system message con el tool prompt NUNCA se trimee.

```python
# En compressor.py, al hacer trim:
# SIEMPRE preservar el primer mensaje si es system (contiene tool prompt)
if messages and messages[0]["role"] == "system":
    system_msg = messages[0]
    messages_to_trim = messages[1:]
    # Trim solo messages_to_trim, luego reinsertar system_msg
```

### Fix C: Detectar degradación y parar (NICE-TO-HAVE)
**Problema:** El proxy sigue mandando requests incluso cuando el modelo ya no puede generar XML válido.
**Fix:** Si hay N regex failures consecutivos, devolver un error explícito en vez de texto plano.

```python
# Contador de fallos consecutivos por sesión
if consecutive_regex_failures >= 3:
    return error_response(
        "Model has lost XML tool-calling capability. "
        "Context may be too degraded. Consider restarting the session."
    )
```

---

## 7. RESUMEN EJECUTIVO

| Factor | Estado | Severidad |
|--------|--------|-----------|
| **Compressor LLM caído** | Z.AI api.z.ai unreachable (57 fallos, caída intermitente ~1h) | CRÍTICO |
| **Trimming agresivo** | 113K → 25K tokens (78% pérdida) | ALTO |
| **Pérdida de XML prompt** | Modelo "olvida" formato de tools | ALTO |
| **Tool calls en reasoning** | Workaround funciona pero es frágil | MEDIO |
| **CDATA en tool calls** | YA CORREGIDO (Fix #5 + #6 de esta sesión) | RESUELTO |
| **TodoWrite con campos vacíos** | DeepSeek no genera activeForm | BAJO |

**La causa raíz es la caída intermitente de Z.AI** (`api.z.ai/api/paas/v4`), que es el endpoint del compressor. Todo lo demás es consecuencia de la pérdida progresiva de contexto por trimming bruto.

---

## 8. LÍNEA TEMPORAL COMPLETA DEL COMPRESSOR (validada con timestamps)

```
Feb 16 04:21 - Feb 17 08:21  ✓ 56 éxitos   Z.AI estable
Feb 17 18:58 - 19:02:32      ✓  2 éxitos   Último éxito antes de caída
Feb 17 19:02:41 - 19:17:51   ✗ 34 fallos   Z.AI CAÍDO → sesión DeepSeek crashea
Feb 17 20:09:15               ✓  1 éxito    Z.AI vuelve brevemente
Feb 17 20:09:32               ✗  1 fallo    Vuelve a caer
Feb 17 21:51 - 22:02          ✓ 14 éxitos   Z.AI estable de nuevo
Feb 17 22:11 - 22:15          ✗ 21 fallos   Z.AI se cae otra vez
```

**Patrón:** Z.AI tiene caídas intermitentes de 1-2 horas. El compressor no tiene retry ni fallback, así que cada caída se propaga como pérdida total de contexto.

---

*Análisis generado por Claude Opus 4.6. Corregido: atribución original incorrecta (decía "DeepSeek Chat" pero era "Z.AI").*
