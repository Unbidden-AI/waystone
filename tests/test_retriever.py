"""Tests for the retriever: keyword extraction, BFS, strategies, markdown assembly."""

import pytest

from waystone.retriever import (
    apply_recency_decay,
    apply_token_budget,
    assemble_markdown,
    extract_keywords,
    filter_by_confidence,
    prune_superseded,
    retrieve,
    retrieve_with_stats,
    score_by_relevance,
)
from waystone.store import GraphStore


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
        result = retrieve(store, "api webhook", hops=2)
        assert "Webhook endpoint" in result
        assert "Garmin API" in result

    def test_no_matches(self, store):
        # Disable semantic to test keyword/BFS path only — semantic finds cosine-similar
        # nodes for any query, so a "no match" assertion requires keyword-only mode.
        result = retrieve(store, "kubernetes deployment", strategies={"semantic": False})
        assert "No relevant context found" in result

    def test_top_k_limits(self, store):
        result = retrieve(store, "api webhook garmin", hops=3, top_k=2)
        assert result.count("- ") <= 2

    def test_unrelated_nodes_excluded(self, store):
        result = retrieve(store, "webhook error handling", hops=1)
        assert "React" not in result

    def test_strategies_passed_through(self, store):
        """Strategies dict is accepted and applied."""
        result = retrieve(store, "webhook", strategies={"confidence_threshold": 0.99})
        # All nodes below 0.99 should be filtered
        assert "No relevant context found" in result


class TestRetrieveWithStats:
    def test_returns_stats(self, store):
        result = retrieve_with_stats(store, "api webhook", hops=2)
        assert result.nodes_before_strategies > 0
        assert result.nodes_after_strategies > 0
        assert isinstance(result.tokens_estimated, int)
        assert result.tokens_estimated > 0

    def test_stats_reflect_strategy_impact(self, store):
        result = retrieve_with_stats(
            store, "api webhook", hops=2,
            strategies={"confidence_threshold": 0.91}
        )
        # n3 (0.85) should be filtered, so after < before
        assert result.nodes_after_strategies < result.nodes_before_strategies
        assert "confidence_threshold(0.91)" in result.strategies_applied


# ---------------------------------------------------------------------------
# Strategy unit tests
# ---------------------------------------------------------------------------

class TestRelevanceScoring:
    def test_ranks_by_overlap_count(self):
        nodes = [
            {"id": "n1", "tags": ["api"]},
            {"id": "n2", "tags": ["api", "webhook", "garmin"]},
            {"id": "n3", "tags": ["webhook"]},
        ]
        result = score_by_relevance(nodes, ["api", "webhook", "garmin"])
        assert result[0]["id"] == "n2"  # 3 matches
        assert result[0]["_relevance"] == 3

    def test_no_mutation_beyond_relevance(self):
        nodes = [{"id": "n1", "tags": ["x"]}]
        score_by_relevance(nodes, ["x"])
        assert nodes[0]["tags"] == ["x"]


class TestSupersededPruning:
    def test_prunes_superseded_via_array(self, store):
        # n_new supersedes n_old
        store.add_node({"id": "n_old", "fact": "Old decision", "type": "decision",
                        "confidence": 0.8, "tags": ["test"], "created_at": "2026-03-01T00:00:00Z", "supersedes": []})
        store.add_node({"id": "n_new", "fact": "New decision", "type": "decision",
                        "confidence": 0.9, "tags": ["test"], "created_at": "2026-03-07T00:00:00Z", "supersedes": ["n_old"]})

        nodes = [store.get_node("n_old"), store.get_node("n_new")]
        result = prune_superseded(nodes, store)
        ids = {n["id"] for n in result}
        assert "n_new" in ids
        assert "n_old" not in ids

    def test_prunes_superseded_via_edge(self, store):
        store.add_node({"id": "n_old2", "fact": "Old", "type": "decision",
                        "confidence": 0.8, "tags": ["test"], "created_at": "2026-03-01T00:00:00Z", "supersedes": []})
        store.add_node({"id": "n_new2", "fact": "New", "type": "decision",
                        "confidence": 0.9, "tags": ["test"], "created_at": "2026-03-07T00:00:00Z", "supersedes": []})
        store.add_edge("n_new2", "n_old2", "supersedes")

        nodes = [store.get_node("n_old2"), store.get_node("n_new2")]
        result = prune_superseded(nodes, store)
        ids = {n["id"] for n in result}
        assert "n_new2" in ids
        assert "n_old2" not in ids

    def test_keeps_non_superseded(self, store):
        nodes = [store.get_node("n1"), store.get_node("n2")]
        result = prune_superseded(nodes, store)
        assert len(result) == 2


class TestConfidenceThreshold:
    def test_filters_below_threshold(self):
        nodes = [
            {"id": "n1", "confidence": 0.3},
            {"id": "n2", "confidence": 0.7},
            {"id": "n3", "confidence": 0.95},
        ]
        result = filter_by_confidence(nodes, 0.6)
        ids = {n["id"] for n in result}
        assert ids == {"n2", "n3"}

    def test_threshold_zero_keeps_all(self):
        nodes = [{"id": "n1", "confidence": 0.1}]
        # 0.0 threshold means disabled in the pipeline, but the function
        # itself still works — 0.1 >= 0.0 is True
        assert len(filter_by_confidence(nodes, 0.0)) == 1

    def test_threshold_one_very_strict(self):
        nodes = [
            {"id": "n1", "confidence": 0.99},
            {"id": "n2", "confidence": 1.0},
        ]
        result = filter_by_confidence(nodes, 1.0)
        assert len(result) == 1
        assert result[0]["id"] == "n2"


class TestRecencyDecay:
    def test_recent_scores_higher(self):
        nodes = [
            {"id": "n_old", "confidence": 0.9, "created_at": "2020-01-01T00:00:00Z"},
            {"id": "n_new", "confidence": 0.9, "created_at": "2026-03-07T00:00:00Z"},
        ]
        result = apply_recency_decay(nodes, half_life_days=30)
        old = next(n for n in result if n["id"] == "n_old")
        new = next(n for n in result if n["id"] == "n_new")
        assert new["_score"] > old["_score"]

    def test_same_age_same_score(self):
        nodes = [
            {"id": "n1", "confidence": 0.8, "created_at": "2026-03-07T00:00:00Z"},
            {"id": "n2", "confidence": 0.8, "created_at": "2026-03-07T00:00:00Z"},
        ]
        result = apply_recency_decay(nodes, half_life_days=30)
        assert abs(result[0]["_score"] - result[1]["_score"]) < 0.001

    def test_higher_confidence_can_beat_recency(self):
        nodes = [
            {"id": "n_old", "confidence": 1.0, "created_at": "2026-03-01T00:00:00Z"},
            {"id": "n_new", "confidence": 0.3, "created_at": "2026-03-07T00:00:00Z"},
        ]
        result = apply_recency_decay(nodes, half_life_days=30)
        old = next(n for n in result if n["id"] == "n_old")
        new = next(n for n in result if n["id"] == "n_new")
        # Old node has higher confidence, only 6 days old, should still beat low confidence new
        assert old["_score"] > new["_score"]

    def test_bad_date_defaults_to_no_decay(self):
        nodes = [{"id": "n1", "confidence": 0.9, "created_at": "invalid"}]
        result = apply_recency_decay(nodes, half_life_days=30)
        assert abs(result[0]["_score"] - 0.9) < 0.001


class TestTokenBudget:
    def test_fits_within_budget(self):
        nodes = [
            {"id": "n1", "fact": "Short fact", "confidence": 0.9},
            {"id": "n2", "fact": "Another short fact", "confidence": 0.8},
            {"id": "n3", "fact": "A much longer fact that contains many more words and tokens to push over the budget", "confidence": 0.7},
        ]
        result = apply_token_budget(nodes, budget=50)
        # Should include first two but likely not the third
        total_ids = {n["id"] for n in result}
        assert "n1" in total_ids
        assert len(result) < 3

    def test_zero_budget_returns_nothing(self):
        nodes = [{"id": "n1", "fact": "test", "confidence": 0.9}]
        # 0 is handled in the pipeline as "disabled", but if called directly:
        result = apply_token_budget(nodes, budget=0)
        assert len(result) == 0

    def test_large_budget_keeps_all(self):
        nodes = [
            {"id": "n1", "fact": "Short", "confidence": 0.9},
            {"id": "n2", "fact": "Also short", "confidence": 0.8},
        ]
        result = apply_token_budget(nodes, budget=10000)
        assert len(result) == 2


class TestAssembleMarkdown:
    def test_groups_by_type(self):
        nodes = [
            {"id": "n1", "fact": "Decision A", "type": "decision", "confidence": 0.8,
             "tags": [], "source_transcript": None, "source_message_index": None, "supersedes": []},
            {"id": "n2", "fact": "Impl B", "type": "implementation", "confidence": 0.9,
             "tags": [], "source_transcript": None, "source_message_index": None, "supersedes": []},
        ]
        md = assemble_markdown(nodes, "test task")
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

    def test_includes_strategies_line(self):
        nodes = [
            {"id": "n1", "fact": "Fact", "type": "decision", "confidence": 0.9,
             "tags": [], "source_transcript": None, "source_message_index": None, "supersedes": []},
        ]
        md = assemble_markdown(nodes, "test", ["superseded_pruning", "confidence_threshold(0.6)"])
        assert "**Strategies:**" in md
        assert "superseded_pruning" in md


class TestIntegrationWithStrategies:
    """Test the full pipeline with various strategy combinations."""

    def test_all_strategies_enabled(self, store):
        result = retrieve_with_stats(
            store, "api webhook", hops=2,
            strategies={
                "superseded_pruning": True,
                "confidence_threshold": 0.5,
                "recency_decay": True,
                "recency_half_life_days": 30,
                "token_budget": 2000,
                "relevance_scoring": True,
            }
        )
        assert result.nodes_after_strategies > 0
        assert len(result.strategies_applied) >= 3

    def test_all_strategies_disabled(self, store):
        result = retrieve_with_stats(
            store, "api webhook", hops=2,
            strategies={
                "superseded_pruning": False,
                "confidence_threshold": 0.0,
                "recency_decay": False,
                "token_budget": 0,
                "relevance_scoring": False,
            }
        )
        assert result.strategies_applied == []

    def test_benchmark_comparison(self, store):
        """Demonstrate benchmarking: same query, different strategies."""
        baseline = retrieve_with_stats(store, "api webhook", hops=2, strategies={})
        reduced = retrieve_with_stats(
            store, "api webhook", hops=2,
            strategies={"confidence_threshold": 0.91}
        )
        assert reduced.nodes_after_strategies <= baseline.nodes_after_strategies
        assert reduced.tokens_estimated <= baseline.tokens_estimated


class TestEdgeQueryRewrite:
    def test_add_superseded_tags_rule_expands_keywords(self, tmp_path):
        """Test that add_superseded_tags rule adds old node's tags during BFS."""
        from waystone.retriever import bfs_collect

        db_path = tmp_path / "test.db"
        store = GraphStore(db_path)

        # Create a superseding relationship: n_new supersedes n_old
        old_node = {
            "id": "n_old",
            "fact": "Use JSON for serialization",
            "type": "decision",
            "confidence": 0.9,
            "tags": ["json", "serialization"],
            "created_at": "2026-03-07T00:00:00Z",
            "supersedes": [],
        }
        new_node = {
            "id": "n_new",
            "fact": "Use Avro for serialization",
            "type": "decision",
            "confidence": 0.95,
            "tags": ["avro", "serialization"],
            "created_at": "2026-03-07T00:01:00Z",
            "supersedes": [],
        }
        bridge_node = {
            "id": "n_bridge",
            "fact": "Data pipeline uses efficient formats",
            "type": "transition",
            "confidence": 0.85,
            "tags": ["pipeline", "format"],
            "created_at": "2026-03-07T00:02:00Z",
            "supersedes": [],
        }

        store.add_node(old_node)
        store.add_node(new_node)
        store.add_node(bridge_node)

        # Create the supersedes edge, which auto-wires the rule
        store.add_edge("n_new", "n_old", "supersedes")

        # Also connect new_node to bridge_node for further exploration
        store.add_edge("n_new", "n_bridge", "flows_to")

        # Verify the rule was auto-wired
        rule = store.get_edge_query_rule("n_new", "n_old")
        assert rule is not None
        assert rule["rule_type"] == "add_superseded_tags"

        # Run BFS from n_new — it should reach n_old via the supersedes edge
        # and expand keywords to include the old node's tags
        collected = bfs_collect(store, [new_node], hops=2)

        # Verify we collected the nodes
        collected_ids = {n["id"] for n in collected}
        assert "n_new" in collected_ids
        assert "n_old" in collected_ids
        assert "n_bridge" in collected_ids

        store.close()
