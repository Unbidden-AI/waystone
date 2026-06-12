"""Tests for extraction failure logging system."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from waystone.store import GraphStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store(tmp_path: Path) -> GraphStore:
    """Create a fresh GraphStore for testing."""
    db = tmp_path / "context.db"
    return GraphStore(db)


# ---------------------------------------------------------------------------
# Tests: logging failures
# ---------------------------------------------------------------------------

class TestLogExtractionFailure:
    """Test log_extraction_failure method."""

    def test_log_failure_writes_to_db(self, tmp_path):
        """Verify that a failure is recorded in the database."""
        store = _make_store(tmp_path)
        store.log_extraction_failure(
            error_type="json_parse",
            error_message="Could not parse JSON from LLM response",
            raw_response='{"incomplete":',
            source_transcript="test_transcript.md",
            model="gpt-4",
        )
        store.close()

        # Reopen and verify
        store = _make_store(tmp_path)
        failures = store.get_extraction_failures(limit=50)
        assert len(failures) == 1
        assert failures[0]["error_type"] == "json_parse"
        assert failures[0]["error_message"] == "Could not parse JSON from LLM response"
        assert failures[0]["source_transcript"] == "test_transcript.md"
        assert failures[0]["model"] == "gpt-4"
        store.close()

    def test_log_failure_truncates_raw_response(self, tmp_path):
        """Verify that raw_response is truncated to 2000 chars."""
        store = _make_store(tmp_path)
        long_response = "x" * 3000
        store.log_extraction_failure(
            error_type="api",
            error_message="Timeout",
            raw_response=long_response,
        )
        store.close()

        store = _make_store(tmp_path)
        failures = store.get_extraction_failures(limit=1)
        assert len(failures[0]["raw_response"]) == 2000
        store.close()

    def test_log_failure_with_optional_fields_omitted(self, tmp_path):
        """Verify that optional fields can be omitted."""
        store = _make_store(tmp_path)
        store.log_extraction_failure(
            error_type="schema",
            error_message="Missing 'relation' key in edge",
        )
        store.close()

        store = _make_store(tmp_path)
        failures = store.get_extraction_failures(limit=1)
        assert failures[0]["error_type"] == "schema"
        assert failures[0]["raw_response"] == ""
        assert failures[0]["source_transcript"] is None or failures[0]["source_transcript"] == ""
        assert failures[0]["model"] is None or failures[0]["model"] == ""
        store.close()

    def test_multiple_failures_recorded(self, tmp_path):
        """Verify that multiple failures can be logged."""
        store = _make_store(tmp_path)
        store.log_extraction_failure(
            error_type="json_parse",
            error_message="First error",
            source_transcript="transcript1.md",
        )
        store.log_extraction_failure(
            error_type="api",
            error_message="Second error",
            source_transcript="transcript2.md",
        )
        store.close()

        store = _make_store(tmp_path)
        failures = store.get_extraction_failures(limit=50)
        assert len(failures) == 2
        assert failures[0]["source_transcript"] == "transcript2.md"  # newest first
        assert failures[1]["source_transcript"] == "transcript1.md"
        store.close()


# ---------------------------------------------------------------------------
# Tests: retrieving failures
# ---------------------------------------------------------------------------

class TestGetExtractionFailures:
    """Test get_extraction_failures method."""

    def test_get_failures_returns_list(self, tmp_path):
        """Verify that get_extraction_failures returns a list of dicts."""
        store = _make_store(tmp_path)
        store.log_extraction_failure(
            error_type="timeout",
            error_message="Request timeout after 600s",
        )
        failures = store.get_extraction_failures(limit=10)
        assert isinstance(failures, list)
        assert len(failures) == 1
        assert isinstance(failures[0], dict)
        assert "error_type" in failures[0]
        assert "error_message" in failures[0]
        store.close()

    def test_get_failures_returns_empty_list_when_none(self, tmp_path):
        """Verify that get_extraction_failures returns [] when no failures."""
        store = _make_store(tmp_path)
        failures = store.get_extraction_failures(limit=10)
        assert failures == []
        store.close()

    def test_get_failures_respects_limit(self, tmp_path):
        """Verify that the limit parameter is respected."""
        store = _make_store(tmp_path)
        for i in range(20):
            store.log_extraction_failure(
                error_type="json_parse",
                error_message=f"Error {i}",
            )
        store.close()

        store = _make_store(tmp_path)
        failures = store.get_extraction_failures(limit=5)
        assert len(failures) == 5
        # Most recent first
        assert "Error 19" in failures[0]["error_message"]
        store.close()

    def test_get_failures_ordered_by_created_at_desc(self, tmp_path):
        """Verify that failures are ordered newest first."""
        store = _make_store(tmp_path)
        store.log_extraction_failure(
            error_type="schema",
            error_message="First",
            source_transcript="t1.md",
        )
        store.log_extraction_failure(
            error_type="api",
            error_message="Second",
            source_transcript="t2.md",
        )
        store.log_extraction_failure(
            error_type="timeout",
            error_message="Third",
            source_transcript="t3.md",
        )
        store.close()

        store = _make_store(tmp_path)
        failures = store.get_extraction_failures(limit=50)
        assert len(failures) == 3
        assert failures[0]["source_transcript"] == "t3.md"
        assert failures[1]["source_transcript"] == "t2.md"
        assert failures[2]["source_transcript"] == "t1.md"
        store.close()


# ---------------------------------------------------------------------------
# Tests: migration safety
# ---------------------------------------------------------------------------

class TestFailuresTableMigration:
    """Test that the extraction_failures table is migration-safe."""

    def test_failures_table_created_on_new_db(self, tmp_path):
        """Verify that extraction_failures table is created on new database."""
        store = _make_store(tmp_path)
        # Should not raise an error
        store.log_extraction_failure(
            error_type="test",
            error_message="Test message",
        )
        store.close()

    def test_failures_table_created_on_existing_db_with_nodes(self, tmp_path):
        """Verify that extraction_failures table is created on existing DB.

        This tests migration safety: the table should be created even if
        the DB already has nodes, edges, and feedback.
        """
        db_path = tmp_path / "context.db"

        # Step 1: Create DB with nodes and feedback (simulating existing DB)
        store = _make_store(tmp_path)
        import uuid
        nid = "n_" + uuid.uuid4().hex[:8]
        store.merge_extraction(
            [{"id": nid, "fact": "test fact", "type": "decision", "tags": ["test"]}],
            []
        )
        store.add_feedback(nid, rating=1, comment="good")
        store.close()

        # Step 2: Reopen same DB and try to log extraction failure
        # This should succeed even though extraction_failures table didn't exist before
        store = GraphStore(db_path)
        store.log_extraction_failure(
            error_type="json_parse",
            error_message="Test error",
            source_transcript="test.md",
        )
        store.close()

        # Step 3: Verify all data persisted
        store = GraphStore(db_path)
        nodes = store.get_all_nodes()
        feedback = store.get_rated_nodes(rating=1)
        failures = store.get_extraction_failures(limit=10)

        assert len(nodes) == 1
        assert len(feedback) == 1
        assert len(failures) == 1
        assert failures[0]["source_transcript"] == "test.md"
        store.close()

    def test_failures_table_exists_query_succeeds(self, tmp_path):
        """Verify that the extraction_failures table exists and is queryable."""
        store = _make_store(tmp_path)
        # This query should succeed
        failures = store.get_extraction_failures(limit=1)
        assert isinstance(failures, list)
        store.close()

    def test_failures_table_has_correct_columns(self, tmp_path):
        """Verify that the extraction_failures table has the expected columns."""
        db_path = tmp_path / "context.db"
        store = GraphStore(db_path)
        store.log_extraction_failure(
            error_type="test",
            error_message="test",
            raw_response="response",
            source_transcript="source",
            model="model",
        )
        store.close()

        # Verify columns by querying directly
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(extraction_failures)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        assert "id" in columns
        assert "created_at" in columns
        assert "source_transcript" in columns
        assert "model" in columns
        assert "error_type" in columns
        assert "raw_response" in columns
        assert "error_message" in columns
        conn.close()
