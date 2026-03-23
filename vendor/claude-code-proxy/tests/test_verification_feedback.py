# tests/test_verification_feedback.py
"""
Tests for factual verification feedback in quality refinement.

Verifies that the system can detect when a model mentions non-existent
files and provides specific feedback with alternatives.
"""
import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from llm.transformers.quality_refinement import _build_verification_feedback


@pytest.fixture
def temp_codebase():
    """Create a temporary codebase with known file structure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a realistic structure
        base = Path(tmpdir) / "vendor" / "test-project"
        base.mkdir(parents=True)

        # Create actual Python files
        (base / "server.py").write_text("""
@app.route('/api/data')
def get_data():
    return {"status": "ok"}
""")
        (base / "compressor.py").write_text("""
def compress(data):
    return zlib.compress(data.encode())
""")
        (base / "streaming.py").write_text("""
async def stream_response():
    yield chunk
""")
        (base / "__init__.py").write_text("")

        # Create a subdirectory FIRST, then create files inside it
        (base / "utils").mkdir(parents=True, exist_ok=True)
        (base / "utils" / "__init__.py").write_text("")
        (base / "utils" / "helpers.py").write_text("def helper(): pass")

        yield str(base)


class TestBuildVerificationFeedback:
    """Tests for _build_verification_feedback function."""

    @pytest.mark.asyncio
    async def test_no_feedback_when_no_file_mentions(self, temp_codebase):
        """No feedback when response doesn't mention files."""
        text = "The system is well-architected and follows best practices."
        feedback = await _build_verification_feedback(text, temp_codebase)
        assert feedback == []

    @pytest.mark.asyncio
    async def test_no_feedback_when_files_exist(self, temp_codebase):
        """No feedback when mentioned files actually exist."""
        text = "In server.py:3 the route is defined. compressor.py:5 has the compress function."
        feedback = await _build_verification_feedback(text, temp_codebase)
        assert feedback == []

    @pytest.mark.asyncio
    async def test_feedback_when_file_doesnt_exist(self, temp_codebase):
        """Feedback when mentioned file doesn't exist."""
        text = "The bug is in nonexistent.py:45. Another issue in missing.py:100."
        feedback = await _build_verification_feedback(text, temp_codebase)
        assert len(feedback) == 2
        assert "❌ 'nonexistent.py' no existe" in feedback[0]
        assert "❌ 'missing.py' no existe" in feedback[1]

    @pytest.mark.asyncio
    async def test_feedback_includes_alternatives(self, temp_codebase):
        """Feedback includes alternatives when similar files exist."""
        text = "The bug is in serber.py:45."  # Typo of "server.py"
        feedback = await _build_verification_feedback(text, temp_codebase)
        assert len(feedback) == 1
        assert "❌ 'serber.py' no existe" in feedback[0]
        # Should find server.py as similar alternative (fuzzy match for typo)
        assert "server.py" in feedback[0]

    @pytest.mark.asyncio
    async def test_feedback_guides_to_glob_when_no_alternatives(self, temp_codebase):
        """Feedback guides to Glob when no alternatives found."""
        text = "The bug is in totally_fake.py:123."
        feedback = await _build_verification_feedback(text, temp_codebase)
        assert len(feedback) == 1
        assert "❌ 'totally_fake.py' no existe" in feedback[0]
        assert "Glob primero" in feedback[0]

    @pytest.mark.asyncio
    async def test_limits_checks_to_5_files(self, temp_codebase):
        """Only checks first 5 file mentions to avoid spam."""
        # Mention 10 files
        text = " ".join([f"file{i}.py:{i}" for i in range(10)])
        feedback = await _build_verification_feedback(text, temp_codebase)
        # Should only generate feedback for the first 5 non-existent files
        # (or fewer if there are fewer unique paths)
        assert len(feedback) <= 5

    @pytest.mark.asyncio
    async def test_handles_duplicate_file_mentions(self, temp_codebase):
        """Doesn't generate duplicate feedback for same file."""
        text = "The bug is in fake.py:10 and also in fake.py:20."
        feedback = await _build_verification_feedback(text, temp_codebase)
        # Should only mention fake.py once
        fake_mentions = sum(1 for f in feedback if "fake.py" in f)
        assert fake_mentions == 1

    @pytest.mark.asyncio
    async def test_detects_wrong_extension(self, temp_codebase):
        """Detects when file exists but with different extension."""
        text = "In server.ts:45 there's a race condition."  # .ts vs .py
        feedback = await _build_verification_feedback(text, temp_codebase)
        assert len(feedback) == 1
        assert "❌ 'server.ts' no existe" in feedback[0]
        # Should suggest server.py as alternative
        assert "server.py" in feedback[0]

    @pytest.mark.asyncio
    async def test_handles_absolute_paths(self, temp_codebase):
        """Correctly handles absolute file paths."""
        text = "The issue is in /tmp/random.py:99."
        feedback = await _build_verification_feedback(text, temp_codebase)
        # /tmp/random.py likely doesn't exist, should get feedback
        assert len(feedback) >= 1

    @pytest.mark.asyncio
    async def test_multiple_formats_supported(self, temp_codebase):
        """Detects various file:line reference formats."""
        text = """
        Issues in: server.py:10, client.ts:20, utils/helper.go:30.
        """
        feedback = await _build_verification_feedback(text, temp_codebase)
        # server.py exists, so no feedback for it
        # client.ts and utils/helper.go don't exist
        filtered = [f for f in feedback if "server.py" not in f]
        assert len(filtered) >= 1  # At least one of the wrong files

    @pytest.mark.asyncio
    async def test_empty_response(self, temp_codebase):
        """Handles empty response gracefully."""
        feedback = await _build_verification_feedback("", temp_codebase)
        assert feedback == []

    @pytest.mark.asyncio
    async def test_code_with_common_path_patterns(self, temp_codebase):
        """Detects files in subdirectories correctly."""
        text = "The helper function in utils/helpers.py:15 is useful."
        # File exists at vendor/test-project/utils/helpers.py
        feedback = await _build_verification_feedback(text, temp_codebase)
        assert feedback == []  # File exists, no feedback

    @pytest.mark.asyncio
    async def test_wrong_subdirectory(self, temp_codebase):
        """Detects wrong subdirectory."""
        text = "The file in utils/wrong.py:50 has a bug."
        feedback = await _build_verification_feedback(text, temp_codebase)
        assert len(feedback) == 1
        assert "utils/wrong.py" in feedback[0]

    @pytest.mark.asyncio
    async def test_detects_any_extension_generic(self, temp_codebase):
        """Detects file refs with ANY extension (not just code files)."""
        # Create additional files with non-code extensions
        import os
        base = Path(temp_codebase)
        (base / "README.md").write_text("# Test")
        (base / "config.json").write_text("{}")
        (base / "notes.txt").write_text("test")

        # Test with markdown file
        text = "In README.md:10 there's info. Also config.json:5."
        feedback = await _build_verification_feedback(text, temp_codebase)
        # Files exist, no feedback
        assert len(feedback) == 0

        # Test with wrong non-code file
        text2 = "The issue is in MISSING.md:45."
        feedback2 = await _build_verification_feedback(text2, temp_codebase)
        assert len(feedback2) == 1
        assert "MISSING.md" in feedback2[0]
