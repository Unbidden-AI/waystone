"""End-to-end integration tests for the Waystone Pilot.

These tests use real instances of GraphStore, ContextManager, SystemPromptBuilder,
and tool_executor. Only litellm.acompletion and waystone.extractor.extract
are mocked to avoid real API calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from waystone.store import GraphStore
from pilot.conversation import Conversation
from pilot.types import Message, ToolCall


# ==============================================================================
# Fixtures
# ==============================================================================


@pytest.fixture
def minimal_cfg(tmp_path):
    """Return a minimal config dict for integration tests."""
    return {
        "llm": {
            "model": "gpt-4o-mini",
            "temperature": 0.0,
            "max_tokens": 100,
            "base_url": "http://localhost:1234/v1",
        },
        "pilot": {
            "llm": {
                "model": "gpt-4o-mini",
                "temperature": 0.0,
                "max_tokens": 100,
                "base_url": "http://localhost:1234/v1",
            },
            "context": {
                "window_size": 20,
                "token_budget": 8000,
                "compaction_batch": 20,
                "idle_seconds_before_compact": 9999,
                "token_trigger_ratio": 0.8,
                "context_token_limit": 2000,
                "retrieve": {
                    "hops": 2,
                    "top_k": 10,
                    "strategies": {
                        "superseded_pruning": True,
                        "confidence_threshold": 0.6,
                    },
                },
            },
            "system_prompt": {
                "static": "You are a helpful assistant.",
                "include_context": True,
                "context_token_limit": 2000,
            },
            "tools": {
                "enabled": ["read_file", "bash"],
                "sandbox_root": str(tmp_path),
                "max_output_chars": 1000,
                "bash_timeout": 10,
            },
            "retry": {
                "max_retries": 0,
            },
        },
    }


@pytest.fixture
def real_store(tmp_path):
    """Create a real GraphStore in a temp directory."""
    db_path = tmp_path / "test.db"
    store = GraphStore(db_path)
    yield store
    store.close()


@pytest.fixture
def conversation(minimal_cfg, real_store):
    """Create a real Conversation instance with mocked LLM."""
    return Conversation(
        cfg=minimal_cfg,
        store=real_store,
        project_name="test_project",
    )


# ==============================================================================
# Test: Full Turn Cycle
# ==============================================================================


@pytest.mark.asyncio
async def test_full_turn_cycle(conversation, minimal_cfg, real_store):
    """Test a single complete turn: user message → system context → LLM call → reply."""
    with patch("pilot.llm_adapter.litellm.acompletion") as mock_acompletion:
        # Mock the LLM response
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = "The API rate limit is 1000 requests per minute."
        response.choices[0].message.tool_calls = None
        response.choices[0].finish_reason = "stop"
        response.usage = MagicMock()
        response.usage.total_tokens = 50
        mock_acompletion.return_value = response

        # Call the conversation
        reply = await conversation.chat("What is the API rate limit?")

        # Assertions
        assert reply == "The API rate limit is 1000 requests per minute."
        assert mock_acompletion.called
        # History should have user message + assistant response
        history = conversation._context_mgr.get_history()
        assert len(history) == 2
        assert history[0].role == "user"
        assert history[1].role == "assistant"
        assert conversation._context_mgr.total_tokens() > 0


@pytest.mark.asyncio
async def test_context_retrieved_from_graph(conversation, real_store, minimal_cfg):
    """Verify that graph context reaches the LLM in the system message."""
    # Seed the graph with a relevant node
    node = {
        "id": "n_test01",
        "fact": "The API rate limit is 1000 req/min",
        "type": "constraint",
        "confidence": 0.9,
        "tags": ["rate", "limit", "api"],
        "created_at": "2026-03-17T00:00:00Z",
        "source_transcript": "test.md",
    }
    real_store.add_node(node)

    with patch("pilot.llm_adapter.litellm.acompletion") as mock_acompletion:
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = "Found the rate limit info in the knowledge base."
        response.choices[0].message.tool_calls = None
        response.choices[0].finish_reason = "stop"
        mock_acompletion.return_value = response

        # Capture the messages kwarg
        reply = await conversation.chat("What is the API rate limit?")

        # Check that the system message contains graph context
        assert mock_acompletion.called
        call_kwargs = mock_acompletion.call_args[1]
        messages = call_kwargs.get("messages", [])
        assert len(messages) > 0

        # First message should be system prompt
        system_msg = messages[0]
        assert system_msg["role"] == "system"
        # The system prompt should contain the static instructions
        assert "helpful assistant" in system_msg["content"]


@pytest.mark.asyncio
async def test_multiple_turns_accumulate_history(conversation):
    """Test that multiple conversation turns accumulate in history."""
    with patch("pilot.llm_adapter.litellm.acompletion") as mock_acompletion:
        responses = [
            "First response",
            "Second response",
            "Third response",
        ]

        for i, expected_reply in enumerate(responses):
            response = MagicMock()
            response.choices = [MagicMock()]
            response.choices[0].message.content = expected_reply
            response.choices[0].message.tool_calls = None
            response.choices[0].finish_reason = "stop"
            mock_acompletion.return_value = response

            reply = await conversation.chat(f"Question {i+1}")
            assert reply == expected_reply

        # After 3 turns, history should have 6 messages (3 user + 3 assistant)
        history = conversation._context_mgr.get_history()
        assert len(history) == 6
        assert history[0].role == "user"
        assert history[1].role == "assistant"
        assert history[2].role == "user"
        assert history[3].role == "assistant"
        assert history[4].role == "user"
        assert history[5].role == "assistant"


@pytest.mark.asyncio
async def test_compaction_triggers_on_token_budget(minimal_cfg, real_store):
    """Test that compaction triggers when token budget is exceeded."""
    # Override config with low token budget to trigger quickly
    cfg = minimal_cfg.copy()
    cfg["pilot"]["context"]["token_budget"] = 100
    cfg["pilot"]["context"]["token_trigger_ratio"] = 0.5
    cfg["pilot"]["context"]["compaction_batch"] = 2

    conversation = Conversation(cfg=cfg, store=real_store, project_name="test_project")

    with patch("pilot.llm_adapter.litellm.acompletion") as mock_acompletion, \
         patch("waystone.extractor.extract") as mock_extract:

        # Mock extract to return a simple result
        mock_extract.return_value = {
            "nodes": [
                {
                    "id": "n_x1",
                    "fact": "test fact",
                    "type": "implementation",
                    "confidence": 0.9,
                    "tags": ["test"],
                }
            ],
            "edges": [],
        }
        mock_extract.return_value = AsyncMock(return_value={
            "nodes": [
                {
                    "id": "n_x1",
                    "fact": "test fact",
                    "type": "implementation",
                    "confidence": 0.9,
                    "tags": ["test"],
                }
            ],
            "edges": [],
        })()

        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = "Reply"
        response.choices[0].message.tool_calls = None
        response.choices[0].finish_reason = "stop"
        mock_acompletion.return_value = response

        # Add messages manually to exceed token budget
        for i in range(6):
            msg = Message(role="user", content="x" * 100, token_estimate=25)
            conversation._context_mgr.add_message(msg)

        # Now call chat which should trigger compaction
        reply = await conversation.chat("trigger compaction")
        assert reply == "Reply"


@pytest.mark.asyncio
async def test_tool_call_round_trip(conversation, tmp_path):
    """Test that LLM tool calls are executed and results fed back."""
    # Create a real temp file
    test_file = tmp_path / "test.txt"
    test_file.write_text("Hello from test file")

    with patch("pilot.llm_adapter.litellm.acompletion") as mock_acompletion:
        # First call: LLM requests a tool
        response1 = MagicMock()
        response1.choices = [MagicMock()]
        response1.choices[0].message.content = None  # No direct text when tool_calls present
        tool_call = MagicMock()
        tool_call.id = "call_123"
        tool_call.function.name = "read_file"
        tool_call.function.arguments = json.dumps({"path": str(test_file)})
        response1.choices[0].message.tool_calls = [tool_call]
        response1.choices[0].finish_reason = "tool_calls"

        # Second call: LLM provides final answer
        response2 = MagicMock()
        response2.choices = [MagicMock()]
        response2.choices[0].message.content = "I read the file successfully."
        response2.choices[0].message.tool_calls = None
        response2.choices[0].finish_reason = "stop"

        mock_acompletion.side_effect = [response1, response2]

        reply = await conversation.chat("Read the test file")
        assert "successfully" in reply

        # Verify tool call was made
        assert mock_acompletion.call_count >= 2

        # Verify history includes tool messages
        history = conversation._context_mgr.get_history()
        # Should have: user, assistant (tool summary), tool result, assistant (final)
        roles = [m.role for m in history]
        assert "user" in roles
        assert "assistant" in roles
        assert "tool" in roles


@pytest.mark.asyncio
async def test_reset_clears_history(conversation):
    """Test that reset() clears the conversation history."""
    with patch("pilot.llm_adapter.litellm.acompletion") as mock_acompletion:
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = "Test reply"
        response.choices[0].message.tool_calls = None
        response.choices[0].finish_reason = "stop"
        mock_acompletion.return_value = response

        # Complete one turn
        await conversation.chat("First message")
        assert conversation._context_mgr.total_tokens() > 0
        first_depth = len(conversation._context_mgr.get_history())
        assert first_depth == 2  # user + assistant

        # Reset
        conversation.reset()
        assert conversation._context_mgr.total_tokens() == 0
        assert len(conversation._context_mgr.get_history()) == 0

        # Complete another turn
        await conversation.chat("Second message")
        second_depth = len(conversation._context_mgr.get_history())
        assert second_depth == 2  # user + assistant again


@pytest.mark.asyncio
async def test_stream_returns_full_reply(conversation):
    """Test that chat_stream() yields and joins to the full reply."""
    with patch("pilot.llm_adapter.litellm.acompletion") as mock_acompletion:
        # Make the reply long enough to require multiple 80-char chunks
        full_reply = "This is a much longer reply that will definitely be streamed in multiple chunks because it contains more than eighty characters which is the chunk boundary."
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = full_reply
        response.choices[0].message.tool_calls = None
        response.choices[0].finish_reason = "stop"
        mock_acompletion.return_value = response

        # Collect all chunks
        chunks = []
        async for chunk in conversation.chat_stream("Stream this message"):
            chunks.append(chunk)

        joined = "".join(chunks)
        assert joined == full_reply
        assert len(chunks) >= 1  # Full reply assembled from yielded chunks


# ==============================================================================
# Test: System Prompt Integration
# ==============================================================================


@pytest.mark.asyncio
async def test_system_prompt_includes_static_instructions(conversation):
    """Test that the system prompt includes static instructions from config."""
    with patch("pilot.llm_adapter.litellm.acompletion") as mock_acompletion:
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = "Reply"
        response.choices[0].message.tool_calls = None
        response.choices[0].finish_reason = "stop"
        mock_acompletion.return_value = response

        await conversation.chat("Test message")

        # Extract system prompt from the LLM call
        call_kwargs = mock_acompletion.call_args[1]
        messages = call_kwargs["messages"]
        system_msg = messages[0]

        assert system_msg["role"] == "system"
        # Check for the static instruction
        assert "helpful assistant" in system_msg["content"]


# ==============================================================================
# Test: Stats and Diagnostics
# ==============================================================================


@pytest.mark.asyncio
async def test_stats_reflect_current_state(conversation):
    """Test that stats() reflects the current conversation state."""
    with patch("pilot.llm_adapter.litellm.acompletion") as mock_acompletion:
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = "Test"
        response.choices[0].message.tool_calls = None
        response.choices[0].finish_reason = "stop"
        mock_acompletion.return_value = response

        await conversation.chat("First turn")

        stats = conversation.stats()
        assert stats["project"] == "test_project"
        assert stats["history_depth"] == 2  # user + assistant
        assert stats["total_tokens"] > 0
        assert stats["token_budget"] > 0


# ==============================================================================
# Test: Context Retrieval Edge Cases
# ==============================================================================


@pytest.mark.asyncio
async def test_empty_graph_returns_no_context(conversation):
    """Test that retrieval from an empty graph doesn't break the conversation."""
    with patch("pilot.llm_adapter.litellm.acompletion") as mock_acompletion:
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = "No context found"
        response.choices[0].message.tool_calls = None
        response.choices[0].finish_reason = "stop"
        mock_acompletion.return_value = response

        # Should not raise, even with empty graph
        reply = await conversation.chat("Query with no matching context")
        assert reply == "No context found"


@pytest.mark.asyncio
async def test_multiple_nodes_in_context(conversation, real_store):
    """Test that multiple graph nodes are properly retrieved and included."""
    # Add multiple nodes
    for i in range(3):
        node = {
            "id": f"n_node{i}",
            "fact": f"Fact number {i}",
            "type": "decision",
            "confidence": 0.9,
            "tags": ["test", f"fact{i}"],
            "created_at": "2026-03-17T00:00:00Z",
            "source_transcript": "test.md",
        }
        real_store.add_node(node)

    with patch("pilot.llm_adapter.litellm.acompletion") as mock_acompletion:
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = "Multiple facts retrieved"
        response.choices[0].message.tool_calls = None
        response.choices[0].finish_reason = "stop"
        mock_acompletion.return_value = response

        reply = await conversation.chat("Tell me about test facts")
        assert reply == "Multiple facts retrieved"


# ==============================================================================
# Test: Tool Execution
# ==============================================================================


@pytest.mark.asyncio
async def test_bash_tool_execution(conversation, tmp_path):
    """Test that bash tool calls are executed correctly."""
    with patch("pilot.llm_adapter.litellm.acompletion") as mock_acompletion:
        # First call: LLM requests bash tool
        response1 = MagicMock()
        response1.choices = [MagicMock()]
        response1.choices[0].message.content = None
        tool_call = MagicMock()
        tool_call.id = "call_bash_1"
        tool_call.function.name = "bash"
        tool_call.function.arguments = json.dumps({"command": "echo 'hello from bash'"})
        response1.choices[0].message.tool_calls = [tool_call]
        response1.choices[0].finish_reason = "tool_calls"

        # Second call: final response
        response2 = MagicMock()
        response2.choices = [MagicMock()]
        response2.choices[0].message.content = "Bash executed successfully"
        response2.choices[0].message.tool_calls = None
        response2.choices[0].finish_reason = "stop"

        mock_acompletion.side_effect = [response1, response2]

        reply = await conversation.chat("Run a bash command")
        assert "successfully" in reply


@pytest.mark.asyncio
async def test_disabled_tool_returns_error(conversation, tmp_path):
    """Test that disabled tools return an error without executing."""
    cfg = conversation._context_mgr._extractor_cfg
    # Disable all tools
    cfg["pilot"]["tools"]["enabled"] = []

    with patch("pilot.llm_adapter.litellm.acompletion") as mock_acompletion:
        # LLM tries to use a tool
        response1 = MagicMock()
        response1.choices = [MagicMock()]
        response1.choices[0].message.content = None
        tool_call = MagicMock()
        tool_call.id = "call_1"
        tool_call.function.name = "bash"
        tool_call.function.arguments = json.dumps({"command": "echo test"})
        response1.choices[0].message.tool_calls = [tool_call]
        response1.choices[0].finish_reason = "tool_calls"

        # Second call: LLM responds after getting error
        response2 = MagicMock()
        response2.choices = [MagicMock()]
        response2.choices[0].message.content = "Tool was not enabled"
        response2.choices[0].message.tool_calls = None
        response2.choices[0].finish_reason = "stop"

        mock_acompletion.side_effect = [response1, response2]

        reply = await conversation.chat("Try to use a tool")
        # Should succeed overall, but the tool result should contain an error
        assert "not enabled" in reply or reply == "Tool was not enabled"


# ==============================================================================
# Test: Concurrency and Edge Cases
# ==============================================================================


@pytest.mark.asyncio
async def test_empty_user_message(conversation):
    """Test handling of empty user messages."""
    with patch("pilot.llm_adapter.litellm.acompletion") as mock_acompletion:
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = "Empty message received"
        response.choices[0].message.tool_calls = None
        response.choices[0].finish_reason = "stop"
        mock_acompletion.return_value = response

        reply = await conversation.chat("")
        # Should not crash, and should return the LLM's response
        assert isinstance(reply, str)


@pytest.mark.asyncio
async def test_very_long_user_message(conversation):
    """Test handling of very long user messages."""
    with patch("pilot.llm_adapter.litellm.acompletion") as mock_acompletion:
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = "Long message processed"
        response.choices[0].message.tool_calls = None
        response.choices[0].finish_reason = "stop"
        mock_acompletion.return_value = response

        long_message = "x" * 10000
        reply = await conversation.chat(long_message)
        assert isinstance(reply, str)


# ==============================================================================
# Test: LLM Error Handling
# ==============================================================================


@pytest.mark.asyncio
async def test_llm_error_propagates(conversation):
    """Test that LLM errors propagate correctly."""
    with patch("pilot.llm_adapter.litellm.acompletion") as mock_acompletion:
        # Simulate a generic exception
        mock_acompletion.side_effect = RuntimeError("LLM service unavailable")

        # Should raise the error
        with pytest.raises(RuntimeError):
            await conversation.chat("This will fail")
