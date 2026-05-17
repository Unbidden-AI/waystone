"""Tests for the feedback labeling system (store methods + CLI command)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from waystone.store import GraphStore
from waystone.cli import cli


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store(tmp_path: Path) -> GraphStore:
    db = tmp_path / "context.db"
    return GraphStore(db)


def _add_node(store: GraphStore, fact: str = "test fact", ntype: str = "decision") -> str:
    """Insert a node and return its ID."""
    import uuid
    nid = "n_" + uuid.uuid4().hex[:8]
    nodes = [{"id": nid, "fact": fact, "type": ntype, "confidence": 0.9, "tags": ["test"]}]
    store.merge_extraction(nodes, [])
    return nid


# ---------------------------------------------------------------------------
# GraphStore feedback methods
# ---------------------------------------------------------------------------

class TestAddFeedback:
    def test_thumbs_up(self, tmp_path):
        store = _make_store(tmp_path)
        nid = _add_node(store)
        result = store.add_feedback(nid, rating=1)
        assert result is True
        store.close()

    def test_thumbs_down(self, tmp_path):
        store = _make_store(tmp_path)
        nid = _add_node(store)
        result = store.add_feedback(nid, rating=-1)
        assert result is True
        store.close()

    def test_unknown_node_returns_false(self, tmp_path):
        store = _make_store(tmp_path)
        result = store.add_feedback("n_doesnotexist", rating=1)
        assert result is False
        store.close()

    def test_upsert_changes_rating(self, tmp_path):
        store = _make_store(tmp_path)
        nid = _add_node(store)
        store.add_feedback(nid, rating=1)
        store.add_feedback(nid, rating=-1)  # overwrite
        fb = store.get_feedback(nid)
        assert fb is not None
        assert fb["rating"] == -1
        store.close()

    def test_comment_stored(self, tmp_path):
        store = _make_store(tmp_path)
        nid = _add_node(store)
        store.add_feedback(nid, rating=1, comment="great fact")
        fb = store.get_feedback(nid)
        assert fb["comment"] == "great fact"
        store.close()


class TestGetFeedback:
    def test_returns_none_for_unrated(self, tmp_path):
        store = _make_store(tmp_path)
        nid = _add_node(store)
        assert store.get_feedback(nid) is None
        store.close()

    def test_returns_dict_after_rating(self, tmp_path):
        store = _make_store(tmp_path)
        nid = _add_node(store)
        store.add_feedback(nid, rating=1)
        fb = store.get_feedback(nid)
        assert fb is not None
        assert fb["node_id"] == nid
        assert fb["rating"] == 1
        assert fb["created_at"]
        store.close()


class TestGetUnratedNodes:
    def test_returns_unrated_only(self, tmp_path):
        store = _make_store(tmp_path)
        nid1 = _add_node(store, fact="fact 1")
        nid2 = _add_node(store, fact="fact 2")
        store.add_feedback(nid1, rating=1)
        unrated = store.get_unrated_nodes(limit=50)
        ids = [n["id"] for n in unrated]
        assert nid1 not in ids
        assert nid2 in ids
        store.close()

    def test_limit_respected(self, tmp_path):
        store = _make_store(tmp_path)
        for i in range(5):
            _add_node(store, fact=f"fact {i}")
        unrated = store.get_unrated_nodes(limit=3)
        assert len(unrated) <= 3
        store.close()

    def test_empty_when_all_rated(self, tmp_path):
        store = _make_store(tmp_path)
        nid = _add_node(store)
        store.add_feedback(nid, rating=1)
        assert store.get_unrated_nodes() == []
        store.close()


class TestGetRatedNodes:
    def test_all_rated(self, tmp_path):
        store = _make_store(tmp_path)
        nid1 = _add_node(store, fact="good fact")
        nid2 = _add_node(store, fact="bad fact")
        store.add_feedback(nid1, rating=1)
        store.add_feedback(nid2, rating=-1)
        rated = store.get_rated_nodes()
        ids = [n["id"] for n in rated]
        assert nid1 in ids
        assert nid2 in ids
        store.close()

    def test_filter_thumbs_up(self, tmp_path):
        store = _make_store(tmp_path)
        nid1 = _add_node(store, fact="good")
        nid2 = _add_node(store, fact="bad")
        store.add_feedback(nid1, rating=1)
        store.add_feedback(nid2, rating=-1)
        up = store.get_rated_nodes(rating=1)
        ids = [n["id"] for n in up]
        assert nid1 in ids
        assert nid2 not in ids
        store.close()

    def test_filter_thumbs_down(self, tmp_path):
        store = _make_store(tmp_path)
        nid1 = _add_node(store, fact="good")
        nid2 = _add_node(store, fact="bad")
        store.add_feedback(nid1, rating=1)
        store.add_feedback(nid2, rating=-1)
        down = store.get_rated_nodes(rating=-1)
        ids = [n["id"] for n in down]
        assert nid2 in ids
        assert nid1 not in ids
        store.close()


class TestGetFeedbackStats:
    def test_empty_stats(self, tmp_path):
        store = _make_store(tmp_path)
        _add_node(store)
        stats = store.get_feedback_stats()
        assert stats["total"] == 1
        assert stats["rated"] == 0
        assert stats["unrated"] == 1
        assert stats["thumbs_up"] == 0
        assert stats["thumbs_down"] == 0
        store.close()

    def test_mixed_ratings(self, tmp_path):
        store = _make_store(tmp_path)
        nid1 = _add_node(store, fact="a")
        nid2 = _add_node(store, fact="b")
        nid3 = _add_node(store, fact="c")
        store.add_feedback(nid1, rating=1)
        store.add_feedback(nid2, rating=-1)
        stats = store.get_feedback_stats()
        assert stats["total"] == 3
        assert stats["rated"] == 2
        assert stats["unrated"] == 1
        assert stats["thumbs_up"] == 1
        assert stats["thumbs_down"] == 1
        store.close()


# ---------------------------------------------------------------------------
# JSONL export
# ---------------------------------------------------------------------------

class TestExportJsonl:
    def test_exports_rated_nodes(self, tmp_path):
        from waystone.feedback import export_jsonl
        store = _make_store(tmp_path)
        nid = _add_node(store, fact="JWT is stateless")
        store.add_feedback(nid, rating=1, comment="accurate")

        out = tmp_path / "out.jsonl"
        count = export_jsonl(store, out)
        assert count == 1

        lines = out.read_text().splitlines()
        record = json.loads(lines[0])
        assert record["label"] == 1
        assert record["messages"][1]["content"] == "JWT is stateless"
        assert record["metadata"]["comment"] == "accurate"
        store.close()

    def test_filter_thumbs_up_only(self, tmp_path):
        from waystone.feedback import export_jsonl
        store = _make_store(tmp_path)
        nid1 = _add_node(store, fact="good")
        nid2 = _add_node(store, fact="bad")
        store.add_feedback(nid1, rating=1)
        store.add_feedback(nid2, rating=-1)

        out = tmp_path / "up_only.jsonl"
        count = export_jsonl(store, out, rating=1)
        assert count == 1
        record = json.loads(out.read_text().strip())
        assert record["label"] == 1
        store.close()

    def test_empty_export_returns_zero(self, tmp_path):
        from waystone.feedback import export_jsonl
        store = _make_store(tmp_path)
        _add_node(store)  # unrated
        out = tmp_path / "empty.jsonl"
        count = export_jsonl(store, out)
        assert count == 0
        store.close()

    def test_jsonl_format_has_messages_field(self, tmp_path):
        from waystone.feedback import export_jsonl
        store = _make_store(tmp_path)
        nid = _add_node(store, fact="Redis chosen for caching")
        store.add_feedback(nid, rating=1)

        out = tmp_path / "out.jsonl"
        export_jsonl(store, out)
        record = json.loads(out.read_text().strip())
        assert "messages" in record
        assert len(record["messages"]) == 2
        assert record["messages"][0]["role"] == "user"
        assert record["messages"][1]["role"] == "assistant"
        store.close()


# ---------------------------------------------------------------------------
# rate_node helper
# ---------------------------------------------------------------------------

class TestRateNode:
    def test_valid_node(self, tmp_path):
        from waystone.feedback import rate_node
        store = _make_store(tmp_path)
        nid = _add_node(store)
        assert rate_node(store, nid, 1) is True
        assert store.get_feedback(nid)["rating"] == 1
        store.close()

    def test_unknown_node(self, tmp_path):
        from waystone.feedback import rate_node
        store = _make_store(tmp_path)
        assert rate_node(store, "n_ghost", -1) is False
        store.close()


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------

@pytest.fixture
def project_with_nodes(tmp_path):
    """Create a real project dir + DB with a few nodes; return (runner, project_name, config_path)."""
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    project_dir = projects_dir / "testproject"
    project_dir.mkdir()

    store = GraphStore(project_dir / "context.db")
    nid1 = _add_node(store, fact="We chose PostgreSQL for ACID guarantees")
    nid2 = _add_node(store, fact="Redis is used for caching")
    store.close()

    # Write a minimal config.yaml
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"llm:\n  base_url: http://localhost:1234/v1\n  model: test\n"
        f"projects_dir: {projects_dir}\n"
    )

    runner = CliRunner()
    return runner, "testproject", str(config_path), project_dir / "context.db"


class TestFeedbackCLI:
    def test_stats_command(self, project_with_nodes):
        runner, project, cfg, db_path = project_with_nodes
        result = runner.invoke(cli, ["--config", cfg, "feedback", project, "--stats"])
        assert result.exit_code == 0
        assert "Total nodes" in result.output
        assert "Unrated" in result.output

    def test_single_node_rating_up(self, project_with_nodes):
        runner, project, cfg, db_path = project_with_nodes
        store = GraphStore(db_path)
        nid = store.get_unrated_nodes(limit=1)[0]["id"]
        store.close()

        result = runner.invoke(cli, ["--config", cfg, "feedback", project,
                                     "--node", nid, "--rating", "up"])
        assert result.exit_code == 0
        assert "thumbs up" in result.output

        store = GraphStore(db_path)
        fb = store.get_feedback(nid)
        store.close()
        assert fb is not None
        assert fb["rating"] == 1

    def test_single_node_rating_down(self, project_with_nodes):
        runner, project, cfg, db_path = project_with_nodes
        store = GraphStore(db_path)
        nid = store.get_unrated_nodes(limit=1)[0]["id"]
        store.close()

        result = runner.invoke(cli, ["--config", cfg, "feedback", project,
                                     "--node", nid, "--rating", "down"])
        assert result.exit_code == 0
        assert "thumbs down" in result.output

    def test_single_node_requires_rating(self, project_with_nodes):
        runner, project, cfg, db_path = project_with_nodes
        result = runner.invoke(cli, ["--config", cfg, "feedback", project,
                                     "--node", "n_abc123"])
        assert result.exit_code != 0

    def test_export_jsonl(self, project_with_nodes, tmp_path):
        runner, project, cfg, db_path = project_with_nodes
        store = GraphStore(db_path)
        nid = store.get_unrated_nodes(limit=1)[0]["id"]
        store.add_feedback(nid, rating=1)
        store.close()

        out_file = tmp_path / "training.jsonl"
        result = runner.invoke(cli, ["--config", cfg, "feedback", project,
                                     "--export", str(out_file)])
        assert result.exit_code == 0
        assert "1 record" in result.output
        assert out_file.exists()
        record = json.loads(out_file.read_text().strip())
        assert record["label"] == 1

    def test_export_empty_no_rated(self, project_with_nodes, tmp_path):
        runner, project, cfg, db_path = project_with_nodes
        out_file = tmp_path / "empty.jsonl"
        result = runner.invoke(cli, ["--config", cfg, "feedback", project,
                                     "--export", str(out_file)])
        assert result.exit_code == 0
        assert "No rated nodes" in result.output

    def test_export_only_up(self, project_with_nodes, tmp_path):
        runner, project, cfg, db_path = project_with_nodes
        store = GraphStore(db_path)
        nodes = store.get_unrated_nodes(limit=2)
        store.add_feedback(nodes[0]["id"], rating=1)
        store.add_feedback(nodes[1]["id"], rating=-1)
        store.close()

        out_file = tmp_path / "up.jsonl"
        result = runner.invoke(cli, ["--config", cfg, "feedback", project,
                                     "--export", str(out_file), "--only-up"])
        assert result.exit_code == 0
        lines = [l for l in out_file.read_text().splitlines() if l.strip()]
        assert len(lines) == 1
        assert json.loads(lines[0])["label"] == 1

    def test_interactive_mode_quit(self, project_with_nodes):
        runner, project, cfg, db_path = project_with_nodes
        # Simulate pressing 'q' immediately
        result = runner.invoke(cli, ["--config", cfg, "feedback", project], input="q\n")
        assert result.exit_code == 0
        # Should not have rated anything
        store = GraphStore(db_path)
        stats = store.get_feedback_stats()
        store.close()
        assert stats["rated"] == 0


# ---------------------------------------------------------------------------
# Auto-labeling tests
# ---------------------------------------------------------------------------

class TestAutoLabel:
    def test_auto_label_rates_nodes(self, tmp_path):
        """Test that auto_label calls LLM and writes ratings to DB."""
        from waystone.feedback import auto_label

        store = _make_store(tmp_path)
        nid1 = _add_node(store, fact="PostgreSQL is used for persistence")
        nid2 = _add_node(store, fact="Redis caches frequently accessed data")

        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()

        # Mock LLM to return "1" for each call
        with patch("waystone.feedback._call_llm_judge") as mock_llm:
            mock_llm.return_value = "1"
            result = auto_label(store, "testproject", transcripts_dir, {"model": "test", "base_url": "http://localhost:1234/v1"}, limit=10, dry_run=False)

        assert result["rated"] == 2
        assert result["thumbs_up"] == 2
        assert result["thumbs_down"] == 0
        assert result["skipped"] == 0

        # Verify ratings were written
        fb1 = store.get_feedback(nid1)
        fb2 = store.get_feedback(nid2)
        store.close()
        assert fb1 is not None and fb1["rating"] == 1
        assert fb2 is not None and fb2["rating"] == 1

    def test_auto_label_dry_run(self, tmp_path):
        """Test that dry_run prevents writing ratings to DB."""
        from waystone.feedback import auto_label

        store = _make_store(tmp_path)
        nid = _add_node(store, fact="Redis caches frequently accessed data")

        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()

        with patch("waystone.feedback._call_llm_judge") as mock_llm:
            mock_llm.return_value = "1"
            result = auto_label(store, "testproject", transcripts_dir, {"model": "test", "base_url": "http://localhost:1234/v1"}, limit=10, dry_run=True)

        # Dry run reports the rating was processed, but...
        assert result["rated"] == 1
        assert result["thumbs_up"] == 1

        # ...the actual feedback is NOT written to DB
        fb = store.get_feedback(nid)
        store.close()
        assert fb is None

    def test_auto_label_skips_on_bad_response(self, tmp_path):
        """Test that unparseable responses are skipped (node not rated)."""
        from waystone.feedback import auto_label

        store = _make_store(tmp_path)
        nid1 = _add_node(store, fact="Fact 1")
        nid2 = _add_node(store, fact="Fact 2")

        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()

        # Get the nodes in the order they'll be processed (newest first)
        unrated_order = store.get_unrated_nodes(limit=10)
        unrated_ids = [n["id"] for n in unrated_order]

        with patch("waystone.feedback._call_llm_judge") as mock_llm:
            # First call returns unparseable, second returns valid "1"
            mock_llm.side_effect = ["maybe", "1"]
            result = auto_label(store, "testproject", transcripts_dir, {"model": "test", "base_url": "http://localhost:1234/v1"}, limit=10, dry_run=False)

        assert result["rated"] == 1
        assert result["thumbs_up"] == 1
        assert result["thumbs_down"] == 0
        assert result["skipped"] == 1

        # First unrated node (nid2, newest) should NOT be rated
        fb_first = store.get_feedback(unrated_ids[0])
        assert fb_first is None

        # Second unrated node (nid1, older) SHOULD be rated
        fb_second = store.get_feedback(unrated_ids[1])
        store.close()
        assert fb_second is not None and fb_second["rating"] == 1

    def test_auto_label_returns_stats(self, tmp_path):
        """Test that returned dict has correct keys."""
        from waystone.feedback import auto_label

        store = _make_store(tmp_path)
        nid = _add_node(store, fact="Test fact")

        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()

        with patch("waystone.feedback._call_llm_judge") as mock_llm:
            mock_llm.return_value = "1"
            result = auto_label(store, "testproject", transcripts_dir, {"model": "test", "base_url": "http://localhost:1234/v1"}, limit=10, dry_run=False)

        store.close()

        # Verify dict structure
        assert "rated" in result
        assert "skipped" in result
        assert "thumbs_up" in result
        assert "thumbs_down" in result
        assert result["rated"] == 1
        assert result["skipped"] == 0
        assert result["thumbs_up"] == 1
        assert result["thumbs_down"] == 0

    def test_auto_label_empty_when_all_rated(self, tmp_path):
        """Test that auto_label returns empty stats when no unrated nodes."""
        from waystone.feedback import auto_label

        store = _make_store(tmp_path)
        nid = _add_node(store, fact="Already rated")
        store.add_feedback(nid, rating=1)

        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()

        with patch("waystone.feedback._call_llm_judge") as mock_llm:
            result = auto_label(store, "testproject", transcripts_dir, {"model": "test", "base_url": "http://localhost:1234/v1"}, limit=10, dry_run=False)

        store.close()

        # Should not call LLM at all
        assert not mock_llm.called
        assert result["rated"] == 0
        assert result["skipped"] == 0
