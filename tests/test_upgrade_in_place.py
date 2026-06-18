"""Gut check #5 — upgrade-in-place over an existing OLD graph.

Clean installs are well tested (acceptance_install.sh), but the path real users
hit on every update — opening a database created by an OLDER version — had zero
coverage. `GraphStore.init_db()` carries a chain of `ALTER TABLE ADD COLUMN`
migrations gated on `PRAGMA user_version`; if any migration is wrong, a user's
existing graph breaks on first open after upgrading.

This fabricates a pre-migration DB (the base schema, user_version=0, none of the
later columns), opens it with the current GraphStore, and asserts the schema
migrates cleanly AND the old data survives and stays usable.
"""

import sqlite3

from waystone.store import SCHEMA_VERSION, GraphStore

# The original schema, before the ALTER-added columns (fact_hash, pinned,
# weight, occurred_at, hit_count, entry_hit_count, last_used_at, domain,
# valid_to, is_active). supersedes has no ALTER, so it predates them.
_OLD_SCHEMA = """
CREATE TABLE nodes (
    id TEXT PRIMARY KEY,
    fact TEXT NOT NULL,
    type TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    source_transcript TEXT,
    source_message_index INTEGER,
    tags TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    supersedes TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE edges (
    from_id TEXT NOT NULL,
    to_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    PRIMARY KEY (from_id, to_id, relation)
);
"""


def _make_old_db(path):
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(_OLD_SCHEMA)
        conn.execute(
            "INSERT INTO nodes (id, fact, type, confidence, tags, created_at, supersedes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("n_old1", "The cache layer uses Redis.", "decision", 0.9,
             '["cache", "redis"]', "2025-01-01T00:00:00", "[]"),
        )
        conn.execute(
            "INSERT INTO nodes (id, fact, type, confidence, tags, created_at, supersedes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("n_old2", "Auth uses JWT tokens.", "decision", 0.8,
             '["auth", "jwt"]', "2025-01-02T00:00:00", "[]"),
        )
        conn.execute("INSERT INTO edges (from_id, to_id, relation) VALUES (?, ?, ?)",
                     ("n_old1", "n_old2", "relates_to"))
        conn.execute("PRAGMA user_version = 0")  # pre-migration
        conn.commit()
    finally:
        conn.close()


def _columns(db_path, table):
    conn = sqlite3.connect(str(db_path))
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    finally:
        conn.close()


def test_opening_old_db_migrates_and_preserves_data(tmp_path):
    db_path = tmp_path / "context.db"
    _make_old_db(db_path)

    # Opening with the current GraphStore must run the migrations without error.
    store = GraphStore(db_path, vec_enabled=False)
    try:
        # Schema is now current.
        assert store.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION

        # All the ALTER-added columns now exist.
        node_cols = _columns(db_path, "nodes")
        for col in ("fact_hash", "pinned", "occurred_at", "hit_count",
                    "entry_hit_count", "last_used_at", "domain",
                    "valid_to", "is_active"):
            assert col in node_cols, f"migration did not add nodes.{col}"
        assert "weight" in _columns(db_path, "edges")

        # The old data survived, with sane defaults for the new columns.
        nodes = {n["id"]: n for n in store.get_all_nodes()}
        assert set(nodes) == {"n_old1", "n_old2"}, "old nodes lost on upgrade"
        assert nodes["n_old1"]["fact"] == "The cache layer uses Redis."
        assert nodes["n_old1"].get("is_active", 1) == 1

        # fact_hash was backfilled (it was NULL in the old rows).
        hashes = store.conn.execute(
            "SELECT fact_hash FROM nodes WHERE fact_hash IS NOT NULL"
        ).fetchall()
        assert len(hashes) == 2, "fact_hash not backfilled for old rows"

        # Retrieval still works against the migrated graph.
        assert store.get_stats()["node_count"] == 2
        assert any(n["id"] == "n_old1"
                   for n in store.get_nodes_by_tags(["cache", "redis"]))
    finally:
        store.close()


def test_old_db_still_writable_after_upgrade(tmp_path):
    """After migrating, the graph must accept new writes — including a
    supersession that expires one of the pre-existing nodes (bi-temporal path)."""
    db_path = tmp_path / "context.db"
    _make_old_db(db_path)

    store = GraphStore(db_path, vec_enabled=False)
    try:
        # A new decision that supersedes the old Redis node.
        store.add_node({
            "id": "n_new1",
            "fact": "Migrated the cache layer from Redis to Memcached.",
            "type": "decision", "confidence": 0.95,
            "tags": ["cache", "redis", "memcached"],
            "supersedes": ["n_old1"],
        })
        # The superseding node exists and is active.
        nodes = {n["id"]: n for n in store.get_all_nodes()}
        assert "n_new1" in nodes
        # The superseded old node is now expired (is_active flipped by add_node).
        old = store.get_node("n_old1")
        assert old is not None and old.get("is_active", 1) == 0, \
            "supersession did not expire the old node after upgrade"
    finally:
        store.close()


def test_reopening_current_db_is_idempotent(tmp_path):
    """Opening an already-migrated DB again is a no-op (fast-path) and safe."""
    db_path = tmp_path / "context.db"
    _make_old_db(db_path)
    GraphStore(db_path, vec_enabled=False).close()  # migrate once
    # Second open hits the user_version fast-path; must not raise or lose data.
    store = GraphStore(db_path, vec_enabled=False)
    try:
        assert store.get_stats()["node_count"] == 2
    finally:
        store.close()
