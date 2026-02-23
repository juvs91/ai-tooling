# AI-Tooling Proxy - Exhaustive Analysis

## Resumen del Sistema

El **Claude Code Proxy** es un proxy HTTP que intercepta solicitudes en formato Anthropic (Claude Code) y las convierte a formato OpenAI/LiteLLM para enrutarlas a múltiples proveedores de modelos (Z.AI, Groq, Gemini, Ollama, etc.). Proporciona enrutamiento inteligente, políticas de seguridad, manejo de fallos, streaming robusto y métricas detalladas.

**Arquitectura clave**:
- Puerto: 8083
- Contenedor: `ai-tooling-proxy_cloud-1`
- Directorio del código: `/Users/jeguzman/ai-tooling/vendor/claude-code-proxy/`
- Framework: FastAPI + LiteLLM

## 1. Conversión de Formatos (llm/converters.py)

### Funcionalidades principales:
- **Conversión bidireccional** Anthropic ↔ OpenAI/LiteLLM
- **Manejo de bloques complejos**:
  - `ContentBlockToolUse` → `tool_calls` con `function`
  - `ContentBlockToolResult` → mensajes con `role: "tool"`
  - `ContentBlockThinking` y `ContentBlockRedactedThinking` → eliminados (stripped)
  - `ContentBlockServerToolUse` y `ContentBlockServerToolResult` → aplanados a texto
- **Conversión de `tool_choice`**:
  - `{"type": "any"}` → `"required"`
  - `{"type": "auto"}` → `"auto"`
  - `{"type": "tool", "name": "X"}` → `{"type": "function", "function": {"name": "X"}}`
- **Caché de conversión de herramientas**: Memoización para evitar reprocesamiento
- **Reparación de JSON en streaming**: Detecta JSON truncado y añade sufijos de cierre

**Archivos clave**: `llm/converters.py`, `llm/streaming.py`

## 2. Enrutamiento Inteligente (router/)

### Clasificación de intención:
- **Modos**: `CHAT`, `PLANNING`, `BUILDING`
- **Implementación dual**:
  - **Clasificador LLM**: Usa modelo barato (DeepSeek) cuando `CLASSIFIER_MODEL` está configurado
  - **Fallback regex**: Patrones simples cuando no hay LLM disponible
- **Umbral de herramientas**: Si hay ≥5 herramientas, `CHAT`/`PLANNING` se actualizan a `BUILDING`
- **Detección de análisis**: Regex para identificar solicitudes de análisis (`/analyze`, `explain this`, etc.)

### Mapeo de modelos:
- `map_claude_alias_to_target()`: Traduce alias Claude (`claude-3-5-sonnet`) a modelos reales del proveedor
- Configuración por intención:
  - `SMALL_MODEL`: Para `CHAT`
  - `BIG_MODEL`: Para `PLANNING`
  - `BUILDING_MODEL`: Para `BUILDING`

**Archivos clave**: `router/llm_router.py`, `router/model_mapper.py`

## 3. Políticas de Seguridad (proxy/proxy.py)

### Allowlist de herramientas:
- **Formato**: `TOOL_ALLOWLIST="Read,Write,Bash"` o `"*"` para todas
- **Filtrado**: `filter_tools_allowlist()` elimina herramientas no permitidas
- **Normalización**: `normalize_tool_choice()` ajusta `tool_choice` después del filtrado

### Límites de tokens:
- `MAX_INPUT_TOKENS`: Rechaza solicitudes que exceden el límite
- `HARD_BLOCK_OVERSIZE`: Bloqueo estricto vs. solo advertencia
- **Escalado**: `scale_tokens()` ajusta conteos para ventanas de contexto más pequeñas

### Notas del sistema:
- `POLICY_NOTE_IN_SYSTEM`: Inserta advertencias en el system prompt
- **Deduplicación**: `ensure_system_note()` evita duplicados

**Archivos clave**: `proxy/proxy.py`, `utils/utils.py`

## 4. Manejo de Fallos (proxy/fallback.py)

### Cadena de fallback:
- Configuración dinámica: `FALLBACK_1_PROVIDER`, `FALLBACK_1_API_KEY`, etc.
- Hasta 9 niveles de fallback
- **Propagación de errores**: Intenta proveedor principal, luego cadena secuencial

### Clasificación de errores:
- `_classify_llm_error()`: Mapea excepciones LiteLLM a códigos HTTP
- **Errores manejados**:
  - 400: Context window exceeded, bad request
  - 401: Authentication
  - 429: Rate limit
  - 502/503/504: Connection/timeout issues

**Archivos clave**: `proxy/fallback.py`, `server.py` (función `_classify_llm_error`)

## 5. Streaming Robusto (llm/streaming.py)

### Manejo de chunks:
- **Parseo SSE**: Extrae chunks de datos del stream LiteLLM
- **Reparación JSON**: `_compute_repair_suffix()` para JSON truncado en tool_calls
- **Transformación a Anthropic**: Convierte chunks OpenAI a formato Anthropic SSE

### Características especiales:
- **Thinking blocks**: Detecta y elimina bloques de pensamiento
- **Stop reasons**: Traduce `finish_reason` de OpenAI a `stop_reason` de Anthropic

**Archivos clave**: `llm/streaming.py`, `llm/sse.py`

## 6. Métricas y Monitoreo (utils/metrics.py)

### Métricas en tiempo real:
- **Contadores**: Requests, tokens, fallbacks, cache hits/misses
- **Latencia**: Promedio, percentiles (p50, p90, p99)
- **Distribución de intenciones**: CHAT, PLANNING, BUILDING

### Logs detallados:
- **RequestLog**: Timestamp, intent, modelos, proveedor, tokens, latency
- **API endpoints**: `/api/stats`, `/api/logs`
- **Persistencia en memoria**: Mantiene últimas 200 solicitudes

**Archivos clave**: `utils/metrics.py`

## 7. Caché (múltiples niveles)

### Caché de conteo de tokens:
- **Clave hash**: Contenido de mensajes + modelo + system
- **LRU simple**: 256 entradas máximo, evicción del más antiguo
- **Fallback heurístico**: chars/4 cuando token_counter falla

### Caché de respuestas LiteLLM:
- **Configurable**: `CACHE_ENABLED=1`, `CACHE_TTL=60`
- **In-memory**: Usa caché local de LiteLLM
- **Reduce duplicados**: Para retries y bursts

**Archivos clave**: `utils/utils.py` (funciones de caché), `server.py` (config LiteLLM cache)

## 8. Bloques Especiales y Edge Cases

### Thinking blocks:
- **`ContentBlockThinking`**: Eliminado en conversión
- **`ContentBlockRedactedThinking`**: Eliminado en conversión
- **Propósito**: Claude genera pensamientos internos que no deben enviarse al proveedor

### Server tools:
- **`ContentBlockServerToolUse`**: Aplanado a texto descriptivo
- **`ContentBlockServerToolResult`**: Aplanado a texto descriptivo
- **Propósito**: Herramientas del servidor Claude que no existen en proveedores externos

### Tool results complejos:
- **Contenido anidado**: Listas de bloques, imágenes, etc.
- **Error handling**: `is_error=True` → prefijo `[ERROR]`
- **Extracción de texto**: `_tool_result_content_to_str()`

**Archivos clave**: `llm/converters.py` (funciones de extracción)

## 9. Escalado de Tokens para Context Windows Pequeñas

### Problema:
- Claude Code asume ventana de 200K tokens
- Modelos pequeños (32K, 128K) activarían compresión muy temprano

### Solución:
- `MODEL_CONTEXT_WINDOW`: Configurar tamaño real del modelo
- `scale_tokens(raw_count, window)`: Escala proporcionalmente
- **Fórmula**: `scaled = raw * (200000 / window)` si `0 < window < 200000`

**Archivos clave**: `utils/utils.py` (función `scale_tokens`)

## 10. Detección de Análisis y Enforcement

### Detección:
- `is_analysis_request()`: Regex para `/analyze`, `explain`, `review`, etc.
- **Configurable**: `ANALYSIS_ENFORCEMENT=1` para activar

### Enforcement:
- **Inyección de prompt**: Añade instrucción de uso de herramientas al system
- **Propósito**: Forzar a Claude Code a usar herramientas para análisis de código

**Archivos clave**: `router/llm_router.py` (función `is_analysis_request`)

## 11. Configuración por Perfiles (profile-envs/)

### Diseño modular:
- Archivos `.env` por proveedor: `cloud.zai.env`, `cloud.groq.env`, etc.
- **Variables clave**:
  ```bash
  OPENAI_API_KEY=...
  OPENAI_BASE_URL=...  # Para Z.AI: https://api.z.ai/api/paas/v4
  SMALL_MODEL=glm-4.7-flash
  BIG_MODEL=glm-4.7
  BUILDING_MODEL=glm-4.7
  ```

### Proveedores soportados:
- **Z.AI**: GLM-4.7, GLM-4.7-flash
- **Groq**: Llama, Mixtral, etc.
- **Gemini**: Gemini Pro, Flash
- **Ollama**: Modelos locales
- **OpenAI**: GPT-4, GPT-3.5

## 12. Lecciones Aprendidas y Soluciones Críticas

### 1. Thinking blocks (422 error)
**Problema**: Los bloques `ContentBlockThinking` causaban error 422 en conversión.
**Solución**: Añadidos a `schemas.py` y eliminados en `converters.py`.

### 2. Comillas simples en tool_call XML
**Problema**: DeepSeek-reasoner usa `<tool_call name='X'>` (comillas simples) y dicts Python `{'key': 'val'}`.
**Solución**: Regex actualizados para aceptar ambos estilos + `json_repair` para conversión.

### 3. Token counting heurístico
**Problema**: `chars/4` era impreciso para algunos modelos.
**Solución**: Usar `litellm.token_counter()` + fallback a chars/3.

### 4. Streaming JSON truncado
**Problema**: Tool calls en streaming podían terminar con JSON incompleto.
**Solución**: `_compute_repair_suffix()` detecta y repara JSON truncado.

### 5. Server tools desconocidos
**Problema**: `ContentBlockServerToolUse` no existe en proveedores externos.
**Solución**: Aplanar a texto descriptivo.

## 13. Endpoints Principales

### `POST /v1/messages`
- **Entrada**: Formato Anthropic Messages API
- **Proceso**: Clasificación → Políticas → Enrutamiento → Conversión
- **Salida**: Formato Anthropic (streaming o no)

### `POST /v1/messages/count_tokens`
- **Entrada**: `TokenCountRequest` (Anthropic)
- **Proceso**: Conversión → Cache → LiteLLM token_counter
- **Salida**: `TokenCountResponse` con tokens escalados

### `GET /health`
- **Estado**: Configuración actual, modelos, clasificador, fallbacks

### `GET /api/stats`
- **Métricas agregadas**: Counts, latency, fallback rate, cache hits

### `GET /api/logs`
- **Logs recientes**: Hasta 200 solicitudes detalladas

## 14. Estructura de Directorios

```
claude-code-proxy/
├── llm/                    # Conversión y streaming
│   ├── converters.py      # Conversión de formatos
│   ├── streaming.py       # Manejo de streaming SSE
│   ├── schemas.py         # Modelos Pydantic
│   ├── tool_prompting.py  # Inyección de prompts
│   ├── compressor.py      # Compresión de contexto
│   └── sse.py            # Utilidades SSE
├── proxy/                 # Lógica de proxy
│   ├── proxy.py          # Políticas y enrutamiento
│   └── fallback.py       # Cadena de fallback
├── router/               # Enrutamiento inteligente
│   ├── llm_router.py     # Clasificador de intención
│   └── model_mapper.py   # Mapeo de alias de modelos
├── utils/                # Utilidades
│   ├── utils.py          # Helpers y caché de tokens
│   └── metrics.py        # Métricas y logs
├── tests/                # Tests unitarios
│   ├── test_converters.py
│   ├── test_utils.py
│   ├── test_router.py
│   ├── test_fallback.py
│   ├── test_metrics.py
│   └── test_server.py
├── server.py             # FastAPI server principal
├── pyproject.toml        # Dependencias
├── .env.example          # Variables de entorno ejemplo
└── profile-envs/         # Configuraciones por proveedor
```

## 15. Configuración de Ejemplo

```bash
# Proveedor Z.AI
OPENAI_API_KEY=zai_...
OPENAI_BASE_URL=https://api.z.ai/api/paas/v4
SMALL_MODEL=glm-4.7-flash
BIG_MODEL=glm-4.7
BUILDING_MODEL=glm-4.7
PREFERRED_PROVIDER=openai

# Clasificador LLM
CLASSIFIER_MODEL=openai/deepseek-chat
CLASSIFIER_API_KEY=sk-...
CLASSIFIER_BASE_URL=https://api.z.ai/api/paas/v4

# Seguridad
TOOL_ALLOWLIST=Read,Write,Bash,Glob,Grep
MAX_INPUT_TOKENS=100000
HARD_BLOCK_OVERSIZE=0
POLICY_NOTE_IN_SYSTEM=1

# Fallback
FALLBACK_1_PROVIDER=groq
FALLBACK_1_API_KEY=gsk_...
FALLBACK_1_BIG_MODEL=llama3-70b-8192
FALLBACK_1_SMALL_MODEL=llama3-8b-8192

# Caché
CACHE_ENABLED=1
CACHE_TTL=60

# Ventana de contexto
MODEL_CONTEXT_WINDOW=131072  # 128K
```

## 16. Flujo de una Solicitud

1. **Cliente** → `POST /v1/messages` (formato Anthropic)
2. **Clasificación**: LLM o regex determina intención (CHAT/PLANNING/BUILDING)
3. **Políticas**: Allowlist, límite de tokens, notas del sistema
4. **Enrutamiento**: Selección de modelo basada en intención
5. **Conversión**: Anthropic → LiteLLM format
6. **Ejecución**: LiteLLM llama al proveedor (con fallback chain si falla)
7. **Conversión inversa**: LiteLLM → Anthropic format
8. **Métricas**: Registro de tokens, latencia, proveedor usado
9. **Respuesta**: Formato Anthropic (streaming o no)

---

*Última actualización: Análisis exhaustivo basado en revisión de código completo*
