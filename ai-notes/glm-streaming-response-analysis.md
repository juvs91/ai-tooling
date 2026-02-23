# GLM-4 / Z.AI Streaming Response Analysis

> Research session: 2026-02-20
> Goal: Understand if GLM puts tool-like content in fields OTHER than delta.content,
> which would explain why XmlToolBuffer never sees the <tool_call> XML.

---

## 1. Non-Standard Fields in GLM Streaming Responses

### 1a. `reasoning_content` (CONFIRMED)

GLM-4.5+ and GLM-4.7 support a `reasoning_content` field in streaming deltas:

```json
{
  "choices": [{
    "index": 0,
    "delta": {
      "role": "assistant",
      "reasoning_content": "Let me think about this...",
      "content": null
    },
    "finish_reason": null
  }]
}
```

- **Field name**: `delta.reasoning_content` (same as DeepSeek)
- **Activation**: Request must include `"thinking": {"type": "enabled"}` in `extra_body`
- **Interleaved Thinking**: GLM-4.7 supports thinking BETWEEN tool calls (reasons before each tool invocation)
- **CRITICAL**: If our proxy is NOT sending `thinking.type=enabled`, GLM should NOT return reasoning_content. But if litellm or the Z.AI OpenAI-compat layer enables it by default, we could be losing content there.

### 1b. `web_search` (CONFIRMED - top-level response field)

When web_search tool is enabled in the request, the response includes a TOP-LEVEL `web_search` array:

```json
{
  "choices": [...],
  "web_search": [
    {
      "content": "summarized page content",
      "link": "https://...",
      "title": "Page Title",
      "media": "source name",
      "publish_date": "2026-01-15",
      "refer": "ref_1",
      "icon": "favicon url"
    }
  ]
}
```

- This is a TOP-LEVEL field, NOT inside choices[0].delta
- The actual response text in `delta.content` contains citations referencing these results
- **Impact on proxy**: Our proxy does NOT send `web_search` tool in requests (CC doesn't know about it), so this should NOT be present in our responses. **NOT a concern for tool call visibility.**

### 1c. `function_call` (old OpenAI format) -- NOT USED

GLM uses `tool_calls` (new format), not `function_call` (deprecated OpenAI format).
The delta structure follows OpenAI's current tool_calls format:

```json
{
  "choices": [{
    "delta": {
      "tool_calls": [{
        "index": 0,
        "function": {
          "name": "ToolName",
          "arguments": "{\"param1\":"
        }
      }]
    }
  }]
}
```

### 1d. No separate `thinking` or `thought` field

GLM uses `reasoning_content` (same field name as DeepSeek), not a separate `thinking` field.

---

## 2. Where Tool Call Content Appears When GLM Can't Use Native tool_calls

When GLM is used in NO_TOOLS mode (tool definitions injected as XML prompt):
- Tool call XML appears in `delta.content` -- this is correct and handled by XmlToolBuffer
- GLM may use a non-standard arg_key/arg_value format (already handled):
  ```xml
  <tool_call>ToolName<arg_key>param</arg_key><arg_value>value</arg_value></tool_call>
  ```

When GLM has native tool_calls enabled:
- Tool calls appear in `delta.tool_calls` -- standard OpenAI format
- The proxy handles this via the native tool_calls processing path

**KEY QUESTION: Is GLM-4.7 listed in NO_TOOLS_MODELS?**
Based on the config, NO_TOOLS_MODELS is set in `.env` (not in profile-envs/cloud.zai.env),
and glm-4.7 is NOT a reasoning model. GLM-4.7 supports native function calling.
So GLM should use native tool_calls, NOT XML simulation.

---

## 3. Z.AI OpenAI-Compatible Endpoint Deviations

Endpoint: `https://api.z.ai/api/paas/v4`

### Confirmed deviations from standard OpenAI:
1. **`reasoning_content` in delta** -- extra field not in OpenAI spec (shared with DeepSeek)
2. **`web_search` top-level response field** -- not in OpenAI spec (only present when web_search tool is in request)
3. **`prompt_tokens_details.cached_tokens`** -- in usage object (minor)
4. **`tool_stream` request parameter** -- Z.AI-specific, enables streaming tool call arguments without buffering
5. **`thinking` request parameter** -- enables reasoning mode (not in OpenAI spec)

### Likely standard (no deviation):
- `delta.content` -- standard text
- `delta.tool_calls` -- standard format with index, function.name, function.arguments
- `finish_reason` values: "stop", "length", "tool_calls" -- standard
- SSE format with `data: [DONE]` terminator -- standard

---

## 4. LiteLLM Handling of GLM Models

### Provider routing:
- `zai/` prefix: built-in Z.AI provider (added in PR #17307)
- `openai/` prefix with custom base_url: treated as OpenAI-compatible
- **Our proxy uses `openai/glm-4.7` with OPENAI_BASE_URL pointing to Z.AI** -- litellm treats it as generic OpenAI-compatible

### reasoning_content handling (CRITICAL BUG):
- **litellm v1.81.6** introduced a regression: `OpenAIChatCompletionResponseIterator.chunk_parser()` creates `ModelResponseStream` from chunks, but the Pydantic model only defines `reasoning_content: Optional[str]`
- Chunks with `reasoning` field (used by some providers) are DROPPED in streaming
- GLM uses `reasoning_content` (correct field name), so this specific litellm bug should NOT affect GLM
- **However**: litellm's Delta Pydantic model may not pass through `reasoning_content` for `openai/` prefixed models unless the provider is explicitly recognized
- Issue #15690 was closed as "not planned" -- no fix was implemented

### Our litellm version:
- pyproject.toml requires: `litellm>=1.77.7`
- Local installation: `litellm==1.81.7` (may differ in Docker container)
- v1.81.7 may or may not have the reasoning_content passthrough fix for OpenAI-compatible providers

---

## 5. Root Cause Analysis: Why XmlToolBuffer Never Sees <tool_call>

### Hypothesis evaluation:

**H1: GLM puts tool calls in reasoning_content, not content** -- POSSIBLE but unlikely
- Only happens if thinking mode is somehow enabled
- Our proxy reads `reasoning_content` via `bget(delta, "reasoning_content")` on line 444 of streaming.py
- In no_tools_mode, reasoning is buffered; in tools mode, it's emitted as text
- If GLM's tool call XML ends up in reasoning_content AND the model has native tools (not no_tools_mode), the reasoning_content would be emitted as text_delta but NOT fed to XmlToolBuffer
- **FIX NEEDED**: Feed reasoning_content through XmlToolBuffer too (not just delta.content)

**H2: GLM uses native tool_calls (not XML) because it's not in NO_TOOLS_MODELS** -- VERY LIKELY
- GLM-4.7 supports native function calling
- The proxy sends tool definitions as OpenAI function tools
- GLM responds with delta.tool_calls, NOT XML in delta.content
- The native tool_calls path in streaming.py handles this correctly
- **If this is working, the question is: when does it NOT work?**

**H3: litellm strips reasoning_content for openai/ prefix providers** -- POSSIBLE
- litellm's Pydantic Delta model may drop unknown fields during deserialization
- If litellm strips reasoning_content before our proxy sees it, and GLM put tool-like content there, we'd lose it
- **Test needed**: Log raw SSE chunks from Z.AI before litellm processes them

**H4: GLM puts content in a field litellm doesn't forward** -- UNLIKELY
- GLM's only non-standard fields are reasoning_content and web_search
- web_search is top-level (not in delta), and only appears when explicitly requested
- No evidence of other custom fields

---

## 6. Recommended Actions

### Immediate (diagnostic):
1. **Add raw chunk logging**: Before litellm processes chunks, log the raw SSE data from Z.AI to see ALL fields
   ```python
   # In streaming loop, log the raw chunk object
   print(f"[streaming] RAW CHUNK: {chunk}")
   ```

2. **Check if reasoning_content is present**: Add logging for `delta_reasoning` in streaming.py to confirm whether GLM is sending reasoning_content without being asked

3. **Verify NO_TOOLS_MODELS**: Confirm glm-4.7 is NOT in NO_TOOLS_MODELS. If it is, XML simulation is active and tool calls should appear in delta.content

### Short-term (fixes):
4. **Feed reasoning_content through XmlToolBuffer**: When in no_tools_mode AND reasoning_content contains `<tool_call`, it should be extracted (this already happens in `_process_reasoning_buffer`, but only at stream end, not per-chunk)

5. **Test native tool_calls with GLM**: Make a direct API call to Z.AI with tools to verify GLM returns proper delta.tool_calls

### Medium-term:
6. **Consider `zai/` prefix instead of `openai/`**: litellm's built-in Z.AI provider may handle reasoning_content better than the generic openai/ path

---

## 7. Key Finding Summary

| Field | Present in GLM? | Our proxy handles it? | Risk |
|-------|----------------|----------------------|------|
| `delta.content` | Yes | Yes (XmlToolBuffer) | LOW |
| `delta.tool_calls` | Yes (native) | Yes (native path) | LOW |
| `delta.reasoning_content` | Yes (if thinking enabled) | Partial -- buffered/emitted but NOT fed to XmlToolBuffer per-chunk | MEDIUM |
| `web_search` (top-level) | Only if web_search tool in request | No (not needed) | NONE |
| `delta.function_call` | No (uses tool_calls) | N/A | NONE |
| `delta.reasoning` (alt name) | No (GLM uses reasoning_content) | N/A | NONE |
| `delta.thinking` | No | N/A | NONE |

**Bottom line**: The most likely explanation for "XmlToolBuffer never sees the <tool_call> XML" is that GLM-4.7 is using NATIVE tool_calls (not XML), so tool calls go through the native path (delta.tool_calls), not through delta.content at all. The XmlToolBuffer is irrelevant when native tool_calls work correctly. If native tool_calls are NOT working, the issue is more likely in tool definition conversion or Z.AI's tool_calls format, not in a missing field.

---

## Sources
- Z.AI Chat Completion API: https://docs.z.ai/api-reference/llm/chat-completion
- Z.AI Thinking Mode: https://docs.z.ai/guides/capabilities/thinking-mode
- Z.AI Stream Tool: https://docs.z.ai/guides/capabilities/stream-tool
- Z.AI Web Search: https://docs.z.ai/guides/tools/web-search
- Z.AI Streaming: https://docs.z.ai/guides/capabilities/streaming
- litellm Z.AI provider: https://docs.litellm.ai/docs/providers/zai
- litellm reasoning_content: https://docs.litellm.ai/docs/reasoning_content
- litellm issue #15690 (reasoning field not passed through): https://github.com/BerriAI/litellm/issues/15690
- litellm issue #20246 (streaming reasoning missing for VLLM): https://github.com/BerriAI/litellm/issues/20246
- Vercel AI issue #11682 (GLM 4.7 reasoning_content not parsed): https://github.com/vercel/ai/issues/11682
- ZhipuAI Python SDK: https://github.com/MetaGLM/zhipuai-sdk-python-v4
