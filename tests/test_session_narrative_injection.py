"""Tests for P4 per-prompt injection of the latest session narrative."""

import uuid

from waystone._hooks import submit
from waystone.store import GraphStore


def _nid():
    return f"n_{uuid.uuid4().hex[:8]}"


def _add_summary(store, fact, session_id, created_at, supersedes=None):
    node = {
        "id": _nid(),
        "fact": fact,
        "type": "session_summary",
        "source_transcript": f"live_session:{session_id}",
        "confidence": 1.0,
        "tags": ["rolling", "session"],
        "created_at": created_at,
        "occurred_at": created_at,
        "supersedes": supersedes or [],
    }
    store.add_node(node)
    return node["id"]


def test_returns_empty_when_no_summaries(tmp_path):
    store = GraphStore(tmp_path / "db.sqlite")
    try:
        assert submit._latest_session_narrative(store) == ""
    finally:
        store.close()


def test_returns_most_recent_active_across_project(tmp_path):
    store = GraphStore(tmp_path / "db.sqlite")
    try:
        _add_summary(store, "Older narrative.", "sessA", "2026-06-10T01:00:00")
        _add_summary(store, "Newest narrative.", "sessB", "2026-06-10T05:00:00")
        out = submit._latest_session_narrative(store)
        assert out == "Newest narrative."
    finally:
        store.close()


def test_prefers_current_session_for_continuity(tmp_path):
    """When the current session has its own summary, prefer it over a newer other-session one."""
    store = GraphStore(tmp_path / "db.sqlite")
    try:
        _add_summary(store, "Current session state.", "mine", "2026-06-10T02:00:00")
        _add_summary(store, "Someone else, but newer.", "other", "2026-06-10T09:00:00")
        out = submit._latest_session_narrative(store, session_id="mine")
        assert out == "Current session state."
    finally:
        store.close()


def test_superseded_summary_is_skipped(tmp_path):
    """Only the active head of a supersede chain is injected (history kept, not surfaced)."""
    store = GraphStore(tmp_path / "db.sqlite")
    try:
        first = _add_summary(store, "Step one.", "s", "2026-06-10T01:00:00")
        _add_summary(store, "Step two (current).", "s", "2026-06-10T02:00:00", supersedes=[first])
        out = submit._latest_session_narrative(store, session_id="s")
        assert out == "Step two (current)."
    finally:
        store.close()
