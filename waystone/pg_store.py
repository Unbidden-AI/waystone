"""PostgresGraphStore — the multi-writer backend behind the same GraphStore interface.

M1 keystone for the Team tier (see internal/POSTGRES_GRAPHSTORE_SPEC.md). Implements
the core graph contract on Postgres + pgvector so the team server can hold a shared,
concurrently-written graph. Callers (extractor, retriever, hooks, MCP, CLI) talk to the
same interface as the SQLite ``GraphStore`` — only the backend changes.

Reuses the pure, SQL-free logic from ``waystone.store`` (fact hashing, numeric tagging,
surrogate stripping) so the two backends can't diverge on those rules.

STATUS: core slice implemented + tested against real Postgres. The advanced surface
(worlds, hyperedges, time-travel ``get_nodes_at_time``, edge-query-rules, raw_sentences,
FTS keyword search, semantic dedup-at-insert, feedback) is TODO — tracked in the spec.
"""

from __future__ import annotations

import json
import logging
import os
import struct
import threading
from datetime import datetime, timezone

from .store import (
    _auto_tag_numerics,
    _fact_hash,
    _strip_surrogates,
    compute_fact_hash,  # noqa: F401 — re-exported for callers/tests
)

log = logging.getLogger("waystone.pg_store")

try:
    import psycopg
    from pgvector import Vector
    from pgvector.psycopg import register_vector
    from psycopg.rows import dict_row
    _PSYCOPG_OK = True
except Exception:  # pragma: no cover - import guard
    psycopg = None
    dict_row = None
    register_vector = None
    Vector = None
    _PSYCOPG_OK = False

try:
    from psycopg_pool import ConnectionPool  # the `team` extra
except Exception:  # pragma: no cover
    ConnectionPool = None


def _blob_to_vec(blob: bytes):
    """float32 byte blob (embedder format) → pgvector Vector for binding."""
    return Vector(list(struct.unpack(f"{len(blob) // 4}f", blob)))


def _writes(method):
    """Decorator for write methods: roll back on error so a failed query never
    leaves the (long-lived, server-shared) connection stuck in an aborted-transaction
    state poisoning subsequent calls. Each method still commits on success."""
    import functools

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception:
            try:
                self.conn.rollback()
            except Exception:
                pass
            raise
    return wrapper


# Schema is created/migrated once per (process, DSN) — NOT on every store open.
# Running DDL (CREATE/ALTER) per request would serialize on table locks and is the
# kind of thing that hangs a server when a stray connection holds a lock.
_SCHEMA_READY: set = set()

# Process-wide connection pool per DSN. The server opens many short-lived stores
# (one per request); a pool reuses warm connections instead of connect-and-discard,
# which keeps latency low and avoids exhausting Postgres `max_connections` under a
# busy team. Opt out with WAYSTONE_PG_POOL=0; size with WAYSTONE_PG_POOL_MAX.
_POOLS: dict = {}
# Guards first-touch creation of _POOLS[dsn] and the one-time _init_schema per DSN —
# the server runs sync endpoints in a threadpool, so concurrent first requests for a
# DSN must not both create a pool (leaking one) or both run schema DDL.
_INIT_LOCK = threading.Lock()


def _pooling_enabled() -> bool:
    return ConnectionPool is not None and os.environ.get(
        "WAYSTONE_PG_POOL", "1").lower() not in ("0", "false", "no")


def _get_pool(dsn: str):
    """Return (creating once) the shared ConnectionPool for `dsn`. Each physical
    connection is configured with dict rows + pgvector adaptation via `configure`."""
    pool = _POOLS.get(dsn)
    if pool is not None:
        return pool
    with _INIT_LOCK:
        pool = _POOLS.get(dsn)  # double-checked under the lock
        if pool is not None:
            return pool

        def _configure(conn):
            conn.row_factory = dict_row
            if register_vector is not None:
                # Let a failure propagate — the pool rejects the connection rather
                # than handing out one that can't adapt vectors.
                register_vector(conn)

        max_size = int(os.environ.get("WAYSTONE_PG_POOL_MAX", "10"))
        pool = ConnectionPool(
            dsn, min_size=1, max_size=max_size,
            kwargs={"autocommit": False, "row_factory": dict_row},
            configure=_configure, open=True, name="waystone",
        )
        _POOLS[dsn] = pool
        return pool

# Per-type half-lives for proactive staleness detection (mirror of GraphStore's).
_INVALIDATION_HALF_LIVES = {
    "transition": 14, "question": 30, "implementation": 60, "process": 90,
    "preference": 90, "resolved": 90, "decision": 180,
    "lesson_learned": 365, "constraint": 365,
}


class PostgresGraphStore:
    """Postgres + pgvector implementation of the core GraphStore interface.

    One instance is scoped to a ``tenant_id`` (the team/project). All reads and writes
    filter on it, so many tenants can share one database with no cross-leak.
    """

    def __init__(self, dsn: str, tenant_id: str = "default", *,
                 dedup_threshold: float = 0.95, embedding_dim: int | None = None):
        if not _PSYCOPG_OK:
            raise RuntimeError(
                "PostgresGraphStore requires the 'team' extra: pip install 'waystone[team]'"
            )
        self.tenant_id = tenant_id
        self._dedup_threshold = dedup_threshold
        if embedding_dim is None:
            from . import embedder
            embedding_dim = embedder.get_embedding_dim()
        self._dim = int(embedding_dim)
        self._pool = _get_pool(dsn) if _pooling_enabled() else None
        if self._pool is not None:
            # Borrowed conn already has dict rows + pgvector (pool `configure`).
            self.conn = self._pool.getconn()
        else:
            self.conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=False)
            register_vector(self.conn)  # adapt pgvector <-> Python lists
        try:
            if dsn not in _SCHEMA_READY:
                with _INIT_LOCK:
                    if dsn not in _SCHEMA_READY:  # double-checked under the lock
                        self._init_schema()       # once per process+DSN (idempotent DDL)
                        _SCHEMA_READY.add(dsn)
        except Exception:
            # Don't leak the just-borrowed connection if schema init fails.
            self.close()
            raise

    # ------------------------------------------------------------------ schema
    def _init_schema(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS nodes (
                    tenant_id   text NOT NULL,
                    id          text NOT NULL,
                    fact        text NOT NULL,
                    type        text NOT NULL,
                    confidence  real NOT NULL DEFAULT 0.5,
                    tags        jsonb NOT NULL DEFAULT '[]',
                    supersedes  jsonb NOT NULL DEFAULT '[]',
                    source_transcript     text,
                    source_message_index  int,
                    domain      text,
                    fact_hash   text,
                    occurred_at timestamptz,
                    created_at  timestamptz NOT NULL DEFAULT now(),
                    valid_to    timestamptz,
                    is_active   boolean NOT NULL DEFAULT true,
                    pinned      boolean NOT NULL DEFAULT false,
                    hit_count   int NOT NULL DEFAULT 0,
                    entry_hit_count int NOT NULL DEFAULT 0,
                    last_used_at    timestamptz,
                    world_id    text,
                    embedding   vector({self._dim}),
                    fact_fts    tsvector GENERATED ALWAYS AS (to_tsvector('english', fact)) STORED,
                    PRIMARY KEY (tenant_id, id)
                )
            """)
            # Migrations (idempotent) for schemas created by earlier versions.
            cur.execute("ALTER TABLE nodes ADD COLUMN IF NOT EXISTS world_id text")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_nodes_active ON nodes (tenant_id, is_active)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes (tenant_id, type)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_nodes_fhash ON nodes (tenant_id, fact_hash)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_nodes_tags ON nodes USING gin (tags jsonb_path_ops)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_nodes_fts ON nodes USING gin (fact_fts)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS worlds (
                    tenant_id   text NOT NULL,
                    world_id    text NOT NULL,
                    name        text NOT NULL,
                    description text,
                    created_at  timestamptz NOT NULL DEFAULT now(),
                    PRIMARY KEY (tenant_id, world_id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS edges (
                    tenant_id text NOT NULL,
                    from_id   text NOT NULL,
                    to_id     text NOT NULL,
                    relation  text NOT NULL,
                    weight    real NOT NULL DEFAULT 1.0,
                    on_query_rewrite text,
                    PRIMARY KEY (tenant_id, from_id, to_id, relation)
                )
            """)
            cur.execute("ALTER TABLE edges ADD COLUMN IF NOT EXISTS weight real NOT NULL DEFAULT 1.0")
            cur.execute("ALTER TABLE edges ADD COLUMN IF NOT EXISTS on_query_rewrite text")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_edges_to ON edges (tenant_id, to_id)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS hyperedge_members (
                    tenant_id    text NOT NULL,
                    hyperedge_id text NOT NULL,
                    node_id      text NOT NULL,
                    PRIMARY KEY (tenant_id, hyperedge_id, node_id)
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_he_node ON hyperedge_members (tenant_id, node_id)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS edge_query_rules (
                    tenant_id text NOT NULL,
                    rule_id   text NOT NULL,
                    rule_type text NOT NULL,
                    params    jsonb,
                    PRIMARY KEY (tenant_id, rule_id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS extraction_failures (
                    tenant_id   text NOT NULL,
                    id          bigserial PRIMARY KEY,
                    created_at  timestamptz NOT NULL DEFAULT now(),
                    source_transcript text, model text, error_type text,
                    raw_response text, error_message text
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS kv (
                    tenant_id text NOT NULL,
                    key       text NOT NULL,
                    value     jsonb NOT NULL,
                    PRIMARY KEY (tenant_id, key)
                )
            """)
        self.conn.commit()

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _row_to_node(row: dict) -> dict:
        """Mirror GraphStore._row_to_node: drop internal fact_hash + tenant_id.

        psycopg returns jsonb as parsed Python objects and timestamptz as datetimes;
        timestamps are normalized to ISO strings to match the SQLite store's dicts.
        """
        d = dict(row)
        d.pop("fact_hash", None)
        d.pop("tenant_id", None)
        d.pop("embedding", None)
        d.pop("fact_fts", None)
        d["is_active"] = 1 if d.get("is_active") else 0
        d["pinned"] = 1 if d.get("pinned") else 0
        for k in ("created_at", "occurred_at", "valid_to", "last_used_at"):
            v = d.get(k)
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        return d

    _COLS = ("id, fact, type, confidence, tags, supersedes, source_transcript, "
             "source_message_index, domain, fact_hash, occurred_at, created_at, "
             "valid_to, is_active, pinned, hit_count, entry_hit_count, last_used_at, world_id")

    # ------------------------------------------------------------------ writes
    @_writes
    def add_node(self, node: dict) -> str:
        """Insert a node, deduplicating by normalized fact-text hash (exact).

        Mirrors GraphStore.add_node's exact-hash dedup + supersede expiry. Semantic
        dedup-at-insert is TODO (pgvector path); exact-hash dedup is in place.
        """
        node["fact"] = _strip_surrogates(node["fact"])
        if node.get("source_transcript"):
            node["source_transcript"] = _strip_surrogates(node["source_transcript"])
        tags = [_strip_surrogates(t) for t in _auto_tag_numerics(node["fact"], node.get("tags", []))]
        fhash = _fact_hash(node["fact"])

        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT id, tags, confidence FROM nodes WHERE tenant_id = %s AND fact_hash = %s LIMIT 1",
                (self.tenant_id, fhash),
            )
            existing = cur.fetchone()
            if existing and existing["id"] != node["id"]:
                merged_tags = sorted(set(existing["tags"]) | set(tags))
                merged_conf = max(existing["confidence"], node.get("confidence", 0.5))
                cur.execute(
                    "UPDATE nodes SET tags = %s, confidence = %s WHERE tenant_id = %s AND id = %s",
                    (json.dumps(merged_tags), merged_conf, self.tenant_id, existing["id"]),
                )
                self.conn.commit()
                return existing["id"]

            cur.execute(
                """INSERT INTO nodes
                   (tenant_id, id, fact, type, confidence, source_transcript,
                    source_message_index, tags, created_at, occurred_at, supersedes,
                    fact_hash, domain, valid_to, is_active)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (tenant_id, id) DO UPDATE SET
                     fact=EXCLUDED.fact, type=EXCLUDED.type, confidence=EXCLUDED.confidence,
                     source_transcript=EXCLUDED.source_transcript,
                     source_message_index=EXCLUDED.source_message_index, tags=EXCLUDED.tags,
                     occurred_at=EXCLUDED.occurred_at, supersedes=EXCLUDED.supersedes,
                     fact_hash=EXCLUDED.fact_hash, domain=EXCLUDED.domain,
                     valid_to=EXCLUDED.valid_to, is_active=EXCLUDED.is_active""",
                (
                    self.tenant_id, node["id"], node["fact"], node["type"],
                    node.get("confidence", 0.5), node.get("source_transcript"),
                    node.get("source_message_index"), json.dumps(tags),
                    node.get("created_at", datetime.now(timezone.utc).isoformat()),
                    node.get("occurred_at"), json.dumps(node.get("supersedes", [])),
                    fhash, node.get("domain"), node.get("valid_to"),
                    bool(node.get("is_active", 1)),
                ),
            )
            supersedes_ids = node.get("supersedes", [])
            if supersedes_ids:
                expiry = (node.get("occurred_at") or node.get("created_at")
                          or datetime.now(timezone.utc).isoformat())
                cur.executemany(
                    """UPDATE nodes SET valid_to = COALESCE(valid_to, %s), is_active = false
                       WHERE tenant_id = %s AND id = %s AND is_active = true""",
                    [(expiry, self.tenant_id, sid) for sid in supersedes_ids],
                )
        self.conn.commit()
        return node["id"]

    @_writes
    def add_edge(self, from_id: str, to_id: str, relation: str) -> None:
        """Insert an edge (idempotent). Mirrors the supersedes→expiry side effect."""
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO edges (tenant_id, from_id, to_id, relation)
                   VALUES (%s,%s,%s,%s)
                   ON CONFLICT (tenant_id, from_id, to_id, relation) DO NOTHING""",
                (self.tenant_id, from_id, to_id, relation),
            )
            if relation == "supersedes":
                # Close the superseded node's validity window (occurred_at of the new
                # node if present, else its created_at, else now).
                cur.execute(
                    "SELECT COALESCE(occurred_at, created_at) AS t FROM nodes "
                    "WHERE tenant_id = %s AND id = %s", (self.tenant_id, from_id))
                r = cur.fetchone()
                expiry = (r["t"] if r and r["t"] else datetime.now(timezone.utc))
                cur.execute(
                    """UPDATE nodes SET valid_to = COALESCE(valid_to, %s), is_active = false
                       WHERE tenant_id = %s AND id = %s AND is_active = true""",
                    (expiry, self.tenant_id, to_id),
                )
        self.conn.commit()

    @_writes
    def deactivate_node(self, node_id: str) -> None:
        """Soft-retire: is_active=false, valid_to=now (kept for audit/time-travel)."""
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE nodes SET is_active = false, valid_to = COALESCE(valid_to, %s) "
                "WHERE tenant_id = %s AND id = %s",
                (datetime.now(timezone.utc), self.tenant_id, node_id),
            )
        self.conn.commit()

    @_writes
    def delete_node(self, node_id: str) -> bool:
        with self.conn.cursor() as cur:
            cur.execute("SELECT 1 FROM nodes WHERE tenant_id = %s AND id = %s",
                        (self.tenant_id, node_id))
            if not cur.fetchone():
                return False
            cur.execute("DELETE FROM edges WHERE tenant_id = %s AND (from_id = %s OR to_id = %s)",
                        (self.tenant_id, node_id, node_id))
            cur.execute("DELETE FROM nodes WHERE tenant_id = %s AND id = %s",
                        (self.tenant_id, node_id))
        self.conn.commit()
        return True

    @_writes
    def record_hits(self, node_ids: list[str], entry_ids: set[str]) -> None:
        if not node_ids:
            return
        now = datetime.now(timezone.utc)
        with self.conn.cursor() as cur:
            cur.executemany(
                "UPDATE nodes SET hit_count = hit_count + 1, last_used_at = %s "
                "WHERE tenant_id = %s AND id = %s",
                [(now, self.tenant_id, nid) for nid in node_ids])
            entries = [nid for nid in entry_ids if nid in set(node_ids)]
            if entries:
                cur.executemany(
                    "UPDATE nodes SET entry_hit_count = entry_hit_count + 1 "
                    "WHERE tenant_id = %s AND id = %s",
                    [(self.tenant_id, nid) for nid in entries])
        self.conn.commit()

    @_writes
    def merge_extraction(self, nodes: list[dict], edges: list[dict]) -> dict[str, str]:
        """Merge extracted nodes + edges; returns id_map (new_id → canonical_id).

        Mirrors GraphStore.merge_extraction: add each node (dedup may remap its id),
        rewrite edges through the id_map, and for supersedes edges record the link on
        the superseding node's supersedes list (add_edge already expires the target).
        """
        id_map: dict[str, str] = {}
        for node in nodes:
            cid = self.add_node(node)
            if cid != node["id"]:
                id_map[node["id"]] = cid

        def _resolve(nid: str) -> str:
            return id_map.get(nid, nid)

        for edge in edges:
            f, t = _resolve(edge["from_id"]), _resolve(edge["to_id"])
            self.add_edge(f, t, edge["relation"])
            if edge["relation"] == "supersedes":
                existing = self.get_node(f)
                if existing:
                    sl = list(existing.get("supersedes", []))
                    if t not in sl:
                        sl.append(t)
                        with self.conn.cursor() as cur:
                            cur.execute(
                                "UPDATE nodes SET supersedes = %s WHERE tenant_id = %s AND id = %s",
                                (json.dumps(sl), self.tenant_id, f))
                        self.conn.commit()
        return id_map

    # ------------------------------------------------------------------ reads
    def _query_nodes(self, where: str, params: tuple) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT {self._COLS} FROM nodes WHERE tenant_id = %s {where}",
                        (self.tenant_id, *params))
            return [self._row_to_node(r) for r in cur.fetchall()]

    def get_node(self, node_id: str) -> dict | None:
        rows = self._query_nodes("AND id = %s", (node_id,))
        return rows[0] if rows else None

    def get_node_by_hash(self, fact_hash: str) -> dict | None:
        rows = self._query_nodes("AND fact_hash = %s LIMIT 1", (fact_hash,))
        return rows[0] if rows else None

    def get_all_nodes(self) -> list[dict]:
        return self._query_nodes("", ())

    def get_active_nodes(self) -> list[dict]:
        return self._query_nodes("AND is_active = true", ())

    def get_nodes_by_type(self, node_type: str) -> list[dict]:
        return self._query_nodes("AND type = %s", (node_type,))

    def get_nodes_by_tags(self, tags: list[str]) -> list[dict]:
        """Nodes whose tags overlap any of `tags` (jsonb containment, GIN-indexed)."""
        if not tags:
            return []
        clauses = " OR ".join(["tags @> %s"] * len(tags))
        return self._query_nodes(f"AND ({clauses})", tuple(json.dumps([t]) for t in tags))

    def get_typed_nodes_by_tags(
        self, types: list[str], keywords: list[str], limit: int
    ) -> list[dict]:
        """Mirror of GraphStore.get_typed_nodes_by_tags for the jsonb tags column.

        Substring match is done against the jsonb text form (``tags::text ILIKE``)
        to match SQLite's ``tags LIKE`` semantics rather than exact-element @>.
        """
        if not types or not keywords:
            return []
        kw_cond = " OR ".join(["tags::text ILIKE %s"] * len(keywords))
        where = f"AND type = ANY(%s) AND ({kw_cond}) ORDER BY confidence DESC LIMIT %s"
        params = (list(types), *(f"%{kw}%" for kw in keywords), int(limit))
        return self._query_nodes(where, params)

    def get_superseded_target_ids(self, candidate_ids: list[str]) -> set[str]:
        """Mirror of GraphStore.get_superseded_target_ids — single ``= ANY`` query,
        no chunking needed (Postgres binds the array natively)."""
        ids = list(candidate_ids)
        if not ids:
            return set()
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT to_id FROM edges WHERE tenant_id = %s AND relation = 'supersedes'"
                " AND to_id = ANY(%s)",
                (self.tenant_id, ids),
            )
            return {r["to_id"] for r in cur.fetchall()}

    def get_nodes_by_fact_text(self, keywords: list[str], limit: int = 150) -> list[dict]:
        """Active nodes whose fact text contains any keyword (substring, case-insensitive).

        Mirrors GraphStore.get_nodes_by_fact_text (the retriever's keyword path).
        """
        if not keywords:
            return []
        seen: set[str] = set()
        out: list[dict] = []
        lim = int(limit)
        for i in range(0, len(keywords), 200):
            chunk = keywords[i:i + 200]
            conds = " OR ".join(["fact ILIKE %s"] * len(chunk))
            params = (self.tenant_id, *[f"%{kw}%" for kw in chunk], lim)
            with self.conn.cursor() as cur:
                cur.execute(
                    f"SELECT {self._COLS} FROM nodes WHERE tenant_id = %s "
                    f"AND is_active = true AND ({conds}) LIMIT %s", params)
                for r in cur.fetchall():
                    if r["id"] not in seen:
                        seen.add(r["id"])
                        out.append(self._row_to_node(r))
        return out

    def get_recent_nodes(self, limit: int = 20) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT {self._COLS} FROM nodes WHERE tenant_id = %s "
                        "ORDER BY created_at DESC LIMIT %s", (self.tenant_id, limit))
            return [self._row_to_node(r) for r in cur.fetchall()]

    def get_edges_from(self, node_id: str) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT from_id, to_id, relation FROM edges "
                        "WHERE tenant_id = %s AND from_id = %s", (self.tenant_id, node_id))
            return [dict(r) for r in cur.fetchall()]

    def get_edges_to(self, node_id: str) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT from_id, to_id, relation FROM edges "
                        "WHERE tenant_id = %s AND to_id = %s", (self.tenant_id, node_id))
            return [dict(r) for r in cur.fetchall()]

    def get_all_edges(self) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT from_id, to_id, relation FROM edges WHERE tenant_id = %s",
                        (self.tenant_id,))
            return [dict(r) for r in cur.fetchall()]

    def get_stats(self) -> dict:
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM nodes WHERE tenant_id = %s", (self.tenant_id,))
            nodes = cur.fetchone()["n"]
            cur.execute("SELECT count(*) AS n FROM edges WHERE tenant_id = %s", (self.tenant_id,))
            edges = cur.fetchone()["n"]
        return {"node_count": nodes, "edge_count": edges}

    def detect_stale_candidates(self, *, never_retrieved_days: int = 90, min_age_days: int = 30,
                                max_confidence: float = 0.95, expiry_factor: float = 1.5,
                                half_lives: dict | None = None) -> list[dict]:
        """Proactive staleness detection (Layer 0) — mirror of GraphStore's."""
        hl_map = half_lives or _INVALIDATION_HALF_LIVES
        now = datetime.now(timezone.utc)
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT id, type, fact, confidence, hit_count, "
                "COALESCE(occurred_at, created_at) AS ts FROM nodes "
                "WHERE tenant_id = %s AND is_active = true AND pinned = false",
                (self.tenant_id,))
            rows = cur.fetchall()
        out: list[dict] = []
        for r in rows:
            ts = r["ts"]
            if not isinstance(ts, datetime):
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age = (now - ts).days
            if age < min_age_days:
                continue
            conf = r["confidence"] if r["confidence"] is not None else 0.5
            hl = hl_map.get(r["type"])
            if hl and age > hl * expiry_factor and conf < max_confidence:
                out.append({"id": r["id"], "type": r["type"], "fact": r["fact"],
                            "reason": "temporal_half_life_expired",
                            "detail": f"age={age}d > {hl}d×{expiry_factor:g}", "score": 0.7})
            elif (r["hit_count"] or 0) == 0 and age > never_retrieved_days and conf < 0.75:
                out.append({"id": r["id"], "type": r["type"], "fact": r["fact"],
                            "reason": "never_retrieved_aged",
                            "detail": f"hit_count=0, age={age}d, conf={conf:.2f}", "score": 0.6})
        return out

    # ------------------------------------------------------------------ time-travel
    def get_nodes_at_time(self, valid_at: str | None = None,
                          transaction_at: str | None = None) -> list[dict]:
        """Bi-temporal point-in-time query (mirror of GraphStore.get_nodes_at_time).

        valid_at: facts whose validity window covers the moment. transaction_at:
        facts ingested on/before the moment. Filters stack; omit either to skip it.
        """
        clauses = ["tenant_id = %s"]
        params: list = [self.tenant_id]
        if valid_at:
            clauses.append("(occurred_at IS NULL OR occurred_at <= %s::timestamptz) "
                           "AND (valid_to IS NULL OR valid_to > %s::timestamptz)")
            params += [valid_at, valid_at]
        if transaction_at:
            clauses.append("created_at <= %s::timestamptz")
            params.append(transaction_at)
        where = " AND ".join(clauses)
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT {self._COLS} FROM nodes WHERE {where} "
                        "ORDER BY occurred_at, created_at", params)
            return [self._row_to_node(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------ worlds (namespaces)
    @_writes
    def create_world(self, name: str, tags: list[str] | None = None,
                     parent_world_id: str | None = None,
                     description: str | None = None) -> str:
        """Create a world container node (type='world') + worlds row; return its id."""
        from uuid import uuid4
        world_node_id = f"n_{uuid4().hex[:8]}"
        world_tags = sorted(set(list(tags or []) + [t.lower() for t in name.split()]))
        self.add_node({
            "id": world_node_id, "fact": name, "type": "world",
            "confidence": 1.0, "tags": world_tags,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO worlds (tenant_id, world_id, name, description, created_at) "
                "VALUES (%s,%s,%s,%s,%s)",
                (self.tenant_id, world_node_id, name, description,
                 datetime.now(timezone.utc)))
        self.conn.commit()
        if parent_world_id is not None:
            self.add_edge(parent_world_id, world_node_id, "contains")
        return world_node_id

    def get_world(self, world_id: str) -> dict | None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT world_id, name, description, created_at FROM worlds "
                        "WHERE tenant_id = %s AND world_id = %s", (self.tenant_id, world_id))
            r = cur.fetchone()
            if r and isinstance(r.get("created_at"), datetime):
                r["created_at"] = r["created_at"].isoformat()
            return dict(r) if r else None

    @_writes
    def add_node_to_world(self, node_id: str, world_id: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT 1 FROM worlds WHERE tenant_id = %s AND world_id = %s",
                        (self.tenant_id, world_id))
            if not cur.fetchone():
                raise ValueError(f"World {world_id} does not exist")
            cur.execute("UPDATE nodes SET world_id = %s WHERE tenant_id = %s AND id = %s",
                        (world_id, self.tenant_id, node_id))
        self.conn.commit()

    def get_world_nodes(self, world_id: str, recursive: bool = False) -> list[dict]:
        """Nodes assigned to a world. recursive=True follows 'contains' edges to children."""
        world_ids = [world_id]
        if recursive:
            visited = {world_id}
            frontier = [world_id]
            while frontier:
                wid = frontier.pop()
                for e in self.get_edges_from(wid):
                    if e["relation"] == "contains" and e["to_id"] not in visited:
                        visited.add(e["to_id"])
                        frontier.append(e["to_id"])
                        world_ids.append(e["to_id"])
        placeholders = ",".join(["%s"] * len(world_ids))
        return self._query_nodes(f"AND world_id IN ({placeholders})", tuple(world_ids))

    def list_worlds(self) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT w.world_id, w.name, w.description, "
                "(SELECT count(*) FROM nodes n WHERE n.tenant_id = w.tenant_id "
                " AND n.world_id = w.world_id) AS node_count "
                "FROM worlds w WHERE w.tenant_id = %s ORDER BY w.created_at",
                (self.tenant_id,))
            return [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------ retrieval parity
    def get_nodes_by_ids(self, node_ids: list[str]) -> list[dict]:
        if not node_ids:
            return []
        return self._query_nodes("AND id = ANY(%s)", (list(node_ids),))

    def get_pinned_nodes(self) -> list[dict]:
        return self._query_nodes("AND pinned = true ORDER BY type, confidence DESC", ())

    @_writes
    def pin_node(self, node_id: str) -> bool:
        with self.conn.cursor() as cur:
            cur.execute("UPDATE nodes SET pinned = true WHERE tenant_id = %s AND id = %s",
                        (self.tenant_id, node_id))
            ok = cur.rowcount > 0
        self.conn.commit()
        return ok

    @_writes
    def unpin_node(self, node_id: str) -> bool:
        with self.conn.cursor() as cur:
            cur.execute("UPDATE nodes SET pinned = false WHERE tenant_id = %s AND id = %s",
                        (self.tenant_id, node_id))
            ok = cur.rowcount > 0
        self.conn.commit()
        return ok

    def get_edges_for_nodes(self, node_ids: list[str]) -> list[dict]:
        """All edges touching any of node_ids (either direction), deduped."""
        if not node_ids:
            return []
        ids = list(node_ids)
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT from_id, to_id, relation, weight FROM edges "
                "WHERE tenant_id = %s AND (from_id = ANY(%s) OR to_id = ANY(%s))",
                (self.tenant_id, ids, ids))
            return [dict(r) for r in cur.fetchall()]

    def search_by_fts(self, query: str, top_k: int = 50) -> list:
        """Keyword relevance search via Postgres FTS (ts_rank). Returns (id, score)."""
        if not query.strip():
            return []
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT id, ts_rank_cd(fact_fts, plainto_tsquery('english', %s)) AS score "
                "FROM nodes WHERE tenant_id = %s AND is_active = true "
                "AND fact_fts @@ plainto_tsquery('english', %s) "
                "ORDER BY score DESC LIMIT %s",
                (query, self.tenant_id, query, int(top_k)))
            return [(r["id"], float(r["score"])) for r in cur.fetchall()]

    # raw-sentence / episodes tier not yet ported → graceful no-ops (fallback skips)
    def has_raw_sentences(self) -> bool:
        return False

    def semantic_search_raw_sentences(self, *args, **kwargs) -> list:
        return []

    # ------------------------------------------------------------------ hyperedges (EHRAG)
    @_writes
    def add_hyperedge_member(self, hyperedge_id: str, node_id: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO hyperedge_members (tenant_id, hyperedge_id, node_id) "
                        "VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                        (self.tenant_id, hyperedge_id, node_id))
        self.conn.commit()

    def has_hyperedges(self) -> bool:
        with self.conn.cursor() as cur:
            cur.execute("SELECT 1 FROM hyperedge_members WHERE tenant_id = %s LIMIT 1",
                        (self.tenant_id,))
            return cur.fetchone() is not None

    def get_hyperedge_siblings(self, node_ids: list[str]) -> list[str]:
        if not node_ids:
            return []
        ids = list(node_ids)
        with self.conn.cursor() as cur:
            cur.execute("SELECT DISTINCT hyperedge_id FROM hyperedge_members "
                        "WHERE tenant_id = %s AND node_id = ANY(%s)", (self.tenant_id, ids))
            he_ids = [r["hyperedge_id"] for r in cur.fetchall()]
            if not he_ids:
                return []
            cur.execute("SELECT DISTINCT node_id FROM hyperedge_members "
                        "WHERE tenant_id = %s AND hyperedge_id = ANY(%s) AND NOT (node_id = ANY(%s))",
                        (self.tenant_id, he_ids, ids))
            return [r["node_id"] for r in cur.fetchall()]

    # ------------------------------------------------------------------ edge query rules
    @_writes
    def set_edge_query_rule(self, from_id: str, to_id: str, rule_type: str,
                            params: dict | None = None) -> None:
        rule_id = f"{from_id}__{to_id}"
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO edge_query_rules (tenant_id, rule_id, rule_type, params) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT (tenant_id, rule_id) DO UPDATE SET "
                "rule_type = EXCLUDED.rule_type, params = EXCLUDED.params",
                (self.tenant_id, rule_id, rule_type,
                 json.dumps(params) if params else None))
            cur.execute("UPDATE edges SET on_query_rewrite = %s "
                        "WHERE tenant_id = %s AND from_id = %s AND to_id = %s",
                        (rule_id, self.tenant_id, from_id, to_id))
        self.conn.commit()

    def get_edge_query_rule(self, from_id: str, to_id: str) -> dict | None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT rule_id, rule_type, params FROM edge_query_rules "
                        "WHERE tenant_id = %s AND rule_id = %s",
                        (self.tenant_id, f"{from_id}__{to_id}"))
            r = cur.fetchone()
            return {"rule_id": r["rule_id"], "rule_type": r["rule_type"],
                    "params": r["params"]} if r else None

    # ------------------------------------------------------------------ extraction failures
    @_writes
    def log_extraction_failure(self, error_type: str, error_message: str,
                               raw_response: str = "", source_transcript: str = "",
                               model: str = "") -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO extraction_failures (tenant_id, source_transcript, model, "
                "error_type, raw_response, error_message) VALUES (%s,%s,%s,%s,%s,%s)",
                (self.tenant_id, source_transcript, model, error_type,
                 raw_response[:2000], error_message))
        self.conn.commit()

    def get_extraction_failures(self, limit: int = 50) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT id, created_at, source_transcript, model, error_type, "
                        "raw_response, error_message FROM extraction_failures "
                        "WHERE tenant_id = %s ORDER BY created_at DESC LIMIT %s",
                        (self.tenant_id, int(limit)))
            out = []
            for r in cur.fetchall():
                d = dict(r)
                if isinstance(d.get("created_at"), datetime):
                    d["created_at"] = d["created_at"].isoformat()
                out.append(d)
            return out

    # ------------------------------------------------------------------ buffer / watermark (kv)
    def _kv_get(self, key: str) -> dict:
        with self.conn.cursor() as cur:
            cur.execute("SELECT value FROM kv WHERE tenant_id = %s AND key = %s",
                        (self.tenant_id, key))
            r = cur.fetchone()
            return r["value"] if r else {}

    @_writes
    def _kv_set(self, key: str, value: dict) -> None:
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO kv (tenant_id, key, value) VALUES (%s,%s,%s) "
                        "ON CONFLICT (tenant_id, key) DO UPDATE SET value = EXCLUDED.value",
                        (self.tenant_id, key, json.dumps(value)))
        self.conn.commit()

    def load_buffer(self) -> list[str]:
        return self._kv_get("buffer").get("turns", [])

    def save_buffer(self, turns: list[str]) -> None:
        d = self._kv_get("buffer")
        d["turns"] = turns
        self._kv_set("buffer", d)

    def clear_buffer(self) -> None:
        d = self._kv_get("buffer")
        d["turns"] = []
        self._kv_set("buffer", d)

    def load_watermark(self, transcript_path: str) -> int:
        d = self._kv_get("buffer")
        return d.get("transcript_watermark", 0) if d.get("transcript_path") == transcript_path else 0

    def save_watermark(self, transcript_path: str, line_count: int) -> None:
        d = self._kv_get("buffer")
        d["transcript_path"] = transcript_path
        d["transcript_watermark"] = line_count
        self._kv_set("buffer", d)

    # ------------------------------------------------------------------ misc parity
    @_writes
    def boost_confidence(self, node_id: str, delta: float = 0.02) -> None:
        with self.conn.cursor() as cur:
            cur.execute("UPDATE nodes SET confidence = LEAST(1.0, COALESCE(confidence,0.5) + %s) "
                        "WHERE tenant_id = %s AND id = %s", (delta, self.tenant_id, node_id))
        self.conn.commit()

    @_writes
    def prune_superseded(self) -> int:
        """Deactivate active nodes that appear in any node's supersedes[] list."""
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE nodes SET is_active = false, valid_to = COALESCE(valid_to, now()) "
                "WHERE tenant_id = %s AND is_active = true AND id IN ("
                "  SELECT jsonb_array_elements_text(supersedes) FROM nodes "
                "  WHERE tenant_id = %s AND jsonb_typeof(supersedes) = 'array')",
                (self.tenant_id, self.tenant_id))
            n = cur.rowcount
        self.conn.commit()
        return n

    def get_sources(self) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT source_transcript AS source, count(*) AS node_count "
                        "FROM nodes WHERE tenant_id = %s AND source_transcript IS NOT NULL "
                        "GROUP BY source_transcript ORDER BY node_count DESC", (self.tenant_id,))
            return [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------ vectors (pgvector)
    @_writes
    def store_embeddings(self, pairs: list) -> None:
        """Upsert (node_id, float32-blob) embeddings into the nodes.embedding column."""
        if not pairs:
            return
        with self.conn.cursor() as cur:
            cur.executemany(
                "UPDATE nodes SET embedding = %s WHERE tenant_id = %s AND id = %s",
                [(_blob_to_vec(blob), self.tenant_id, nid) for nid, blob in pairs])
        self.conn.commit()

    def search_by_embedding(self, query_blob: bytes, top_k: int = 10) -> list[str]:
        """Return node ids nearest the query embedding by cosine distance (pgvector hnsw)."""
        vec = _blob_to_vec(query_blob)
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM nodes WHERE tenant_id = %s AND embedding IS NOT NULL "
                "ORDER BY embedding <=> %s LIMIT %s",
                (self.tenant_id, vec, int(top_k)))
            return [r["id"] for r in cur.fetchall()]

    def close(self) -> None:
        conn = getattr(self, "conn", None)
        if conn is None:
            return
        pool = getattr(self, "_pool", None)
        if pool is not None:
            # Roll back any open transaction so the next borrower never inherits a
            # dirty/aborted one (a no-op after a committed write). A broken conn is
            # detected + discarded by the pool on putconn.
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                pool.putconn(conn)
                return  # the pool owns it now — do NOT also close it
            except Exception:
                pass  # putconn failed → it wasn't returned; hard-close as cleanup
        try:
            conn.close()
        except Exception:
            pass
