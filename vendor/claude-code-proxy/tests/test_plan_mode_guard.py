"""Tests for PlanModeGuardTransformer (plan_mode_guard.py)"""
import pytest
from unittest.mock import MagicMock

from llm.transformers.plan_mode_guard import PlanModeGuardTransformer, _is_plan_file, _bash_has_write
from llm.pipeline import TransformContext


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------

class TestIsPlanFile:
    def test_standard_plan_path(self):
        assert _is_plan_file("/Users/jeguzman/.claude/plans/my-plan.md")

    def test_relative_plan_path(self):
        assert _is_plan_file(".claude/plans/feature-plan.md")

    def test_nested_under_project(self):
        assert _is_plan_file("/home/user/project/.claude/plans/fix.md")

    def test_source_file_not_plan(self):
        assert not _is_plan_file("src/api.ts")

    def test_config_file_not_plan(self):
        assert not _is_plan_file("pyproject.toml")

    def test_similar_but_wrong_path(self):
        assert not _is_plan_file(".claude/plans-backup/foo.md")

    def test_non_md_in_plans_dir(self):
        assert not _is_plan_file(".claude/plans/foo.txt")


class TestBashHasWrite:
    def test_redirect_write(self):
        has, pat = _bash_has_write("echo hello > file.py")
        assert has
        assert ">" in pat

    def test_append_redirect(self):
        has, pat = _bash_has_write("echo line >> file.txt")
        assert has

    def test_tee(self):
        has, _ = _bash_has_write("cat x | tee output.py")
        assert has

    def test_rm(self):
        has, _ = _bash_has_write("rm -rf dist/")
        assert has

    def test_git_commit(self):
        has, _ = _bash_has_write("git commit -m 'fix'")
        assert has

    def test_npm_install(self):
        has, _ = _bash_has_write("npm install express")
        assert has

    def test_git_log_is_safe(self):
        has, _ = _bash_has_write("git log --oneline -10")
        assert not has

    def test_ls_is_safe(self):
        has, _ = _bash_has_write("ls -la src/")
        assert not has

    def test_grep_is_safe(self):
        has, _ = _bash_has_write("grep -r 'pattern' src/")
        assert not has

    def test_cat_is_safe(self):
        has, _ = _bash_has_write("cat README.md")
        assert not has


# ---------------------------------------------------------------------------
# Transformer integration tests
# ---------------------------------------------------------------------------

def _make_ctx(plan_mode_active: bool) -> TransformContext:
    ctx = TransformContext()
    ctx.plan_mode_active = plan_mode_active
    return ctx


def _make_response(content: list) -> MagicMock:
    resp = MagicMock()
    resp.content = content
    return resp


def _tool_use(name: str, **inputs) -> dict:
    return {"type": "tool_use", "id": f"tu_{name}", "name": name, "input": inputs}


def _text_block(text: str) -> dict:
    return {"type": "text", "text": text}


@pytest.mark.asyncio
async def test_no_op_when_plan_mode_inactive():
    """When plan_mode_active=False, nothing is blocked."""
    transformer = PlanModeGuardTransformer()
    ctx = _make_ctx(plan_mode_active=False)
    original = [_tool_use("Edit", file_path="src/api.ts", old_string="x", new_string="y")]
    resp = _make_response(list(original))

    await transformer.transform(resp, ctx)

    assert resp.content == original  # unchanged


@pytest.mark.asyncio
async def test_edit_blocked_on_source_file():
    """Edit on a non-plan file is replaced with text block."""
    transformer = PlanModeGuardTransformer()
    ctx = _make_ctx(plan_mode_active=True)
    resp = _make_response([_tool_use("Edit", file_path="src/api.ts", old_string="x", new_string="y")])

    await transformer.transform(resp, ctx)

    assert len(resp.content) == 1
    block = resp.content[0]
    assert block["type"] == "text"
    assert "PLAN MODE" in block["text"]
    assert "src/api.ts" in block["text"]
    assert "ExitPlanMode" in block["text"]


@pytest.mark.asyncio
async def test_edit_allowed_on_plan_file():
    """Edit on a .claude/plans/*.md file is allowed."""
    transformer = PlanModeGuardTransformer()
    ctx = _make_ctx(plan_mode_active=True)
    original = [_tool_use("Edit", file_path="/Users/user/.claude/plans/my-plan.md",
                           old_string="x", new_string="y")]
    resp = _make_response(list(original))

    await transformer.transform(resp, ctx)

    assert resp.content == original  # unchanged — plan file allowed


@pytest.mark.asyncio
async def test_write_blocked_on_source_file():
    """Write on a non-plan file is blocked."""
    transformer = PlanModeGuardTransformer()
    ctx = _make_ctx(plan_mode_active=True)
    resp = _make_response([_tool_use("Write", file_path="lib/utils.py", content="code")])

    await transformer.transform(resp, ctx)

    assert resp.content[0]["type"] == "text"
    assert "PLAN MODE" in resp.content[0]["text"]


@pytest.mark.asyncio
async def test_write_allowed_on_plan_file():
    """Write to .claude/plans/*.md is allowed."""
    transformer = PlanModeGuardTransformer()
    ctx = _make_ctx(plan_mode_active=True)
    original = [_tool_use("Write", file_path=".claude/plans/new-plan.md", content="# Plan")]
    resp = _make_response(list(original))

    await transformer.transform(resp, ctx)

    assert resp.content == original


@pytest.mark.asyncio
async def test_bash_write_blocked():
    """Bash command with write pattern is blocked."""
    transformer = PlanModeGuardTransformer()
    ctx = _make_ctx(plan_mode_active=True)
    resp = _make_response([_tool_use("Bash", command="echo 'hello' > output.py")])

    await transformer.transform(resp, ctx)

    assert resp.content[0]["type"] == "text"
    assert "PLAN MODE" in resp.content[0]["text"]
    assert "ExitPlanMode" in resp.content[0]["text"]


@pytest.mark.asyncio
async def test_bash_read_allowed():
    """Read-only Bash commands pass through."""
    transformer = PlanModeGuardTransformer()
    ctx = _make_ctx(plan_mode_active=True)
    original = [_tool_use("Bash", command="git log --oneline -10")]
    resp = _make_response(list(original))

    await transformer.transform(resp, ctx)

    assert resp.content == original


@pytest.mark.asyncio
async def test_exit_plan_mode_always_allowed():
    """ExitPlanMode is never blocked."""
    transformer = PlanModeGuardTransformer()
    ctx = _make_ctx(plan_mode_active=True)
    original = [_tool_use("ExitPlanMode")]
    resp = _make_response(list(original))

    await transformer.transform(resp, ctx)

    assert resp.content == original


@pytest.mark.asyncio
async def test_read_tool_always_allowed():
    """Read tool passes through in plan mode."""
    transformer = PlanModeGuardTransformer()
    ctx = _make_ctx(plan_mode_active=True)
    original = [_tool_use("Read", file_path="src/api.ts")]
    resp = _make_response(list(original))

    await transformer.transform(resp, ctx)

    assert resp.content == original


@pytest.mark.asyncio
async def test_mixed_content_partial_block():
    """Only blocked tool_use is replaced; other blocks preserved."""
    transformer = PlanModeGuardTransformer()
    ctx = _make_ctx(plan_mode_active=True)
    resp = _make_response([
        _text_block("Let me read first..."),
        _tool_use("Read", file_path="src/api.ts"),
        _tool_use("Edit", file_path="src/api.ts", old_string="x", new_string="y"),
        _tool_use("Bash", command="git log --oneline"),
    ])

    await transformer.transform(resp, ctx)

    assert resp.content[0]["type"] == "text"
    assert resp.content[0]["text"] == "Let me read first..."
    assert resp.content[1]["type"] == "tool_use"  # Read: allowed
    assert resp.content[2]["type"] == "text"       # Edit: blocked → text
    assert "PLAN MODE" in resp.content[2]["text"]
    assert resp.content[3]["type"] == "tool_use"  # Bash read: allowed


@pytest.mark.asyncio
async def test_no_content_attribute():
    """Transformer is safe when response has no content."""
    transformer = PlanModeGuardTransformer()
    ctx = _make_ctx(plan_mode_active=True)
    resp = MagicMock(spec=[])  # no 'content' attribute

    # Should not raise
    await transformer.transform(resp, ctx)


@pytest.mark.asyncio
async def test_streaming_ctx_plan_mode_propagated():
    """Regression: streaming resp_ctx must propagate plan_mode_active (was missing in quality_refinement.py).
    Verify that plan_mode_active=True is honoured when the transformer is called
    with a ctx built the same way stream_response_pipeline builds resp_ctx."""
    from llm.pipeline import TransformContext

    transformer = PlanModeGuardTransformer()

    # Simulate how quality_refinement.py now builds resp_ctx for streaming
    original_ctx = TransformContext()
    original_ctx.plan_mode_active = True
    original_ctx.intent = "PLAN"

    resp_ctx = TransformContext(
        intent=original_ctx.intent,
        plan_mode_active=original_ctx.plan_mode_active,  # the fixed line
    )

    resp = _make_response([_tool_use("Edit", file_path="src/api.ts", old_string="x", new_string="y")])
    await transformer.transform(resp, resp_ctx)

    # Must be blocked — plan_mode_active was propagated correctly
    assert resp.content[0]["type"] == "text"
    assert "PLAN MODE" in resp.content[0]["text"]


@pytest.mark.asyncio
async def test_streaming_ctx_without_plan_mode_not_blocked():
    """Without plan_mode_active, streaming resp_ctx behaves same as non-streaming — no blocking."""
    from llm.pipeline import TransformContext

    transformer = PlanModeGuardTransformer()

    resp_ctx = TransformContext(
        intent="BUILD",
        plan_mode_active=False,
    )

    original = [_tool_use("Edit", file_path="src/api.ts", old_string="x", new_string="y")]
    resp = _make_response(list(original))
    await transformer.transform(resp, resp_ctx)

    assert resp.content == original  # unchanged


# ---------------------------------------------------------------------------
# Regression: Layer 1 must NOT re-activate plan mode after ExitPlanMode
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_intent_classifier_no_reactivation_after_exit_plan_mode():
    """Regression: 'implement the plan' after ExitPlanMode must NOT re-enter plan mode.

    'implement the plan' matches both PLANNING_RE and BUILDING_RE.
    The regex fallback returns PLAN (prefers PLAN on ambiguity).
    Layer 1 fix must detect ExitPlanMode in history and skip activation.
    """
    from types import SimpleNamespace
    from llm.transformers.intent_classifier import IntentClassifierTransformer
    from llm.pipeline import TransformContext
    from config import ClassifierConfig, PolicyConfig

    classifier = IntentClassifierTransformer(
        ClassifierConfig(model="", api_key="", base_url=None, timeout=3.0, max_consecutive_errors=3, circuit_reset_seconds=60.0),
        PolicyConfig(tool_allowlist_raw="*", policy_note_in_system=True,
                     max_input_tokens=0, hard_block_oversize=False,
                     analysis_enforcement=False, tool_upgrade_threshold=5,
                     guard_system=""),
        models_differ=False,
    )

    # History: plan was made and ExitPlanMode was called
    messages = [
        SimpleNamespace(role="user", content="do a deep plan for the feature"),
        SimpleNamespace(role="assistant", content=[
            SimpleNamespace(type="tool_use", name="EnterPlanMode", input={}),
        ]),
        SimpleNamespace(role="assistant", content=[
            SimpleNamespace(type="tool_use", name="Read", input={"file_path": "src/api.ts"}),
        ]),
        SimpleNamespace(role="assistant", content=[
            SimpleNamespace(type="tool_use", name="Write",
                            input={"file_path": "/Users/user/.claude/plans/feature.md",
                                   "content": "# Plan"}),
        ]),
        SimpleNamespace(role="assistant", content=[
            SimpleNamespace(type="tool_use", name="ExitPlanMode", input={}),
        ]),
        SimpleNamespace(role="user", content="implement the plan"),
    ]

    request = SimpleNamespace(
        messages=messages,
        system="",
        tools=[],
    )
    ctx = TransformContext()
    await classifier.transform(request, ctx)

    # plan_mode_active must be False — ExitPlanMode was called
    assert ctx.plan_mode_active is False, (
        f"plan_mode_active should be False after ExitPlanMode, got True. "
        f"intent={ctx.intent}"
    )


# ---------------------------------------------------------------------------
# Signal 3: Session cache — unlimited coverage for model-initiated plan mode
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_signal3_cache_fallback_for_long_session():
    """Signal 3: session cache covers plan mode when EnterPlanMode is beyond 60-msg window.

    Simulates a 70-message session where EnterPlanMode was called early (message 5)
    but is no longer in the 60-message scan window. Signal 0 would miss it.
    Signal 3 reads from the session cache and restores plan_mode_active=True.
    """
    from types import SimpleNamespace
    from llm.transformers.intent_classifier import IntentClassifierTransformer
    from llm.pipeline import TransformContext
    from config import ClassifierConfig, PolicyConfig
    from llm.compressor import set_session_plan_mode

    SESSION_ID = "test-signal3-long-session"

    # Seed the cache as if a prior turn already set plan_mode_active=True
    await set_session_plan_mode(SESSION_ID, True)

    classifier = IntentClassifierTransformer(
        ClassifierConfig(model="", api_key="", base_url=None, timeout=3.0, max_consecutive_errors=3, circuit_reset_seconds=60.0),
        PolicyConfig(tool_allowlist_raw="*", policy_note_in_system=True,
                     max_input_tokens=0, hard_block_oversize=False,
                     analysis_enforcement=False, tool_upgrade_threshold=5,
                     guard_system=""),
        models_differ=False,
    )

    # 70 messages: EnterPlanMode at position 5 (outside 60-msg window from end)
    messages = []
    for i in range(70):
        if i == 5:
            messages.append(SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="EnterPlanMode", input={}),
            ]))
        elif i == 69:
            messages.append(SimpleNamespace(role="user", content="keep reading files"))
        else:
            messages.append(SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read",
                                input={"file_path": f"src/file{i}.ts"}),
            ]))

    request = SimpleNamespace(messages=messages, system="", tools=[])
    ctx = TransformContext(session_id=SESSION_ID)
    await classifier.transform(request, ctx)

    assert ctx.plan_mode_active is True, (
        f"Signal 3 should have restored plan_mode_active=True from session cache "
        f"for a 70-message session. Got False. intent={ctx.intent}"
    )


@pytest.mark.asyncio
async def test_signal3_exit_plan_mode_clears_cache():
    """Signal 3: ExitPlanMode in recent history clears the session cache.

    Even if the cache says plan_mode_active=True (set from a prior turn),
    when ExitPlanMode appears in the last 60 messages the override must:
    1. Force plan_mode_active=False for this turn.
    2. Write False back to the session cache so future turns stay clear.
    """
    from types import SimpleNamespace
    from llm.transformers.intent_classifier import IntentClassifierTransformer
    from llm.pipeline import TransformContext
    from config import ClassifierConfig, PolicyConfig
    from llm.compressor import set_session_plan_mode, get_session_plan_mode

    SESSION_ID = "test-signal3-exit-clears"

    # Seed cache with True — simulates what was stored before ExitPlanMode was called
    await set_session_plan_mode(SESSION_ID, True)

    classifier = IntentClassifierTransformer(
        ClassifierConfig(model="", api_key="", base_url=None, timeout=3.0, max_consecutive_errors=3, circuit_reset_seconds=60.0),
        PolicyConfig(tool_allowlist_raw="*", policy_note_in_system=True,
                     max_input_tokens=0, hard_block_oversize=False,
                     analysis_enforcement=False, tool_upgrade_threshold=5,
                     guard_system=""),
        models_differ=False,
    )

    messages = [
        SimpleNamespace(role="assistant", content=[
            SimpleNamespace(type="tool_use", name="EnterPlanMode", input={}),
        ]),
        SimpleNamespace(role="assistant", content=[
            SimpleNamespace(type="tool_use", name="Write",
                            input={"file_path": "/Users/user/.claude/plans/plan.md",
                                   "content": "# Plan"}),
        ]),
        SimpleNamespace(role="assistant", content=[
            SimpleNamespace(type="tool_use", name="ExitPlanMode", input={}),
        ]),
        SimpleNamespace(role="user", content="implement the plan"),
    ]

    request = SimpleNamespace(messages=messages, system="", tools=[])
    ctx = TransformContext(session_id=SESSION_ID)
    await classifier.transform(request, ctx)

    # Must be False — ExitPlanMode is in recent history
    assert ctx.plan_mode_active is False, (
        f"ExitPlanMode override must clear plan_mode_active even when cache says True. "
        f"Got True. intent={ctx.intent}"
    )
    # Cache must also be cleared so future turns see False
    cached = await get_session_plan_mode(SESSION_ID)
    assert cached is False, (
        f"Session cache must be written False after ExitPlanMode override. Got {cached}"
    )


@pytest.mark.asyncio
async def test_signal3_no_session_id_skips_cache():
    """Signal 3 must NOT fire when session_id is empty (no X-Session-ID header).

    Ensures anonymous/headerless requests don't accidentally inherit another
    session's plan mode state.
    """
    from types import SimpleNamespace
    from llm.transformers.intent_classifier import IntentClassifierTransformer
    from llm.pipeline import TransformContext
    from config import ClassifierConfig, PolicyConfig
    from llm.compressor import set_session_plan_mode

    # Seed a different session with True — must not bleed into empty-session request
    await set_session_plan_mode("other-session-xyz", True)

    classifier = IntentClassifierTransformer(
        ClassifierConfig(model="", api_key="", base_url=None, timeout=3.0, max_consecutive_errors=3, circuit_reset_seconds=60.0),
        PolicyConfig(tool_allowlist_raw="*", policy_note_in_system=True,
                     max_input_tokens=0, hard_block_oversize=False,
                     analysis_enforcement=False, tool_upgrade_threshold=5,
                     guard_system=""),
        models_differ=False,
    )

    messages = [
        SimpleNamespace(role="user", content="read some files"),
    ]
    request = SimpleNamespace(messages=messages, system="", tools=[])
    # session_id="" — no header provided
    ctx = TransformContext(session_id="")
    await classifier.transform(request, ctx)

    assert ctx.plan_mode_active is False, (
        f"Signal 3 must not fire for empty session_id. Got plan_mode_active=True. "
        f"intent={ctx.intent}"
    )
