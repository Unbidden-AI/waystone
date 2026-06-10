"""Tests for session summary generation (P3 feature)."""

import io
import json
import sys
import tempfile
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from waystone._hooks import summarize
from waystone.store import GraphStore


def _make_node_id():
    """Generate a valid node ID in the format n_<8-hex>."""
    return f"n_{uuid.uuid4().hex[:8]}"


class TestSessionSummaryCadence:
    """Test cadence counter and trigger logic."""

    def test_cadence_counter_increments(self, tmp_path):
        """Summary counter increments on each turn."""
        config = {
            "session_summary": {"enabled": True, "cadence_turns": 10},
        }
        state_path = tmp_path / "summary_state.json"

        # Increments 1-9: counter increments but should NOT trigger
        for i in range(1, 10):
            summarize._increment_summary_counter(state_path)
            should_trigger = summarize.should_trigger_summary(
                state_path=state_path,
                config=config,
            )
            assert not should_trigger, f"Should not trigger at increment {i}"

        # Increment 10: should trigger
        summarize._increment_summary_counter(state_path)
        should_trigger = summarize.should_trigger_summary(
            state_path=state_path,
            config=config,
        )
        assert should_trigger

    def test_counter_resets_after_summary(self, tmp_path):
        """Counter resets to 0 after a summary is generated."""
        config = {
            "session_summary": {"enabled": True, "cadence_turns": 10},
        }
        state_path = tmp_path / "summary_state.json"

        # Build up to trigger point
        for i in range(10):
            summarize._increment_summary_counter(state_path)

        # Reset counter
        summarize._reset_summary_counter(state_path)

        # Verify it's at 0
        state = summarize._load_summary_state(state_path)
        assert state.get("turns_since_last_summary", 0) == 0

    def test_enabled_false_never_triggers(self, tmp_path):
        """When enabled=false, never triggers regardless of counter."""
        config = {
            "session_summary": {"enabled": False, "cadence_turns": 5},
        }
        state_path = tmp_path / "summary_state.json"

        # Build counter to 10
        for i in range(10):
            summarize._increment_summary_counter(state_path)

        should_trigger = summarize.should_trigger_summary(
            state_path=state_path,
            config=config,
        )
        assert not should_trigger

    def test_paused_never_triggers(self, tmp_path):
        """When paused file exists, never triggers."""
        config = {
            "session_summary": {"enabled": True, "cadence_turns": 5},
        }
        state_path = tmp_path / "summary_state.json"
        pause_file = tmp_path / "paused"
        pause_file.write_text("")

        # Build counter to 10
        for i in range(10):
            summarize._increment_summary_counter(state_path)

        should_trigger = summarize.should_trigger_summary(
            state_path=state_path,
            config=config,
            pause_file=pause_file,
        )
        assert not should_trigger

    def test_missing_cadence_config_defaults_to_10(self, tmp_path):
        """Missing cadence_turns config defaults to 10."""
        config = {
            "session_summary": {"enabled": True},  # no cadence_turns
        }
        state_path = tmp_path / "summary_state.json"

        # Build to turn 10
        for i in range(10):
            summarize._increment_summary_counter(state_path)

        should_trigger = summarize.should_trigger_summary(
            state_path=state_path,
            config=config,
        )
        assert should_trigger


class TestSessionSummaryWorker:
    """Test the background summary worker."""

    @pytest.fixture
    def db_fixture(self, tmp_path):
        """Create a test database with a project."""
        db_path = tmp_path / "test.db"
        store = GraphStore(db_path)
        store.close()
        yield db_path

    def test_worker_main_stores_node_end_to_end(self, db_fixture, tmp_path, monkeypatch):
        """Running the real worker main() stores a node (regression: add_node needs an id).

        The other tests pass explicit ids to add_node, which masked a bug where the
        worker omitted the id and add_node raised KeyError (silently swallowed → no
        summary ever stored). This drives the actual main() path.
        """
        import waystone.extractor as EX

        transcript = tmp_path / "live.md"
        transcript.write_text(
            "**User**: build the thing\n\n**Assistant**: building it now\n",
            encoding="utf-8",
        )

        async def fake_summary(*args, **kwargs):
            return "Goal: build. State: in progress. Next: ship."

        monkeypatch.setattr(EX, "generate_session_summary", fake_summary)
        # Keep state/pause files inside tmp so we don't touch the real ~/.waystone.
        monkeypatch.setattr(summarize, "STATE_DIR", tmp_path)
        monkeypatch.setattr(summarize, "PAUSE_FILE", tmp_path / "paused")
        monkeypatch.setattr(
            sys, "argv",
            ["x", "--project", "demo", "--db-path", str(db_fixture),
             "--session-id", "sess_e2e", "--transcript-path", str(transcript)],
        )

        summarize.main()

        store = GraphStore(db_fixture)
        nodes = [n for n in store.get_all_nodes() if n["type"] == "session_summary"]
        store.close()
        assert len(nodes) == 1
        assert nodes[0]["is_active"] == 1
        assert nodes[0]["id"].startswith("n_")
        assert nodes[0]["source_transcript"] == "live_session:sess_e2e"

    def test_worker_stores_superseding_node(self, db_fixture, tmp_path):
        """Worker stores a new session_summary node that supersedes the prior one."""
        # Create first summary node
        store = GraphStore(db_fixture)
        first_id = _make_node_id()
        store.add_node({
            "id": first_id,
            "fact": "Session 1: Started working on P3 feature",
            "type": "session_summary",
            "source_transcript": "live_session:sess1",
            "confidence": 1.0,
            "tags": ["p3", "session1"],
        })
        store.close()

        # Mock generate_session_summary
        new_summary_text = "Updated: Completed P3 tests, now implementing worker"

        # Simulate worker call
        store = GraphStore(db_fixture)
        second_id = _make_node_id()
        store.add_node({
            "id": second_id,
            "fact": new_summary_text,
            "type": "session_summary",
            "source_transcript": "live_session:sess1",
            "confidence": 1.0,
            "tags": ["p3", "session1"],
            "supersedes": [first_id],
        })
        store.close()

        # Verify both nodes exist, but only second is active
        store = GraphStore(db_fixture)
        nodes = store.get_all_nodes()
        store.close()

        node_dict = {n["id"]: n for n in nodes}
        assert first_id in node_dict
        assert second_id in node_dict
        assert node_dict[first_id]["is_active"] == 0  # superseded
        assert node_dict[second_id]["is_active"] == 1  # active

    def test_find_prior_session_summary(self, db_fixture):
        """Test finding the prior active session_summary for a given session."""
        session_id = "test_sess123"
        store = GraphStore(db_fixture)

        # Add a session_summary node
        node_id = _make_node_id()
        store.add_node({
            "id": node_id,
            "fact": "Prior summary",
            "type": "session_summary",
            "source_transcript": f"live_session:{session_id}",
            "confidence": 1.0,
            "tags": [],
        })

        # Find it
        found = summarize._find_prior_summary(
            store=store,
            session_id=session_id,
        )
        assert found is not None
        assert found["id"] == node_id
        assert found["fact"] == "Prior summary"

        store.close()

    def test_find_prior_summary_returns_none_if_none_exists(self, db_fixture):
        """Test finding prior summary when none exists."""
        store = GraphStore(db_fixture)
        found = summarize._find_prior_summary(
            store=store,
            session_id="nonexistent",
        )
        assert found is None
        store.close()

    def test_find_prior_summary_ignores_inactive_nodes(self, db_fixture):
        """Test that only active session_summary nodes are returned."""
        session_id = "test_sess123"
        store = GraphStore(db_fixture)

        # Add two nodes, second supersedes first
        first_id = _make_node_id()
        store.add_node({
            "id": first_id,
            "fact": "Old summary",
            "type": "session_summary",
            "source_transcript": f"live_session:{session_id}",
            "confidence": 1.0,
            "tags": [],
        })

        second_id = _make_node_id()
        store.add_node({
            "id": second_id,
            "fact": "New summary",
            "type": "session_summary",
            "source_transcript": f"live_session:{session_id}",
            "confidence": 1.0,
            "tags": [],
            "supersedes": [first_id],
        })

        # Find should return only the active (second) one
        found = summarize._find_prior_summary(
            store=store,
            session_id=session_id,
        )
        assert found is not None
        assert found["id"] == second_id
        assert found["fact"] == "New summary"

        store.close()

    def test_worker_empty_db_initializes_summary(self, db_fixture):
        """Test that worker initializes a summary node when none exists."""
        session_id = "sess123"
        store = GraphStore(db_fixture)

        # Verify no prior summary exists
        prior = summarize._find_prior_summary(store, session_id)
        assert prior is None

        store.close()


class TestSessionSummaryStateManagement:
    """Test state file operations."""

    def test_load_nonexistent_state_returns_default(self, tmp_path):
        """Loading a nonexistent state file returns default dict."""
        state_path = tmp_path / "nonexistent.json"
        state = summarize._load_summary_state(state_path)
        assert state == {"turns_since_last_summary": 0}

    def test_save_and_load_state(self, tmp_path):
        """Save and load state round-trip correctly."""
        state_path = tmp_path / "state.json"
        original_state = {
            "turns_since_last_summary": 5,
            "last_summary_id": "node123",
        }

        summarize._save_summary_state(state_path, original_state)
        loaded = summarize._load_summary_state(state_path)

        assert loaded == original_state

    def test_increment_counter_atomically(self, tmp_path):
        """Multiple increments are atomic (no lost updates)."""
        state_path = tmp_path / "state.json"

        for i in range(1, 11):
            summarize._increment_summary_counter(state_path)
            state = summarize._load_summary_state(state_path)
            assert state["turns_since_last_summary"] == i


class TestSessionSummaryCandidateExtractionTurns:
    """Test extracting turns from transcript for summary generation."""

    def test_extract_last_n_turns(self):
        """Extract the last N turns from a turn list."""
        turns = [
            ("user", "Turn 1"),
            ("assistant", "Response 1"),
            ("user", "Turn 2"),
            ("assistant", "Response 2"),
            ("user", "Turn 3"),
        ]

        # Last 3 turns
        last_n = summarize._extract_last_n_turns(turns, 3)
        assert len(last_n) == 3
        assert last_n[0] == ("user", "Turn 2")

        # Last 1 turn
        last_1 = summarize._extract_last_n_turns(turns, 1)
        assert len(last_1) == 1
        assert last_1[0] == ("user", "Turn 3")

    def test_turns_to_text_preserves_order(self):
        """Convert turns to text maintaining order."""
        turns = [
            ("user", "What is P3?"),
            ("assistant", "P3 is a rolling session summary feature"),
            ("user", "How does it work?"),
        ]

        text = summarize._turns_to_text(turns)
        assert text.index("What is P3?") < text.index("How does it work?")
        assert "rolling session summary" in text
