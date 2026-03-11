"""Tests for the SQLite graph store."""


import pytest

from context_broker.store import GraphStore


@pytest.fixture
def store(tmp_path):
    """Create a fresh GraphStore in a temp directory."""
    db_path = tmp_path / "test.db"
    s = GraphStore(db_path)
    yield s
    s.close()


def _make_node(id="n_test1", fact="Test fact", type="implementation", **kwargs):
    return {
        "id": id,
        "fact": fact,
        "type": type,
        "confidence": kwargs.get("confidence", 0.9),
        "source_transcript": kwargs.get("source_transcript", "test.md"),
        "source_message_index": kwargs.get("source_message_index", 0),
        "tags": kwargs.get("tags", ["test"]),
        "created_at": kwargs.get("created_at", "2026-03-07T00:00:00Z"),
        "supersedes": kwargs.get("supersedes", []),
    }


class TestAddAndGetNode:
    def test_add_and_get(self, store):
        node = _make_node()
        store.add_node(node)
        result = store.get_node("n_test1")
        assert result is not None
        assert result["fact"] == "Test fact"
        assert result["type"] == "implementation"
        assert result["confidence"] == 0.9
        assert result["tags"] == ["test"]

    def test_get_nonexistent(self, store):
        assert store.get_node("nonexistent") is None

    def test_replace_on_duplicate_id(self, store):
        store.add_node(_make_node(fact="Original"))
        store.add_node(_make_node(fact="Updated"))
        result = store.get_node("n_test1")
        assert result["fact"] == "Updated"


class TestEdges:
    def test_add_and_get_edges(self, store):
        store.add_node(_make_node(id="n_a"))
        store.add_node(_make_node(id="n_b"))
        store.add_edge("n_a", "n_b", "depends_on")

        edges_from = store.get_edges_from("n_a")
        assert len(edges_from) == 1
        assert edges_from[0]["to_id"] == "n_b"

        edges_to = store.get_edges_to("n_b")
        assert len(edges_to) == 1
        assert edges_to[0]["from_id"] == "n_a"

    def test_duplicate_edge_ignored(self, store):
        store.add_node(_make_node(id="n_a"))
        store.add_node(_make_node(id="n_b"))
        store.add_edge("n_a", "n_b", "depends_on")
        store.add_edge("n_a", "n_b", "depends_on")
        assert len(store.get_edges_from("n_a")) == 1

    def test_different_relations(self, store):
        store.add_node(_make_node(id="n_a"))
        store.add_node(_make_node(id="n_b"))
        store.add_edge("n_a", "n_b", "depends_on")
        store.add_edge("n_a", "n_b", "relates_to")
        assert len(store.get_edges_from("n_a")) == 2


class TestTagMatching:
    def test_finds_matching_tags(self, store):
        store.add_node(_make_node(id="n_1", tags=["api", "webhook"]))
        store.add_node(_make_node(id="n_2", tags=["database", "sql"]))
        store.add_node(_make_node(id="n_3", tags=["api", "auth"]))

        results = store.get_nodes_by_tags(["api"])
        ids = {n["id"] for n in results}
        assert ids == {"n_1", "n_3"}

    def test_multiple_tags_or(self, store):
        store.add_node(_make_node(id="n_1", tags=["api"]))
        store.add_node(_make_node(id="n_2", tags=["database"]))
        results = store.get_nodes_by_tags(["api", "database"])
        assert len(results) == 2

    def test_empty_tags(self, store):
        assert store.get_nodes_by_tags([]) == []


class TestMergeExtraction:
    def test_merge_nodes_and_edges(self, store):
        nodes = [
            _make_node(id="n_1", fact="Fact 1"),
            _make_node(id="n_2", fact="Fact 2"),
        ]
        edges = [{"from_id": "n_1", "to_id": "n_2", "relation": "depends_on"}]
        store.merge_extraction(nodes, edges)

        assert store.get_node("n_1") is not None
        assert store.get_node("n_2") is not None
        assert len(store.get_edges_from("n_1")) == 1

    def test_merge_with_supersedes(self, store):
        store.add_node(_make_node(id="n_old", fact="Old decision"))
        nodes = [_make_node(id="n_new", fact="New decision")]
        edges = [{"from_id": "n_new", "to_id": "n_old", "relation": "supersedes"}]
        store.merge_extraction(nodes, edges)

        new_node = store.get_node("n_new")
        assert "n_old" in new_node["supersedes"]


class TestStats:
    def test_empty_stats(self, store):
        stats = store.get_stats()
        assert stats["node_count"] == 0
        assert stats["edge_count"] == 0
        assert stats["type_counts"] == {}

    def test_stats_with_data(self, store):
        store.add_node(_make_node(id="n_1", type="decision"))
        store.add_node(_make_node(id="n_2", type="decision"))
        store.add_node(_make_node(id="n_3", type="implementation"))
        store.add_edge("n_1", "n_2", "relates_to")

        stats = store.get_stats()
        assert stats["node_count"] == 3
        assert stats["edge_count"] == 1
        assert stats["type_counts"] == {"decision": 2, "implementation": 1}


class TestBulkOperations:
    def test_get_all_nodes(self, store):
        store.add_node(_make_node(id="n_1"))
        store.add_node(_make_node(id="n_2"))
        assert len(store.get_all_nodes()) == 2

    def test_get_recent_nodes(self, store):
        store.add_node(_make_node(id="n_1", created_at="2026-01-01T00:00:00Z"))
        store.add_node(_make_node(id="n_2", created_at="2026-03-07T00:00:00Z"))
        recent = store.get_recent_nodes(1)
        assert len(recent) == 1
        assert recent[0]["id"] == "n_2"

    def test_get_all_edges(self, store):
        store.add_node(_make_node(id="n_1"))
        store.add_node(_make_node(id="n_2"))
        store.add_edge("n_1", "n_2", "depends_on")
        assert len(store.get_all_edges()) == 1
