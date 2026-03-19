"""Tests for orchestrator/conversation.py."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from orchestrator.conversation import (
    Conversation,
    _format_tool_summary,
    _truncate_args,
)
from orchestrator.types import (
    CompactionResult,
    CompactionTrigger,
    Message,
    ToolCall,
    ToolResult,
)


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def mock_config():
    """Return a minimal config dict."""
    return {
        "orchestrator": {
            "context": {
                "window_size": 20,
                "token_budget": 8000,
                "compaction_batch": 10,
                "idle_seconds_before_compact": 600,
                "token_trigger_ratio": 0.8,
                "retrieve": {
                    "hops": 2,
                    "top_k": 20,
                    "strategies": {"superseded_pruning": True},
                },
            },
            "system_prompt": {
                "static": "You are a helpful assistant.",
                "include_context": True,
                "context_token_limit": 2000,
            },
            "llm": {
                "model": "gpt-4",
                "temperature": 0.7,
                "max_tokens": 4096,
            },
            "tools": {
                "enabled": ["bash", "read_file"],
            },
            "retry": {},
        },
    }


@pytest.fixture
def mock_store():
    """Return a mock GraphStore."""
    store = MagicMock()
    return store


@pytest.fixture
def conversation(mock_config, mock_store):
    """Return a Conversation instance with mocked dependencies."""
    with patch("orchestrator.conversation.ContextManager") as mock_ctx_cls, \
         patch("orchestrator.conversation.SystemPromptBuilder") as mock_spb_cls:

        # Mock ContextManager with both sync and async methods
        mock_ctx = MagicMock()
        mock_ctx.retrieve_context = MagicMock(return_value="## Project Knowledge\n\nSome context")
        mock_ctx.add_message = MagicMock()
        mock_ctx.compact_if_needed = AsyncMock(return_value=None)
        mock_ctx.get_history = MagicMock(return_value=[])
        mock_ctx.stats = MagicMock(return_value={
            "project": "test_project",
            "history_depth": 2,
            "total_tokens": 100,
        })
        mock_ctx._history = []
        mock_ctx._total_tokens = 0
        mock_ctx_cls.return_value = mock_ctx

        # Mock SystemPromptBuilder
        mock_spb = MagicMock()
        mock_spb.build.return_value = "System prompt with context"
        mock_spb_cls.return_value = mock_spb

        conversation = Conversation(
            cfg=mock_config,
            store=mock_store,
            project_name="test_project",
        )

        # Attach mocks for assertions
        conversation._context_mgr = mock_ctx
        conversation._prompt_builder = mock_spb

        return conversation


# ===========================================================================
# Tests: chat() method
# ===========================================================================


@pytest.mark.asyncio
async def test_chat_happy_path(conversation):
    """Test chat() with a normal reply (no tool calls)."""
    with patch("orchestrator.conversation.call_llm") as mock_llm:
        mock_llm.return_value = ("Hello, I can help!", None, "stop")

        # Mock touch() to avoid AsyncMock issues
        conversation._context_mgr.touch = MagicMock()

        reply = await conversation.chat("What can you help with?")

        assert reply == "Hello, I can help!"

        # Verify context retrieval
        conversation._context_mgr.retrieve_context.assert_called_once_with("What can you help with?")

        # Verify system prompt building
        conversation._prompt_builder.build.assert_called_once()

        # Verify user message was added
        conversation._context_mgr.add_message.assert_called()
        calls = conversation._context_mgr.add_message.call_args_list
        # First call should be user message
        assert calls[0][0][0].role == "user"
        assert calls[0][0][0].content == "What can you help with?"

        # Second call should be assistant message
        assert calls[1][0][0].role == "assistant"
        assert calls[1][0][0].content == "Hello, I can help!"


@pytest.mark.asyncio
async def test_chat_with_compaction(conversation):
    """Test chat() logs compaction info when compaction occurs."""
    with patch("orchestrator.conversation.call_llm") as mock_llm, \
         patch("orchestrator.conversation.log") as mock_log:

        mock_llm.return_value = ("Response", None, "stop")

        # Mock compaction result
        compaction_result = CompactionResult(
            nodes_extracted=5,
            messages_removed=3,
            tokens_freed=150,
            trigger=CompactionTrigger.TOKEN_BUDGET,
        )
        conversation._context_mgr.compact_if_needed.return_value = compaction_result
        conversation._context_mgr.touch = MagicMock()

        reply = await conversation.chat("Test message")

        assert reply == "Response"

        # Verify compaction was logged
        mock_log.info.assert_called_once()
        log_call = mock_log.info.call_args
        assert "Compacted" in str(log_call)


@pytest.mark.asyncio
async def test_chat_empty_reply(conversation):
    """Test chat() with empty LLM reply."""
    with patch("orchestrator.conversation.call_llm") as mock_llm:
        mock_llm.return_value = ("", None, "stop")
        conversation._context_mgr.touch = MagicMock()

        reply = await conversation.chat("Test message")

        assert reply == ""


@pytest.mark.asyncio
async def test_chat_with_no_context(conversation):
    """Test chat() when no context is retrieved."""
    with patch("orchestrator.conversation.call_llm") as mock_llm:
        conversation._context_mgr.retrieve_context.return_value = ""
        mock_llm.return_value = ("Response", None, "stop")
        conversation._context_mgr.touch = MagicMock()

        reply = await conversation.chat("Test message")

        assert reply == "Response"


# ===========================================================================
# Tests: chat_stream() method
# ===========================================================================


@pytest.mark.asyncio
async def test_chat_stream_yields_chunks(conversation):
    """chat_stream() with tools: LLM returns text on round 1, yielded inline as chunks."""
    # conversation fixture has tools enabled — goes through call_llm first
    with patch("orchestrator.conversation.call_llm") as mock_llm, \
         patch("orchestrator.conversation.stream_llm") as mock_stream:
        mock_llm.return_value = ("Hello world", None, "stop")

        chunks = []
        async for chunk in conversation.chat_stream("Test"):
            chunks.append(chunk)

        assert "".join(chunks) == "Hello world"
        mock_stream.assert_not_called()  # no double call when text returned directly


@pytest.mark.asyncio
async def test_chat_stream_no_tools_uses_stream_llm():
    """chat_stream() without tools calls stream_llm directly (not call_llm) for best TTFT."""
    cfg = {
        "orchestrator": {
            "context": {},
            "system_prompt": {},
            "llm": {"model": "gpt-4"},
            "tools": {"enabled": []},  # no tools
            "retry": {},
        }
    }
    called = []

    async def fake_stream_llm(messages, system, cfg_arg):
        called.append(True)
        yield "streamed reply"

    with patch("orchestrator.conversation.ContextManager") as mock_ctx_cls, \
         patch("orchestrator.conversation.SystemPromptBuilder") as mock_spb_cls, \
         patch("orchestrator.conversation.stream_llm", new=fake_stream_llm), \
         patch("orchestrator.conversation.call_llm") as mock_llm:

        mock_ctx = MagicMock()
        mock_ctx.retrieve_context = MagicMock(return_value="")
        mock_ctx.add_message = MagicMock()
        mock_ctx.compact_if_needed = AsyncMock(return_value=None)
        mock_ctx.get_history = MagicMock(return_value=[])
        mock_ctx_cls.return_value = mock_ctx
        mock_spb_cls.return_value.build.return_value = "system"

        conv = Conversation(cfg=cfg, store=MagicMock(), project_name="test")
        chunks = []
        async for chunk in conv.chat_stream("User input"):
            chunks.append(chunk)

        assert called, "stream_llm should have been called"
        mock_llm.assert_not_called()
        assert "".join(chunks) == "streamed reply"


@pytest.mark.asyncio
async def test_chat_stream_with_tools_no_tool_calls_yields_inline(conversation):
    """chat_stream() with tools: if LLM returns text on round 1, yield directly (no double call)."""
    with patch("orchestrator.conversation.call_llm") as mock_llm, \
         patch("orchestrator.conversation.stream_llm") as mock_stream:
        mock_llm.return_value = ("direct reply", None, "stop")

        chunks = []
        async for chunk in conversation.chat_stream("User input"):
            chunks.append(chunk)

        mock_llm.assert_called_once()
        mock_stream.assert_not_called()
        assert "".join(chunks) == "direct reply"


# ===========================================================================
# Tests: reset() method
# ===========================================================================


def test_reset_clears_history():
    """Test reset() delegates to ContextManager.reset()."""
    with patch("orchestrator.conversation.ContextManager") as mock_ctx_cls, \
         patch("orchestrator.conversation.SystemPromptBuilder"):
        cfg = {
            "orchestrator": {
                "context": {},
                "system_prompt": {},
                "llm": {},
                "tools": {"enabled": []},
                "retry": {},
            }
        }
        mock_ctx = MagicMock()
        mock_ctx_cls.return_value = mock_ctx

        store = MagicMock()
        conversation = Conversation(cfg, store, "test")

        conversation.reset()

        mock_ctx.reset.assert_called_once()


# ===========================================================================
# Tests: stats() method
# ===========================================================================


def test_stats_delegates_to_context_mgr():
    """Test stats() returns context manager stats."""
    with patch("orchestrator.conversation.ContextManager"), \
         patch("orchestrator.conversation.SystemPromptBuilder"):
        cfg = {
            "orchestrator": {
                "context": {},
                "system_prompt": {},
                "llm": {},
                "tools": {"enabled": []},
                "retry": {},
            }
        }
        store = MagicMock()
        conversation = Conversation(cfg, store, "test")

        expected_stats = {
            "project": "test",
            "history_depth": 5,
            "total_tokens": 500,
        }
        conversation._context_mgr.stats.return_value = expected_stats

        result = conversation.stats()

        assert result == expected_stats
        conversation._context_mgr.stats.assert_called_once()


# ===========================================================================
# Tests: _llm_loop() method
# ===========================================================================


@pytest.mark.asyncio
async def test_llm_loop_text_reply_no_tools(conversation):
    """Test _llm_loop() returns text when finish_reason != 'tool_calls'."""
    with patch("orchestrator.conversation.call_llm") as mock_llm:
        mock_llm.return_value = ("Final reply", None, "stop")
        conversation._context_mgr.get_history.return_value = [
            Message(role="user", content="Test")
        ]
        conversation._context_mgr.touch = MagicMock()

        reply = await conversation._llm_loop("System prompt")

        assert reply == "Final reply"
        mock_llm.assert_called_once()


@pytest.mark.asyncio
async def test_llm_loop_executes_tool_calls(conversation):
    """Test _llm_loop() executes tool calls and feeds results back."""
    with patch("orchestrator.conversation.call_llm") as mock_llm, \
         patch("orchestrator.conversation.execute_tools") as mock_execute:

        # First call: tool call request
        tool_call = ToolCall(id="tc1", name="bash", args={"command": "ls"})
        mock_llm.side_effect = [
            (None, [tool_call], "tool_calls"),  # First round
            ("Final reply", None, "stop"),  # After tool execution
        ]

        tool_result = ToolResult(
            tool_call_id="tc1",
            name="bash",
            output="file1.txt\nfile2.txt",
            error=None,
        )
        mock_execute.return_value = [tool_result]

        conversation._context_mgr.get_history.return_value = [
            Message(role="user", content="List files")
        ]
        conversation._context_mgr.touch = MagicMock()

        reply = await conversation._llm_loop("System prompt")

        assert reply == "Final reply"
        assert mock_llm.call_count == 2
        mock_execute.assert_called_once()


@pytest.mark.asyncio
async def test_llm_loop_max_tool_rounds(conversation):
    """Test _llm_loop() stops after _MAX_TOOL_ROUNDS and requests final answer."""
    with patch("orchestrator.conversation.call_llm") as mock_llm, \
         patch("orchestrator.conversation.execute_tools") as mock_execute:

        # Return tool calls on every round until we hit max (_MAX_TOOL_ROUNDS = 4)
        tool_call = ToolCall(id="tc1", name="bash", args={"command": "test"})
        mock_llm.side_effect = [
            (None, [tool_call], "tool_calls"),  # Rounds 1-4
        ] * 4 + [
            ("Final answer after max rounds", None, "stop"),  # Round 5 (after max)
        ]

        tool_result = ToolResult(tool_call_id="tc1", name="bash", output="result", error=None)
        mock_execute.return_value = [tool_result]

        conversation._context_mgr.get_history.return_value = [
            Message(role="user", content="Test")
        ]
        conversation._context_mgr.touch = MagicMock()

        reply = await conversation._llm_loop("System prompt")

        # Should have made max_rounds + 1 call
        assert mock_llm.call_count == 5
        # Last call should have tools=None
        last_call = mock_llm.call_args_list[-1]
        assert last_call[1]["tools"] is None


@pytest.mark.asyncio
async def test_llm_loop_empty_text_returns_empty_string(conversation):
    """Test _llm_loop() returns empty string when LLM returns None."""
    with patch("orchestrator.conversation.call_llm") as mock_llm:
        mock_llm.return_value = (None, None, "stop")
        conversation._context_mgr.get_history.return_value = []
        conversation._context_mgr.touch = MagicMock()

        reply = await conversation._llm_loop("System prompt")

        assert reply == ""


@pytest.mark.asyncio
async def test_llm_loop_no_tool_calls_on_final_round(conversation):
    """Test that _llm_loop() doesn't process empty tool_calls list."""
    with patch("orchestrator.conversation.call_llm") as mock_llm, \
         patch("orchestrator.conversation.execute_tools") as mock_execute:

        # finish_reason is "tool_calls" but tool_calls is empty list
        mock_llm.return_value = ("Some text", [], "tool_calls")
        conversation._context_mgr.get_history.return_value = []
        conversation._context_mgr.touch = MagicMock()

        reply = await conversation._llm_loop("System prompt")

        # Should return immediately, not execute empty list
        assert reply == "Some text"
        mock_execute.assert_not_called()


# ===========================================================================
# Tests: Helper functions
# ===========================================================================


def test_truncate_args_short_values():
    """Test _truncate_args() with short argument values."""
    args = {"command": "ls", "timeout": 30}
    result = _truncate_args(args)

    assert "command='ls'" in result
    assert "timeout=" in result and "30" in result


def test_truncate_args_long_values():
    """Test _truncate_args() truncates long values at 60 chars."""
    long_value = "x" * 100
    args = {"data": long_value}
    result = _truncate_args(args)

    assert "…" in result
    assert len(result) < len(long_value)


def test_truncate_args_custom_max_len():
    """Test _truncate_args() with custom max_len."""
    args = {"text": "Hello world"}
    result = _truncate_args(args, max_len=5)

    assert "…" in result


def test_truncate_args_empty():
    """Test _truncate_args() with empty args."""
    result = _truncate_args({})
    assert result == ""


def test_format_tool_summary_success():
    """Test _format_tool_summary() with successful tool calls."""
    tool_calls = [
        ToolCall(id="tc1", name="bash", args={"command": "ls"}),
        ToolCall(id="tc2", name="read_file", args={"path": "/etc/hosts"}),
    ]
    results = [
        ToolResult(tool_call_id="tc1", name="bash", output="file1.txt", error=None),
        ToolResult(tool_call_id="tc2", name="read_file", output="content", error=None),
    ]

    summary = _format_tool_summary(tool_calls, results)

    assert "[tool:bash" in summary
    assert "ok]" in summary
    assert "[tool:read_file" in summary
    assert len(summary.splitlines()) == 2


def test_format_tool_summary_with_errors():
    """Test _format_tool_summary() with failed tool calls."""
    tool_calls = [
        ToolCall(id="tc1", name="bash", args={"command": "invalid"}),
    ]
    results = [
        ToolResult(tool_call_id="tc1", name="bash", output="", error="Command failed"),
    ]

    summary = _format_tool_summary(tool_calls, results)

    assert "error: Command failed" in summary
    assert "[tool:bash" in summary


def test_format_tool_summary_empty():
    """Test _format_tool_summary() with empty lists."""
    summary = _format_tool_summary([], [])
    assert summary == ""


def test_format_tool_summary_truncates_long_args():
    """Test _format_tool_summary() truncates long argument values."""
    long_content = "x" * 200
    tool_calls = [
        ToolCall(id="tc1", name="write_file", args={"content": long_content}),
    ]
    results = [
        ToolResult(tool_call_id="tc1", name="write_file", output="wrote 200 chars", error=None),
    ]

    summary = _format_tool_summary(tool_calls, results)

    assert "…" in summary
    assert "ok]" in summary


# ===========================================================================
# Tests: Integration with multiple tool rounds
# ===========================================================================


@pytest.mark.asyncio
async def test_llm_loop_multiple_tool_rounds(conversation):
    """Test _llm_loop() handles multiple sequential tool rounds."""
    with patch("orchestrator.conversation.call_llm") as mock_llm, \
         patch("orchestrator.conversation.execute_tools") as mock_execute:

        tc1 = ToolCall(id="tc1", name="bash", args={"command": "test"})
        tc2 = ToolCall(id="tc2", name="bash", args={"command": "test2"})

        mock_llm.side_effect = [
            (None, [tc1], "tool_calls"),  # Round 1
            (None, [tc2], "tool_calls"),  # Round 2
            ("Final result", None, "stop"),  # Round 3
        ]

        tr1 = ToolResult(tool_call_id="tc1", name="bash", output="result1", error=None)
        tr2 = ToolResult(tool_call_id="tc2", name="bash", output="result2", error=None)
        mock_execute.side_effect = [[tr1], [tr2]]

        conversation._context_mgr.get_history.return_value = [
            Message(role="user", content="Test")
        ]
        conversation._context_mgr.touch = MagicMock()

        reply = await conversation._llm_loop("System prompt")

        assert reply == "Final result"
        assert mock_llm.call_count == 3
        assert mock_execute.call_count == 2


# ===========================================================================
# Tests: Configuration edge cases
# ===========================================================================


def test_conversation_with_no_enabled_tools():
    """Test Conversation with empty tools list."""
    with patch("orchestrator.conversation.ContextManager"), \
         patch("orchestrator.conversation.SystemPromptBuilder"):
        cfg = {
            "orchestrator": {
                "context": {},
                "system_prompt": {},
                "llm": {},
                "tools": {"enabled": []},
                "retry": {},
            }
        }
        store = MagicMock()
        conversation = Conversation(cfg, store, "test")

        assert conversation._enabled_tools == []


def test_conversation_with_multiple_enabled_tools():
    """Test Conversation with multiple enabled tools."""
    with patch("orchestrator.conversation.ContextManager"), \
         patch("orchestrator.conversation.SystemPromptBuilder"):
        cfg = {
            "orchestrator": {
                "context": {},
                "system_prompt": {},
                "llm": {},
                "tools": {"enabled": ["bash", "read_file", "write_file"]},
                "retry": {},
            }
        }
        store = MagicMock()
        conversation = Conversation(cfg, store, "test")

        assert conversation._enabled_tools == ["bash", "read_file", "write_file"]


@pytest.mark.asyncio
async def test_llm_loop_passes_tools_list_when_enabled(conversation):
    """Test _llm_loop() passes enabled tools to call_llm."""
    with patch("orchestrator.conversation.call_llm") as mock_llm:
        mock_llm.return_value = ("Reply", None, "stop")
        conversation._context_mgr.get_history.return_value = []
        conversation._context_mgr.touch = MagicMock()
        conversation._enabled_tools = ["bash", "read_file"]

        await conversation._llm_loop("System")

        # Should pass tools list
        call_kwargs = mock_llm.call_args[1]
        assert call_kwargs["tools"] == ["bash", "read_file"]


@pytest.mark.asyncio
async def test_llm_loop_passes_none_tools_when_disabled(conversation):
    """Test _llm_loop() passes None tools when list is empty."""
    with patch("orchestrator.conversation.call_llm") as mock_llm:
        mock_llm.return_value = ("Reply", None, "stop")
        conversation._context_mgr.get_history.return_value = []
        conversation._context_mgr.touch = MagicMock()
        conversation._enabled_tools = []

        await conversation._llm_loop("System")

        # Should pass None (no tools available)
        call_kwargs = mock_llm.call_args[1]
        assert call_kwargs["tools"] is None
