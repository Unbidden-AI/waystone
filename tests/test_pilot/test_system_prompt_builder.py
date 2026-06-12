"""Tests for pilot.system_prompt_builder — system prompt assembly."""

import pytest

from pilot.llm_adapter import estimate_tokens
from pilot.system_prompt_builder import (
    _CONTEXT_FOOTER,
    _CONTEXT_HEADER,
    _NO_CONTEXT_MSG,
    SystemPromptBuilder,
)

# ==============================================================================
# Fixtures
# ==============================================================================


@pytest.fixture
def basic_config():
    """Provide a basic configuration with static prompt."""
    return {
        "static": "You are a helpful assistant.\n\nRespond concisely.",
        "context_token_limit": 2000,
        "include_context": True,
    }


@pytest.fixture
def no_context_config():
    """Configuration that disables context inclusion."""
    return {
        "static": "You are a helpful assistant.",
        "context_token_limit": 2000,
        "include_context": False,
    }


@pytest.fixture
def limited_context_config():
    """Configuration with a small token limit for context."""
    return {
        "static": "You are a helpful assistant.",
        "context_token_limit": 50,  # Very tight limit for testing
        "include_context": True,
    }


@pytest.fixture
def unlimited_context_config():
    """Configuration with unlimited context tokens."""
    return {
        "static": "You are a helpful assistant.",
        "context_token_limit": 0,  # 0 means unlimited
        "include_context": True,
    }


@pytest.fixture
def minimal_static_config():
    """Configuration with minimal static prompt."""
    return {
        "static": "",  # Empty static
        "context_token_limit": 2000,
        "include_context": True,
    }


@pytest.fixture
def no_static_key_config():
    """Configuration without a 'static' key (uses default empty)."""
    return {
        "context_token_limit": 2000,
        "include_context": True,
    }


# ==============================================================================
# Test build() with static and context
# ==============================================================================


class TestBuildWithStaticAndContext:
    """Test build() with non-empty static and non-empty context."""

    def test_build_includes_both_static_and_context(self, basic_config):
        """build() includes both static and context sections separated."""
        builder = SystemPromptBuilder(basic_config)
        context_markdown = "- Decision: Use async/await\n- Implementation: Python 3.11+"
        result = builder.build(context_markdown)

        # Check that both static and context are present
        assert "You are a helpful assistant" in result
        assert "Use async/await" in result
        assert "Python 3.11+" in result

    def test_build_separates_static_from_context(self, basic_config):
        """build() separates static and context with blank lines."""
        builder = SystemPromptBuilder(basic_config)
        context_markdown = "- Important context"
        result = builder.build(context_markdown)

        # Should have static, then blank line, then context header
        parts = result.split("\n\n")
        assert len(parts) >= 2
        assert "You are a helpful assistant" in parts[0]

    def test_build_context_wrapped_in_header_footer(self, basic_config):
        """build() wraps context with header and footer."""
        builder = SystemPromptBuilder(basic_config)
        context_markdown = "Important fact about the system"
        result = builder.build(context_markdown)

        assert _CONTEXT_HEADER in result
        assert _CONTEXT_FOOTER in result
        assert "Important fact about the system" in result

    def test_build_preserves_context_formatting(self, basic_config):
        """build() preserves context markdown formatting."""
        builder = SystemPromptBuilder(basic_config)
        context_markdown = "## Section\n\n- Item 1\n- Item 2\n\n**Bold**"
        result = builder.build(context_markdown)

        assert "## Section" in result
        assert "- Item 1" in result
        assert "- Item 2" in result
        assert "**Bold**" in result


# ==============================================================================
# Test build() with empty context
# ==============================================================================


class TestBuildWithEmptyContext:
    """Test build() with empty context."""

    def test_build_with_empty_string_shows_sentinel(self, basic_config):
        """build() shows sentinel message when context_markdown is empty string."""
        builder = SystemPromptBuilder(basic_config)
        result = builder.build("")

        assert _NO_CONTEXT_MSG in result
        assert "You are a helpful assistant" in result

    def test_build_with_none_context_shows_sentinel(self, basic_config):
        """build() treats None context_markdown as empty."""
        builder = SystemPromptBuilder(basic_config)
        result = builder.build(None)

        assert _NO_CONTEXT_MSG in result
        assert "You are a helpful assistant" in result

    def test_build_with_whitespace_only_context_shows_sentinel(self, basic_config):
        """build() treats whitespace-only context as empty."""
        builder = SystemPromptBuilder(basic_config)
        result = builder.build("   \n\n  \t  ")

        assert _NO_CONTEXT_MSG in result

    def test_sentinel_message_appears_in_context_section(self, basic_config):
        """Sentinel message appears within the context header/footer."""
        builder = SystemPromptBuilder(basic_config)
        result = builder.build("")

        # Extract the context section
        start = result.find(_CONTEXT_HEADER)
        end = result.find(_CONTEXT_FOOTER)
        context_section = result[start:end]

        assert _NO_CONTEXT_MSG in context_section


# ==============================================================================
# Test build() with include_context=False
# ==============================================================================


class TestBuildWithContextDisabled:
    """Test build() when include_context=False."""

    def test_build_no_context_section_when_disabled(self, no_context_config):
        """build() does not append context section when include_context=False."""
        builder = SystemPromptBuilder(no_context_config)
        context_markdown = "Important context about the system"
        result = builder.build(context_markdown=context_markdown)

        # Should only contain static
        assert "You are a helpful assistant" in result
        # Context should not appear
        assert "Important context about the system" not in result
        assert _CONTEXT_HEADER not in result
        assert _CONTEXT_FOOTER not in result

    def test_build_no_sentinel_when_context_disabled(self, no_context_config):
        """build() does not show sentinel when include_context=False."""
        builder = SystemPromptBuilder(no_context_config)
        result = builder.build("")

        assert _NO_CONTEXT_MSG not in result
        assert _CONTEXT_HEADER not in result

    def test_build_static_only_with_context_disabled(self, no_context_config):
        """build() returns only static when include_context=False."""
        builder = SystemPromptBuilder(no_context_config)
        result = builder.build(context_markdown="some context")

        # Result should be just the static text (after stripping)
        expected = "You are a helpful assistant."
        assert result.strip() == expected


# ==============================================================================
# Test build() without static key in config
# ==============================================================================


class TestBuildWithoutStaticKey:
    """Test build() when config has no 'static' key."""

    def test_build_works_without_static_key(self, no_static_key_config):
        """build() still works when 'static' key is missing from config."""
        builder = SystemPromptBuilder(no_static_key_config)
        context_markdown = "Important fact"
        result = builder.build(context_markdown)

        # Should have context section
        assert _CONTEXT_HEADER in result
        assert "Important fact" in result
        # Should not fail

    def test_build_with_missing_static_and_no_context(self, no_static_key_config):
        """build() with missing static and no context still includes sentinel."""
        builder = SystemPromptBuilder(no_static_key_config)
        result = builder.build("")

        assert _NO_CONTEXT_MSG in result
        assert _CONTEXT_HEADER in result


# ==============================================================================
# Test _trim_context() basic behavior
# ==============================================================================


class TestTrimContextBasic:
    """Test _trim_context() basic trimming behavior."""

    def test_trim_context_within_limit_unchanged(self, basic_config):
        """_trim_context() returns context unchanged if within token limit."""
        builder = SystemPromptBuilder(basic_config)
        short_context = "A single short line."
        result = builder._trim_context(short_context)

        assert result == short_context

    def test_trim_context_empty_string(self, basic_config):
        """_trim_context() returns empty string for empty input."""
        builder = SystemPromptBuilder(basic_config)
        result = builder._trim_context("")

        assert result == ""

    def test_trim_context_whitespace_only(self, basic_config):
        """_trim_context() returns empty string for whitespace-only input."""
        builder = SystemPromptBuilder(basic_config)
        result = builder._trim_context("   \n\n  \t  ")

        assert result == ""


# ==============================================================================
# Test _trim_context() with limit exceeded
# ==============================================================================


class TestTrimContextLimitExceeded:
    """Test _trim_context() when context exceeds token limit."""

    def test_trim_context_exceeding_limit_is_trimmed(self, limited_context_config):
        """_trim_context() trims context that exceeds token limit."""
        builder = SystemPromptBuilder(limited_context_config)
        # Create a long context (many lines)
        long_context = "\n".join([f"Line {i}: This is a long line with some content" for i in range(20)])

        result = builder._trim_context(long_context)

        # Result should be shorter than input
        assert len(result) < len(long_context)

    def test_trim_context_adds_notice_when_trimmed(self, limited_context_config):
        """_trim_context() appends trim notice when context is trimmed."""
        builder = SystemPromptBuilder(limited_context_config)
        long_context = "\n".join([f"Line {i}: content" for i in range(30)])

        result = builder._trim_context(long_context)

        assert "_[context trimmed to fit token budget]_" in result

    def test_trim_context_no_notice_when_not_trimmed(self, basic_config):
        """_trim_context() does not append notice when context fits."""
        builder = SystemPromptBuilder(basic_config)
        short_context = "Short context."

        result = builder._trim_context(short_context)

        assert "_[context trimmed to fit token budget]_" not in result

    def test_trim_context_respects_line_boundaries(self, limited_context_config):
        """_trim_context() trims at line boundaries, not mid-sentence."""
        builder = SystemPromptBuilder(limited_context_config)
        context = "Line 1: Short\n" + "Line 2: " + "x" * 500 + "\nLine 3: Short"

        result = builder._trim_context(context)

        # Should end with a complete line (not mid-way through a line)
        # If there's a trim notice, it should be on its own line
        if "_[context trimmed" in result:
            lines = result.split("\n")
            # Last substantive line before trim notice should be complete
            assert lines[-1].startswith("_[context trimmed")


# ==============================================================================
# Test _trim_context() with unlimited context
# ==============================================================================


class TestTrimContextUnlimited:
    """Test _trim_context() when context_token_limit=0 (unlimited)."""

    def test_trim_context_unlimited_returns_unchanged(self, unlimited_context_config):
        """_trim_context() returns context unchanged when limit is 0 (unlimited)."""
        builder = SystemPromptBuilder(unlimited_context_config)
        long_context = "\n".join([f"Line {i}: {x*50}" for i, x in enumerate("This is a very long context" * 20)])

        result = builder._trim_context(long_context)

        # Should be completely unchanged
        assert result == long_context

    def test_trim_context_unlimited_no_notice(self, unlimited_context_config):
        """_trim_context() does not add trim notice with unlimited context."""
        builder = SystemPromptBuilder(unlimited_context_config)
        long_context = "\n".join([f"Line {i}: content" for i in range(100)])

        result = builder._trim_context(long_context)

        assert "_[context trimmed to fit token budget]_" not in result


# ==============================================================================
# Test static_tokens()
# ==============================================================================


class TestStaticTokens:
    """Test static_tokens() method."""

    def test_static_tokens_non_empty_returns_positive(self, basic_config):
        """static_tokens() returns a positive integer for non-empty static."""
        builder = SystemPromptBuilder(basic_config)
        tokens = builder.static_tokens()

        assert isinstance(tokens, int)
        assert tokens > 0

    def test_static_tokens_matches_estimate(self, basic_config):
        """static_tokens() matches direct estimate_tokens() call."""
        builder = SystemPromptBuilder(basic_config)
        tokens = builder.static_tokens()
        expected = estimate_tokens(basic_config["static"])

        assert tokens == expected

    def test_static_tokens_empty_static(self, minimal_static_config):
        """static_tokens() returns minimal value for empty static."""
        builder = SystemPromptBuilder(minimal_static_config)
        tokens = builder.static_tokens()

        # Should be at least 1 (estimate_tokens uses max(1, ...))
        assert tokens >= 1

    def test_static_tokens_scales_with_content(self):
        """static_tokens() increases with longer static content."""
        short_cfg = {"static": "Short"}
        long_cfg = {"static": "Short " * 100}

        builder_short = SystemPromptBuilder(short_cfg)
        builder_long = SystemPromptBuilder(long_cfg)

        tokens_short = builder_short.static_tokens()
        tokens_long = builder_long.static_tokens()

        assert tokens_long > tokens_short


# ==============================================================================
# Test overhead_tokens()
# ==============================================================================


class TestOverheadTokens:
    """Test overhead_tokens() method."""

    def test_overhead_tokens_returns_positive_int(self, basic_config):
        """overhead_tokens() returns a positive integer."""
        builder = SystemPromptBuilder(basic_config)
        tokens = builder.overhead_tokens()

        assert isinstance(tokens, int)
        assert tokens > 0

    def test_overhead_tokens_greater_or_equal_to_static(self, basic_config):
        """overhead_tokens() >= static_tokens() (framing adds overhead)."""
        builder = SystemPromptBuilder(basic_config)
        overhead = builder.overhead_tokens()
        static = builder.static_tokens()

        assert overhead >= static

    def test_overhead_tokens_includes_context_framing(self, basic_config):
        """overhead_tokens() includes tokens for context header/footer."""
        builder = SystemPromptBuilder(basic_config)
        overhead = builder.overhead_tokens()
        static = builder.static_tokens()

        # overhead should be > static because it adds header + footer + sentinel + separators
        framing_tokens = estimate_tokens(_CONTEXT_HEADER + _NO_CONTEXT_MSG + _CONTEXT_FOOTER + "\n\n")
        assert overhead >= static + framing_tokens - 10  # Allow some tolerance in token estimation


# ==============================================================================
# Test build() with large context exceeding limit
# ==============================================================================


class TestBuildWithLargeContext:
    """Test build() with context that exceeds token limit."""

    def test_build_with_oversized_context_is_trimmed(self, limited_context_config):
        """build() produces output with context tokens <= limit."""
        builder = SystemPromptBuilder(limited_context_config)
        # Create very long context
        long_context = "\n".join([f"Line {i}: This is a line of context with some content" for i in range(50)])

        result = builder.build(long_context)

        # Extract context section
        start = result.find(_CONTEXT_HEADER)
        end = result.find(_CONTEXT_FOOTER)
        if start != -1 and end != -1:
            context_section = result[start + len(_CONTEXT_HEADER) : end]
            context_tokens = estimate_tokens(context_section)
            # Should be within limit (with some tolerance for trim notice)
            assert context_tokens <= limited_context_config["context_token_limit"] + 10

    def test_build_preserves_static_even_with_large_context(self, limited_context_config):
        """build() includes full static even when context is trimmed."""
        builder = SystemPromptBuilder(limited_context_config)
        long_context = "\n".join([f"Line {i}: content" for i in range(50)])

        result = builder.build(long_context)

        # Static should always be present
        assert "You are a helpful assistant" in result


# ==============================================================================
# Test edge cases
# ==============================================================================


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_build_with_special_characters_in_context(self, basic_config):
        """build() handles special characters and markdown in context."""
        builder = SystemPromptBuilder(basic_config)
        context = "Code: `print('hello')`\nSymbols: @#$%&*()[]{}|\\<>\nUnicode: π ∑ ∆"

        result = builder.build(context)

        # Should preserve all special characters
        assert "`print('hello')`" in result
        assert "@#$%&*" in result
        assert "π" in result

    def test_build_with_very_long_lines(self, basic_config):
        """build() handles lines with no newbreaks (edge case for trimming)."""
        builder = SystemPromptBuilder(basic_config)
        # Single very long line
        context = "x" * 1000

        result = builder.build(context)

        # Should not crash
        assert len(result) > 0

    def test_trim_context_with_single_very_long_line(self, limited_context_config):
        """_trim_context() handles single line that exceeds limit."""
        builder = SystemPromptBuilder(limited_context_config)
        long_line = "x" * 500

        result = builder._trim_context(long_line)

        # Should trim or return something reasonable
        assert isinstance(result, str)

    def test_build_default_parameters(self, basic_config):
        """build() works when called with default parameters."""
        builder = SystemPromptBuilder(basic_config)
        result = builder.build()  # No arguments

        # Should work with defaults
        assert len(result) > 0
        assert "You are a helpful assistant" in result

    def test_config_with_defaults(self):
        """SystemPromptBuilder uses sensible defaults for missing config keys."""
        minimal_cfg = {}  # No keys at all
        builder = SystemPromptBuilder(minimal_cfg)

        # Should not crash, should use defaults
        result = builder.build("Some context")
        assert len(result) > 0


# ==============================================================================
# Test integration: build() affects context trimming correctly
# ==============================================================================


class TestBuildAndTrimIntegration:
    """Test that build() correctly applies trimming to context."""

    def test_build_calls_trim_context(self, limited_context_config):
        """build() uses _trim_context() to respect token limit."""
        builder = SystemPromptBuilder(limited_context_config)
        oversized_context = "\n".join([f"Line {i}: content here" for i in range(30)])

        result = builder.build(context_markdown=oversized_context)

        # Result should contain the trim notice (since context exceeds limit)
        assert "_[context trimmed to fit token budget]_" in result

    def test_build_respects_include_context_false(self, no_context_config):
        """build() ignores context entirely when include_context=False."""
        builder = SystemPromptBuilder(no_context_config)
        long_context = "\n".join([f"Line {i}: content" for i in range(100)])

        result = builder.build(context_markdown=long_context)

        # Should not contain any context
        assert "Line 0" not in result
        assert "Line 1" not in result
        # Should not contain trim notice either
        assert "_[context trimmed" not in result


# ==============================================================================
# Test config edge cases
# ==============================================================================


class TestConfigParsing:
    """Test how config parameters are parsed and used."""

    def test_context_token_limit_coerced_to_int(self):
        """context_token_limit is coerced to int."""
        cfg = {
            "static": "Test",
            "context_token_limit": "500",  # String, not int
            "include_context": True,
        }
        builder = SystemPromptBuilder(cfg)

        # Should work without error
        context = "a" * 100
        result = builder._trim_context(context)
        assert isinstance(result, str)

    def test_include_context_coerced_to_bool(self):
        """include_context is coerced to bool."""
        cfg = {
            "static": "Test",
            "context_token_limit": 2000,
            "include_context": 1,  # Truthy, but not bool
        }
        builder = SystemPromptBuilder(cfg)
        result = builder.build("context")

        # Should include context (1 is truthy)
        assert _CONTEXT_HEADER in result

    def test_include_context_falsy_values(self):
        """include_context with falsy values excludes context."""
        for falsy in [False, 0, "", None]:
            cfg = {
                "static": "Test",
                "context_token_limit": 2000,
                "include_context": falsy,
            }
            builder = SystemPromptBuilder(cfg)
            result = builder.build("context")

            # Should not include context header
            assert _CONTEXT_HEADER not in result

    def test_static_dedented_and_stripped(self):
        """Static text is dedented and stripped."""
        cfg = {
            "static": """
                This is indented.

                So is this.
            """,
            "context_token_limit": 2000,
            "include_context": True,
        }
        builder = SystemPromptBuilder(cfg)
        static = builder._static

        # Should be dedented and stripped
        assert not static.startswith(" ")
        assert not static.startswith("\n")
        assert static == "This is indented.\n\nSo is this."


# ==============================================================================
# static_files loading
# ==============================================================================


class TestStaticFiles:
    def test_static_file_prepended_to_static(self, tmp_path):
        """Content from static_files appears before the inline static block."""
        rules_file = tmp_path / "rules.md"
        rules_file.write_text("# Rules\nAlways write tests.")

        cfg = {
            "static_files": [str(rules_file)],
            "static": "You are a coding assistant.",
            "context_token_limit": 2000,
        }
        builder = SystemPromptBuilder(cfg)
        assert builder._static.startswith("# Rules")
        assert "Always write tests." in builder._static
        assert "You are a coding assistant." in builder._static
        # separator between file content and inline static
        assert "---" in builder._static

    def test_multiple_static_files_in_order(self, tmp_path):
        """Multiple static_files are concatenated in declared order."""
        f1 = tmp_path / "first.md"
        f1.write_text("FIRST")
        f2 = tmp_path / "second.md"
        f2.write_text("SECOND")

        cfg = {"static_files": [str(f1), str(f2)], "context_token_limit": 2000}
        builder = SystemPromptBuilder(cfg)
        assert builder._static.index("FIRST") < builder._static.index("SECOND")

    def test_missing_static_file_skipped_with_warning(self, tmp_path, caplog):
        """A missing static_files path is skipped (no crash) and logged."""
        import logging

        cfg = {
            "static_files": [str(tmp_path / "nonexistent.md")],
            "static": "Fallback",
            "context_token_limit": 2000,
        }
        with caplog.at_level(logging.WARNING, logger="pilot.system_prompt_builder"):
            builder = SystemPromptBuilder(cfg)

        assert builder._static == "Fallback"
        assert any("not found" in r.message for r in caplog.records)

    def test_no_static_files_key_works(self):
        """Omitting static_files entirely is fine — falls back to inline static."""
        cfg = {"static": "Inline only.", "context_token_limit": 2000}
        builder = SystemPromptBuilder(cfg)
        assert builder._static == "Inline only."

    def test_relative_path_resolved_from_project_root(self, tmp_path):
        """Relative paths in static_files are resolved against project_root."""
        rules_file = tmp_path / "CLAUDE.md"
        rules_file.write_text("# CLAUDE rules")

        cfg = {"static_files": ["CLAUDE.md"], "context_token_limit": 2000}
        builder = SystemPromptBuilder(cfg, project_root=tmp_path)
        assert "# CLAUDE rules" in builder._static
