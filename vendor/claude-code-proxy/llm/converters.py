# llm/converters.py
from __future__ import annotations

import hashlib
import json
import uuid
from utils.utils import bget, make_tool_id, map_stop_reason, scale_tokens, to_dict, TOOL_ID_PREFIX
from typing import Any, Dict, List, Optional, Union
from json_repair import repair_json
import os
from llm.schemas import MessagesRequest, MessagesResponse, Usage
from llm.tool_prompting import (
    is_no_tools_model, build_tool_prompt, rewrite_messages_without_tools,
    extract_tool_calls_from_text, strip_tool_call_xml, recover_incomplete_tool_call,
    _build_valid_tool_names, validate_tool_name,
)


# ── Shared helpers ────────────────────────────────────────────────────

def _safe_json(obj: Any, ensure_ascii: bool = False) -> str:
    """json.dumps with str() fallback."""
    try:
        return json.dumps(obj, ensure_ascii=ensure_ascii)
    except Exception:
        return str(obj)


def _extract_tool_fields(block: Any) -> tuple[str, str, Any]:
    """Extract (name, id, input) from tool_use or server_tool_use block."""
    return (
        bget(block, "name") or "",
        bget(block, "id") or "",
        bget(block, "input"),
    )


def clean_gemini_schema(schema: Any) -> Any:
    """
    Sanitizer best-effort para Gemini / Vertex tools schemas.
    Mantiene subset seguro; elimina keywords que suelen romper validación.
    """
    DROP_KEYS = {
        "$schema", "$id", "id", "$ref",
        "definitions", "$defs",
        "additionalProperties", "unevaluatedProperties",
        "propertyNames", "patternProperties",
        "dependencies", "dependentSchemas", "dependentRequired",
        "contentEncoding", "contentMediaType",
        "examples", "example", "default",
        "readOnly", "writeOnly", "deprecated",
        "not", "if", "then", "else",
        "contains", "minContains", "maxContains",
        "const",
        "discriminator",
    }
    ALLOWED_STRING_FORMATS = {"date-time"}

    def _merge_dict(a: dict, b: dict) -> dict:
        out = dict(a)
        for k, v in b.items():
            if k in out and isinstance(out[k], dict) and isinstance(v, dict):
                out[k] = _merge_dict(out[k], v)
            else:
                out[k] = v
        return out

    def _normalize_type(d: dict) -> dict:
        t = d.get("type")
        if isinstance(t, list):
            non_null = [x for x in t if x != "null"]
            if len(non_null) == 1:
                d["type"] = non_null[0]
            elif len(non_null) > 1:
                d["type"] = non_null[0]
            else:
                d.pop("type", None)
        if "type" not in d and "properties" in d and isinstance(d["properties"], dict):
            d["type"] = "object"
        return d

    def _rewrite_const(d: dict) -> dict:
        if "const" in d:
            d["enum"] = [d["const"]]
            d.pop("const", None)
        return d

    def _rewrite_exclusive_bounds(d: dict) -> dict:
        if "exclusiveMinimum" in d and "minimum" not in d:
            d["minimum"] = d["exclusiveMinimum"]
        d.pop("exclusiveMinimum", None)
        if "exclusiveMaximum" in d and "maximum" not in d:
            d["maximum"] = d["exclusiveMaximum"]
        d.pop("exclusiveMaximum", None)
        return d

    def _drop_unsupported_keys(d: dict) -> dict:
        for k in list(d.keys()):
            if k in DROP_KEYS:
                d.pop(k, None)
        return d

    def _clean(x: Any) -> Any:
        if isinstance(x, list):
            return [_clean(i) for i in x]
        if not isinstance(x, dict):
            return x

        x = _rewrite_const(x)
        x = _rewrite_exclusive_bounds(x)
        x = _normalize_type(x)

        if x.get("type") == "string" and "format" in x:
            if x["format"] not in ALLOWED_STRING_FORMATS:
                x.pop("format", None)

        x = _drop_unsupported_keys(x)

        if "allOf" in x and isinstance(x["allOf"], list) and x["allOf"]:
            base = dict(x)
            parts = base.pop("allOf", [])
            merged = {}
            for p in parts:
                p = _clean(p)
                if isinstance(p, dict):
                    merged = _merge_dict(merged, p)
            x = _merge_dict(base, merged)

        for key in ("anyOf", "oneOf"):
            if key in x and isinstance(x[key], list) and x[key]:
                first = _clean(x[key][0])
                base = dict(x)
                base.pop(key, None)
                if isinstance(first, dict):
                    x = _merge_dict(base, first)
                else:
                    x = base

        if "properties" in x and isinstance(x["properties"], dict):
            for pk, pv in list(x["properties"].items()):
                x["properties"][pk] = _clean(pv)

        if "items" in x:
            x["items"] = _clean(x["items"])

        if "required" in x and not isinstance(x["required"], list):
            x.pop("required", None)
        if "enum" in x and not isinstance(x["enum"], list):
            x.pop("enum", None)

        x.pop("additionalProperties", None)

        x = _normalize_type(x)
        x = _drop_unsupported_keys(x)
        return x

    cleaned = _clean(schema)
    if isinstance(cleaned, dict):
        if cleaned.get("type") is None and "properties" in cleaned:
            cleaned["type"] = "object"
    return cleaned


# ── Gemini Schema Memoization ────────────────────────────────────────
_gemini_schema_cache: dict[str, Any] = {}


def clean_gemini_schema_cached(schema: Any) -> Any:
    """Memoized wrapper around clean_gemini_schema."""
    key = hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()[:16]
    if key not in _gemini_schema_cache:
        _gemini_schema_cache[key] = clean_gemini_schema(schema)
    return _gemini_schema_cache[key]


# ── Tool Definition Conversion Cache ─────────────────────────────────
_tool_conversion_cache: dict[str, dict] = {}


def _convert_tool_cached(tool_dict: dict, is_gemini: bool) -> dict:
    """Convert Anthropic tool dict to OpenAI format with memoization."""
    name = tool_dict["name"]
    input_schema = tool_dict.get("input_schema", {}) or {}
    schema_str = json.dumps(input_schema, sort_keys=True)
    key = f"{name}:{'g' if is_gemini else 'o'}:{hashlib.sha256(schema_str.encode()).hexdigest()[:16]}"

    if key in _tool_conversion_cache:
        return _tool_conversion_cache[key]

    if is_gemini:
        input_schema = clean_gemini_schema_cached(input_schema)

    converted = {
        "type": "function",
        "function": {
            "name": name,
            "description": tool_dict.get("description", "") or "",
            "parameters": input_schema,
        },
    }
    _tool_conversion_cache[key] = converted
    return converted


def _system_to_text(system: Any) -> str:
    if system is None:
        return ""
    if isinstance(system, str):
        return system.strip()
    if isinstance(system, list):
        parts = []
        for b in system:
            if hasattr(b, "type") and getattr(b, "type") == "text":
                parts.append(getattr(b, "text", ""))
            elif isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
        return "\n\n".join([p for p in parts if p]).strip()
    return ""


def _content_blocks_to_text(content: Any) -> str:
    if content is None:
        return "..."
    if isinstance(content, str):
        return content if content.strip() else "..."
    if not isinstance(content, list):
        try:
            return json.dumps(content)[:8000]
        except Exception:
            return str(content)[:8000]

    out = []
    for b in content:
        btype = bget(b, "type")

        if btype == "text":
            txt = bget(b, "text")
            if txt:
                out.append(str(txt))
        elif btype == "tool_result":
            tool_id = bget(b, "tool_use_id")
            out.append(f"[Tool Result ID: {tool_id or 'unknown'}]")
            out.append(_content_blocks_to_text(bget(b, "content")))
        elif btype in ("tool_use", "server_tool_use"):
            label = "ServerTool" if btype == "server_tool_use" else "Tool"
            name, tid, inp = _extract_tool_fields(b)
            out.append(f"[{label}: {name} (ID: {tid})] Input: {_safe_json(inp, ensure_ascii=False)}")
        elif btype in ("thinking", "redacted_thinking"):
            pass
        elif btype == "server_tool_result":
            tool_id = bget(b, "tool_use_id")
            out.append(f"[ServerTool Result ID: {tool_id or 'unknown'}]")
            out.append(_content_blocks_to_text(bget(b, "content")))
        elif btype == "image":
            out.append("[Image content omitted]")
        else:
            out.append(_safe_json(b)[:1000])

    text = "\n".join([x for x in out if x]).strip()
    return text if text else "..."


def _tool_result_content_to_str(content: Any) -> str:
    """Normalize tool_result content (str, list, dict, None) to a plain string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif item.get("type") == "image":
                    parts.append("[Image content]")
                else:
                    try:
                        parts.append(json.dumps(item)[:2000])
                    except Exception:
                        parts.append(str(item)[:2000])
            elif hasattr(item, "type") and getattr(item, "type") == "text":
                parts.append(getattr(item, "text", ""))
            else:
                parts.append(str(item)[:2000])
        return "\n".join(parts)
    if isinstance(content, dict):
        if content.get("type") == "text":
            return content.get("text", "")
        try:
            return json.dumps(content)[:4000]
        except Exception:
            return str(content)[:4000]
    return str(content)[:4000]


def _convert_assistant_blocks(blocks: Any) -> List[Dict[str, Any]]:
    """
    Convert assistant content blocks to OpenAI format.
    - text -> content field
    - tool_use -> tool_calls array
    - thinking/redacted_thinking -> stripped
    - server_tool_use -> text fallback
    """
    text_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []

    for b in blocks:
        btype = bget(b, "type")

        if btype == "text":
            txt = bget(b, "text")
            if txt:
                text_parts.append(str(txt))

        elif btype == "tool_use":
            name, tid, inp = _extract_tool_fields(b)
            try:
                args_str = json.dumps(inp, ensure_ascii=False)
            except Exception:
                args_str = json.dumps({"raw": str(inp)})
            tool_calls.append({
                "id": tid,
                "type": "function",
                "function": {"name": name, "arguments": args_str},
            })

        elif btype in ("thinking", "redacted_thinking"):
            pass

        elif btype == "server_tool_use":
            name, tid, inp = _extract_tool_fields(b)
            text_parts.append(f"[ServerTool: {name} (ID: {tid})] Input: {_safe_json(inp, ensure_ascii=False)}")

        else:
            text_parts.append(_safe_json(to_dict(b))[:1000])

    content_text = "\n".join(text_parts).strip() if text_parts else None
    result: Dict[str, Any] = {"role": "assistant"}

    if tool_calls:
        result["content"] = content_text
        result["tool_calls"] = tool_calls
    else:
        result["content"] = content_text if content_text else "..."

    return [result]


def _convert_user_blocks(blocks: Any) -> List[Dict[str, Any]]:
    """
    Convert user content blocks to OpenAI format.
    - tool_result -> individual role:"tool" messages
    - text -> single role:"user" message
    - server_tool_result -> text fallback
    """
    text_parts: List[str] = []
    tool_messages: List[Dict[str, Any]] = []

    for b in blocks:
        btype = bget(b, "type")

        if btype == "text":
            txt = bget(b, "text")
            if txt:
                text_parts.append(str(txt))

        elif btype == "tool_result":
            tool_use_id = bget(b, "tool_use_id")
            content_str = _tool_result_content_to_str(bget(b, "content"))

            if bget(b, "is_error") and content_str:
                content_str = f"[ERROR] {content_str}"

            tool_messages.append({
                "role": "tool",
                "tool_call_id": tool_use_id or "unknown",
                "content": content_str or "...",
            })

        elif btype == "server_tool_result":
            tool_id = bget(b, "tool_use_id")
            text_parts.append(f"[ServerTool Result ID: {tool_id or 'unknown'}]")
            text_parts.append(_content_blocks_to_text(bget(b, "content")))

        elif btype == "image":
            text_parts.append("[Image content omitted]")

        else:
            dumped = b if isinstance(b, dict) else str(b)
            text_parts.append(_safe_json(dumped)[:1000])

    result: List[Dict[str, Any]] = []
    result.extend(tool_messages)

    user_text = "\n".join(text_parts).strip()
    if user_text:
        result.append({"role": "user", "content": user_text})

    if not result:
        result.append({"role": "user", "content": "..."})

    return result


def _convert_message_blocks(msg: Any) -> List[Dict[str, Any]]:
    """
    Convert a single Anthropic message to one or more OpenAI-format messages.
    A single Anthropic user message with tool_results may expand to multiple messages.
    """
    if isinstance(msg.content, str):
        content_text = msg.content if msg.content.strip() else "..."
        return [{"role": msg.role, "content": content_text}]

    if msg.role == "assistant":
        return _convert_assistant_blocks(msg.content)
    elif msg.role == "user":
        return _convert_user_blocks(msg.content)
    else:
        return [{"role": msg.role, "content": _content_blocks_to_text(msg.content)}]


def convert_anthropic_to_litellm(anthropic_request: MessagesRequest, model_context_window: int = 0) -> Dict[str, Any]:
    """
    Anthropic /v1/messages -> LiteLLM(OpenAI-style) request dict.
    """
    messages: List[Dict[str, Any]] = []

    system_text = _system_to_text(anthropic_request.system)
    if system_text:
        messages.append({"role": "system", "content": system_text})

    # conversation messages - properly convert tool_use/tool_result blocks
    for msg in anthropic_request.messages:
        messages.extend(_convert_message_blocks(msg))

    max_tokens = anthropic_request.max_tokens
    no_tools = is_no_tools_model(anthropic_request.model)

    # Cap max_tokens for openai/ non-reasoning models to avoid absurd requests.
    # Skip: gemini/ (1M context), no_tools/reasoning models (reasoning_content consumes output tokens).
    if (
        isinstance(anthropic_request.model, str)
        and anthropic_request.model.startswith("openai/")
        and not no_tools
    ):
        if model_context_window > 0:
            # Dynamic cap: only for providers with MODEL_CONTEXT_WINDOW set (e.g. DeepSeek 64K)
            # Groq, OpenAI, OpenRouter have MODEL_CONTEXT_WINDOW=0 → fall through to else
            
            provider_max = int(os.environ.get("MAX_OUTPUT_TOKENS", "8192"))
            input_estimate = sum(len(str(m.get("content", ""))) for m in messages) // 4
            tools_estimate = 0
            if anthropic_request.tools:
                for t in anthropic_request.tools:
                    tools_estimate += len(json.dumps(to_dict(t))) // 4
            remaining = model_context_window - input_estimate - tools_estimate
            safe_remaining = int(remaining * 0.85)  # 15% margin for overhead
            dynamic_cap = max(1024, min(safe_remaining, provider_max))
            max_tokens = min(max_tokens, dynamic_cap)
            print(f"[tokens] input~{input_estimate} tools~{tools_estimate} "
                  f"remaining~{remaining} cap={dynamic_cap} max_tokens={max_tokens}")
        else:
            # Providers WITHOUT MODEL_CONTEXT_WINDOW: identical to current behavior
            max_tokens = min(max_tokens, 16384)

    if no_tools:
        print(f"[no-tools] max_completion_tokens={max_tokens} (uncapped, reasoning model)")

    litellm_request: Dict[str, Any] = {
        "model": anthropic_request.model,
        "messages": messages,
        # litellm acepta max_tokens o max_completion_tokens según provider.
        # en tu server usabas max_completion_tokens; mantenemos por compat.
        "max_completion_tokens": max_tokens,
        "stream": anthropic_request.stream,
    }

    # reasoning models ignore temperature; strip to avoid potential API errors
    if not no_tools:
        litellm_request["temperature"] = anthropic_request.temperature

    if anthropic_request.stop_sequences:
        litellm_request["stop"] = anthropic_request.stop_sequences
    if anthropic_request.top_p is not None:
        litellm_request["top_p"] = anthropic_request.top_p
    if anthropic_request.top_k is not None:
        litellm_request["top_k"] = anthropic_request.top_k

    # tools -> OpenAI function tools (cached per tool name + schema hash)
    if anthropic_request.tools and not no_tools:
        is_gemini_model = anthropic_request.model.startswith("gemini/")
        openai_tools = []
        for tool in anthropic_request.tools:
            openai_tools.append(_convert_tool_cached(to_dict(tool), is_gemini_model))
        litellm_request["tools"] = openai_tools
    elif anthropic_request.tools and no_tools:
        # Inject tool definitions as XML prompt in system message
        tool_dicts = [to_dict(t) for t in anthropic_request.tools]
        tool_prompt = build_tool_prompt(tool_dicts)
        if messages and messages[0]["role"] == "system":
            messages[0]["content"] = tool_prompt + "\n\n" + messages[0]["content"]
        else:
            messages.insert(0, {"role": "system", "content": tool_prompt})
        # Rewrite history: tool_calls/tool_results → XML text
        litellm_request["messages"] = rewrite_messages_without_tools(messages)
        print(f"[no-tools] Injected {len(tool_dicts)} tools as XML prompt for {anthropic_request.model}")

    # tool_choice (Anthropic-style) -> OpenAI — skip for no-tools models
    if anthropic_request.tool_choice and not no_tools:
        tc = anthropic_request.tool_choice
        choice_type = tc.get("type") if isinstance(tc, dict) else None

        if choice_type == "auto":
            litellm_request["tool_choice"] = "auto"
        elif choice_type == "any":
            litellm_request["tool_choice"] = "required"
        elif choice_type == "tool" and isinstance(tc, dict) and "name" in tc:
            litellm_request["tool_choice"] = {"type": "function", "function": {"name": tc["name"]}}
        else:
            litellm_request["tool_choice"] = "auto"

    return litellm_request


def convert_litellm_to_anthropic(litellm_response: Union[Dict[str, Any], Any], original_request: MessagesRequest, model_context_window: int = 0) -> MessagesResponse:
    """
    LiteLLM(OpenAI-ish) response -> Anthropic /v1/messages response object
    """
    # Normalize response to dict for uniform extraction
    if isinstance(litellm_response, dict):
        resp = litellm_response
    else:
        try:
            resp = litellm_response.model_dump() if hasattr(litellm_response, "model_dump") else litellm_response.__dict__
        except Exception:
            resp = {}

    choices = resp.get("choices", [{}])
    message = choices[0].get("message", {}) if choices else {}
    content_text = message.get("content", "") if isinstance(message, dict) else ""
    reasoning_text = message.get("reasoning_content", "") if isinstance(message, dict) else ""
    tool_calls = message.get("tool_calls", None) if isinstance(message, dict) else None
    finish_reason = choices[0].get("finish_reason", "stop") if choices else "stop"
    usage_info = resp.get("usage", {})
    response_id = resp.get("id", f"msg_{uuid.uuid4()}")

    content: List[Dict[str, Any]] = []

    # reasoning_content (deepseek-reasoner): surface as text block
    if reasoning_text:
        content.append({"type": "text", "text": f"<reasoning>\n{reasoning_text}\n</reasoning>\n\n"})

    if content_text is not None and content_text != "":
        content.append({"type": "text", "text": content_text})

    # Always generate tool_use blocks in Anthropic format.
    # The proxy MUST return Anthropic-compatible responses to Claude Code
    # regardless of which upstream provider (Z.AI, Groq, Gemini, Ollama) generated them.
    if tool_calls:
        if not isinstance(tool_calls, list):
            tool_calls = [tool_calls]

        for tool_call in tool_calls:
            if isinstance(tool_call, dict):
                function = tool_call.get("function", {}) or {}
                raw_id = tool_call.get("id", "")
                tool_id = raw_id if raw_id.startswith(TOOL_ID_PREFIX) else make_tool_id()
                name = function.get("name", "")
                arguments = function.get("arguments", "{}")
            else:
                function = getattr(tool_call, "function", None)
                raw_id = getattr(tool_call, "id", "") or ""
                tool_id = raw_id if raw_id.startswith(TOOL_ID_PREFIX) else make_tool_id()
                name = getattr(function, "name", "") if function else ""
                arguments = getattr(function, "arguments", "{}") if function else "{}"

            if not name:
                print(f"[converters] WARNING: Skipping tool_call with empty name (id={raw_id})")
                continue

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

            content.append(
                {"type": "tool_use", "id": tool_id, "name": name, "input": arguments}
            )

    # Usage extraction
    if isinstance(usage_info, dict):
        prompt_tokens = usage_info.get("prompt_tokens", 0)
        completion_tokens = usage_info.get("completion_tokens", 0)
    else:
        prompt_tokens = getattr(usage_info, "prompt_tokens", 0)
        completion_tokens = getattr(usage_info, "completion_tokens", 0)

    # Extract XML tool calls from text response:
    # - No-tools models: always check (they use XML simulation)
    # - Native models: check when <tool_call present AND no native tool_calls (avoids dupes)
    has_native_tools = bool(tool_calls)
    should_extract = (
        is_no_tools_model(original_request.model)
        or (not has_native_tools and "<tool_call" in (content_text or ""))
    )
    if should_extract and content_text:
        request_tools = getattr(original_request, "tools", None)
        valid_names = _build_valid_tool_names(request_tools)
        xml_tool_blocks, clean_text = extract_tool_calls_from_text(content_text, valid_tool_names=valid_names, tools=request_tools)
        if xml_tool_blocks:
            # Rebuild content: clean text + tool_use blocks
            # OMIT reasoning_content when tool calls present — 5-15K tokens of reasoning
            # before tool_use blocks crashes CC's SSE parser
            content = []
            if reasoning_text:
                print(f"[no-tools] Suppressed {len(reasoning_text)} chars of reasoning_content (tool calls present)")
            clean_text = clean_text.strip()
            if clean_text:
                content.append({"type": "text", "text": clean_text})
            content.extend(xml_tool_blocks)
            finish_reason = "tool_calls"
            print(f"[no-tools] Extracted {len(xml_tool_blocks)} tool calls from text response")
        elif "<tool_call" in content_text:
            # Extraction failed but <tool_call present → try async recovery
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Already inside async context — create a task
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        recovered = pool.submit(
                            asyncio.run,
                            recover_incomplete_tool_call(
                                partial_xml=content_text,
                                tools=[to_dict(t) for t in request_tools] if request_tools else None,
                                model=os.environ.get("CLASSIFIER_MODEL", "openai/deepseek-chat"),
                                api_key=os.environ.get("CLASSIFIER_API_KEY", ""),
                                api_base=os.environ.get("CLASSIFIER_BASE_URL"),
                            )
                        ).result(timeout=5)
                else:
                    recovered = loop.run_until_complete(
                        recover_incomplete_tool_call(
                            partial_xml=content_text,
                            tools=[to_dict(t) for t in request_tools] if request_tools else None,
                            model=os.environ.get("CLASSIFIER_MODEL", "openai/deepseek-chat"),
                            api_key=os.environ.get("CLASSIFIER_API_KEY", ""),
                            api_base=os.environ.get("CLASSIFIER_BASE_URL"),
                        )
                    )
            except Exception as e:
                print(f"[recovery] Non-streaming recovery failed: {type(e).__name__}: {e}")
                recovered = None

            if recovered:
                if valid_names:
                    recovered = [tc for tc in recovered if validate_tool_name(tc.get("name", ""), valid_names)]
                if recovered:
                    content = []
                    clean = strip_tool_call_xml(content_text)
                    if clean:
                        content.append({"type": "text", "text": clean})
                    content.extend(recovered)
                    finish_reason = "tool_calls"
                    print(f"[recovery] Non-streaming: recovered {len(recovered)} tool(s)")
            else:
                # Level 3: strip XML from content text
                clean = strip_tool_call_xml(content_text)
                if clean != content_text:
                    content = [{"type": "text", "text": clean}] if clean else []
                    print(f"[recovery] Non-streaming fallback: stripped XML from content")

    # When native tool model has XML fragments in content (but extraction was skipped
    # because native tools were present), strip the XML to avoid showing raw tags to CC
    if has_native_tools and content_text and "<tool_call" in content_text:
        clean = strip_tool_call_xml(content_text)
        if clean != content_text:
            for i, block in enumerate(content):
                if isinstance(block, dict) and block.get("type") == "text":
                    content[i] = {"type": "text", "text": clean}
                    break
            print(f"[recovery] Non-streaming: stripped XML fragments from native-tool content")

    # finish_reason → stop_reason
    # Special case: finish_reason=length with tool_use blocks — only report "tool_use"
    # if all tool inputs are valid dicts (not corrupted JSON from truncation).
    has_tool_use = any(bget(c, "type") == "tool_use" for c in content)
    if finish_reason == "length" and has_tool_use:
        all_valid = all(
            isinstance(bget(c, "input"), dict)
            for c in content if bget(c, "type") == "tool_use"
        )
        stop_reason = "tool_use" if all_valid else "max_tokens"
    else:
        stop_reason = map_stop_reason(finish_reason, has_tool_use)

    if not content:
        content.append({"type": "text", "text": ""})

    
    return MessagesResponse(
        id=response_id,
        model=getattr(original_request, "original_model", None) or original_request.model,
        role="assistant",
        content=content,
        stop_reason=stop_reason,
        stop_sequence=None,
        usage=Usage(
            input_tokens=scale_tokens(prompt_tokens, model_context_window),
            output_tokens=scale_tokens(completion_tokens, model_context_window),
        ),
    )
