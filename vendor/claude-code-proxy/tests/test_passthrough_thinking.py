# tests/test_passthrough_thinking.py
"""Tests for model-agnostic passthrough thinking parameter injection."""
from types import SimpleNamespace

import pytest

from llm.passthrough import build_passthrough_body


class DummyRequest:
    """Minimal Anthropic-format request stand-in."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def _req():
    return DummyRequest(max_tokens=4096, messages=[{"role": "user", "content": "hi"}])


def test_injects_thinking_for_kimi_for_coding():
    pt = {"kimi-for-coding": {"thinking": {"type": "enabled", "budget_tokens": 65536}}}
    body = build_passthrough_body(_req(), "kimi-for-coding", passthrough_thinking=pt)
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 65536}


def test_injects_thinking_for_k3():
    pt = {"k3": {"thinking": {"type": "enabled", "budget_tokens": 65536}}}
    body = build_passthrough_body(_req(), "k3", passthrough_thinking=pt)
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 65536}


def test_strips_provider_prefix_for_lookup():
    pt = {"kimi-for-coding": {"thinking": {"type": "enabled", "budget_tokens": 123}}}
    body = build_passthrough_body(_req(), "anthropic/kimi-for-coding", passthrough_thinking=pt)
    assert body["thinking"]["budget_tokens"] == 123


def test_no_injection_for_unlisted_model():
    pt = {"kimi-for-coding": {"thinking": {"type": "enabled", "budget_tokens": 65536}}}
    body = build_passthrough_body(_req(), "some-other-model", passthrough_thinking=pt)
    assert "thinking" not in body


def test_no_injection_when_passthrough_thinking_is_none():
    body = build_passthrough_body(_req(), "kimi-for-coding")
    assert "thinking" not in body


def test_analysis_thinking_overrides_passthrough_thinking():
    pt = {"kimi-for-coding": {"thinking": {"type": "enabled", "budget_tokens": 65536}}}
    analysis = {"thinking": {"type": "enabled", "budget_tokens": 99999}}
    body = build_passthrough_body(
        _req(),
        "kimi-for-coding",
        analysis_phase="ANALYZING",
        analysis_thinking=analysis,
        passthrough_thinking=pt,
    )
    assert body["thinking"]["budget_tokens"] == 99999


def test_passthrough_thinking_applies_to_non_analysis_phase():
    pt = {"kimi-for-coding": {"thinking": {"type": "enabled", "budget_tokens": 65536}}}
    body = build_passthrough_body(
        _req(),
        "kimi-for-coding",
        analysis_phase="DEFAULT",
        analysis_thinking={"thinking": {"type": "enabled", "budget_tokens": 111}},
        passthrough_thinking=pt,
    )
    # Passthrough thinking applies in all phases; analysis thinking only in ANALYZING/READ/SYNTHESIZING.
    assert body["thinking"]["budget_tokens"] == 65536


def test_analysis_thinking_only_in_analysis_phases():
    analysis = {"thinking": {"type": "enabled", "budget_tokens": 111}}
    for phase in ("ANALYZING", "READ", "SYNTHESIZING"):
        body = build_passthrough_body(_req(), "kimi-for-coding", analysis_phase=phase, analysis_thinking=analysis)
        assert body["thinking"]["budget_tokens"] == 111

    body = build_passthrough_body(_req(), "kimi-for-coding", analysis_phase="DEFAULT", analysis_thinking=analysis)
    assert "thinking" not in body


def test_model_agnostic_merge_does_not_hardcode_kimi():
    # Any model key works; the function has no Kimi-specific logic.
    pt = {"custom-model": {"foo": "bar"}}
    body = build_passthrough_body(_req(), "custom-model", passthrough_thinking=pt)
    assert body["foo"] == "bar"
    assert "thinking" not in body


def test_preserves_other_body_fields():
    req = DummyRequest(
        max_tokens=8192,
        temperature=0.5,
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"name": "Read"}],
    )
    pt = {"kimi-for-coding": {"thinking": {"type": "enabled", "budget_tokens": 65536}}}
    body = build_passthrough_body(req, "kimi-for-coding", passthrough_thinking=pt)
    assert body["model"] == "kimi-for-coding"
    assert body["max_tokens"] == 8192
    assert body["temperature"] == 0.5
    assert body["system"] == "sys"
    assert body["tools"] == [{"name": "Read"}]
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 65536}


def test_system_list_is_converted():
    class SysItem:
        def dict(self):
            return {"type": "text", "text": "sys"}

    req = DummyRequest(max_tokens=4096, system=[SysItem()], messages=[])
    body = build_passthrough_body(req, "kimi-for-coding")
    assert body["system"] == [{"type": "text", "text": "sys"}]


def test_tools_list_is_converted():
    class ToolItem:
        def dict(self):
            return {"name": "Read"}

    req = DummyRequest(max_tokens=4096, messages=[], tools=[ToolItem()])
    body = build_passthrough_body(req, "kimi-for-coding")
    assert body["tools"] == [{"name": "Read"}]
