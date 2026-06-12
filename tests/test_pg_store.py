"""Tests for PostgresGraphStore (M1) against a real Postgres + pgvector.

Skips unless BOTH are true:
  - psycopg + pgvector importable (the `team` extra)
  - WAYSTONE_TEST_PG_DSN points at a reachable Postgres with the `vector` extension

CI provides these via a `pgvector/pgvector` service container. Locally:
  docker run -d --name waystone-pg -e POSTGRES_PASSWORD=waystone \
    -e POSTGRES_DB=waystone_test -p 55432:5432 pgvector/pgvector:pg16
  export WAYSTONE_TEST_PG_DSN=postgresql://postgres:waystone@localhost:55432/waystone_test

Each test runs in its own tenant_id, so they're isolated without truncating shared tables.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

DSN = os.environ.get("WAYSTONE_TEST_PG_DSN")

try:
    import pgvector  # noqa: F401
    import psycopg  # noqa: F401
    _HAVE_DRIVER = True
except Exception:
    _HAVE_DRIVER = False

pytestmark = pytest.mark.skipif(
    not (_HAVE_DRIVER and DSN),
    reason="PostgresGraphStore tests need psycopg+pgvector and WAYSTONE_TEST_PG_DSN",
)


@pytest.fixture
def store():
    from waystone.pg_store import PostgresGraphStore
    s = PostgresGraphStore(DSN, tenant_id=f"test_{uuid.uuid4().hex[:8]}", embedding_dim=384)
    yield s
    # clean up this tenant's rows
    with s.conn.cursor() as cur:
        cur.execute("DELETE FROM edges WHERE tenant_id = %s", (s.tenant_id,))
        cur.execute("DELETE FROM nodes WHERE tenant_id = %s", (s.tenant_id,))
    s.conn.commit()
    s.close()


def _node(id="n_a", fact=None, type="implementation", **kw):
    # Distinct default fact per id — same-fact nodes intentionally dedup (exact-hash),
    # so tests that add several nodes must give them different facts.
    fact = fact if fact is not None else f"distinct fact about {id}"
    n = {"id": id, "fact": fact, "type": type, "confidence": kw.get("confidence", 0.9),
         "tags": kw.get("tags", ["t"]), "supersedes": kw.get("supersedes", []),
         "created_at": kw.get("created_at", "2026-03-07T00:00:00+00:00")}
    for k in ("occurred_at", "source_transcript", "domain", "is_active"):
        if k in kw:
            n[k] = kw[k]
    return n


def test_add_and_get(store):
    store.add_node(_node(id="n1", fact="Use JWT for auth", tags=["jwt", "auth"]))
    n = store.get_node("n1")
    assert n["fact"] == "Use JWT for auth"
    assert set(n["tags"]) >= {"jwt", "auth"}
    assert n["is_active"] == 1
    assert "fact_hash" not in n and "tenant_id" not in n


def test_get_nonexistent(store):
    assert store.get_node("nope") is None


def test_exact_hash_dedup_merges(store):
    store.add_node(_node(id="n1", fact="Same fact", confidence=0.6, tags=["a"]))
    # different id, identical fact → merges into existing, unions tags, max confidence
    rid = store.add_node(_node(id="n2", fact="Same fact", confidence=0.9, tags=["b"]))
    assert rid == "n1"
    n = store.get_node("n1")
    assert set(n["tags"]) >= {"a", "b"}
    assert n["confidence"] == 0.9
    assert store.get_node("n2") is None


def test_replace_on_duplicate_id(store):
    store.add_node(_node(id="n1", fact="Original"))
    store.add_node(_node(id="n1", fact="Updated"))
    assert store.get_node("n1")["fact"] == "Updated"


def test_get_node_by_hash(store):
    from waystone.pg_store import compute_fact_hash
    store.add_node(_node(id="n1", fact="Hashable fact"))
    got = store.get_node_by_hash(compute_fact_hash("Hashable fact"))
    assert got and got["id"] == "n1"
    assert store.get_node_by_hash(compute_fact_hash("missing")) is None


def test_active_all_type_filters(store):
    store.add_node(_node(id="d1", type="decision"))
    store.add_node(_node(id="c1", type="constraint"))
    assert len(store.get_all_nodes()) == 2
    assert {n["id"] for n in store.get_nodes_by_type("decision")} == {"d1"}
    assert len(store.get_active_nodes()) == 2


def test_tags_overlap(store):
    store.add_node(_node(id="n1", tags=["jwt", "auth"]))
    store.add_node(_node(id="n2", tags=["cache"]))
    ids = {n["id"] for n in store.get_nodes_by_tags(["jwt"])}
    assert ids == {"n1"}
    ids2 = {n["id"] for n in store.get_nodes_by_tags(["jwt", "cache"])}
    assert ids2 == {"n1", "n2"}


def test_supersedes_closes_valid_to(store):
    store.add_node(_node(id="old", fact="Old decision", type="decision"))
    store.add_node(_node(id="new", fact="New decision", type="decision",
                         supersedes=["old"], occurred_at="2026-04-01T00:00:00+00:00"))
    old = store.get_node("old")
    assert old["is_active"] == 0
    assert old["valid_to"] is not None
    assert store.get_node("new")["is_active"] == 1


def test_add_edge_supersedes_closes_valid_to(store):
    store.add_node(_node(id="a", fact="A", type="decision"))
    store.add_node(_node(id="b", fact="B", type="decision"))
    store.add_edge("a", "b", "supersedes")   # a supersedes b → b closes
    assert store.get_node("b")["is_active"] == 0
    assert store.get_node("a")["is_active"] == 1


def test_edges_roundtrip_and_dedup(store):
    store.add_node(_node(id="a"))
    store.add_node(_node(id="b"))
    store.add_edge("a", "b", "relates_to")
    store.add_edge("a", "b", "relates_to")  # idempotent
    assert len(store.get_all_edges()) == 1
    assert store.get_edges_from("a")[0]["to_id"] == "b"
    assert store.get_edges_to("b")[0]["from_id"] == "a"


def test_delete_cascades_edges(store):
    store.add_node(_node(id="a"))
    store.add_node(_node(id="b"))
    store.add_edge("a", "b", "relates_to")
    assert store.delete_node("a") is True
    assert store.get_node("a") is None
    assert store.get_all_edges() == []
    assert store.delete_node("a") is False


def test_deactivate_keeps_history(store):
    store.add_node(_node(id="n1"))
    store.deactivate_node("n1")
    n = store.get_node("n1")
    assert n is not None and n["is_active"] == 0 and n["valid_to"] is not None


def test_stats(store):
    assert store.get_stats() == {"node_count": 0, "edge_count": 0}
    store.add_node(_node(id="a"))
    store.add_node(_node(id="b"))
    store.add_edge("a", "b", "relates_to")
    assert store.get_stats() == {"node_count": 2, "edge_count": 1}


def test_detect_stale_candidates(store):
    old = (datetime.now(timezone.utc) - timedelta(days=160)).isoformat()
    recent = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    store.add_node(_node(id="stale", type="implementation", confidence=0.6, created_at=old))
    store.add_node(_node(id="fresh", type="implementation", confidence=0.6, created_at=recent))
    store.add_node(_node(id="strong", type="implementation", confidence=0.98, created_at=old))
    reasons = {c["id"]: c["reason"] for c in store.detect_stale_candidates()}
    assert reasons.get("stale") == "temporal_half_life_expired"
    assert "fresh" not in reasons     # too recent
    assert "strong" not in reasons    # high confidence exempt
    store.deactivate_node("stale")
    assert not any(c["id"] == "stale" for c in store.detect_stale_candidates())


def test_get_nodes_by_fact_text(store):
    store.add_node(_node(id="n1", fact="We chose PostgreSQL for the team backend"))
    store.add_node(_node(id="n2", fact="Redis is used for the cache"))
    ids = {n["id"] for n in store.get_nodes_by_fact_text(["postgresql"])}
    assert ids == {"n1"}
    # case-insensitive, multi-keyword OR
    ids2 = {n["id"] for n in store.get_nodes_by_fact_text(["REDIS", "postgres"])}
    assert ids2 == {"n1", "n2"}
    # inactive nodes excluded
    store.deactivate_node("n1")
    assert {n["id"] for n in store.get_nodes_by_fact_text(["postgresql"])} == set()


def test_merge_extraction(store):
    nodes = [
        {"id": "a", "fact": "Auth uses JWT", "type": "decision", "tags": ["jwt"],
         "confidence": 0.9, "supersedes": [], "created_at": "2026-03-01T00:00:00+00:00"},
        {"id": "b", "fact": "Cache uses Redis", "type": "implementation", "tags": ["redis"],
         "confidence": 0.9, "supersedes": [], "created_at": "2026-03-01T00:00:00+00:00"},
    ]
    edges = [{"from_id": "a", "to_id": "b", "relation": "relates_to"}]
    id_map = store.merge_extraction(nodes, edges)
    assert id_map == {}                      # no dedup remaps here
    assert store.get_stats() == {"node_count": 2, "edge_count": 1}
    assert store.get_edges_from("a")[0]["to_id"] == "b"


def test_merge_extraction_supersedes(store):
    store.add_node(_node(id="old", fact="Auth uses sessions", type="decision"))
    nodes = [{"id": "new", "fact": "Auth uses JWT now", "type": "decision", "tags": ["jwt"],
              "confidence": 0.9, "supersedes": [], "created_at": "2026-04-01T00:00:00+00:00",
              "occurred_at": "2026-04-01T00:00:00+00:00"}]
    edges = [{"from_id": "new", "to_id": "old", "relation": "supersedes"}]
    store.merge_extraction(nodes, edges)
    old = store.get_node("old")
    new = store.get_node("new")
    assert old["is_active"] == 0 and old["valid_to"] is not None   # superseded → expired
    assert "old" in new["supersedes"]                               # link recorded


def test_tenant_isolation(store):
    from waystone.pg_store import PostgresGraphStore
    store.add_node(_node(id="mine", fact="tenant A fact"))
    other = PostgresGraphStore(DSN, tenant_id=f"test_{uuid.uuid4().hex[:8]}", embedding_dim=384)
    try:
        assert other.get_node("mine") is None        # no cross-tenant leak
        assert other.get_stats()["node_count"] == 0
    finally:
        other.close()
