# Proxy Response Pipeline Architecture

## Overview

The proxy has 4 distinct paths for response processing. Each path has different
characteristics and capabilities based on whether it uses streaming and which
provider is used.

## 4 Response Paths

### 1. Passthrough Non-Streaming

**Entry**: `proxy.py:425` → `server.py:347`

**Characteristics**:
- Direct model call via Anthropic API
- Complete response object available
- Response pipeline runs synchronously
- Grounding runs in response pipeline
- Quality refinement runs with re-sending capability

**Flow**:
```
proxy.py:_passthrough_route()
  → pt.create_message()
  → _run_response_pipeline()
     → GroundingValidatorTransformer (synchronous)
  → Return to server.py:347
  → analysis_quality_nonstream()
     → Uses ctx.grounding_score from response pipeline
     → Re-sends if needed
```

**Grounding location**: Response pipeline (synchronous)
**Re-sending**: YES (via quality refinement)

---

### 2. LiteLLM Non-Streaming

**Entry**: `server.py:345` → `server.py:347`

**Characteristics**:
- OpenAI-compatible API call
- Complete response object available
- Response pipeline runs synchronously
- Grounding runs in response pipeline
- Quality refinement runs with re-sending capability

**Flow**:
```
server.py:_route_litellm(is_stream=False)
  → convert_litellm_to_anthropic()
  → _run_response_pipeline()
     → GroundingValidatorTransformer (synchronous)
  → analysis_quality_nonstream()
     → Uses ctx.grounding_score from response pipeline
     → Re-sends if needed
```

**Grounding location**: Response pipeline (synchronous)
**Re-sending**: YES (via quality refinement)

---

### 3. Passthrough Streaming

**Entry**: `server.py:252-274`

**Characteristics**:
- Direct model call via Anthropic API
- Response is generator of SSE event strings
- Response pipeline SKIPPED (incompatible with SSE)
- Grounding runs asynchronously post-stream
- Quality refinement runs before streaming starts

**Flow**:
```
server.py:_route_litellm(is_stream=True)
  → passthrough_xml_tool_extraction() (parse XML)
  → analysis_quality_stream()
     → Accumulate stream
     → Evaluate quality
     → Re-sends if needed (converts to non-stream temporarily)
  → tracked_stream()
     → Yield chunks to client
     → Post-stream: _run_post_stream_validation()
        → GroundingValidatorTransformer (async)
```

**Grounding location**: `tracked_stream()` → `_run_post_stream_validation()` (asynchronous)
**Re-sending**: NO (DX preservation - client receives response immediately)

---

### 4. LiteLLM Streaming

**Entry**: `server.py:302-338`

**Characteristics**:
- OpenAI-compatible API call
- Response is generator of SSE event strings
- Response pipeline SKIPPED (incompatible with SSE)
- Grounding runs asynchronously post-stream
- Quality refinement runs before streaming starts

**Flow**:
```
server.py:_route_litellm(is_stream=True)
  → handle_streaming() (convert to SSE)
  → analysis_quality_stream()
     → Accumulate stream
     → Evaluate quality
     → Re-sends if needed (converts to non-stream temporarily)
  → tracked_stream()
     → Yield chunks to client
     → Post-stream: _run_post_stream_validation()
        → GroundingValidatorTransformer (async)
```

**Grounding location**: `tracked_stream()` → `_run_post_stream_validation()` (asynchronous)
**Re-sending**: NO (DX preservation - client receives response immediately)

---

## Why Separate Paths?

Streaming responses are generators of SSE (Server-Sent Events) event strings:
```
event: content_block_delta
data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello"}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use",...}}
```

Response transformers (including `GroundingValidatorTransformer`) expect:
- Complete response objects with `.content` attribute
- Lists of content blocks (text, tool_use, tool_result)

This fundamental difference requires separate handling.

---

## Grounding Validation

### Non-Streaming (Paths 1 & 2)

- Runs synchronously in response pipeline
- Can trigger refinement loop if score < threshold
- Re-sends to model with feedback
- Client waits for refinement to complete

### Streaming (Paths 3 & 4)

- Runs asynchronously in `tracked_stream()`
- NO refinement loop based on grounding score
- Multi-hop data persisted for NEXT request
- Client receives response immediately (no waiting)

**Why no re-sending for streaming?**
1. DX preservation - re-sending would delay response
2. Complexity - would need to accumulate, validate, re-send, stream back
3. Async approach provides benefit without UX cost

**Trade-off**: Can't fix current response, but multi-hop improves future responses.

---

## Multi-Hop Grounding

All paths support multi-hop grounding:

**Non-streaming**: Via response pipeline (synchronous)
- Entity relationships tracked in session cache
- Injected into next request via `inject_grounding_context()` in `guardrail.py`

**Streaming**: Via `_run_post_stream_validation()` (async)
- Same tracking mechanism
- Same injection into next request
- Just runs asynchronously to avoid blocking stream

**Session cache**: `/tmp/proxy_session_cache.json` (7-day TTL, disk persistence)
```json
{
  "session_id": {
    "summary": "...",
    "old_msg_count": 42,
    "timestamp": 1710761234.567,
    "grounding_graph": {
      "AuthService": {
        "file": "src/auth/AuthService.ts",
        "related": ["validateToken", "error_handler"],
        "citations": ["(AuthService.ts:123)"],
        "code_snippet": "function validateToken() {...}",
        "last_seen": 1710761234.567
      }
    },
    "verified_claims": ["abc123...", "def456..."]
  }
}
```

**Memory protection**: No leak - TTL + max entities + age-based pruning

---

## Quality Refinement Loop

### Non-Streaming

1. Quality evaluation (grounding score for analysis tasks)
2. If score < threshold: re-request with feedback
3. Response pipeline runs again (grounding re-validated)
4. Repeat up to `max_refinements` times
5. Return best response

### Streaming

1. Accumulate complete stream
2. Quality evaluation (skip if tool-heavy or workflow tools)
3. If score < threshold: convert to non-stream, re-request
4. Return refined response as SSE events
5. Async grounding runs post-stream (no refinement based on grounding)

**Important**: Streaming path does NOT re-send based on grounding score - only based on quality score.

---

## Guardrails Injection

Guardrails are injected based on analysis phase:

- **ANALYZING/READ**: Tool enforcement + analysis reasoning guardrails
  - Forces model to use available tools
  - Requires evidence-based analysis with citations
  - Loop guard detects duplicate file reads

- **SYNTHESIZING**: Synthesis guide
  - Encourages writing analysis with citations
  - Still allows tool use for verification

Guardrails are injected before the model sees the prompt, ensuring consistent behavior.

---

## Response Pipeline Components

The response pipeline (`proxy.py:build_response_pipeline()`) runs ONLY for non-streaming:

```python
Pipeline([
    ReasoningHandlingTransformer(cfg.analysis),
    UniversalToolExtractionTransformer(),
    GroundingValidatorTransformer(enabled=True),  # ← Grounding here
    ModelFeedbackTransformer(cfg),
])
```

**Does NOT run for streaming** - would break SSE generator pattern.

---

## Key Design Decisions

1. **Separate paths**: Streaming/non-streaming have fundamentally different response types
2. **Async grounding for streaming**: DX preservation over immediate quality fix
3. **No redundancy**: Grounding runs exactly once per request (response pipeline for non-stream, async post-stream for stream)
4. **Multi-hop for all paths**: Entity relationships tracked consistently
5. **Session caching**: 7-day TTL with disk persistence for stateful conversations

---

*Last updated: 2026-03-18*