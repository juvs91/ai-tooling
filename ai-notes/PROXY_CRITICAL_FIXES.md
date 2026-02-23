# Proxy Claude Code - Critical Fixes Documentation

## Overview

This document details the critical fixes applied to the Claude Code Proxy to ensure compatibility with various LLM providers (Z.AI, Groq, Gemini, Ollama) and proper handling of Claude Code's tool-calling requirements.

## Fix 1: Thinking Blocks (422 Error)

**Problem**: Claude Code sends requests with `ContentBlockThinking` and `ContentBlockRedactedThinking` blocks. When converting to OpenAI format, these blocks were not properly handled, causing 422 validation errors.

**Solution**: Added support for thinking-related content blocks in the schema definitions and stripped them during conversion.

### Code Changes

#### 1. Added thinking block schemas in `schemas.py`

```python
# Added to llm/schemas.py
class ContentBlockThinking(BaseModel):
    type: Literal["thinking"]
    thinking: str
    signature: str

class ContentBlockRedactedThinking(BaseModel):
    type: Literal["redacted_thinking"]
    data: str

class ContentBlockServerToolUse(BaseModel):
    type: Literal["server_tool_use"]
    id: str
    name: str
    input: Dict[str, Any] = {}

class ContentBlockServerToolResult(BaseModel):
    type: Literal["server_tool_result"]
    tool_use_id: str
    content: Union[str, List[Dict[str, Any]], Dict[str, Any], List[Any], Any] = ""
```

#### 2. Updated Message model to include thinking blocks

```python
class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: Union[
        str,
        List[Union[
            ContentBlockText,
            ContentBlockImage,
            ContentBlockToolUse,
            ContentBlockToolResult,
            ContentBlockThinking,          # Added
            ContentBlockRedactedThinking,  # Added
            ContentBlockServerToolUse,     # Added
            ContentBlockServerToolResult,  # Added
        ]],
    ]
```

#### 3. Stripping thinking blocks in `converters.py`

```python
def _convert_assistant_blocks(blocks: Any) -> List[Dict[str, Any]]:
    # ...
    for b in blocks:
        btype = bget(b, "type")

        if btype == "text":
            # Handle text
        elif btype == "tool_use":
            # Handle tool_use
        elif btype in ("thinking", "redacted_thinking"):  # STRIPPED
            pass  # Ignore thinking blocks
        # ...
```

**Impact**: Eliminates 422 errors when Claude Code sends requests with thinking content blocks.

## Fix 2: Single-Quote XML Tool Calls

**Problem**: deepseek-reasoner and some other models output XML with SINGLE quotes (`<tool_call name='X'>`) instead of double quotes, and use Python dict syntax (`{'key': 'val'}`) instead of JSON.

**Solution**: Updated all regex patterns to accept both single and double quotes, and added `json_repair` to handle Python dict syntax.

### Code Changes

#### 1. Updated regex patterns in `tool_prompting.py`

```python
# Original pattern (double quotes only):
_NAME_ATTR = r'name="([^"]+)"'

# Updated pattern (both single and double quotes):
_NAME_ATTR = r"name=(?:""|')([^"']+)(?:""|')"
```

Multiple regex patterns updated:
- `_TOOL_CALL_RE`
- `_TOOL_CALL_FALLBACK_RE`
- `_TOOL_CALL_BARE_RE`
- `_PARTIAL_TOOL_RE`

#### 2. Added JSON repair for Python dict syntax

```python
# In converters.py, when parsing tool call arguments:
if isinstance(arguments, str):
    try:
        arguments = json.loads(arguments)
    except json.JSONDecodeError:
        try:
            repaired = repair_json(arguments, return_objects=True)
            if isinstance(repaired, dict):
                arguments = repaired
                print(f"[json-repair] Repaired tool_call arguments for {name}")
            else:
                arguments = {"raw": arguments}
        except Exception:
            arguments = {"raw": arguments}
```

#### 3. Added bare regex fallback

Added a 3rd regex level (`_TOOL_CALL_BARE_RE`) to handle tool calls without inner tags (JSON directly inside `<tool_call>`).

**Impact**: Models like deepseek-reasoner can now successfully execute tools through the proxy.

## Fix 3: Token Counting Improvements

**Problem**: The original heuristic of `chars/4` for token estimation was inaccurate for multilingual content and didn't account for model-specific tokenization.

**Solution**: Replaced with `litellm.token_counter()` for accurate token counting with fallback to `chars/3`.

### Code Changes

#### 1. Updated compressor logic

```python
# In compressor.py or similar:
try:
    # Use litellm's token counter when available
    from litellm import token_counter
    input_tokens = token_counter(model=model_name, text=text)
except Exception:
    # Fallback to chars/3 (more conservative than chars/4)
    input_tokens = len(text) // 3
```

**Impact**: More accurate context window management, especially for providers with strict token limits (e.g., DeepSeek 64K context).

## Fix 4: Intent Classification & Routing System

**Problem**: Different LLM tasks (chat, building, planning) require different model capabilities and cost profiles.

**Solution**: Implemented intent-based routing with LLM classifier fallback to regex patterns.

### Architecture

```
Claude Code Request
        ↓
[Intent Classifier]
        ↓
   ├── CHAT → Small Model (e.g., glm-4.7-flash)
   ├── BUILDING → Big Model (e.g., glm-4.7)
   └── PLANNING → Big Model (e.g., glm-4.7)
```

### Code Implementation

#### 1. Environment variables for classifier

```bash
# profile-envs/cloud.zai.env
CLASSIFIER_MODEL=glm-4.7-flash  # Cheap model for classification
CLASSIFIER_API_KEY=${ZAI_API_KEY}
CLASSIFIER_BASE_URL=https://api.z.ai/api/paas/v4
```

#### 2. Provider configuration with intent-based model selection

```python
# In schemas.py
class ProviderConfig:
    def get_litellm_model(self, intent: str) -> str:
        """Return litellm-style 'prefix/model' string for the given intent."""
        building = self.building_model or self.big_model
        if intent == "CHAT" and self.small_model != self.big_model:
            model = self.small_model
        elif intent == "BUILDING" and building != self.big_model:
            model = building
        else:
            model = self.big_model
        return f"{self.provider_prefix}/{model}"
```

#### 3. Fallback to regex-based classification

When `CLASSIFIER_MODEL` is empty or classifier fails, falls back to regex patterns:
- `CHAT`: Short, conversational messages
- `BUILDING`: Contains code, file paths, or tool usage
- `PLANNING`: Long, structured analysis or planning requests

**Impact**: Cost optimization by routing chat requests to cheaper models while maintaining quality for complex tasks.

## Fix 5: No-Tools Model Support

**Problem**: Some models (like deepseek-reasoner) don't support native tool calling and require XML prompts.

**Solution**: Detect no-tools models and inject tool definitions as XML prompts in system messages.

### Code Implementation

#### 1. Model detection

```python
def is_no_tools_model(model_name: str) -> bool:
    """Check if a model requires XML tool prompting instead of native tools."""
    no_tools_patterns = [
        "deepseek-reasoner",
        "deepseek-chat",
        "llama",
        "mistral",
        # ... other models without native tool support
    ]
    return any(pattern in model_name.lower() for pattern in no_tools_patterns)
```

#### 2. XML tool prompt injection

```python
if is_no_tools_model(original_request.model) and content_text:
    request_tools = getattr(original_request, "tools", None)
    valid_names = _build_valid_tool_names(request_tools)
    xml_tool_blocks, clean_text = extract_tool_calls_from_text(
        content_text,
        valid_tool_names=valid_names,
        tools=request_tools
    )

    if xml_tool_blocks:
        # Rebuild content: clean text + tool_use blocks
        content = []
        clean_text = clean_text.strip()
        if clean_text:
            content.append({"type": "text", "text": clean_text})
        content.extend(xml_tool_blocks)
        finish_reason = "tool_calls"
```

**Impact**: Models without native tool support can still execute tools through XML prompting.

## Fix 6: Gemini Schema Sanitization

**Problem**: Gemini/Vertex AI has strict JSON schema validation that rejects many valid Anthropic tool schemas.

**Solution**: Implemented schema sanitization to remove unsupported keywords and normalize schemas.

### Code Implementation

```python
def clean_gemini_schema(schema: Any) -> Any:
    """Sanitizer best-effort para Gemini / Vertex tools schemas."""
    DROP_KEYS = {
        "$schema", "$id", "id", "$ref",
        "definitions", "$defs",
        "additionalProperties", "unevaluatedProperties",
        # ... 20+ other problematic keywords
    }

    def _clean(x: Any) -> Any:
        if isinstance(x, list):
            return [_clean(i) for i in x]
        if not isinstance(x, dict):
            return x

        # Remove unsupported keys
        for k in list(x.keys()):
            if k in DROP_KEYS:
                x.pop(k, None)

        # Normalize type fields
        if x.get("type") == "string" and "format" in x:
            if x["format"] not in ALLOWED_STRING_FORMATS:
                x.pop("format", None)

        # Recursively clean nested structures
        if "properties" in x and isinstance(x["properties"], dict):
            for pk, pv in list(x["properties"].items()):
                x["properties"][pk] = _clean(pv)

        return x

    return _clean(schema)
```

**Impact**: Gemini models can now accept and process tool definitions that were previously rejected.

## Fix 7: Context Window Management

**Problem**: Different providers have different context windows (e.g., DeepSeek 64K, Z.AI 128K, OpenAI 128K).

**Solution**: Dynamic token capping based on provider context windows.

### Code Implementation

```python
# In converters.py
def convert_anthropic_to_litellm(anthropic_request: MessagesRequest, model_context_window: int = 0):
    max_tokens = anthropic_request.max_tokens

    if model_context_window > 0:
        # Dynamic cap for providers with MODEL_CONTEXT_WINDOW set
        provider_max = int(os.environ.get("MAX_OUTPUT_TOKENS", "8192"))
        input_estimate = sum(len(str(m.get("content", ""))) for m in messages) // 4
        tools_estimate = 0

        if anthropic_request.tools:
            for t in anthropic_request.tools:
                tools_estimate += len(json.dumps(to_dict(t))) // 4

        remaining = model_context_window - input_estimate - tools_estimate
        safe_remaining = int(remaining * 0.85)  # 15% margin
        dynamic_cap = max(1024, min(safe_remaining, provider_max))
        max_tokens = min(max_tokens, dynamic_cap)

    return max_tokens
```

**Impact**: Prevents token overflow errors and ensures responses fit within provider limits.

## Testing and Validation

Each fix includes comprehensive testing:

1. **Unit Tests**: For schema validation and conversion logic
2. **Integration Tests**: End-to-end tool calling with different providers
3. **Regression Tests**: Ensure fixes don't break existing functionality

## Lessons Learned

1. **Be Conservative with Token Counting**: `chars/3` is safer than `chars/4` for multilingual content
2. **Handle All XML Variants**: Models output XML in different formats (single vs double quotes)
3. **Schema Compatibility Matters**: Different providers have different JSON schema strictness
4. **Intent-Based Routing Saves Costs**: Cheap models work fine for chat, save big models for complex tasks
5. **Always Have Fallbacks**: LLM classifiers can fail; regex patterns provide reliable fallback

## Future Improvements

1. **Automatic Provider Selection**: Based on cost, latency, and capability requirements
2. **Adaptive Token Budgeting**: Dynamic allocation based on task complexity
3. **Multi-Hop Grounding**: Chain multiple providers for complex reasoning tasks
4. **Real-Time Performance Monitoring**: Track latency, success rates, and costs per provider

---

*Last Updated: Based on proxy code as of commit c6c5ec4*