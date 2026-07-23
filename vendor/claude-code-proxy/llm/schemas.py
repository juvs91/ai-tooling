# llm/schemas.py
from __future__ import annotations

from dataclasses import dataclass
from pydantic import BaseModel, ConfigDict, field_validator
from typing import List, Dict, Any, Optional, Union, Literal


# ---------- Provider fallback chain ----------
@dataclass
class ProviderConfig:
    """Configuration for a single LLM provider (primary or fallback)."""
    name: str                        # "primary", "fallback_1", etc.
    provider_prefix: str             # "openai", "gemini", "anthropic"
    api_key: str
    big_model: str
    small_model: str
    base_url: str | None = None
    building_model: str | None = None
    context_window: int = 0

    def get_litellm_model(self, intent: str) -> str:
        """Return litellm-style 'prefix/model' string for the given intent."""
        building = self.building_model or self.big_model
        if intent == "CHAT" and self.small_model != self.big_model:
            model = self.small_model
        elif intent == "BUILDING" and building != self.big_model:
            model = building
        else:
            model = self.big_model
        # Strip only if model already starts with THIS provider's prefix to avoid double-prefix.
        # e.g. FALLBACK_1_BIG_MODEL=anthropic/glm-4.7 with provider_prefix=anthropic → anthropic/glm-4.7
        # Preserves multi-segment models like google/gemini-3-flash-preview for openrouter.
        if model.startswith(f"{self.provider_prefix}/"):
            bare_model = model[len(self.provider_prefix) + 1:]
        else:
            bare_model = model
        return f"{self.provider_prefix}/{bare_model}"

# ---------- Anthropic-style content blocks ----------
class ContentBlockText(BaseModel):
    type: Literal["text"]
    text: str

class ContentBlockImage(BaseModel):
    type: Literal["image"]
    source: Dict[str, Any]

class ContentBlockToolUse(BaseModel):
    type: Literal["tool_use"]
    id: str
    name: str
    input: Dict[str, Any]

class ContentBlockToolResult(BaseModel):
    type: Literal["tool_result"]
    tool_use_id: str
    content: Union[str, List[Dict[str, Any]], Dict[str, Any], List[Any], Any]
    is_error: Optional[bool] = None

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

class SystemContent(BaseModel):
    type: Literal["text"]
    text: str

class Message(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: Union[
        str,
        List[Union[
            ContentBlockText,
            ContentBlockImage,
            ContentBlockToolUse,
            ContentBlockToolResult,
            ContentBlockThinking,
            ContentBlockRedactedThinking,
            ContentBlockServerToolUse,
            ContentBlockServerToolResult,
        ]],
    ]

class Tool(BaseModel):
    """Anthropic tool definition. `input_schema` is optional and `type`/extra
    fields are preserved (not dropped) to support Anthropic server-side tools
    (web_search_20250305, etc.) which have no input_schema — Anthropic resolves
    them server-side. See ADR-0029."""
    model_config = ConfigDict(extra="allow")

    name: str
    description: Optional[str] = None
    input_schema: Optional[Dict[str, Any]] = None
    type: Optional[str] = None

class ThinkingConfig(BaseModel):
    enabled: bool = True

# ---------- Request/Response models ----------
class MessagesRequest(BaseModel):
    model: str
    max_tokens: int
    messages: List[Message]
    system: Optional[Union[str, List[SystemContent]]] = None
    stop_sequences: Optional[List[str]] = None
    stream: Optional[bool] = False
    temperature: Optional[float] = 1.0
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None
    tools: Optional[List[Tool]] = None
    tool_choice: Optional[Dict[str, Any]] = None
    thinking: Optional[ThinkingConfig] = None

    # nota: esto lo usabas para logging/mapping, lo dejamos por compat
    original_model: Optional[str] = None

    @field_validator("model")
    def preserve_original_model(cls, v, info):
        # guardamos el original (pydantic v2)
        data = info.data
        if isinstance(data, dict) and data.get("original_model") is None:
            data["original_model"] = v
        return v

class TokenCountRequest(BaseModel):
    model: str
    messages: List[Message]
    system: Optional[Union[str, List[SystemContent]]] = None
    tools: Optional[List[Tool]] = None
    thinking: Optional[ThinkingConfig] = None
    tool_choice: Optional[Dict[str, Any]] = None
    original_model: Optional[str] = None

    @field_validator("model")
    def preserve_original_model_token(cls, v, info):
        data = info.data
        if isinstance(data, dict) and data.get("original_model") is None:
            data["original_model"] = v
        return v

class TokenCountResponse(BaseModel):
    input_tokens: int

class Usage(BaseModel):
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

class MessagesResponse(BaseModel):
    id: str
    model: str
    role: Literal["assistant"] = "assistant"
    content: List[Union[ContentBlockText, ContentBlockToolUse]]
    type: Literal["message"] = "message"
    stop_reason: Optional[Literal["end_turn", "max_tokens", "stop_sequence", "tool_use"]] = None
    stop_sequence: Optional[str] = None
    usage: Usage
