# llm/schemas.py
from __future__ import annotations

from dataclasses import dataclass
from pydantic import BaseModel, field_validator
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
        return f"{self.provider_prefix}/{model}"

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
    role: Literal["user", "assistant"]
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
    name: str
    description: Optional[str] = None
    input_schema: Dict[str, Any]

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
