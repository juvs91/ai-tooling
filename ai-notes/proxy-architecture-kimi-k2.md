# Claude Code Proxy — Exhaustive Architecture & Pipeline Diagram

> Scope: `vendor/claude-code-proxy/`
> Focus: Kimi K2 integration path and the components it touches.

---

## 1. High-Level Architecture

```text
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│  CLIENT (Claude Code / VSCode Extension / CLI)                                           │
│  Speaks Anthropic /v1/messages API                                                       │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                                           │
                                           ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│  FASTAPI LAYER                                                                           │
│  server.py                                                                               │
│  • POST /v1/messages          → create_message()                                         │
│  • POST /v1/messages/count_tokens  → count_tokens_endpoint()                             │
│  • GET  /health               → health_check()                                           │
│  • GET  /api/stats            → get_stats()                                              │
│  • GET  /api/logs             → get_logs()                                               │
│  Loads ProxyConfig once at startup via config.load_config()                              │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                                           │
                                           ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│  PHASE 1 — REQUEST PIPELINE (Anthropic-format request)                                   │
│  proxy/proxy.py::build_request_pipeline()                                                │
│  Runs on the incoming Anthropic MessagesRequest                                          │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                                           │
                                           ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│  PHASE 2 — LITELLM / PASSTHROUGH PIPELINE (provider-format request)                      │
│  proxy/proxy.py::build_litellm_pipeline() / build_passthrough_pipeline()                 │
│  Converts/transforms request into the format the upstream provider expects               │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                                           │
                                           ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│  EXECUTION LAYER                                                                         │
│  proxy/proxy.py                                                                          │
│  • litellm.acompletion() via _call_provider_with_retry()                                 │
│  • OR httpx passthrough via llm/passthrough.py (Anthropic-compatible endpoints)            │
│  • Fallback chain if FALLBACK_* env vars configured                                      │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                                           │
                                           ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│  PHASE 3 — RESPONSE PIPELINE (agnostic response transformers)                            │
│  proxy/proxy.py::build_response_pipeline()                                               │
│  Runs on the model's response before returning to Claude Code                            │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                                           │
                                           ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│  CONVERSION BACK TO ANTHROPIC FORMAT                                                     │
│  llm/converters.py::convert_litellm_to_anthropic()                                       │
│  Anthropic MessagesResponse returned to client                                           │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. File-to-Component Mapping

| Component | File(s) | Responsibility |
|-----------|---------|----------------|
| Entry Point / HTTP API | `server.py` | FastAPI app, endpoints, orchestrates Phase 1 → run_messages → response handling |
| Pipeline Orchestration | `proxy/proxy.py` | Builds all pipelines, executes LiteLLM/passthrough calls, retry/fallback logic |
| Shared Pipeline State | `llm/pipeline.py` | `TransformContext` dataclass + `Pipeline` runner + `Transformer` ABC |
| Format Conversion | `llm/converters.py` | Anthropic ↔ OpenAI/LiteLLM; tool XML extraction for no-tools models |
| Model Name Mapping | `router/model_mapper.py` | Claude aliases (`claude-sonnet-*`, etc.) → `provider/model` |
| Configuration | `config.py` | All env vars → `ProxyConfig` dataclasses |
| Request Transformers | `llm/transformers/*.py` | Individual transformers (intent, guardrails, routing, credentials, quirks, etc.) |
| Streaming | `llm/streaming.py`, `llm/transformers/stream_event.py` | SSE generation, passthrough XML extraction, tracked streams |
| Quality Refinement | `llm/transformers/quality_refinement.py` | Re-send loop for analysis responses |
| Session / Compression Cache | `llm/compressor.py` | Context compression + session cache (deferred tools reuse it) |
| Metrics | `utils/metrics.py` | Request logs, cost, cache, quality events |
| Schemas | `llm/schemas.py` | Pydantic request/response models |
| Tool Utilities | `utils/tool_utils.py` | Deferred tool name extraction, no-tools model detection |

---

## 3. End-to-End Request Flow

```text
Claude Code POST /v1/messages
        │
        ▼
┌──────────────────────────────────────────────────────────────┐
│ server.py::create_message()                                    │
│ 1. Safety: max_turns, cost budget                              │
│ 2. Build TransformContext(raw_body, session_id)                │
│ 3. await _request_pipeline.process(request, ctx)               │
└──────────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────┐
│ PHASE 1: REQUEST PIPELINE                                      │
│ Order defined in proxy/proxy.py:59-73                          │
│                                                                │
│  1. IntentClassifierTransformer                                │
│     • Sets ctx.intent (CHAT/READ/BUILD/VERIFY/PLAN)            │
│     • Sets ctx.phase (EXPLORE/PLAN/EXECUTE)                    │
│     • Sets ctx.is_analysis, ctx.analysis_phase                 │
│     • Sets ctx.plan_mode_active, ctx.ralph_mode                │
│     File: llm/transformers/intent_classifier.py                │
│                                                                │
│  2. IntentEnforcementTransformer                               │
│     • Validates intent compliance                              │
│     File: llm/transformers/intent_enforcement.py               │
│                                                                │
│  3. GuardrailTransformer                                       │
│     • Injects guard system prompt from GUARDRAILS_FILE         │
│     File: llm/transformers/guardrail.py                        │
│                                                                │
│  4. DeferredToolsTransformer                                   │
│     • Parses <available-deferred-tools> from system prompt     │
│     • Injects EnterPlanMode/ExitPlanMode/TodoWrite/etc.        │
│       into request.tools so non-Claude models can call them    │
│     • Caches list per session in llm/compressor.py             │
│     File: llm/transformers/deferred_tools.py                   │
│                                                                │
│  5. TokenCapTransformer                                        │
│     • Estimates tokens, optionally blocks oversized input      │
│     File: llm/transformers/token_cap.py                        │
│                                                                │
│  6. ToolAllowlistTransformer                                   │
│     • Drops tools not in TOOL_ALLOWLIST                        │
│     File: llm/transformers/tool_allowlist.py                   │
│                                                                │
│  7. AdaptiveContextTransformer                                 │
│     • Compresses/summarizes long context if needed             │
│     File: llm/transformers/adaptive_context.py                 │
│                                                                │
│  8. ModelRouterTransformer                                     │
│     • Maps claude-* aliases → provider/model                   │
│     • Routes by phase/intent (small/big/building/analysis)     │
│     • Applies cross-provider RouteOverride                     │
│     File: llm/transformers/model_router.py                     │
│     Helpers: router/model_mapper.py, router/llm_router.py      │
└──────────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────┐
│ server.py calls proxy/proxy.py::run_messages()                 │
│                                                                │
│ DECISION: passthrough or LiteLLM?                              │
│ _is_passthrough_compatible(model, cfg)                         │
│   • PASSTHROUGH_DISABLED=0                                     │
│   • ANTHROPIC_BASE_URL is set                                  │
│   • ANTHROPIC_API_KEY is set                                   │
│   • model has anthropic/ prefix (or bare if legacy)            │
└──────────────────────────────────────────────────────────────┘
        │
        ├──────────────────────┬──────────────────────┐
        ▼                      ▼                      ▼
┌──────────────┐      ┌─────────────────┐    ┌──────────────────┐
│ PASSTHROUGH  │      │ LITELLM         │    │ LITELLM          │
│ PATH         │      │ NON-STREAMING   │    │ STREAMING        │
│ (Anthropic-  │      │                 │    │                  │
│  compatible  │      │                 │    │                  │
│  endpoint)   │      │                 │    │                  │
└──────────────┘      └─────────────────┘    └──────────────────┘
```

---

## 4. Passthrough Path (Kimi K2 / Z.AI / GLM-4.7 via Anthropic endpoint)

```text
run_messages()
   │
   ▼
_is_passthrough_compatible(model, cfg) == True
   │
   ▼
proxy/proxy.py:387
convert_anthropic_to_litellm()   ← used only for CompressionTransformer
   │
   ▼
_get_passthrough_pipeline(cfg).process(request, ctx)
   │
   ├─ CompressionTransformer (context compression)
   └─ ProviderQuirksTransformer (temperature clamp, thinking params)
   │
   ▼
PassthroughClient initialized with:
   • cfg.credentials.anthropic_base_url
   • cfg.credentials.anthropic_api_key
   • endpoint_path=cfg.credentials.anthropic_endpoint_path   ← Kimi /coding/v1/messages
   File: llm/passthrough.py
   │
   ▼
_build_passthrough_body(request, model, ctx, analysis_thinking)
   • Anthropic-format body dict
   • Injects ANALYSIS_THINKING_PARAMS during ANALYZING/READ/SYNTHESIZING
   │
   ├─ Stream  → pt.stream_message(body, strip_reasoning=..., response_model=original_model)
   │            → returns SSE generator
   │            → server.py wraps with passthrough_xml_tool_extraction()
   │            → analysis_quality_stream() (if analysis + stream_buffer_quality)
   │            → tracked_stream() for metrics + async grounding
   │
   └─ Non-stream → pt.create_message(body, response_model=original_model)
                 → Anthropic dict response
                 → _run_response_pipeline() inside proxy.py
                 → server.py returns directly
```

**Kimi K2 specific wiring in passthrough:**

- `config.py:48` → `ProviderCredentials.anthropic_endpoint_path` defaults to `/v1/messages`
- `config.py:54-65` → `anthropic_litellm_api_base` concatenates `ANTHROPIC_BASE_URL + ANTHROPIC_ENDPOINT_PATH`
- For Kimi: set `ANTHROPIC_BASE_URL=https://kimi-api.example.com` and `ANTHROPIC_ENDPOINT_PATH=/coding/v1/messages`
- `llm/passthrough.py` uses `endpoint_path` to hit the correct URL.

---

## 5. LiteLLM Path

```text
run_messages()
   │
   ▼
convert_anthropic_to_litellm(request_obj, model_context_window, max_output_tokens, reasoning_max_tokens)
   File: llm/converters.py:307
   │
   ├─ system → system message
   ├─ messages → OpenAI-format messages (tool_use/tool_result converted)
   ├─ max_completion_tokens calculated
   ├─ tools → OpenAI function tools (deduped by name)
   └─ no-tools models get XML tool prompt injection
   │
   ▼
_get_litellm_pipeline(cfg).process(request, ctx)
   File: proxy/proxy.py:76-87
   │
   ├─ CompressionTransformer
   │     File: llm/transformers/compression.py
   │
   ├─ ProviderQuirksTransformer
   │     File: llm/transformers/provider_quirks.py
   │     • STREAM_EXTRA_BODY for streaming + tools
   │     • reasoning_content injection for "reasoner" models
   │     • DeepSeek R1 temperature floor (0.6)
   │     • Kimi K2 temperature clamp (max 0.8 → clamp 0.6)
   │     • LiteLLM thinking params for deepseek/minimax/kimi
   │     • Generic ANALYSIS_THINKING_PARAMS fallback
   │
   └─ CredentialTransformer
         File: llm/transformers/credential.py
         • Analysis override (SYNTHESIZING phase)
         • Route override (cross-provider small/building routes)
         • Provider-prefix-based injection:
           - openai/ → OPENAI_API_KEY + OPENAI_BASE_URL
           - gemini/ → GEMINI_API_KEY or Vertex auth
           - anthropic/ or bare → ANTHROPIC_API_KEY + anthropic_litellm_api_base
   │
   ▼
_call_provider_with_retry()  → litellm.acompletion(**ctx.litellm_request)
   File: proxy/proxy.py:222-257
   │
   ▼
server.py handles response:
   Non-stream → convert_litellm_to_anthropic() → _run_response_pipeline() → analysis_quality_nonstream()
   Stream     → handle_streaming() → analysis_quality_stream() → tracked_stream()
```

---

## 6. Response Pipeline

```text
proxy/proxy.py::build_response_pipeline()  (lines 100-122)
Runs on complete response objects, NOT on SSE event strings.

Order:
 1. ReasoningHandlingTransformer
       • Strips/places <reasoning> blocks based on policy
       File: llm/transformers/reasoning_handling.py

 2. UniversalToolExtractionTransformer
       • Extracts XML <tool_call> from text for any model
       File: llm/transformers/universal_tool_extraction.py

 3. ToolCallValidatorTransformer
       • Validates/fixes Claude Code tool params (AskUserQuestion, etc.)
       File: llm/transformers/tool_call_validator.py

 4. PlanModeGuardTransformer
       • Blocks Edit/Write/Bash-write while in plan mode
       File: llm/transformers/plan_mode_guard.py

 5. GroundingValidatorTransformer
       • Verifies citations against actual files
       File: llm/transformers/grounding_validator.py

 6. ModelFeedbackTransformer
       • Injects quality feedback into response for refinement loops
       File: llm/transformers/model_feedback.py

 7. QualityRecorderTransformer
       • Records scores for adaptive routing history
       File: llm/transformers/quality_recorder.py
```

**Streaming note:** Response pipeline is skipped for streaming because it needs a complete response object. Grounding validation for streams runs asynchronously via `stream_event.py:tracked_stream()` (`_run_post_stream_validation`).

---

## 7. Kimi K2 Integration Specifics

### 7.1 Configurable Endpoint Path

```python
# config.py:48
class ProviderCredentials:
    anthropic_endpoint_path: str = "/v1/messages"  # ENV: ANTHROPIC_ENDPOINT_PATH

    @property
    def anthropic_litellm_api_base(self) -> Optional[str]:
        if not self.anthropic_base_url:
            return None
        return self.anthropic_base_url + self.anthropic_endpoint_path
```

**Why it matters:** Kimi K2's Anthropic-compatible endpoint lives at `/coding/v1/messages`, not the standard `/v1/messages`. LiteLLM normally appends `/v1/messages` to the base URL. By setting `LITELLM_ANTHROPIC_DISABLE_URL_SUFFIX=true` (documented in the comment at `config.py:58-59`) and using `anthropic_litellm_api_base`, the proxy sends requests to the exact Kimi URL.

**Files touched:**
- `config.py` (new property)
- `llm/transformers/credential.py:33-34` uses `anthropic_litellm_api_base`
- `llm/passthrough.py` uses `endpoint_path=cfg.credentials.anthropic_endpoint_path`

### 7.2 Temperature Clamping

```python
# llm/transformers/provider_quirks.py:72-78
if "kimi" in model.lower():
    current_temp = ctx.litellm_request.get("temperature")
    max_temp = self._quirks.kimi_max_temp if self._quirks else 0.8
    clamp_temp = self._quirks.kimi_clamp_temp if self._quirks else 0.6
    if current_temp is not None and current_temp > max_temp:
        ctx.litellm_request["temperature"] = clamp_temp
        logger.info("[quirks] kimi-k2: temp_clamped %.1f (was %.1f)", clamp_temp, current_temp)
```

**Env tuning:**
- `QUIRKS_KIMI_MAX_TEMP` default 0.8
- `QUIRKS_KIMI_CLAMP_TEMP` default 0.6

This runs in both LiteLLM and passthrough pipelines (passthrough uses the same `ProviderQuirksTransformer` in `build_passthrough_pipeline()`).

### 7.3 Thinking Parameters

```python
# llm/transformers/provider_quirks.py:100-105
elif "kimi" in model.lower():
    kimi_params = self._litellm_thinking_params.get("kimi")
    if kimi_params and kimi_params.get("thinking"):
        ctx.litellm_request.setdefault("extra_body", {}).update(kimi_params["thinking"])
        logger.info("[quirks] kimi-k2: thinking injected %s", list(kimi_params["thinking"].keys()))
```

Configured via `LITELLM_THINKING_PARAMS` JSON env var, e.g.:
```json
{"kimi": {"thinking": {"type": "enabled"}}}
```

---

## 8. Deferred Tools (Non-Claude Model Support)

Claude Code advertises special workflow tools (`EnterPlanMode`, `ExitPlanMode`, `TodoWrite`, `AskUserQuestion`, etc.) inside the system prompt as an XML block, not in `request.tools`. Non-Claude models never see them unless the proxy re-injects them.

```text
llm/transformers/deferred_tools.py

Step 1: extract_deferred_tool_names(system, messages)
        └── utils/tool_utils.py

Step 2: save_session_deferred_tools(session_id, deferred)
        └── llm/compressor.py (session cache)

Step 3: If system prompt has no list, load from session cache

Step 4: PLAN-phase guarantee:
        • Always inject EnterPlanMode, ExitPlanMode, AskUserQuestion, TodoWrite
        • Uses ctx.plan_mode_active (set by IntentClassifierTransformer)

Step 5: Build minimal tool definitions with verified schemas
        └── _CC_TOOL_SCHEMAS and _CC_TOOL_DESCRIPTIONS in deferred_tools.py

Step 6: Append to request.tools
```

**Files touched:**
- `llm/transformers/deferred_tools.py`
- `utils/tool_utils.py`
- `llm/compressor.py`

---

## 9. Credential & Routing Logic

### 9.1 Model Prefix → Credentials

```python
# llm/transformers/credential.py:20-34
def _inject_credentials(litellm_request, *, model, creds):
    if model.startswith("openai/"):
        litellm_request["api_key"] = creds.openai_api_key
        if creds.openai_base_url:
            litellm_request["api_base"] = creds.openai_base_url
    elif model.startswith("gemini/"):
        if creds.use_vertex_auth:
            litellm_request["vertex_project"] = creds.vertex_project
            litellm_request["vertex_location"] = creds.vertex_location
            litellm_request["custom_llm_provider"] = "vertex_ai"
        else:
            litellm_request["api_key"] = creds.gemini_api_key
    else:  # anthropic/ prefix or bare model names
        litellm_request["api_key"] = creds.anthropic_api_key
        if creds.anthropic_litellm_api_base:
            litellm_request["api_base"] = creds.anthropic_litellm_api_base
```

### 9.2 Override Priority

`CredentialTransformer.transform()` resolves credentials in this order:

1. **Analysis override** — if `ctx.analysis_phase == "SYNTHESIZING"` and `ANALYSIS_MODEL` + `ANALYSIS_API_KEY` configured
2. **Route override** — if `ctx.route_override` set by `ModelRouterTransformer` (cross-provider small/building routes)
3. **Prefix-based default** — `_inject_credentials()`

### 9.3 Model Router Logic

```python
# llm/transformers/model_router.py:54-192
async def transform(self, request, ctx):
    1. Preserve original_model
    2. map_claude_alias_to_target()  → e.g. claude-sonnet → anthropic/glm-4.7
    3. If SYNTHESIZING + ANALYSIS_MODEL → switch to analysis model
    4. Else if Ollama → choose_local_model()
    5. Else:
         • EXPLORE  → small_model  (+ small_route override)
         • EXECUTE tools=0 → big_model
         • EXECUTE tools>0 → building_model (+ building_route override)
         • PLAN     → big_model
    6. PLAN intent + reasoning_max_tokens → bump max_tokens
    7. Resolve effective_context_window
    8. Low-confidence upgrade → big_model
    9. Adaptive routing upgrade if quality history is poor
```

---

## 10. Configuration Environment Variables

| Env Var | Dataclass Field | Used By | Kimi Relevance |
|---------|-----------------|---------|----------------|
| `ANTHROPIC_BASE_URL` | `ProviderCredentials.anthropic_base_url` | credential, passthrough | Kimi base host |
| `ANTHROPIC_ENDPOINT_PATH` | `ProviderCredentials.anthropic_endpoint_path` | config, passthrough | Kimi `/coding/v1/messages` |
| `ANTHROPIC_API_KEY` | `ProviderCredentials.anthropic_api_key` | credential, passthrough | Kimi API key |
| `PREFERRED_PROVIDER` | `ModelRouting.preferred_provider` | model_mapper, router | e.g. `anthropic` for Kimi |
| `BIG_MODEL` | `ModelRouting.big_model` | router | Kimi model ID |
| `LITELLM_ANTHROPIC_DISABLE_URL_SUFFIX` | n/a (external LiteLLM flag) | LiteLLM | Must be true for non-standard endpoint |
| `LITELLM_THINKING_PARAMS` | `ProxyConfig.litellm_thinking_params` | provider_quirks | Kimi thinking params |
| `QUIRKS_KIMI_MAX_TEMP` | `ProviderQuirksConfig.kimi_max_temp` | provider_quirks | Clamp threshold |
| `QUIRKS_KIMI_CLAMP_TEMP` | `ProviderQuirksConfig.kimi_clamp_temp` | provider_quirks | Clamp value |
| `PASSTHROUGH_DISABLED` | `ProxyConfig.passthrough_disabled` | proxy | Set 0 to allow passthrough |
| `PASSTHROUGH_REQUIRE_PREFIX` | `ProxyConfig.passthrough_require_prefix` | proxy | 1 requires `anthropic/` prefix |

---

## 11. Recent Changes Impact (as of current branch)

1. **Configurable Anthropic endpoint path** (`config.py`, `credential.py`, `passthrough.py`)
   - Enables Kimi K2 `/coding/v1/messages` and any other non-standard Anthropic-compatible provider.

2. **Kimi K2 temperature clamping** (`provider_quirks.py`)
   - Prevents quality collapse in long sessions when CC sends high temperature.

3. **Deferred tools injection** (`deferred_tools.py`)
   - Makes non-Claude models aware of plan-mode/workflow tools.
   - Fixes `AskUserQuestion` schema confusion with explicit examples.

4. **Three-phase analysis state machine** (`TransformContext.analysis_phase`)
   - Replaces sticky `is_analysis` boolean.
   - Allows ANALYZING/READ/SYNTHESIZING-specific behavior (credentials, thinking params).

5. **Agnostic response pipeline**
   - Universal tool extraction, grounding validation, plan-mode guard work across all providers.

---

## 12. Component Interaction Summary

```text
server.py
  ├── config.py ......................... loads ProxyConfig once
  ├── proxy/proxy.py .................... builds/executes pipelines
  │     ├── llm/pipeline.py ............. TransformContext + Pipeline runner
  │     ├── llm/transformers/*.py ....... individual transformers
  │     │     ├── model_router.py ....... uses router/model_mapper.py
  │     │     ├── credential.py ......... uses config.py credentials
  │     │     ├── provider_quirks.py .... uses config.py quirks
  │     │     └── deferred_tools.py ..... uses llm/compressor.py + utils/tool_utils.py
  │     ├── llm/converters.py ........... Anthropic ↔ LiteLLM
  │     ├── llm/passthrough.py .......... direct httpx Anthropic-compatible calls
  │     └── llm/streaming.py ............ SSE handling
  ├── llm/transformers/quality_refinement.py ......... analysis re-send loop
  ├── utils/metrics.py .................. request logging + stats endpoints
  └── router/model_mapper.py ............ Claude alias resolution
```

---

*Document generated for the Kimi K2 integration deep-dive. No code changes were made.*
