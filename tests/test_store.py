"""Tests for the SQLite graph store."""


import pytest

from waystone.store import GraphStore


@pytest.fixture
def store(tmp_path):
    """Create a fresh GraphStore in a temp directory."""
    db_path = tmp_path / "test.db"
    s = GraphStore(db_path)
    yield s
    s.close()


def _make_node(id="n_test1", fact="Test fact", type="implementation", **kwargs):
    node = {
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
    if "occurred_at" in kwargs:
        node["occurred_at"] = kwargs["occurred_at"]
    return node


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

    def test_set_and_get_edge_query_rule(self, store):
        store.add_node(_make_node(id="n_a"))
        store.add_node(_make_node(id="n_b"))
        store.add_edge("n_a", "n_b", "depends_on")

        store.set_edge_query_rule("n_a", "n_b", "add_synonyms", {"synonyms": ["api", "rest"]})

        rule = store.get_edge_query_rule("n_a", "n_b")
        assert rule is not None
        assert rule["rule_type"] == "add_synonyms"
        assert rule["params"] == {"synonyms": ["api", "rest"]}

    def test_supersedes_edge_auto_wires_rule(self, store):
        store.add_node(_make_node(id="n_old", fact="Old decision"))
        store.add_node(_make_node(id="n_new", fact="New decision"))
        store.add_edge("n_new", "n_old", "supersedes")

        rule = store.get_edge_query_rule("n_new", "n_old")
        assert rule is not None
        assert rule["rule_type"] == "add_superseded_tags"

    def test_get_edge_query_rule_none_when_not_set(self, store):
        store.add_node(_make_node(id="n_a"))
        store.add_node(_make_node(id="n_b"))
        store.add_edge("n_a", "n_b", "depends_on")

        rule = store.get_edge_query_rule("n_a", "n_b")
        assert rule is None


class TestTagMatching:
    def test_finds_matching_tags(self, store):
        store.add_node(_make_node(id="n_1", fact="Fact 1", tags=["api", "webhook"]))
        store.add_node(_make_node(id="n_2", fact="Fact 2", tags=["database", "sql"]))
        store.add_node(_make_node(id="n_3", fact="Fact 3", tags=["api", "auth"]))

        results = store.get_nodes_by_tags(["api"])
        ids = {n["id"] for n in results}
        assert ids == {"n_1", "n_3"}

    def test_multiple_tags_or(self, store):
        store.add_node(_make_node(id="n_1", fact="Fact 1", tags=["api"]))
        store.add_node(_make_node(id="n_2", fact="Fact 2", tags=["database"]))
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
        store.add_node(_make_node(id="n_1", fact="Fact 1", type="decision"))
        store.add_node(_make_node(id="n_2", fact="Fact 2", type="decision"))
        store.add_node(_make_node(id="n_3", fact="Fact 3", type="implementation"))
        store.add_edge("n_1", "n_2", "relates_to")

        stats = store.get_stats()
        assert stats["node_count"] == 3
        assert stats["edge_count"] == 1
        assert stats["type_counts"] == {"decision": 2, "implementation": 1}


class TestBulkOperations:
    def test_get_all_nodes(self, store):
        store.add_node(_make_node(id="n_1", fact="Fact 1"))
        store.add_node(_make_node(id="n_2", fact="Fact 2"))
        assert len(store.get_all_nodes()) == 2

    def test_get_recent_nodes(self, store):
        store.add_node(_make_node(id="n_1", fact="Fact 1", created_at="2026-01-01T00:00:00Z"))
        store.add_node(_make_node(id="n_2", fact="Fact 2", created_at="2026-03-07T00:00:00Z"))
        recent = store.get_recent_nodes(1)
        assert len(recent) == 1
        assert recent[0]["id"] == "n_2"

    def test_get_all_edges(self, store):
        store.add_node(_make_node(id="n_1"))
        store.add_node(_make_node(id="n_2"))
        store.add_edge("n_1", "n_2", "depends_on")
        assert len(store.get_all_edges()) == 1


class TestBitemporalSupersession:
    """Bi-temporal validity window: valid_to is set when a node is superseded."""

    def test_add_node_with_supersedes_closes_valid_to(self, store):
        """add_node() with supersedes list sets valid_to on the old node."""
        old = _make_node(id="n_old", fact="Old fact", occurred_at="2023-01-01T00:00:00+00:00")
        store.add_node(old)
        assert store.get_node("n_old")["is_active"] == 1
        assert store.get_node("n_old")["valid_to"] is None

        new = _make_node(
            id="n_new",
            fact="New fact",
            occurred_at="2023-06-01T00:00:00+00:00",
            supersedes=["n_old"],
        )
        store.add_node(new)

        old_after = store.get_node("n_old")
        assert old_after["is_active"] == 0
        assert old_after["valid_to"] == "2023-06-01T00:00:00+00:00"

    def test_add_edge_supersedes_closes_valid_to(self, store):
        """add_edge() with relation='supersedes' sets valid_to on the superseded node."""
        old = _make_node(id="n_old", fact="Old fact", occurred_at="2023-01-01T00:00:00+00:00")
        new = _make_node(id="n_new", fact="New fact", occurred_at="2023-06-01T00:00:00+00:00")
        store.add_node(old)
        store.add_node(new)

        store.add_edge("n_new", "n_old", "supersedes")

        old_after = store.get_node("n_old")
        assert old_after["is_active"] == 0
        assert old_after["valid_to"] == "2023-06-01T00:00:00+00:00"

    def test_add_edge_supersedes_falls_back_to_created_at(self, store):
        """add_edge() uses created_at if occurred_at is absent."""
        old = _make_node(id="n_old", fact="Old fact")
        new = _make_node(id="n_new", fact="New fact", created_at="2023-09-01T00:00:00+00:00")
        store.add_node(old)
        store.add_node(new)

        store.add_edge("n_new", "n_old", "supersedes")

        old_after = store.get_node("n_old")
        assert old_after["is_active"] == 0
        assert old_after["valid_to"] == "2023-09-01T00:00:00+00:00"

    def test_valid_to_not_overwritten_if_already_set(self, store):
        """valid_to is never moved earlier — COALESCE(valid_to, ?) protects it."""
        old = _make_node(id="n_old", fact="Old fact", occurred_at="2023-01-01T00:00:00+00:00")
        store.add_node(old)
        # First supersession closes at June
        new1 = _make_node(id="n_new1", fact="Mid fact", occurred_at="2023-06-01T00:00:00+00:00",
                           supersedes=["n_old"])
        store.add_node(new1)
        # Second call shouldn't push valid_to later
        new2 = _make_node(id="n_new2", fact="Newest fact", occurred_at="2023-12-01T00:00:00+00:00")
        store.add_node(new2)
        store.add_edge("n_new2", "n_old", "supersedes")

        old_after = store.get_node("n_old")
        assert old_after["valid_to"] == "2023-06-01T00:00:00+00:00"

    def test_get_nodes_at_time_point_in_time_query(self, store):
        """get_nodes_at_time returns only nodes valid at the given timestamp."""
        old = _make_node(
            id="n_old", fact="Old auth: password only",
            occurred_at="2023-01-01T00:00:00+00:00",
        )
        new = _make_node(
            id="n_new", fact="New auth: MFA required",
            occurred_at="2023-06-01T00:00:00+00:00",
            supersedes=["n_old"],
        )
        store.add_node(old)
        store.add_node(new)

        # Before supersession: only old fact valid
        nodes_jan = store.get_nodes_at_time(valid_at="2023-03-01T00:00:00+00:00")
        ids_jan = {n["id"] for n in nodes_jan}
        assert "n_old" in ids_jan
        assert "n_new" not in ids_jan

        # After supersession: only new fact valid
        nodes_dec = store.get_nodes_at_time(valid_at="2023-12-01T00:00:00+00:00")
        ids_dec = {n["id"] for n in nodes_dec}
        assert "n_new" in ids_dec
        assert "n_old" not in ids_dec

    def test_merge_extraction_closes_valid_to_via_supersedes_edge(self, store):
        """merge_extraction() closes valid_to for superseded nodes via edge list."""
        old = _make_node(id="n_old", fact="Old impl", occurred_at="2023-01-01T00:00:00+00:00")
        store.add_node(old)

        new_node = _make_node(id="n_new", fact="New impl", occurred_at="2023-08-01T00:00:00+00:00")
        edge = {"from_id": "n_new", "to_id": "n_old", "relation": "supersedes"}
        store.merge_extraction([new_node], [edge])

        old_after = store.get_node("n_old")
        assert old_after["is_active"] == 0
        assert old_after["valid_to"] == "2023-08-01T00:00:00+00:00"


class TestWorldDBTriggers:
    """SQLite reactive triggers added in WorldDB (SCHEMA_VERSION 17).

    The critical tests bypass Python's add_edge() and insert raw SQL so they
    verify the trigger itself, not the Python belt-and-suspenders.
    """

    def test_edge_supersedes_trigger_fires_via_raw_sql(self, store):
        """INSERT into edges directly — trigger must expire the superseded node."""
        old = _make_node(id="n_old", fact="Old approach", occurred_at="2023-01-01T00:00:00+00:00")
        new = _make_node(id="n_new", fact="New approach", occurred_at="2023-09-01T00:00:00+00:00")
        store.add_node(old)
        store.add_node(new)

        # Bypass add_edge() entirely — only the trigger should fire.
        store.conn.execute(
            "INSERT INTO edges (from_id, to_id, relation) VALUES (?, ?, 'supersedes')",
            ("n_new", "n_old"),
        )
        store.conn.commit()

        old_after = store.get_node("n_old")
        assert old_after["is_active"] == 0, "trigger must set is_active=0"
        assert old_after["valid_to"] == "2023-09-01T00:00:00+00:00", (
            "trigger must use occurred_at of superseding node"
        )

    def test_edge_supersedes_trigger_preserves_existing_valid_to(self, store):
        """Trigger must not overwrite valid_to that is already set (COALESCE guard)."""
        old = _make_node(id="n_old", fact="Old fact", occurred_at="2023-01-01T00:00:00+00:00")
        mid = _make_node(id="n_mid", fact="Mid fact", occurred_at="2023-06-01T00:00:00+00:00")
        new = _make_node(id="n_new", fact="Newest fact", occurred_at="2023-12-01T00:00:00+00:00")
        store.add_node(old)
        store.add_node(mid)
        store.add_node(new)

        # First supersession closes old at June via trigger
        store.conn.execute(
            "INSERT INTO edges (from_id, to_id, relation) VALUES ('n_mid', 'n_old', 'supersedes')"
        )
        store.conn.commit()
        assert store.get_node("n_old")["valid_to"] == "2023-06-01T00:00:00+00:00"

        # Second supersession must NOT move valid_to to December (COALESCE protects it)
        store.conn.execute(
            "INSERT INTO edges (from_id, to_id, relation) VALUES ('n_new', 'n_old', 'supersedes')"
        )
        store.conn.commit()
        assert store.get_node("n_old")["valid_to"] == "2023-06-01T00:00:00+00:00", (
            "valid_to must not be overwritten once set"
        )

    def test_node_delete_cascade_cleans_node_tags(self, store):
        """Deleting a node must cascade-delete its node_tags rows."""
        node = _make_node(id="n_tagged", fact="Tagged node", tags=["alpha", "beta"])
        store.add_node(node)

        count_before = store.conn.execute(
            "SELECT COUNT(*) FROM node_tags WHERE node_id = 'n_tagged'"
        ).fetchone()[0]
        assert count_before > 0, "node_tags must be populated before delete"

        store.conn.execute("DELETE FROM nodes WHERE id = 'n_tagged'")
        store.conn.commit()

        count_after = store.conn.execute(
            "SELECT COUNT(*) FROM node_tags WHERE node_id = 'n_tagged'"
        ).fetchone()[0]
        assert count_after == 0, "trigger must clean node_tags on node delete"

    def test_node_delete_cascade_cleans_edges(self, store):
        """Deleting a node must cascade-delete all edges that reference it."""
        store.add_node(_make_node(id="n_a", fact="Node A"))
        store.add_node(_make_node(id="n_b", fact="Node B"))
        store.add_node(_make_node(id="n_c", fact="Node C"))
        store.add_edge("n_a", "n_b", "depends_on")
        store.add_edge("n_c", "n_a", "relates_to")

        edge_count_before = store.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE from_id='n_a' OR to_id='n_a'"
        ).fetchone()[0]
        assert edge_count_before == 2

        store.conn.execute("DELETE FROM nodes WHERE id = 'n_a'")
        store.conn.commit()

        edge_count_after = store.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE from_id='n_a' OR to_id='n_a'"
        ).fetchone()[0]
        assert edge_count_after == 0, "trigger must clean all edges referencing deleted node"

    def test_node_delete_cascade_cleans_hyperedge_members(self, store):
        """Deleting a node must remove it from hyperedge_members."""
        store.add_node(_make_node(id="n_he", fact="Hyperedge member"))
        store.conn.execute(
            "INSERT INTO hyperedge_members (hyperedge_id, node_id) VALUES ('he_001', 'n_he')"
        )
        store.conn.commit()

        row_before = store.conn.execute(
            "SELECT 1 FROM hyperedge_members WHERE node_id = 'n_he'"
        ).fetchone()
        assert row_before is not None

        store.conn.execute("DELETE FROM nodes WHERE id = 'n_he'")
        store.conn.commit()

        row_after = store.conn.execute(
            "SELECT 1 FROM hyperedge_members WHERE node_id = 'n_he'"
        ).fetchone()
        assert row_after is None, "trigger must remove hyperedge_members row on node delete"

    def test_non_supersedes_edge_does_not_trigger_expiry(self, store):
        """Only relation='supersedes' should activate the expiry trigger."""
        node = _make_node(id="n_stable", fact="Stable fact")
        other = _make_node(id="n_other", fact="Other fact")
        store.add_node(node)
        store.add_node(other)

        store.conn.execute(
            "INSERT INTO edges (from_id, to_id, relation) VALUES ('n_other', 'n_stable', 'relates_to')"
        )
        store.conn.commit()

        result = store.get_node("n_stable")
        assert result["is_active"] == 1, "relates_to edge must not trigger expiry"
        assert result["valid_to"] is None


class TestGetNodeByHash:
    """Content-addressed lookup API (get_node_by_hash / compute_fact_hash)."""

    def test_returns_node_for_known_hash(self, store):
        from waystone.store import compute_fact_hash
        node = _make_node(id="n_hash_test", fact="Content-addressed fact")
        store.add_node(node)

        h = compute_fact_hash("Content-addressed fact")
        result = store.get_node_by_hash(h)
        assert result is not None
        assert result["id"] == "n_hash_test"
        assert result["fact"] == "Content-addressed fact"

    def test_returns_none_for_unknown_hash(self, store):
        result = store.get_node_by_hash("deadbeef00000000")
        assert result is None

    def test_compute_fact_hash_is_stable(self):
        from waystone.store import compute_fact_hash
        h1 = compute_fact_hash("Stable input")
        h2 = compute_fact_hash("Stable input")
        assert h1 == h2

    def test_compute_fact_hash_differs_for_different_inputs(self):
        from waystone.store import compute_fact_hash
        assert compute_fact_hash("Fact A") != compute_fact_hash("Fact B")

    def test_hash_normalizes_whitespace(self):
        from waystone.store import compute_fact_hash
        # Leading/trailing whitespace and case normalization should match stored hash
        h_clean = compute_fact_hash("Some fact")
        h_padded = compute_fact_hash("  Some fact  ")
        assert h_clean == h_padded, "hash should normalize whitespace"


class TestWorldContainers:
    """World-scoped context namespace API and retrieval."""

    def test_create_world_returns_id(self, store):
        """create_world returns a valid world node ID."""
        world_id = store.create_world(name="Test World")
        assert world_id is not None
        assert world_id.startswith("n_")

        # Verify the world node was created
        world_node = store.get_node(world_id)
        assert world_node is not None
        assert world_node["type"] == "world"
        assert world_node["fact"] == "Test World"
        assert world_node["confidence"] == 1.0

    def test_add_node_to_world(self, store):
        """add_node_to_world associates a node with a world."""
        world_id = store.create_world(name="Test World")
        node = _make_node(id="n_test1", fact="Test fact")
        store.add_node(node)

        store.add_node_to_world("n_test1", world_id)

        updated_node = store.get_node("n_test1")
        assert updated_node["world_id"] == world_id

    def test_get_world_nodes_recursive(self, store):
        """get_world_nodes with recursive=True includes child worlds."""
        parent_world = store.create_world(name="Parent World")
        child_world = store.create_world(name="Child World", parent_world_id=parent_world)

        # Add nodes to parent and child
        node1 = _make_node(id="n_p1", fact="Parent fact 1")
        node2 = _make_node(id="n_c1", fact="Child fact 1")
        store.add_node(node1)
        store.add_node(node2)

        store.add_node_to_world("n_p1", parent_world)
        store.add_node_to_world("n_c1", child_world)

        # Non-recursive: only parent's direct members
        parent_nodes = store.get_world_nodes(parent_world, recursive=False)
        parent_ids = {n["id"] for n in parent_nodes}
        assert "n_p1" in parent_ids
        assert "n_c1" not in parent_ids

        # Recursive: parent's members + child world members
        parent_nodes_rec = store.get_world_nodes(parent_world, recursive=True)
        parent_ids_rec = {n["id"] for n in parent_nodes_rec}
        assert "n_p1" in parent_ids_rec
        assert "n_c1" in parent_ids_rec

    def test_add_node_to_nonexistent_world_raises(self, store):
        """add_node_to_world raises ValueError for non-existent world."""
        node = _make_node(id="n_test1", fact="Test fact")
        store.add_node(node)

        with pytest.raises(ValueError):
            store.add_node_to_world("n_test1", "n_nonexistent")

    def test_world_scoped_bfs(self, store):
        """BFS with world_id parameter respects world membership."""
        from waystone.retriever import bfs_collect

        # Create two separate worlds
        world_id = store.create_world(name="Scoped World")
        other_world = store.create_world(name="Other World")

        # Nodes in target world
        n1 = _make_node(id="n_in1", fact="Inside world 1", tags=["world_scoped"])
        n2 = _make_node(id="n_in2", fact="Inside world 2", tags=["world_scoped"])

        # Node in different world (should be excluded)
        n3 = _make_node(id="n_out1", fact="In other world", tags=["not_scoped"])

        # Root-level node directly connected to world node
        n_root = _make_node(id="n_root", fact="Root level fact", tags=["root"])

        store.add_node(n1)
        store.add_node(n2)
        store.add_node(n3)
        store.add_node(n_root)

        # Assign worlds
        store.add_node_to_world("n_in1", world_id)
        store.add_node_to_world("n_in2", world_id)
        store.add_node_to_world("n_out1", other_world)  # Different world
        # n_root stays at world_id=None (root-level)

        # Create edges:
        # - in1 -> in2 (within world)
        # - in2 -> out1 (leaves world, should be filtered)
        # - in1 -> root (within world to root-level, should be reachable)
        store.conn.execute(
            "INSERT INTO edges (from_id, to_id, relation) VALUES (?, ?, ?)",
            ("n_in1", "n_in2", "flows_to")
        )
        store.conn.execute(
            "INSERT INTO edges (from_id, to_id, relation) VALUES (?, ?, ?)",
            ("n_in2", "n_out1", "flows_to")
        )
        store.conn.execute(
            "INSERT INTO edges (from_id, to_id, relation) VALUES (?, ?, ?)",
            ("n_in1", "n_root", "relates_to")
        )
        store.conn.commit()

        # BFS with world_id should:
        # - Include n_in1 (entry), n_in2 (hop 1, same world)
        # - Skip n_out1 (hop 1, different world)
        # - Include n_root (hop 1, root-level accessible from any world)
        collected = bfs_collect(
            store, [n1], hops=2, world_id=world_id
        )

        collected_ids = {n["id"] for n in collected}
        assert "n_in1" in collected_ids
        assert "n_in2" in collected_ids
        assert "n_root" in collected_ids  # Root-level fact accessible
        assert "n_out1" not in collected_ids  # Different world, should be skipped

    def test_world_node_is_type_world(self, store):
        """World nodes have type='world' in the database."""
        world_id = store.create_world(name="Type Check World")
        world_node = store.get_node(world_id)

        assert world_node["type"] == "world"
        assert world_node["fact"] == "Type Check World"

    def test_list_worlds_node_count(self, store):
        """list_worlds returns node_count for each world."""
        world1 = store.create_world(name="World 1")
        world2 = store.create_world(name="World 2")

        # Add nodes to world1
        n1 = _make_node(id="n_w1_1", fact="Node in world 1")
        n2 = _make_node(id="n_w1_2", fact="Another node in world 1")
        store.add_node(n1)
        store.add_node(n2)
        store.add_node_to_world("n_w1_1", world1)
        store.add_node_to_world("n_w1_2", world1)

        # Add one node to world2
        n3 = _make_node(id="n_w2_1", fact="Node in world 2")
        store.add_node(n3)
        store.add_node_to_world("n_w2_1", world2)

        worlds = store.list_worlds()
        world_map = {w["world_id"]: w for w in worlds}

        assert world_map[world1]["node_count"] == 2
        assert world_map[world2]["node_count"] == 1


def test_add_node_sanitizes_surrogates(tmp_path):
    """A lone surrogate in a node fact must not crash the INSERT (UnicodeEncodeError)."""
    from waystone.store import GraphStore
    db = tmp_path / "s.db"
    s = GraphStore(db, vec_enabled=False)
    nid = s.add_node({
        "id": "n1",
        "fact": "Decision text with a bad char \udc8f embedded",
        "type": "decision", "confidence": 1.0, "tags": ["tag\udc8f"],
    })
    assert nid
    fact = s.get_all_nodes()[0]["fact"]
    s.close()
    # surrogate replaced, no crash
    assert not any(0xD800 <= ord(c) <= 0xDFFF for c in fact)
    assert "Decision text with a bad char" in fact
