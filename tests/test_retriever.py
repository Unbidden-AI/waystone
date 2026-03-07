"""Tests for the retriever: keyword extraction, BFS, markdown assembly."""

import pytest

from context_broker.retriever import assemble_markdown, extract_keywords, retrieve
from context_broker.store import GraphStore


@pytest.fixture
def store(tmp_path):
    """Create a store with a sample graph."""
    db_path = tmp_path / "test.db"
    s = GraphStore(db_path)

    # Build a small graph:
    # n1 (api, webhook) --depends_on--> n2 (error, handling)
    # n1 --relates_to--> n3 (garmin, api)
    # n3 --flows_to--> n4 (database, storage)
    nodes = [
        {"id": "n1", "fact": "Webhook endpoint is POST /api/webhooks/garmin", "type": "implementation",
         "confidence": 0.95, "tags": ["api", "webhook"], "created_at": "2026-03-07T00:00:00Z", "supersedes": []},
        {"id": "n2", "fact": "Webhook must validate HMAC signature", "type": "constraint",
         "confidence": 0.9, "tags": ["error", "handling", "webhook"], "created_at": "2026-03-07T00:01:00Z", "supersedes": []},
        {"id": "n3", "fact": "Garmin API uses OAuth2 for auth", "type": "decision",
         "confidence": 0.85, "tags": ["garmin", "api", "auth"], "created_at": "2026-03-07T00:02:00Z", "supersedes": []},
        {"id": "n4", "fact": "Health data stored in PostgreSQL", "type": "implementation",
         "confidence": 0.9, "tags": ["database", "storage"], "created_at": "2026-03-07T00:03:00Z", "supersedes": []},
        {"id": "n5", "fact": "Use React for frontend", "type": "decision",
         "confidence": 0.8, "tags": ["frontend", "react"], "created_at": "2026-03-07T00:04:00Z", "supersedes": []},
    ]
    for n in nodes:
        s.add_node(n)

    s.add_edge("n1", "n2", "depends_on")
    s.add_edge("n1", "n3", "relates_to")
    s.add_edge("n3", "n4", "flows_to")

    yield s
    s.close()


class TestExtractKeywords:
    def test_basic(self):
        assert extract_keywords("add webhook error handling") == ["add", "webhook", "error", "handling"]

    def test_strips_stop_words(self):
        kw = extract_keywords("what is the webhook for the api")
        assert "what" not in kw
        assert "is" not in kw
        assert "the" not in kw
        assert "webhook" in kw
        assert "api" in kw

    def test_strips_punctuation(self):
        kw = extract_keywords("webhook, api! database?")
        assert kw == ["webhook", "api", "database"]

    def test_empty_input(self):
        assert extract_keywords("") == []

    def test_all_stop_words(self):
        assert extract_keywords("the is a an") == []


class TestRetrieve:
    def test_finds_relevant_nodes(self, store):
        result = retrieve(store, "webhook error handling", hops=1)
        assert "Webhook endpoint" in result
        assert "HMAC signature" in result

    def test_bfs_traversal(self, store):
        # Querying "api" should find n1 and n3, and BFS should reach n2 and n4
        result = retrieve(store, "api webhook", hops=2)
        assert "Webhook endpoint" in result
        assert "Garmin API" in result

    def test_no_matches(self, store):
        result = retrieve(store, "kubernetes deployment")
        assert "No relevant context found" in result

    def test_top_k_limits(self, store):
        result = retrieve(store, "api webhook garmin", hops=3, top_k=2)
        # Should only contain 2 nodes
        assert result.count("- ") <= 2

    def test_unrelated_nodes_excluded(self, store):
        result = retrieve(store, "webhook error handling", hops=1)
        assert "React" not in result


class TestAssembleMarkdown:
    def test_groups_by_type(self):
        nodes = [
            {"id": "n1", "fact": "Decision A", "type": "decision", "confidence": 0.8,
             "tags": [], "source_transcript": None, "source_message_index": None, "supersedes": []},
            {"id": "n2", "fact": "Impl B", "type": "implementation", "confidence": 0.9,
             "tags": [], "source_transcript": None, "source_message_index": None, "supersedes": []},
        ]
        md = assemble_markdown(nodes, "test task")
        # Decisions section should come before implementations
        dec_pos = md.index("Decisions")
        impl_pos = md.index("Implementations")
        assert dec_pos < impl_pos

    def test_includes_source_refs(self):
        nodes = [
            {"id": "n1", "fact": "Some fact", "type": "implementation", "confidence": 0.9,
             "tags": [], "source_transcript": "session.md", "source_message_index": 5, "supersedes": []},
        ]
        md = assemble_markdown(nodes, "test")
        assert "[source: session.md:5]" in md

    def test_empty_nodes(self):
        assert "No relevant context found" in assemble_markdown([], "test")
