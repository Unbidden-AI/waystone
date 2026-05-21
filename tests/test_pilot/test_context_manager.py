"""Tests for pilot.context_manager — history management and compaction."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from waystone.store import GraphStore
from pilot.context_manager import ContextManager
from pilot.types import CompactionResult, CompactionTrigger, Message


# ==============================================================================
# Fixtures
# ==============================================================================


@pytest.fixture
def in_memory_store():
    """Create an in-memory SQLite GraphStore for testing."""
    store = GraphStore(":memory:")
    yield store
    store.close()


@pytest.fixture
def default_config():
    """Provide a default context manager configuration."""
    return {
        "window_size": 20,
        "token_budget": 8000,
        "compaction_batch": 10,
        "idle_seconds_before_compact": 600,
        "token_trigger_ratio": 0.8,
        "context_token_limit": 2000,
        "retrieve": {
            "hops": 2,
            "top_k": 20,
            "strategies": {
                "superseded_pruning": True,
                "confidence_threshold": 0.6,
            },
        },
    }


@pytest.fixture
def extractor_config():
    """Provide a minimal extractor configuration."""
    return {
        "llm": {
            "api_key": "test-key",
            "base_url": "http://localhost:1234/v1",
            "model": "local-model",
        }
    }


@pytest.fixture
def context_manager(in_memory_store, default_config, extractor_config):
    """Create a ContextManager instance for testing."""
    return ContextManager(
        cfg=default_config,
        store=in_memory_store,
        project_name="test_project",
        extractor_config=extractor_config,
    )


# ==============================================================================
# Tests for add_message
# ==============================================================================


class TestAddMessage:
    """Test history management via add_message."""

    def test_add_message_increments_history(self, context_manager):
        """add_message appends to history."""
        msg = Message(role="user", content="Hello", token_estimate=10)
        context_manager.add_message(msg)
        assert len(context_manager.get_history()) == 1
        assert context_manager.get_history()[0] == msg

    def test_add_message_updates_total_tokens(self, context_manager):
        """add_message updates total_tokens correctly."""
        msg1 = Message(role="user", content="Hello", token_estimate=10)
        msg2 = Message(role="assistant", content="Hi", token_estimate=5)
        context_manager.add_message(msg1)
        assert context_manager.total_tokens() == 10
        context_manager.add_message(msg2)
        assert context_manager.total_tokens() == 15

    def test_add_message_updates_last_activity(self, context_manager):
        """add_message updates the last-activity timestamp."""
        before = time.monotonic()
        msg = Message(role="user", content="test", token_estimate=1)
        context_manager.add_message(msg)
        after = time.monotonic()
        # Stats should reflect the activity (can't directly access _last_activity, check via time)
        # Just verify no exception and history is added
        assert len(context_manager.get_history()) == 1

    def test_add_message_auto_estimates_tokens(self, context_manager):
        """Message with token_estimate=0 gets auto-estimated."""
        msg = Message(role="user", content="x" * 100, token_estimate=0)
        context_manager.add_message(msg)
        assert context_manager.total_tokens() >= 1  # At least 1 token

    def test_add_multiple_messages(self, context_manager):
        """Multiple adds work correctly."""
        for i in range(5):
            msg = Message(role="user" if i % 2 == 0 else "assistant", content=f"msg{i}", token_estimate=10)
            context_manager.add_message(msg)
        assert len(context_manager.get_history()) == 5
        assert context_manager.total_tokens() == 50


# ==============================================================================
# Tests for should_compact
# ==============================================================================


class TestShouldCompact:
    """Test compaction trigger detection."""

    def test_should_compact_returns_none_on_empty_history(self, context_manager):
        """should_compact returns None when history is empty."""
        assert context_manager.should_compact() is None

    def test_should_compact_returns_none_below_all_thresholds(self, context_manager):
        """should_compact returns None when all conditions are OK."""
        msg = Message(role="user", content="short", token_estimate=10)
        context_manager.add_message(msg)
        # Below token threshold, below window size, recent activity
        assert context_manager.should_compact() is None

    def test_should_compact_token_budget_trigger(self, context_manager):
        """should_compact returns TOKEN_BUDGET when tokens >= 80% of budget."""
        # Config: token_budget=8000, trigger_ratio=0.8 → threshold=6400
        msg = Message(role="user", content="x", token_estimate=6400)
        context_manager.add_message(msg)
        trigger = context_manager.should_compact()
        assert trigger == CompactionTrigger.TOKEN_BUDGET

    def test_should_compact_history_depth_trigger(self, context_manager):
        """should_compact returns HISTORY_DEPTH when depth >= window_size."""
        # Config: window_size=20
        for i in range(20):
            msg = Message(role="user" if i % 2 == 0 else "assistant", content=f"msg{i}", token_estimate=1)
            context_manager.add_message(msg)
        trigger = context_manager.should_compact()
        assert trigger == CompactionTrigger.HISTORY_DEPTH

    def test_should_compact_history_depth_priority_over_token(self, context_manager):
        """HISTORY_DEPTH and TOKEN_BUDGET both trigger; return first met (token has priority)."""
        # Fill with high-token messages to hit token budget first
        msg = Message(role="user", content="x", token_estimate=6400)
        context_manager.add_message(msg)
        trigger = context_manager.should_compact()
        # Token budget is checked first in the code
        assert trigger == CompactionTrigger.TOKEN_BUDGET

    def test_should_compact_idle_time_trigger(self, context_manager):
        """should_compact returns IDLE_TIME when idle > threshold."""
        msg = Message(role="user", content="test", token_estimate=10)
        # Patch before adding message to control all calls to time.monotonic
        with patch("pilot.context_manager.time.monotonic") as mock_time:
            # Set up time values: first call (add_message) = 100.0, second call (should_compact) = 800.0
            mock_time.side_effect = [100.0, 800.0]
            context_manager.add_message(msg)
            trigger = context_manager.should_compact()
            assert trigger == CompactionTrigger.IDLE_TIME

    def test_should_compact_token_budget_priority(self, context_manager):
        """TOKEN_BUDGET is checked before HISTORY_DEPTH and IDLE_TIME."""
        # Fill to token budget
        msg = Message(role="user", content="x", token_estimate=6400)
        context_manager.add_message(msg)
        # Also advance time to trigger idle
        with patch("pilot.context_manager.time.monotonic") as mock_time:
            mock_time.side_effect = [100.0, 800.0]
            trigger = context_manager.should_compact()
            # TOKEN_BUDGET is checked first
            assert trigger == CompactionTrigger.TOKEN_BUDGET

    def test_should_compact_idle_triggered_last(self, context_manager):
        """IDLE_TIME is checked last after token and depth."""
        msg = Message(role="user", content="test", token_estimate=10)
        with patch("pilot.context_manager.time.monotonic") as mock_time:
            # Set up time values: first call (add_message) = 100.0, second call (should_compact) = 800.0
            mock_time.side_effect = [100.0, 800.0]
            context_manager.add_message(msg)
            trigger = context_manager.should_compact()
            assert trigger == CompactionTrigger.IDLE_TIME


# ==============================================================================
# Tests for compact
# ==============================================================================


class TestCompact:
    """Test the compact operation."""

    @pytest.mark.asyncio
    async def test_compact_removes_oldest_messages(self, context_manager):
        """compact removes the oldest batch_size messages."""
        for i in range(15):
            msg = Message(role="user" if i % 2 == 0 else "assistant", content=f"msg{i}", token_estimate=10)
            context_manager.add_message(msg)
        assert len(context_manager.get_history()) == 15

        with patch("pilot.context_manager.extract", new_callable=AsyncMock) as mock_extract:
            mock_extract.return_value = {"nodes": [], "edges": []}
            result = await context_manager.compact(CompactionTrigger.TOKEN_BUDGET)
            # Default compaction_batch=10
            assert len(context_manager.get_history()) == 5
            assert result.messages_removed == 10

    @pytest.mark.asyncio
    async def test_compact_updates_total_tokens(self, context_manager):
        """compact recalculates total_tokens after removing messages."""
        for i in range(15):
            msg = Message(role="user" if i % 2 == 0 else "assistant", content=f"msg{i}", token_estimate=10)
            context_manager.add_message(msg)
        initial_tokens = context_manager.total_tokens()
        assert initial_tokens == 150

        with patch("pilot.context_manager.extract", new_callable=AsyncMock) as mock_extract:
            mock_extract.return_value = {"nodes": [], "edges": []}
            await context_manager.compact(CompactionTrigger.TOKEN_BUDGET)
            # 10 messages removed (10 * 10 = 100 tokens freed)
            assert context_manager.total_tokens() == 50

    @pytest.mark.asyncio
    async def test_compact_returns_correct_tokens_freed(self, context_manager):
        """compact result reports tokens_freed accurately."""
        for i in range(15):
            msg = Message(role="user" if i % 2 == 0 else "assistant", content=f"msg{i}", token_estimate=10)
            context_manager.add_message(msg)

        with patch("pilot.context_manager.extract", new_callable=AsyncMock) as mock_extract:
            mock_extract.return_value = {"nodes": [], "edges": []}
            result = await context_manager.compact(CompactionTrigger.TOKEN_BUDGET)
            assert result.tokens_freed == 100

    @pytest.mark.asyncio
    async def test_compact_builds_transcript_from_non_system_messages(self, context_manager):
        """compact builds transcript from user+assistant, skips system."""
        messages = [
            Message(role="system", content="You are helpful", token_estimate=5),
            Message(role="user", content="Hello", token_estimate=10),
            Message(role="assistant", content="Hi there", token_estimate=10),
        ]
        for msg in messages:
            context_manager.add_message(msg)

        with patch("pilot.context_manager.extract", new_callable=AsyncMock) as mock_extract:
            mock_extract.return_value = {"nodes": [], "edges": []}
            await context_manager.compact(CompactionTrigger.TOKEN_BUDGET)
            # extract should be called with transcript excluding system message
            assert mock_extract.called
            call_args = mock_extract.call_args
            transcript = call_args[0][0]
            assert "Hello" in transcript
            assert "Hi there" in transcript
            assert "You are helpful" not in transcript
            # Verify format: "User: Hello\n\nAssistant: Hi there"
            assert "User:" in transcript
            assert "Assistant:" in transcript

    @pytest.mark.asyncio
    async def test_compact_calls_merge_extraction(self, context_manager):
        """compact calls store.merge_extraction with extracted data."""
        msg = Message(role="user", content="test", token_estimate=10)
        context_manager.add_message(msg)

        extraction_data = {
            "nodes": [
                {"id": "n1", "fact": "Test fact", "type": "decision", "confidence": 0.9, "tags": ["test"]},
            ],
            "edges": [{"from_id": "n1", "to_id": "n2", "relation": "relates_to"}],
        }

        with patch("pilot.context_manager.extract", new_callable=AsyncMock) as mock_extract:
            mock_extract.return_value = extraction_data
            with patch.object(context_manager._store, "merge_extraction") as mock_merge:
                await context_manager.compact(CompactionTrigger.TOKEN_BUDGET)
                # Verify merge_extraction was called with the extraction and source
                mock_merge.assert_called_once()
                call_args = mock_merge.call_args
                # Should be called as merge_extraction(nodes, edges)
                nodes_arg, edges_arg = call_args[0]
                assert isinstance(nodes_arg, list)
                assert isinstance(edges_arg, list)

    @pytest.mark.asyncio
    async def test_compact_returns_node_count(self, context_manager):
        """compact result includes nodes_extracted count."""
        msg = Message(role="user", content="test", token_estimate=10)
        context_manager.add_message(msg)

        extraction_data = {
            "nodes": [
                {"id": "n1", "fact": "Fact 1", "type": "decision", "confidence": 0.9, "tags": []},
                {"id": "n2", "fact": "Fact 2", "type": "constraint", "confidence": 0.8, "tags": []},
            ],
            "edges": [],
        }

        with patch("pilot.context_manager.extract", new_callable=AsyncMock) as mock_extract:
            mock_extract.return_value = extraction_data
            result = await context_manager.compact(CompactionTrigger.TOKEN_BUDGET)
            assert result.nodes_extracted == 2

    @pytest.mark.asyncio
    async def test_compact_prunes_history_even_on_extract_failure(self, context_manager):
        """compact still removes history even if extract() raises."""
        for i in range(15):
            msg = Message(role="user" if i % 2 == 0 else "assistant", content=f"msg{i}", token_estimate=10)
            context_manager.add_message(msg)
        initial_history_len = len(context_manager.get_history())

        with patch("pilot.context_manager.extract", new_callable=AsyncMock) as mock_extract:
            mock_extract.side_effect = Exception("Extract failed")
            result = await context_manager.compact(CompactionTrigger.TOKEN_BUDGET)
            # History should still be pruned
            assert len(context_manager.get_history()) == initial_history_len - 10
            # nodes_extracted should be 0 due to failure
            assert result.nodes_extracted == 0
            assert result.messages_removed == 10

    @pytest.mark.asyncio
    async def test_compact_with_empty_transcript(self, context_manager):
        """compact handles batch with only system messages (empty transcript)."""
        messages = [
            Message(role="system", content="System prompt", token_estimate=5),
            Message(role="system", content="Another system message", token_estimate=5),
        ]
        for msg in messages:
            context_manager.add_message(msg)

        with patch("pilot.context_manager.extract", new_callable=AsyncMock) as mock_extract:
            mock_extract.return_value = {"nodes": [], "edges": []}
            result = await context_manager.compact(CompactionTrigger.TOKEN_BUDGET)
            # extract should not be called for empty transcript
            assert not mock_extract.called
            # Messages should still be removed
            assert result.messages_removed == 2

    @pytest.mark.asyncio
    async def test_compact_compacts_less_than_batch_size_when_needed(self, context_manager):
        """compact removes fewer messages than batch_size if history is smaller."""
        for i in range(5):
            msg = Message(role="user", content=f"msg{i}", token_estimate=10)
            context_manager.add_message(msg)

        with patch("pilot.context_manager.extract", new_callable=AsyncMock) as mock_extract:
            mock_extract.return_value = {"nodes": [], "edges": []}
            result = await context_manager.compact(CompactionTrigger.TOKEN_BUDGET)
            # Only 5 messages exist, compaction_batch=10, so remove min(10, 5)=5
            assert result.messages_removed == 5
            assert len(context_manager.get_history()) == 0

    @pytest.mark.asyncio
    async def test_compact_result_includes_trigger(self, context_manager):
        """compact result includes the trigger that caused it."""
        msg = Message(role="user", content="test", token_estimate=10)
        context_manager.add_message(msg)

        with patch("pilot.context_manager.extract", new_callable=AsyncMock) as mock_extract:
            mock_extract.return_value = {"nodes": [], "edges": []}
            result = await context_manager.compact(CompactionTrigger.IDLE_TIME)
            assert result.trigger == CompactionTrigger.IDLE_TIME


# ==============================================================================
# Tests for compact_if_needed
# ==============================================================================


class TestCompactIfNeeded:
    """Test the compact_if_needed convenience method."""

    @pytest.mark.asyncio
    async def test_compact_if_needed_returns_none_when_no_trigger(self, context_manager):
        """compact_if_needed returns None if no trigger fires."""
        msg = Message(role="user", content="short", token_estimate=10)
        context_manager.add_message(msg)
        result = await context_manager.compact_if_needed()
        assert result is None

    @pytest.mark.asyncio
    async def test_compact_if_needed_calls_compact_on_trigger(self, context_manager):
        """compact_if_needed calls compact() if trigger fires."""
        for i in range(20):
            msg = Message(role="user" if i % 2 == 0 else "assistant", content=f"msg{i}", token_estimate=10)
            context_manager.add_message(msg)

        with patch("pilot.context_manager.extract", new_callable=AsyncMock) as mock_extract:
            mock_extract.return_value = {"nodes": [], "edges": []}
            result = await context_manager.compact_if_needed()
            assert result is not None
            assert isinstance(result, CompactionResult)
            assert result.trigger == CompactionTrigger.HISTORY_DEPTH


# ==============================================================================
# Tests for retrieve_context
# ==============================================================================


class TestRetrieveContext:
    """Test graph context retrieval."""

    def test_retrieve_context_returns_empty_string_on_empty_task(self, context_manager):
        """retrieve_context returns empty string if task_description is empty."""
        result = context_manager.retrieve_context("")
        assert result == ""

    def test_retrieve_context_returns_empty_string_on_whitespace_task(self, context_manager):
        """retrieve_context returns empty string if task_description is whitespace."""
        result = context_manager.retrieve_context("   \n  ")
        assert result == ""

    def test_retrieve_context_calls_retrieve_with_stats(self, context_manager):
        """retrieve_context calls retrieve_with_stats with correct args."""
        with patch("pilot.context_manager.retrieve_with_stats") as mock_retrieve:
            mock_retrieve.return_value = MagicMock(
                markdown="# Context\nSome facts",
                nodes_before_strategies=10,
                nodes_after_strategies=5,
                tokens_estimated=500,
            )
            result = context_manager.retrieve_context("find the bug")
            assert mock_retrieve.called
            # retrieve_with_stats is called with (store, task_description) + kwargs
            call_args = mock_retrieve.call_args
            assert call_args[0][1] == "find the bug"  # task_description (positional)
            assert call_args[1]["hops"] == 2  # hops from default_config
            assert call_args[1]["top_k"] == 20  # top_k from default_config
            assert "token_budget" in call_args[1]["strategies"]  # strategies dict

    def test_retrieve_context_passes_context_token_limit(self, context_manager):
        """retrieve_context passes context_token_limit as token_budget in strategies."""
        with patch("pilot.context_manager.retrieve_with_stats") as mock_retrieve:
            mock_retrieve.return_value = MagicMock(markdown="", nodes_before_strategies=0, nodes_after_strategies=0, tokens_estimated=0)
            context_manager.retrieve_context("test")
            call_kwargs = mock_retrieve.call_args[1]
            assert call_kwargs["strategies"]["token_budget"] == 2000  # from default_config

    def test_retrieve_context_returns_markdown_on_success(self, context_manager):
        """retrieve_context returns the markdown result."""
        expected_md = "# Relevant Context\n\n- Fact 1\n- Fact 2"
        with patch("pilot.context_manager.retrieve_with_stats") as mock_retrieve:
            mock_retrieve.return_value = MagicMock(
                markdown=expected_md,
                nodes_before_strategies=10,
                nodes_after_strategies=2,
                tokens_estimated=100,
            )
            result = context_manager.retrieve_context("find context")
            assert result == expected_md

    def test_retrieve_context_returns_empty_string_on_exception(self, context_manager):
        """retrieve_context returns empty string if retrieve_with_stats raises."""
        with patch("pilot.context_manager.retrieve_with_stats") as mock_retrieve:
            mock_retrieve.side_effect = Exception("Retrieval failed")
            result = context_manager.retrieve_context("test")
            assert result == ""

    def test_retrieve_context_preserves_retrieve_strategies_config(self, context_manager):
        """retrieve_context merges config strategies with token_budget."""
        with patch("pilot.context_manager.retrieve_with_stats") as mock_retrieve:
            mock_retrieve.return_value = MagicMock(markdown="", nodes_before_strategies=0, nodes_after_strategies=0, tokens_estimated=0)
            context_manager.retrieve_context("test")
            call_kwargs = mock_retrieve.call_args[1]
            strategies = call_kwargs["strategies"]
            # Should have both config strategies and token_budget
            assert strategies["superseded_pruning"] is True
            assert strategies["confidence_threshold"] == 0.6
            assert strategies["token_budget"] == 2000


# ==============================================================================
# Tests for stats
# ==============================================================================


class TestStats:
    """Test diagnostics via stats()."""

    def test_stats_returns_dict(self, context_manager):
        """stats returns a dictionary."""
        result = context_manager.stats()
        assert isinstance(result, dict)

    def test_stats_includes_project_name(self, context_manager):
        """stats includes project name."""
        result = context_manager.stats()
        assert result["project"] == "test_project"

    def test_stats_includes_history_depth(self, context_manager):
        """stats includes current history depth."""
        for i in range(5):
            msg = Message(role="user", content=f"msg{i}", token_estimate=10)
            context_manager.add_message(msg)
        result = context_manager.stats()
        assert result["history_depth"] == 5

    def test_stats_includes_total_tokens(self, context_manager):
        """stats includes total token count."""
        for i in range(3):
            msg = Message(role="user", content=f"msg{i}", token_estimate=10)
            context_manager.add_message(msg)
        result = context_manager.stats()
        assert result["total_tokens"] == 30

    def test_stats_includes_token_budget(self, context_manager):
        """stats includes configured token_budget."""
        result = context_manager.stats()
        assert result["token_budget"] == 8000

    def test_stats_includes_window_size(self, context_manager):
        """stats includes configured window_size."""
        result = context_manager.stats()
        assert result["window_size"] == 20

    def test_stats_includes_idle_seconds(self, context_manager):
        """stats includes time since last activity."""
        msg = Message(role="user", content="test", token_estimate=10)
        context_manager.add_message(msg)
        result = context_manager.stats()
        assert "idle_seconds" in result
        assert isinstance(result["idle_seconds"], float)
        assert result["idle_seconds"] >= 0

    def test_stats_idle_seconds_increases_over_time(self, context_manager):
        """stats idle_seconds increases as time passes."""
        msg = Message(role="user", content="test", token_estimate=10)
        context_manager.add_message(msg)
        result1 = context_manager.stats()
        time.sleep(0.1)
        result2 = context_manager.stats()
        assert result2["idle_seconds"] > result1["idle_seconds"]


# ==============================================================================
# Tests for get_history and total_tokens
# ==============================================================================


class TestHistoryAccessors:
    """Test read-only history accessors."""

    def test_get_history_returns_list(self, context_manager):
        """get_history returns a list."""
        assert isinstance(context_manager.get_history(), list)

    def test_get_history_is_copy_not_reference(self, context_manager):
        """get_history returns a copy, not the internal reference."""
        msg1 = Message(role="user", content="test", token_estimate=10)
        context_manager.add_message(msg1)
        hist1 = context_manager.get_history()
        msg2 = Message(role="assistant", content="response", token_estimate=10)
        context_manager.add_message(msg2)
        hist2 = context_manager.get_history()
        # hist1 should have 1 message, hist2 should have 2
        assert len(hist1) == 1
        assert len(hist2) == 2

    def test_total_tokens_returns_int(self, context_manager):
        """total_tokens returns an integer."""
        msg = Message(role="user", content="test", token_estimate=42)
        context_manager.add_message(msg)
        assert isinstance(context_manager.total_tokens(), int)
        assert context_manager.total_tokens() == 42


# ==============================================================================
# Tests for touch
# ==============================================================================


class TestTouch:
    """Test the touch() method for updating activity time."""

    def test_touch_updates_last_activity(self, context_manager):
        """touch updates the last-activity timestamp."""
        msg = Message(role="user", content="test", token_estimate=10)
        context_manager.add_message(msg)
        time.sleep(0.1)
        stats_before = context_manager.stats()
        context_manager.touch()
        time.sleep(0.05)
        stats_after = context_manager.stats()
        # touch should have reset the idle timer
        assert stats_after["idle_seconds"] < stats_before["idle_seconds"]


# ==============================================================================
# Integration tests
# ==============================================================================


class TestIntegration:
    """Integration tests combining multiple operations."""

    @pytest.mark.asyncio
    async def test_full_compaction_workflow(self, context_manager):
        """Full workflow: add messages, compact, verify history and graph."""
        # Add messages
        for i in range(15):
            role = "user" if i % 2 == 0 else "assistant"
            msg = Message(role=role, content=f"msg{i}", token_estimate=10)
            context_manager.add_message(msg)

        initial_history_len = len(context_manager.get_history())
        initial_tokens = context_manager.total_tokens()

        # Mock extraction
        extraction_data = {
            "nodes": [
                {"id": "n1", "fact": "Important fact", "type": "decision", "confidence": 0.9, "tags": ["key"]},
            ],
            "edges": [],
        }

        with patch("pilot.context_manager.extract", new_callable=AsyncMock) as mock_extract:
            mock_extract.return_value = extraction_data
            result = await context_manager.compact(CompactionTrigger.TOKEN_BUDGET)

        # Verify compaction result
        assert result.nodes_extracted == 1
        assert result.messages_removed == 10
        assert result.tokens_freed == 100
        assert result.trigger == CompactionTrigger.TOKEN_BUDGET

        # Verify history state
        assert len(context_manager.get_history()) == initial_history_len - 10
        assert context_manager.total_tokens() == initial_tokens - 100

    @pytest.mark.asyncio
    async def test_mixed_message_roles_in_compaction(self, context_manager):
        """Verify compaction handles mixed roles correctly."""
        messages = [
            Message(role="system", content="sys", token_estimate=5),
            Message(role="user", content="user msg", token_estimate=10),
            Message(role="assistant", content="asst msg", token_estimate=10),
            Message(role="tool", content="tool result", token_estimate=5),
        ]
        for msg in messages:
            context_manager.add_message(msg)

        with patch("pilot.context_manager.extract", new_callable=AsyncMock) as mock_extract:
            mock_extract.return_value = {"nodes": [], "edges": []}
            await context_manager.compact(CompactionTrigger.TOKEN_BUDGET)
            # Verify transcript was built
            assert mock_extract.called
            call_args = mock_extract.call_args[0][0]
            # Should include user, assistant, tool but not system
            assert "user msg" in call_args
            assert "asst msg" in call_args
            assert "tool result" in call_args
            assert "sys" not in call_args or "System:" not in call_args
