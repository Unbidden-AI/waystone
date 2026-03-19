"""SQLite-backed graph store for context nodes and edges."""

import hashlib
import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


def _normalize_fact(fact: str) -> str:
    """Normalize fact text for deduplication hashing.

    Lowercases, strips punctuation, and collapses whitespace so that
    semantically identical facts with minor formatting differences hash
    to the same value.
    """
    text = fact.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _fact_hash(fact: str) -> str:
    """Return a 16-char hex SHA-256 of the normalized fact text."""
    return hashlib.sha256(_normalize_fact(fact).encode()).hexdigest()[:16]


def _auto_tag_numerics(fact: str, tags: list[str]) -> list[str]:
    """Augment tags with digit-containing tokens parsed from fact text.

    Extracts tokens like "15-minute", "1000/min", "rs256", "rfc-7807" that
    contain at least one digit. These are added to tags if not already present,
    so numeric-value queries can find the node even when the extractor omitted
    the specific value from its tag list.
    """
    # Match tokens containing at least one digit; include hyphens/slashes for
    # compound values like "15-minute" or "1000/min"
    raw = re.findall(r"[\w][\w/\-]*\d[\w/\-]*|(?<!\w)\d[\w/\-]*", fact.lower())
    existing = {t.lower() for t in tags}
    extras = [t for t in raw if t not in existing and len(t) >= 1]
    # Deduplicate while preserving order
    seen: set[str] = set()
    result = list(tags)
    for token in extras:
        if token not in seen:
            seen.add(token)
            result.append(token)
    return result


class GraphStore:
    """DAG storage using SQLite with nodes and edges tables."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.init_db()

    def init_db(self):
        """Create tables if they don't exist."""
        # WAL mode allows concurrent readers + one writer without blocking
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS nodes (
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

            CREATE TABLE IF NOT EXISTS edges (
                from_id TEXT NOT NULL,
                to_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                PRIMARY KEY (from_id, to_id, relation)
            );

            CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
            CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(from_id);
            CREATE INDEX IF NOT EXISTS idx_edges_to ON edges(to_id);
        """)
        # Migration: add fact_hash column if it doesn't exist yet
        try:
            self.conn.execute("ALTER TABLE nodes ADD COLUMN fact_hash TEXT")
            self.conn.commit()
            log.info("Migrated nodes table: added fact_hash column")
        except sqlite3.OperationalError:
            pass  # Column already exists
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_nodes_fact_hash ON nodes(fact_hash)"
        )
        # Backfill fact_hash for existing rows that have NULL
        rows = self.conn.execute(
            "SELECT id, fact FROM nodes WHERE fact_hash IS NULL"
        ).fetchall()
        if rows:
            self.conn.executemany(
                "UPDATE nodes SET fact_hash = ? WHERE id = ?",
                [(_fact_hash(r[1]), r[0]) for r in rows],
            )
            log.info("Backfilled fact_hash for %d existing nodes", len(rows))
        self.conn.commit()

    def add_node(self, node: dict) -> str:
        """Insert a node, deduplicating by fact text hash.

        If a node with the same normalized fact text already exists, the
        incoming node is merged into it: tags are unioned and confidence
        takes the maximum of the two values. Returns the canonical node ID
        (either the existing one or the newly inserted one).
        """
        tags = _auto_tag_numerics(node["fact"], node.get("tags", []))
        fhash = _fact_hash(node["fact"])
        existing_row = self.conn.execute(
            "SELECT id, tags, confidence FROM nodes WHERE fact_hash = ? LIMIT 1",
            (fhash,),
        ).fetchone()
        if existing_row and existing_row[0] != node["id"]:
            # Merge into existing node: union tags, keep max confidence
            existing_id = existing_row[0]
            existing_tags: set = set(json.loads(existing_row[1]))
            merged_tags = sorted(existing_tags | set(tags))
            merged_conf = max(existing_row[2], node.get("confidence", 0.5))
            self.conn.execute(
                "UPDATE nodes SET tags = ?, confidence = ? WHERE id = ?",
                (json.dumps(merged_tags), merged_conf, existing_id),
            )
            self.conn.commit()
            log.debug("Dedup: merged node %s into existing %s", node["id"], existing_id)
            return existing_id
        self.conn.execute(
            """INSERT OR REPLACE INTO nodes
               (id, fact, type, confidence, source_transcript,
                source_message_index, tags, created_at, supersedes, fact_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                node["id"],
                node["fact"],
                node["type"],
                node.get("confidence", 0.5),
                node.get("source_transcript"),
                node.get("source_message_index"),
                json.dumps(tags),
                node.get("created_at", datetime.now(timezone.utc).isoformat()),
                json.dumps(node.get("supersedes", [])),
                fhash,
            ),
        )
        self.conn.commit()
        return node["id"]

    def delete_node(self, node_id: str) -> bool:
        """Delete a single node and all its edges. Returns True if node existed."""
        row = self.conn.execute("SELECT id FROM nodes WHERE id = ?", (node_id,)).fetchone()
        if not row:
            return False
        self.conn.execute("DELETE FROM edges WHERE from_id = ? OR to_id = ?", (node_id, node_id))
        self.conn.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
        self.conn.commit()
        return True

    def update_node_fact(self, node_id: str, new_fact: str, new_confidence: float | None = None) -> bool:
        """Update a node's fact text, recalculating its hash and auto-tags.

        Returns True if the node existed and was updated.
        """
        row = self.conn.execute("SELECT tags FROM nodes WHERE id = ?", (node_id,)).fetchone()
        if not row:
            return False
        existing_tags = json.loads(row["tags"])
        new_tags = _auto_tag_numerics(new_fact, existing_tags)
        new_hash = _fact_hash(new_fact)
        if new_confidence is not None:
            self.conn.execute(
                "UPDATE nodes SET fact = ?, fact_hash = ?, tags = ?, confidence = ? WHERE id = ?",
                (new_fact, new_hash, json.dumps(new_tags), new_confidence, node_id),
            )
        else:
            self.conn.execute(
                "UPDATE nodes SET fact = ?, fact_hash = ?, tags = ? WHERE id = ?",
                (new_fact, new_hash, json.dumps(new_tags), node_id),
            )
        self.conn.commit()
        return True

    def add_edge(self, from_id: str, to_id: str, relation: str):
        """Insert an edge, ignoring duplicates."""
        self.conn.execute(
            "INSERT OR IGNORE INTO edges (from_id, to_id, relation) VALUES (?, ?, ?)",
            (from_id, to_id, relation),
        )
        self.conn.commit()

    def get_node(self, node_id: str) -> dict | None:
        """Fetch a single node by ID."""
        row = self.conn.execute(
            "SELECT * FROM nodes WHERE id = ?", (node_id,)
        ).fetchone()
        return self._row_to_node(row) if row else None

    def get_all_nodes(self) -> list[dict]:
        """Fetch all nodes."""
        rows = self.conn.execute("SELECT * FROM nodes").fetchall()
        return [self._row_to_node(r) for r in rows]

    def get_recent_nodes(self, limit: int = 20) -> list[dict]:
        """Fetch most recent nodes."""
        rows = self.conn.execute(
            "SELECT * FROM nodes ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_node(r) for r in rows]

    def get_nodes_by_tags(self, tags: list[str]) -> list[dict]:
        """Find nodes whose tags overlap with the given list.

        Falls back to fact-text search if tag matching returns no results.
        """
        if not tags:
            return []
        conditions = " OR ".join(["tags LIKE ?" for _ in tags])
        params = [f'%{tag}%' for tag in tags]
        rows = self.conn.execute(
            f"SELECT * FROM nodes WHERE {conditions}", params
        ).fetchall()
        if rows:
            return [self._row_to_node(r) for r in rows]

        # Fallback: search fact text when no tag matches found
        fact_conditions = " OR ".join(["fact LIKE ?" for _ in tags])
        fact_params = [f"%{tag}%" for tag in tags]
        rows = self.conn.execute(
            f"SELECT * FROM nodes WHERE {fact_conditions}", fact_params
        ).fetchall()
        return [self._row_to_node(r) for r in rows]

    def get_nodes_by_fact_text(self, keywords: list[str]) -> list[dict]:
        """Find nodes whose fact text contains any of the given keywords."""
        if not keywords:
            return []
        conditions = " OR ".join(["fact LIKE ?" for _ in keywords])
        params = [f"%{kw}%" for kw in keywords]
        rows = self.conn.execute(
            f"SELECT * FROM nodes WHERE {conditions}", params
        ).fetchall()
        return [self._row_to_node(r) for r in rows]

    def get_nodes_by_ids(self, node_ids: list[str]) -> list[dict]:
        """Fetch multiple nodes by ID in a single query."""
        if not node_ids:
            return []
        placeholders = ",".join("?" * len(node_ids))
        rows = self.conn.execute(
            f"SELECT * FROM nodes WHERE id IN ({placeholders})", node_ids
        ).fetchall()
        return [self._row_to_node(r) for r in rows]

    def get_edges_for_nodes(self, node_ids: list[str]) -> list[dict]:
        """Fetch all edges where from_id or to_id is in node_ids."""
        if not node_ids:
            return []
        placeholders = ",".join("?" * len(node_ids))
        rows = self.conn.execute(
            f"SELECT * FROM edges WHERE from_id IN ({placeholders}) OR to_id IN ({placeholders})",
            node_ids + node_ids,
        ).fetchall()
        return [dict(r) for r in rows]

    def get_edges_from(self, node_id: str) -> list[dict]:
        """Get all outgoing edges from a node."""
        rows = self.conn.execute(
            "SELECT * FROM edges WHERE from_id = ?", (node_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_edges_to(self, node_id: str) -> list[dict]:
        """Get all incoming edges to a node."""
        rows = self.conn.execute(
            "SELECT * FROM edges WHERE to_id = ?", (node_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_edges(self) -> list[dict]:
        """Fetch all edges."""
        rows = self.conn.execute("SELECT * FROM edges").fetchall()
        return [dict(r) for r in rows]

    def merge_extraction(self, nodes: list[dict], edges: list[dict]):
        """Merge extracted nodes and edges into the graph.

        Handles supersedes: when a new node supersedes existing ones,
        those are recorded and the new node is inserted.

        Deduplication: nodes whose normalized fact text matches an existing
        node are merged into that node (tags unioned, confidence max). The
        id_map tracks new_id → canonical_id so edges are rewritten to point
        at canonical nodes.
        """
        log.info("Merging %d nodes, %d edges into %s", len(nodes), len(edges), self.db_path.name)
        id_map: dict[str, str] = {}
        for node in nodes:
            canonical_id = self.add_node(node)
            if canonical_id != node["id"]:
                id_map[node["id"]] = canonical_id

        def _resolve(nid: str) -> str:
            return id_map.get(nid, nid)

        for edge in edges:
            self.add_edge(_resolve(edge["from_id"]), _resolve(edge["to_id"]), edge["relation"])
            # If it's a supersedes edge, also record in the node's supersedes list
            if edge["relation"] == "supersedes":
                from_id = _resolve(edge["from_id"])
                to_id = _resolve(edge["to_id"])
                existing = self.get_node(from_id)
                if existing:
                    supersedes_list = existing.get("supersedes", [])
                    if to_id not in supersedes_list:
                        supersedes_list.append(to_id)
                        self.conn.execute(
                            "UPDATE nodes SET supersedes = ? WHERE id = ?",
                            (json.dumps(supersedes_list), from_id),
                        )
                        self.conn.commit()

    def propagate_edge_tags(self):
        """Bidirectionally propagate tags along non-supersedes edges (1 hop).

        For each edge A → B (excluding 'supersedes'), merges B's tags into A
        and A's tags into B. This increases BFS seed coverage: a query that
        matches B's keywords will now also surface A, and vice versa.

        Called once after merge_extraction() to enrich the tag index without
        any additional LLM calls.
        """
        edges = self.conn.execute(
            "SELECT from_id, to_id FROM edges WHERE relation != 'supersedes'"
        ).fetchall()

        if not edges:
            return

        # Collect all node IDs touched by non-supersedes edges
        node_ids = list({nid for row in edges for nid in (row[0], row[1])})
        placeholders = ",".join("?" * len(node_ids))
        rows = self.conn.execute(
            f"SELECT id, tags FROM nodes WHERE id IN ({placeholders})", node_ids
        ).fetchall()
        tags_by_id: dict[str, set] = {row[0]: set(json.loads(row[1])) for row in rows}

        # Merge neighbor tags into each endpoint (1 hop, bidirectional)
        updated: dict[str, set] = {nid: set(tags) for nid, tags in tags_by_id.items()}
        for from_id, to_id in ((r[0], r[1]) for r in edges):
            if from_id in tags_by_id and to_id in tags_by_id:
                updated[from_id] |= tags_by_id[to_id]
                updated[to_id] |= tags_by_id[from_id]

        # Write back only nodes whose tag sets actually changed
        for nid, new_tags in updated.items():
            if new_tags != tags_by_id.get(nid, set()):
                self.conn.execute(
                    "UPDATE nodes SET tags = ? WHERE id = ?",
                    (json.dumps(sorted(new_tags)), nid),
                )
        self.conn.commit()

    def delete_nodes_by_source(self, source_transcript: str) -> int:
        """Delete all nodes (and their edges) with the given source_transcript value.

        Returns the number of nodes deleted.
        """
        rows = self.conn.execute(
            "SELECT id FROM nodes WHERE source_transcript = ?", (source_transcript,)
        ).fetchall()
        ids = [r["id"] for r in rows]
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        self.conn.execute(f"DELETE FROM edges WHERE from_id IN ({placeholders})", ids)
        self.conn.execute(f"DELETE FROM edges WHERE to_id IN ({placeholders})", ids)
        self.conn.execute(f"DELETE FROM nodes WHERE id IN ({placeholders})", ids)
        self.conn.commit()
        return len(ids)

    def prune_nodes(
        self,
        older_than_days: int | None = None,
        confidence_below: float | None = None,
        source_pattern: str | None = None,
        dry_run: bool = True,
    ) -> list[str]:
        """Return (and optionally delete) nodes matching all supplied criteria.

        All criteria are ANDed. Runs in preview mode unless dry_run=False.
        Returns the list of node IDs that match (or were deleted).
        """
        query = "SELECT id FROM nodes WHERE 1=1"
        params: list = []
        if older_than_days is not None:
            from datetime import datetime, timedelta, timezone
            cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
            query += " AND created_at < ?"
            params.append(cutoff)
        if confidence_below is not None:
            query += " AND confidence < ?"
            params.append(confidence_below)
        if source_pattern is not None:
            query += " AND source_transcript LIKE ?"
            params.append(f"%{source_pattern}%")
        rows = self.conn.execute(query, params).fetchall()
        ids = [r["id"] for r in rows]
        if not dry_run and ids:
            placeholders = ",".join("?" * len(ids))
            self.conn.execute(f"DELETE FROM edges WHERE from_id IN ({placeholders})", ids)
            self.conn.execute(f"DELETE FROM edges WHERE to_id IN ({placeholders})", ids)
            self.conn.execute(f"DELETE FROM nodes WHERE id IN ({placeholders})", ids)
            self.conn.commit()
        return ids

    def get_stats(self) -> dict:
        """Get graph statistics."""
        node_count = self.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edge_count = self.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        type_rows = self.conn.execute(
            "SELECT type, COUNT(*) as cnt FROM nodes GROUP BY type"
        ).fetchall()
        type_counts = {row["type"]: row["cnt"] for row in type_rows}
        return {
            "node_count": node_count,
            "edge_count": edge_count,
            "type_counts": type_counts,
        }

    def _buf_path(self) -> Path:
        return self.db_path.parent / "buffer.json"

    def _load_buf_data(self) -> dict:
        p = self._buf_path()
        if p.exists():
            try:
                return json.loads(p.read_text())
            except Exception:
                pass
        return {}

    def _save_buf_data(self, data: dict) -> None:
        self._buf_path().write_text(json.dumps(data))

    def load_buffer(self) -> list[str]:
        """Load persisted turn buffer from disk."""
        return self._load_buf_data().get("turns", [])

    def save_buffer(self, turns: list[str]) -> None:
        """Persist turn buffer to disk, preserving other fields."""
        data = self._load_buf_data()
        data["turns"] = turns
        self._save_buf_data(data)

    def clear_buffer(self) -> None:
        """Clear buffered turns, preserving watermark and other fields."""
        data = self._load_buf_data()
        data["turns"] = []
        self._save_buf_data(data)

    def load_watermark(self, transcript_path: str) -> int:
        """Return the number of JSONL lines already processed for this transcript."""
        data = self._load_buf_data()
        if data.get("transcript_path") != transcript_path:
            return 0
        return data.get("transcript_watermark", 0)

    def save_watermark(self, transcript_path: str, line_count: int) -> None:
        """Persist the JSONL line watermark for this transcript."""
        data = self._load_buf_data()
        data["transcript_path"] = transcript_path
        data["transcript_watermark"] = line_count
        self._save_buf_data(data)

    def close(self):
        """Close the database connection."""
        self.conn.close()

    @staticmethod
    def _row_to_node(row) -> dict:
        """Convert a sqlite3.Row to a node dict with parsed JSON fields."""
        d = dict(row)
        d["tags"] = json.loads(d["tags"])
        d["supersedes"] = json.loads(d["supersedes"])
        # fact_hash is an internal implementation detail; strip it from the public dict
        d.pop("fact_hash", None)
        return d
