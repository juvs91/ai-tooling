# llm/converters.py
from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional, Union

from llm.schemas import MessagesRequest, MessagesResponse, Usage


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
    for block in content:
        b = block
        btype = getattr(b, "type", None) if not isinstance(b, dict) else b.get("type")

        if btype == "text":
            txt = getattr(b, "text", None) if not isinstance(b, dict) else b.get("text")
            if txt:
                out.append(str(txt))
        elif btype == "tool_result":
            tool_id = getattr(b, "tool_use_id", None) if not isinstance(b, dict) else b.get("tool_use_id")
            out.append(f"[Tool Result ID: {tool_id or 'unknown'}]")
            nested = getattr(b, "content", None) if not isinstance(b, dict) else b.get("content")
            out.append(_content_blocks_to_text(nested))
        elif btype == "tool_use":
            name = getattr(b, "name", None) if not isinstance(b, dict) else b.get("name")
            tid = getattr(b, "id", None) if not isinstance(b, dict) else b.get("id")
            inp = getattr(b, "input", None) if not isinstance(b, dict) else b.get("input")
            try:
                inp_s = json.dumps(inp, ensure_ascii=False)
            except Exception:
                inp_s = str(inp)
            out.append(f"[Tool: {name} (ID: {tid})] Input: {inp_s}")
        elif btype == "thinking":
            # Extended thinking block - strip for non-Anthropic providers
            pass
        elif btype == "redacted_thinking":
            # Redacted thinking block - strip for non-Anthropic providers
            pass
        elif btype == "server_tool_use":
            name = getattr(b, "name", None) if not isinstance(b, dict) else b.get("name")
            tid = getattr(b, "id", None) if not isinstance(b, dict) else b.get("id")
            inp = getattr(b, "input", None) if not isinstance(b, dict) else b.get("input")
            try:
                inp_s = json.dumps(inp, ensure_ascii=False)
            except Exception:
                inp_s = str(inp)
            out.append(f"[ServerTool: {name} (ID: {tid})] Input: {inp_s}")
        elif btype == "server_tool_result":
            tool_id = getattr(b, "tool_use_id", None) if not isinstance(b, dict) else b.get("tool_use_id")
            out.append(f"[ServerTool Result ID: {tool_id or 'unknown'}]")
            nested = getattr(b, "content", None) if not isinstance(b, dict) else b.get("content")
            out.append(_content_blocks_to_text(nested))
        elif btype == "image":
            out.append("[Image content omitted]")
        else:
            try:
                out.append(json.dumps(b)[:1000])
            except Exception:
                out.append(str(b)[:1000])

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

    for block in blocks:
        b = block
        btype = getattr(b, "type", None) if not isinstance(b, dict) else b.get("type")

        if btype == "text":
            txt = getattr(b, "text", None) if not isinstance(b, dict) else b.get("text")
            if txt:
                text_parts.append(str(txt))

        elif btype == "tool_use":
            name = getattr(b, "name", None) if not isinstance(b, dict) else b.get("name")
            tid = getattr(b, "id", None) if not isinstance(b, dict) else b.get("id")
            inp = getattr(b, "input", None) if not isinstance(b, dict) else b.get("input")
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
            name = getattr(b, "name", None) if not isinstance(b, dict) else b.get("name")
            tid = getattr(b, "id", None) if not isinstance(b, dict) else b.get("id")
            inp = getattr(b, "input", None) if not isinstance(b, dict) else b.get("input")
            try:
                inp_s = json.dumps(inp, ensure_ascii=False)
            except Exception:
                inp_s = str(inp)
            text_parts.append(f"[ServerTool: {name} (ID: {tid})] Input: {inp_s}")

        else:
            try:
                text_parts.append(json.dumps(b if isinstance(b, dict) else (b.model_dump() if hasattr(b, "model_dump") else str(b)))[:1000])
            except Exception:
                text_parts.append(str(b)[:1000])

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

    for block in blocks:
        b = block
        btype = getattr(b, "type", None) if not isinstance(b, dict) else b.get("type")

        if btype == "text":
            txt = getattr(b, "text", None) if not isinstance(b, dict) else b.get("text")
            if txt:
                text_parts.append(str(txt))

        elif btype == "tool_result":
            tool_use_id = getattr(b, "tool_use_id", None) if not isinstance(b, dict) else b.get("tool_use_id")
            nested_content = getattr(b, "content", None) if not isinstance(b, dict) else b.get("content")
            content_str = _tool_result_content_to_str(nested_content)

            is_error = getattr(b, "is_error", None) if not isinstance(b, dict) else b.get("is_error")
            if is_error and content_str:
                content_str = f"[ERROR] {content_str}"

            tool_messages.append({
                "role": "tool",
                "tool_call_id": tool_use_id or "unknown",
                "content": content_str or "...",
            })

        elif btype == "server_tool_result":
            tool_id = getattr(b, "tool_use_id", None) if not isinstance(b, dict) else b.get("tool_use_id")
            nested = getattr(b, "content", None) if not isinstance(b, dict) else b.get("content")
            text_parts.append(f"[ServerTool Result ID: {tool_id or 'unknown'}]")
            text_parts.append(_content_blocks_to_text(nested))

        elif btype == "image":
            text_parts.append("[Image content omitted]")

        else:
            try:
                text_parts.append(json.dumps(b if isinstance(b, dict) else str(b))[:1000])
            except Exception:
                text_parts.append(str(b)[:1000])

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


def convert_anthropic_to_litellm(anthropic_request: MessagesRequest) -> Dict[str, Any]:
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
    # tu código anterior “cap 16384” lo hacías para openai/gemini;
    # ojo: esto no arregla TPM, pero evita requests absurdos.
    if isinstance(anthropic_request.model, str) and (
        anthropic_request.model.startswith("openai/") or anthropic_request.model.startswith("gemini/")
    ):
        max_tokens = min(max_tokens, 16384)

    litellm_request: Dict[str, Any] = {
        "model": anthropic_request.model,
        "messages": messages,
        # litellm acepta max_tokens o max_completion_tokens según provider.
        # en tu server usabas max_completion_tokens; mantenemos por compat.
        "max_completion_tokens": max_tokens,
        "temperature": anthropic_request.temperature,
        "stream": anthropic_request.stream,
    }

    if anthropic_request.stop_sequences:
        litellm_request["stop"] = anthropic_request.stop_sequences
    if anthropic_request.top_p is not None:
        litellm_request["top_p"] = anthropic_request.top_p
    if anthropic_request.top_k is not None:
        litellm_request["top_k"] = anthropic_request.top_k

    # tools -> OpenAI function tools
    if anthropic_request.tools:
        openai_tools = []
        is_gemini_model = anthropic_request.model.startswith("gemini/")

        for tool in anthropic_request.tools:
            tool_dict = tool.model_dump() if hasattr(tool, "model_dump") else (tool.dict() if hasattr(tool, "dict") else dict(tool))
            input_schema = tool_dict.get("input_schema", {}) or {}

            if is_gemini_model:
                input_schema = clean_gemini_schema(input_schema)

            openai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool_dict["name"],
                        "description": tool_dict.get("description", "") or "",
                        "parameters": input_schema,
                    },
                }
            )

        litellm_request["tools"] = openai_tools

    # tool_choice (Anthropic-style) -> OpenAI
    if anthropic_request.tool_choice:
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


def convert_litellm_to_anthropic(litellm_response: Union[Dict[str, Any], Any], original_request: MessagesRequest) -> MessagesResponse:
    """
    LiteLLM(OpenAI-ish) response -> Anthropic /v1/messages response object
    """
    # Extract response data from either ModelResponse object or dict
    if hasattr(litellm_response, "choices") and hasattr(litellm_response, "usage"):
        choices = litellm_response.choices
        message = choices[0].message if choices else None
        content_text = getattr(message, "content", "") if message else ""
        tool_calls = getattr(message, "tool_calls", None) if message else None
        finish_reason = getattr(choices[0], "finish_reason", "stop") if choices else "stop"
        usage_info = litellm_response.usage
        response_id = getattr(litellm_response, "id", f"msg_{uuid.uuid4()}")
    else:
        resp = litellm_response
        if not isinstance(resp, dict):
            try:
                resp = resp.model_dump() if hasattr(resp, "model_dump") else resp.__dict__
            except Exception:
                resp = {}
        choices = resp.get("choices", [{}])
        message = choices[0].get("message", {}) if choices else {}
        content_text = message.get("content", "") if isinstance(message, dict) else ""
        tool_calls = message.get("tool_calls", None) if isinstance(message, dict) else None
        finish_reason = choices[0].get("finish_reason", "stop") if choices else "stop"
        usage_info = resp.get("usage", {})
        response_id = resp.get("id", f"msg_{uuid.uuid4()}")

    content: List[Dict[str, Any]] = []
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
                tool_id = tool_call.get("id", f"toolu_{uuid.uuid4().hex[:24]}")
                name = function.get("name", "")
                arguments = function.get("arguments", "{}")
            else:
                function = getattr(tool_call, "function", None)
                tool_id = getattr(tool_call, "id", f"toolu_{uuid.uuid4().hex[:24]}")
                name = getattr(function, "name", "") if function else ""
                arguments = getattr(function, "arguments", "{}") if function else "{}"

            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
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

    # finish_reason -> stop_reason
    if finish_reason == "length":
        stop_reason = "max_tokens"
    elif finish_reason == "tool_calls":
        stop_reason = "tool_use"
    else:
        stop_reason = "end_turn"

    if not content:
        content.append({"type": "text", "text": ""})

    return MessagesResponse(
        id=response_id,
        model=original_request.model,
        role="assistant",
        content=content,
        stop_reason=stop_reason,
        stop_sequence=None,
        usage=Usage(input_tokens=prompt_tokens, output_tokens=completion_tokens),
    )
