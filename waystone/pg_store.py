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
    from psycopg.rows import dict_row
    _PSYCOPG_OK = True
except Exception:  # pragma: no cover - import guard
    psycopg = None
    dict_row = None
    _PSYCOPG_OK = False


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
        self.conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=False)
        self._init_schema()

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
                    embedding   vector({self._dim}),
                    fact_fts    tsvector GENERATED ALWAYS AS (to_tsvector('english', fact)) STORED,
                    PRIMARY KEY (tenant_id, id)
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_nodes_active ON nodes (tenant_id, is_active)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes (tenant_id, type)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_nodes_fhash ON nodes (tenant_id, fact_hash)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_nodes_tags ON nodes USING gin (tags jsonb_path_ops)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_nodes_fts ON nodes USING gin (fact_fts)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS edges (
                    tenant_id text NOT NULL,
                    from_id   text NOT NULL,
                    to_id     text NOT NULL,
                    relation  text NOT NULL,
                    PRIMARY KEY (tenant_id, from_id, to_id, relation)
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
             "valid_to, is_active, pinned, hit_count, entry_hit_count, last_used_at")

    # ------------------------------------------------------------------ writes
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

    def add_edge(self, from_id: str, to_id: str, relation: str) -> None:
        """Insert an edge (idempotent). Mirrors the supersedes→expiry side effect."""
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO edges (tenant_id, from_id, to_id, relation)
                   VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
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

    def deactivate_node(self, node_id: str) -> None:
        """Soft-retire: is_active=false, valid_to=now (kept for audit/time-travel)."""
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE nodes SET is_active = false, valid_to = COALESCE(valid_to, %s) "
                "WHERE tenant_id = %s AND id = %s",
                (datetime.now(timezone.utc), self.tenant_id, node_id),
            )
        self.conn.commit()

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

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass
