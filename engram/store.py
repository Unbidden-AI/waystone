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

    ISO date strings (YYYY-MM-DD) are excluded to prevent date tokens from
    polluting keyword BFS and degrading multi-hop entity retrieval.
    """
    # Match tokens containing at least one digit; include hyphens/slashes for
    # compound values like "15-minute" or "1000/min"
    raw = re.findall(r"[\w][\w/\-]*\d[\w/\-]*|(?<!\w)\d[\w/\-]*", fact.lower())
    # Exclude ISO date strings (YYYY-MM-DD) — they pollute BFS tag matching
    iso_date = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    raw = [t for t in raw if not iso_date.match(t)]
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


def _chunk(lst: list, size: int):
    """Yield successive chunks of `size` from `lst`."""
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


class GraphStore:
    """DAG storage using SQLite with nodes and edges tables."""

    def __init__(self, db_path: str | Path, dedup_threshold: float = 0.95):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._vec_available = self._load_sqlite_vec()
        self._dedup_threshold = dedup_threshold
        self.init_db()

    def _load_sqlite_vec(self) -> bool:
        """Attempt to load the sqlite-vec extension. Returns True if successful."""
        try:
            import sqlite_vec
            self.conn.enable_load_extension(True)
            sqlite_vec.load(self.conn)
            self.conn.enable_load_extension(False)
            log.debug("sqlite-vec extension loaded")
            return True
        except (ImportError, AttributeError, Exception) as e:
            log.debug("sqlite-vec not available (%s: %s) — semantic search disabled", type(e).__name__, e)
            return False

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
        # feedback table — added after initial release, migration-safe
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                node_id    TEXT PRIMARY KEY,
                rating     INTEGER NOT NULL CHECK (rating IN (-1, 1)),
                comment    TEXT    NOT NULL DEFAULT '',
                created_at TEXT    NOT NULL
            )
        """)
        # extraction_failures table — added for error tracking, migration-safe
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS extraction_failures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                source_transcript TEXT,
                model TEXT,
                error_type TEXT,
                raw_response TEXT,
                error_message TEXT
            )
        """)
        self.conn.commit()

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

        # Migration: add pinned column to nodes if it doesn't exist yet
        try:
            self.conn.execute("ALTER TABLE nodes ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
            self.conn.commit()
            log.info("Migrated nodes table: added pinned column")
        except sqlite3.OperationalError:
            pass  # Column already exists
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_nodes_pinned ON nodes(pinned)"
        )
        self.conn.commit()

        # Migration: add weight column to edges if it doesn't exist yet
        try:
            self.conn.execute("ALTER TABLE edges ADD COLUMN weight REAL DEFAULT 1.0")
            self.conn.commit()
            log.info("Migrated edges table: added weight column")
        except sqlite3.OperationalError:
            pass  # Column already exists

        # Migration: add occurred_at column (conversation date, for accurate recency decay)
        try:
            self.conn.execute("ALTER TABLE nodes ADD COLUMN occurred_at TEXT")
            self.conn.commit()
            log.info("Migrated nodes table: added occurred_at column")
        except sqlite3.OperationalError:
            pass  # Column already exists

        # Migration: add usage tracking columns
        for col_def in [
            "ALTER TABLE nodes ADD COLUMN hit_count INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE nodes ADD COLUMN entry_hit_count INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE nodes ADD COLUMN last_used_at TEXT",
        ]:
            try:
                self.conn.execute(col_def)
                self.conn.commit()
            except sqlite3.OperationalError:
                pass  # Column already exists

        # Create FTS5 virtual table for BM25 full-text search on fact text.
        # Self-contained (no content= link) so rowid alignment isn't required.
        # Triggers keep the FTS index in sync with the nodes table.
        self.conn.executescript("""
            CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
                node_id UNINDEXED,
                fact,
                tokenize='porter ascii'
            );
            CREATE TRIGGER IF NOT EXISTS nodes_fts_insert AFTER INSERT ON nodes BEGIN
                INSERT INTO nodes_fts(node_id, fact) VALUES (new.id, new.fact);
            END;
            CREATE TRIGGER IF NOT EXISTS nodes_fts_delete AFTER DELETE ON nodes BEGIN
                DELETE FROM nodes_fts WHERE node_id = old.id;
            END;
            CREATE TRIGGER IF NOT EXISTS nodes_fts_update AFTER UPDATE OF fact ON nodes BEGIN
                DELETE FROM nodes_fts WHERE node_id = old.id;
                INSERT INTO nodes_fts(node_id, fact) VALUES (new.id, new.fact);
            END;
        """)
        self.conn.commit()
        # Backfill FTS index for any nodes not yet indexed
        self._backfill_fts()

        # Create vec0 virtual table for semantic search (requires sqlite-vec loaded)
        if self._vec_available:
            from engram.embedder import EMBEDDING_DIM
            self.conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS node_embeddings USING vec0("
                f"node_id TEXT PRIMARY KEY, embedding float[{EMBEDDING_DIM}])"
            )
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

        # Semantic dedup: check for paraphrase duplicates before inserting
        _new_blob: bytes | None = None
        if self._vec_available:
            from engram import embedder as _embedder
            if _embedder.is_available():
                try:
                    _new_blob = _embedder.embed_text(node["fact"])
                    for nid in self.search_by_embedding(_new_blob, top_k=6):
                        nb = self.conn.execute(
                            "SELECT embedding FROM node_embeddings WHERE node_id = ?", (nid,)
                        ).fetchone()
                        if not nb:
                            continue
                        sim = _embedder.cosine_similarity(_new_blob, bytes(nb[0]))
                        if sim >= self._dedup_threshold:
                            existing_row2 = self.conn.execute(
                                "SELECT tags, confidence FROM nodes WHERE id = ?", (nid,)
                            ).fetchone()
                            if existing_row2:
                                merged_tags2 = sorted(set(json.loads(existing_row2[0])) | set(tags))
                                merged_conf2 = max(existing_row2[1], node.get("confidence", 0.5))
                                self.conn.execute(
                                    "UPDATE nodes SET tags = ?, confidence = ? WHERE id = ?",
                                    (json.dumps(merged_tags2), merged_conf2, nid),
                                )
                                self.conn.commit()
                                log.debug(
                                    "Semantic dedup at insert: merged into %s (sim=%.3f)", nid, sim
                                )
                                return nid
                except Exception:
                    log.debug("Semantic dedup check failed, inserting normally", exc_info=True)
                    _new_blob = None

        self.conn.execute(
            """INSERT OR REPLACE INTO nodes
               (id, fact, type, confidence, source_transcript,
                source_message_index, tags, created_at, occurred_at, supersedes, fact_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                node["id"],
                node["fact"],
                node["type"],
                node.get("confidence", 0.5),
                node.get("source_transcript"),
                node.get("source_message_index"),
                json.dumps(tags),
                node.get("created_at", datetime.now(timezone.utc).isoformat()),
                node.get("occurred_at"),
                json.dumps(node.get("supersedes", [])),
                fhash,
            ),
        )
        self.conn.commit()
        # Eagerly store the embedding so future add_node calls can find this node
        if _new_blob is not None:
            try:
                self.store_embeddings([(node["id"], _new_blob)])
            except Exception:
                log.debug("Failed to store embedding for new node %s", node["id"], exc_info=True)
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

    def record_hits(self, node_ids: list[str], entry_ids: set[str]) -> None:
        """Increment retrieval counters for a set of nodes.

        node_ids: all nodes returned by a retrieval (BFS results after strategies)
        entry_ids: subset that were direct BFS entry points (tag/semantic/fact-text matches)
        """
        if not node_ids:
            return
        now = datetime.now(timezone.utc).isoformat()
        self.conn.executemany(
            "UPDATE nodes SET hit_count = hit_count + 1, last_used_at = ? WHERE id = ?",
            [(now, nid) for nid in node_ids],
        )
        if entry_ids:
            self.conn.executemany(
                "UPDATE nodes SET entry_hit_count = entry_hit_count + 1 WHERE id = ?",
                [(nid,) for nid in entry_ids if nid in set(node_ids)],
            )
        self.conn.commit()

    def vacuum_unused(
        self,
        min_age_days: int = 90,
        max_confidence: float = 0.75,
        dry_run: bool = False,
    ) -> list[str]:
        """Delete nodes that have never been retrieved and are likely stale.

        Criteria (all must be true):
          - hit_count = 0  (never returned by any query)
          - last_used_at IS NULL
          - created_at older than min_age_days
          - confidence < max_confidence
          - not pinned

        Returns list of deleted node IDs (or would-delete IDs in dry_run).
        """
        cutoff = (
            datetime.now(timezone.utc).replace(tzinfo=None)
            - __import__("datetime").timedelta(days=min_age_days)
        ).isoformat()
        rows = self.conn.execute(
            """SELECT id FROM nodes
               WHERE hit_count = 0
                 AND last_used_at IS NULL
                 AND created_at < ?
                 AND confidence < ?
                 AND pinned = 0""",
            (cutoff, max_confidence),
        ).fetchall()
        ids = [r[0] for r in rows]
        if not dry_run and ids:
            self.conn.executemany(
                "DELETE FROM edges WHERE from_id = ? OR to_id = ?",
                [(nid, nid) for nid in ids],
            )
            self.conn.executemany("DELETE FROM nodes WHERE id = ?", [(nid,) for nid in ids])
            self.conn.commit()
        return ids

    def vacuum_orphaned_edges(self, dry_run: bool = False) -> int:
        """Delete edges whose from_id or to_id no longer exists in the nodes table.

        Returns the number of orphaned edges deleted (or would-delete count in dry_run).
        """
        rows = self.conn.execute(
            """SELECT COUNT(*) FROM edges
               WHERE from_id NOT IN (SELECT id FROM nodes)
                  OR to_id   NOT IN (SELECT id FROM nodes)"""
        ).fetchone()
        count = rows[0] if rows else 0
        if not dry_run and count:
            self.conn.execute(
                """DELETE FROM edges
                   WHERE from_id NOT IN (SELECT id FROM nodes)
                      OR to_id   NOT IN (SELECT id FROM nodes)"""
            )
            self.conn.commit()
        return count

    def find_semantic_duplicates(
        self, threshold: float = 0.92, top_k: int = 5, limit: int = 0
    ) -> list[tuple[str, str, float]]:
        """Find semantically similar node pairs above a cosine similarity threshold.

        Returns list of (keep_id, drop_id, similarity) where keep_id is the higher-confidence
        node of the pair. Each pair appears exactly once (deduplicated).

        No-op if sqlite-vec or sentence-transformers are unavailable.
        """
        if not self._vec_available:
            return []
        from engram import embedder
        if not embedder.is_available():
            return []

        sql = (
            "SELECT e.node_id, e.embedding, n.confidence "
            "FROM node_embeddings e JOIN nodes n ON e.node_id = n.id "
            "ORDER BY n.created_at DESC"
        )
        rows = self.conn.execute(sql if not limit else sql + f" LIMIT {limit}").fetchall()
        if len(rows) < 2:
            return []

        conf_map = {r[0]: r[2] for r in rows}
        seen_pairs: set[frozenset] = set()
        duplicates: list[tuple[str, str, float]] = []

        for node_id, blob, conf in rows:
            blob_bytes = bytes(blob)
            neighbor_ids = self.search_by_embedding(blob_bytes, top_k=top_k + 1)
            for nid in neighbor_ids:
                if nid == node_id:
                    continue
                pair_key = frozenset({node_id, nid})
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                neighbor_row = self.conn.execute(
                    "SELECT embedding FROM node_embeddings WHERE node_id = ?", (nid,)
                ).fetchone()
                if not neighbor_row:
                    continue
                sim = embedder.cosine_similarity(blob_bytes, bytes(neighbor_row[0]))
                if sim >= threshold:
                    if conf_map.get(node_id, 0) >= conf_map.get(nid, 0):
                        keep_id, drop_id = node_id, nid
                    else:
                        keep_id, drop_id = nid, node_id
                    duplicates.append((keep_id, drop_id, sim))

        return duplicates

    def merge_node_into(self, keep_id: str, drop_id: str) -> bool:
        """Merge drop_id into keep_id: union tags, max confidence, rewrite edges, delete drop_id.

        Returns True if both nodes existed and the merge was performed.
        """
        keep_row = self.conn.execute(
            "SELECT tags, confidence FROM nodes WHERE id = ?", (keep_id,)
        ).fetchone()
        drop_row = self.conn.execute(
            "SELECT tags, confidence FROM nodes WHERE id = ?", (drop_id,)
        ).fetchone()
        if not keep_row or not drop_row:
            return False

        merged_tags = sorted(set(json.loads(keep_row[0])) | set(json.loads(drop_row[0])))
        merged_conf = max(keep_row[1], drop_row[1])
        self.conn.execute(
            "UPDATE nodes SET tags = ?, confidence = ? WHERE id = ?",
            (json.dumps(merged_tags), merged_conf, keep_id),
        )
        # Copy drop's outgoing edges to keep (skip self-loops and conflicts via OR IGNORE)
        self.conn.execute(
            "INSERT OR IGNORE INTO edges (from_id, to_id, relation) "
            "SELECT ?, to_id, relation FROM edges WHERE from_id = ? AND to_id != ?",
            (keep_id, drop_id, keep_id),
        )
        # Copy drop's incoming edges to keep (skip self-loops and conflicts via OR IGNORE)
        self.conn.execute(
            "INSERT OR IGNORE INTO edges (from_id, to_id, relation) "
            "SELECT from_id, ?, relation FROM edges WHERE to_id = ? AND from_id != ?",
            (keep_id, drop_id, keep_id),
        )
        # Remove all edges that still reference drop_id
        self.conn.execute("DELETE FROM edges WHERE from_id = ? OR to_id = ?", (drop_id, drop_id))
        self.conn.execute("DELETE FROM nodes WHERE id = ?", (drop_id,))
        if self._vec_available:
            self.conn.execute("DELETE FROM node_embeddings WHERE node_id = ?", (drop_id,))
        self.conn.commit()
        log.debug("Semantic dedup: merged %s into %s", drop_id, keep_id)
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
        """Insert an edge, or increment its weight if it already exists."""
        self.conn.execute(
            """INSERT INTO edges (from_id, to_id, relation, weight)
               VALUES (?, ?, ?, 1.0)
               ON CONFLICT (from_id, to_id, relation) DO UPDATE SET weight = weight + 1.0""",
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
        """Fetch multiple nodes by ID, chunked to stay within SQLite variable limits."""
        if not node_ids:
            return []
        results = []
        for chunk in _chunk(node_ids, 900):
            placeholders = ",".join("?" * len(chunk))
            rows = self.conn.execute(
                f"SELECT * FROM nodes WHERE id IN ({placeholders})", chunk
            ).fetchall()
            results.extend(self._row_to_node(r) for r in rows)
        return results

    def get_pinned_embeddings(self) -> list[tuple[str, str, bytes]]:
        """Return (id, fact, embedding_blob) for all pinned nodes that have embeddings."""
        if not self._vec_available:
            return []
        rows = self.conn.execute(
            "SELECT n.id, n.fact, e.embedding "
            "FROM nodes n JOIN node_embeddings e ON n.id = e.node_id "
            "WHERE n.pinned = 1"
        ).fetchall()
        return [(r[0], r[1], bytes(r[2])) for r in rows]

    def get_pinned_nodes(self) -> list[dict]:
        """Return all pinned nodes, ordered by type then confidence descending."""
        rows = self.conn.execute(
            "SELECT * FROM nodes WHERE pinned = 1 ORDER BY type, confidence DESC"
        ).fetchall()
        return [self._row_to_node(r) for r in rows]

    def pin_node(self, node_id: str) -> bool:
        """Mark a node as pinned (always inject). Returns True if node existed."""
        row = self.conn.execute("SELECT id FROM nodes WHERE id = ?", (node_id,)).fetchone()
        if not row:
            return False
        self.conn.execute("UPDATE nodes SET pinned = 1 WHERE id = ?", (node_id,))
        self.conn.commit()
        return True

    def unpin_node(self, node_id: str) -> bool:
        """Remove pinned flag from a node. Returns True if node existed."""
        row = self.conn.execute("SELECT id FROM nodes WHERE id = ?", (node_id,)).fetchone()
        if not row:
            return False
        self.conn.execute("UPDATE nodes SET pinned = 0 WHERE id = ?", (node_id,))
        self.conn.commit()
        return True

    def unpin_by_source(self, source_label: str) -> int:
        """Remove pinned flag from all nodes with the given source_transcript label.

        Returns the number of nodes unpinned.
        """
        cursor = self.conn.execute(
            "UPDATE nodes SET pinned = 0 WHERE source_transcript = ? AND pinned = 1",
            (source_label,),
        )
        self.conn.commit()
        return cursor.rowcount

    def get_edges_for_nodes(self, node_ids: list[str]) -> list[dict]:
        """Fetch all edges where from_id or to_id is in node_ids, chunked for SQLite limits."""
        if not node_ids:
            return []
        results = []
        seen_rowids: set = set()
        for chunk in _chunk(node_ids, 499):  # doubled in query, so 499*2 < 999
            placeholders = ",".join("?" * len(chunk))
            rows = self.conn.execute(
                f"SELECT rowid, * FROM edges WHERE from_id IN ({placeholders}) OR to_id IN ({placeholders})",
                chunk + chunk,
            ).fetchall()
            for r in rows:
                if r[0] not in seen_rowids:
                    seen_rowids.add(r[0])
                    results.append(dict(r))
        # Strip the synthetic rowid key we added for dedup
        for d in results:
            d.pop("rowid", None)
        return results

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

    def merge_extraction(self, nodes: list[dict], edges: list[dict]) -> dict[str, str]:
        """Merge extracted nodes and edges into the graph.

        Handles supersedes: when a new node supersedes existing ones,
        those are recorded and the new node is inserted.

        Deduplication: nodes whose normalized fact text matches an existing
        node are merged into that node (tags unioned, confidence max). The
        id_map tracks new_id → canonical_id so edges are rewritten to point
        at canonical nodes.

        Returns id_map so callers can resolve canonical IDs for post-merge
        work (e.g. embedding new nodes with their final IDs).
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
        return id_map

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
        all_tag_rows = []
        for chunk in _chunk(node_ids, 900):
            placeholders = ",".join("?" * len(chunk))
            all_tag_rows.extend(
                self.conn.execute(
                    f"SELECT id, tags FROM nodes WHERE id IN ({placeholders})", chunk
                ).fetchall()
            )
        tags_by_id: dict[str, set] = {row[0]: set(json.loads(row[1])) for row in all_tag_rows}

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

    def infer_involves_edges(
        self,
        weight: float = 1.0,
        name_only: bool = False,
    ) -> int:
        """Post-hoc inference: add `involves` edges from fact nodes to person anchors.

        Scans all person-type nodes and all non-person nodes. For each person,
        any fact node whose tags contain at least one matching tag gets an
        `involves` edge added if one doesn't already exist.

        Args:
            weight: Edge weight for inferred edges (< 1.0 to deprioritize vs
                    extractor-native edges that start at 1.0).
            name_only: When True, only match on the person's primary name (first
                       alphabetic word in their fact text), not all tags. Reduces
                       false positives from shared relationship/attribute tags.

        Returns the number of new edges added.
        """
        # Load all person-type anchor nodes
        person_rows = self.conn.execute(
            "SELECT id, fact, tags FROM nodes WHERE type = 'person'"
        ).fetchall()
        if not person_rows:
            return 0

        # Build inverted index: tag -> [person_ids]
        tag_to_persons: dict[str, list[str]] = {}
        for row in person_rows:
            pid, fact, tags_json = row[0], row[1], row[2]
            if name_only:
                # Extract the primary name: first alphabetic word of the fact text
                name = next(
                    (w.lower() for w in re.split(r"\W+", fact) if w.isalpha() and len(w) >= 2),
                    None,
                )
                match_tags = [name] if name else []
            else:
                match_tags = [t.lower() for t in json.loads(tags_json)]
            for tag in match_tags:
                tag_to_persons.setdefault(tag, []).append(pid)

        if not tag_to_persons:
            return 0

        # Load existing involves edges to avoid duplicates
        existing = set(
            (r[0], r[1])
            for r in self.conn.execute(
                "SELECT from_id, to_id FROM edges WHERE relation = 'involves'"
            ).fetchall()
        )

        # Scan non-person nodes; add missing involves edges
        non_person_rows = self.conn.execute(
            "SELECT id, tags FROM nodes WHERE type != 'person'"
        ).fetchall()

        new_edges: list[tuple[str, str]] = []
        for row in non_person_rows:
            nid = row[0]
            node_tag_set = {t.lower() for t in json.loads(row[1])}
            seen_persons: set[str] = set()
            for tag in node_tag_set:
                for pid in tag_to_persons.get(tag, []):
                    if pid not in seen_persons and (nid, pid) not in existing:
                        new_edges.append((nid, pid))
                        existing.add((nid, pid))
                        seen_persons.add(pid)

        for from_id, to_id in new_edges:
            self.conn.execute(
                """INSERT INTO edges (from_id, to_id, relation, weight)
                   VALUES (?, ?, 'involves', ?)
                   ON CONFLICT (from_id, to_id, relation) DO NOTHING""",
                (from_id, to_id, weight),
            )
        self.conn.commit()
        log.info("infer_involves_edges: added %d new involves edges (weight=%.2f, name_only=%s)",
                 len(new_edges), weight, name_only)
        return len(new_edges)

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
        for chunk in _chunk(ids, 900):
            placeholders = ",".join("?" * len(chunk))
            self.conn.execute(f"DELETE FROM edges WHERE from_id IN ({placeholders})", chunk)
            self.conn.execute(f"DELETE FROM edges WHERE to_id IN ({placeholders})", chunk)
            self.conn.execute(f"DELETE FROM nodes WHERE id IN ({placeholders})", chunk)
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
            for chunk in _chunk(ids, 900):
                placeholders = ",".join("?" * len(chunk))
                self.conn.execute(f"DELETE FROM edges WHERE from_id IN ({placeholders})", chunk)
                self.conn.execute(f"DELETE FROM edges WHERE to_id IN ({placeholders})", chunk)
                self.conn.execute(f"DELETE FROM nodes WHERE id IN ({placeholders})", chunk)
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

    # ------------------------------------------------------------------
    # Feedback / labeling
    # ------------------------------------------------------------------

    def add_feedback(self, node_id: str, rating: int, comment: str = "") -> bool:
        """Record a thumbs-up (1) or thumbs-down (-1) rating for a node.

        Overwrites any existing rating. Returns False if the node doesn't exist.
        """
        if rating not in (1, -1):
            raise ValueError("rating must be 1 (up) or -1 (down)")
        if not self.get_node(node_id):
            return False
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO feedback (node_id, rating, comment, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(node_id) DO UPDATE SET rating=excluded.rating,
                   comment=excluded.comment, created_at=excluded.created_at""",
            (node_id, rating, comment, now),
        )
        self.conn.commit()
        return True

    def get_feedback(self, node_id: str) -> dict | None:
        """Return the feedback row for a node, or None if unrated."""
        row = self.conn.execute(
            "SELECT * FROM feedback WHERE node_id = ?", (node_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_unrated_nodes(self, limit: int = 50) -> list[dict]:
        """Return nodes that have no feedback rating yet, newest first."""
        rows = self.conn.execute(
            """SELECT n.* FROM nodes n
               LEFT JOIN feedback f ON n.id = f.node_id
               WHERE f.node_id IS NULL
               ORDER BY n.created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [self._row_to_node(r) for r in rows]

    def get_rated_nodes(self, rating: int | None = None) -> list[dict]:
        """Return all rated nodes, optionally filtered by rating (1 or -1)."""
        if rating is not None and rating not in (1, -1):
            raise ValueError("rating must be 1, -1, or None")
        if rating is None:
            rows = self.conn.execute(
                """SELECT n.*, f.rating, f.comment, f.created_at as rated_at
                   FROM nodes n JOIN feedback f ON n.id = f.node_id
                   ORDER BY f.created_at DESC"""
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT n.*, f.rating, f.comment, f.created_at as rated_at
                   FROM nodes n JOIN feedback f ON n.id = f.node_id
                   WHERE f.rating = ?
                   ORDER BY f.created_at DESC""",
                (rating,),
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["tags"] = json.loads(d["tags"])
            d["supersedes"] = json.loads(d["supersedes"])
            d.pop("fact_hash", None)
            result.append(d)
        return result

    def get_feedback_stats(self) -> dict:
        """Return counts: total nodes, rated, thumbs_up, thumbs_down."""
        total = self.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        rated = self.conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        up = self.conn.execute(
            "SELECT COUNT(*) FROM feedback WHERE rating = 1"
        ).fetchone()[0]
        down = self.conn.execute(
            "SELECT COUNT(*) FROM feedback WHERE rating = -1"
        ).fetchone()[0]
        return {"total": total, "rated": rated, "unrated": total - rated, "thumbs_up": up, "thumbs_down": down}

    # ------------------------------------------------------------------
    # Extraction failure logging
    # ------------------------------------------------------------------

    def log_extraction_failure(
        self,
        error_type: str,
        error_message: str,
        raw_response: str = "",
        source_transcript: str = "",
        model: str = "",
    ) -> None:
        """Log an extraction failure to the database.

        Args:
            error_type: Category of failure ('json_parse', 'schema', 'api', 'timeout', 'chunk')
            error_message: Human-readable error description
            raw_response: LLM response (truncated to 2000 chars if longer)
            source_transcript: Name/path of the source transcript
            model: Model name/ID used for extraction
        """
        truncated_response = raw_response[:2000] if raw_response else ""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO extraction_failures
               (created_at, source_transcript, model, error_type, raw_response, error_message)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (now, source_transcript, model, error_type, truncated_response, error_message),
        )
        self.conn.commit()
        log.warning(
            "Logged extraction failure: error_type=%s source=%s model=%s",
            error_type, source_transcript, model
        )

    def get_extraction_failures(self, limit: int = 50) -> list[dict]:
        """Retrieve recent extraction failures.

        Args:
            limit: Maximum number of failures to return (default: 50)

        Returns:
            List of dicts with keys: id, created_at, source_transcript, model,
            error_type, raw_response, error_message
        """
        rows = self.conn.execute(
            """SELECT id, created_at, source_transcript, model, error_type,
                      raw_response, error_message
               FROM extraction_failures
               ORDER BY created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Semantic search (sqlite-vec)
    # ------------------------------------------------------------------

    def embed_missing_nodes(self) -> int:
        """Embed all nodes that don't yet have an entry in node_embeddings.

        Returns the number of nodes embedded. No-op if sqlite-vec or
        sentence-transformers are unavailable.
        """
        if not self._vec_available:
            return 0
        from engram import embedder
        if not embedder.is_available():
            return 0
        rows = self.conn.execute(
            """SELECT n.id, n.fact FROM nodes n
               LEFT JOIN node_embeddings e ON n.id = e.node_id
               WHERE e.node_id IS NULL"""
        ).fetchall()
        if not rows:
            return 0
        ids = [r[0] for r in rows]
        facts = [r[1] for r in rows]
        blobs = embedder.embed_texts(facts)
        self.store_embeddings(list(zip(ids, blobs)))
        log.debug("Embedded %d new nodes", len(ids))
        return len(ids)

    def store_embeddings(self, pairs: list[tuple[str, bytes]]) -> None:
        """Upsert (node_id, embedding_blob) pairs into node_embeddings.

        No-op if sqlite-vec is not available.
        """
        if not self._vec_available or not pairs:
            return
        self.conn.executemany(
            "INSERT OR REPLACE INTO node_embeddings(node_id, embedding) VALUES (?, ?)",
            pairs,
        )
        self.conn.commit()

    def _backfill_fts(self) -> None:
        """Populate FTS index for any existing nodes not yet covered."""
        missing = self.conn.execute(
            """SELECT n.id, n.fact FROM nodes n
               WHERE NOT EXISTS (SELECT 1 FROM nodes_fts WHERE node_id = n.id)"""
        ).fetchall()
        if missing:
            self.conn.executemany(
                "INSERT INTO nodes_fts(node_id, fact) VALUES (?, ?)",
                [(r[0], r[1]) for r in missing],
            )
            self.conn.commit()
            log.debug("FTS backfill: indexed %d nodes", len(missing))

    def search_by_fts(self, query: str, top_k: int = 50) -> list[tuple[str, float]]:
        """BM25 full-text search on node fact text via FTS5.

        Returns list of (node_id, bm25_score) tuples, ordered by relevance
        (most relevant first). BM25 scores from FTS5 are negative; we negate
        them so that higher = more relevant.

        Returns an empty list if no matches or FTS table is unavailable.
        """
        if not query.strip():
            return []
        try:
            rows = self.conn.execute(
                """SELECT node_id, -bm25(nodes_fts) AS score
                   FROM nodes_fts
                   WHERE nodes_fts MATCH ?
                   ORDER BY score DESC
                   LIMIT ?""",
                (query, top_k),
            ).fetchall()
            return [(r[0], float(r[1])) for r in rows]
        except Exception as e:
            log.debug("FTS search failed: %s", e)
            return []

    def search_by_embedding(self, query_blob: bytes, top_k: int = 10) -> list[str]:
        """Return up to top_k node IDs ranked by cosine similarity to query_blob.

        Returns an empty list if sqlite-vec is not available or if the
        node_embeddings table is empty.
        """
        if not self._vec_available:
            return []
        rows = self.conn.execute(
            """SELECT node_id, distance
               FROM node_embeddings
               WHERE embedding MATCH ?
               ORDER BY distance
               LIMIT ?""",
            (query_blob, top_k),
        ).fetchall()
        return [r[0] for r in rows]

    def dedup_nodes_brute_force(
        self,
        threshold: float = 0.90,
        batch_size: int = 512,
    ) -> int:
        """Post-ingest brute-force semantic deduplication.

        When sqlite-vec is unavailable (e.g. Python 3.14 on macOS), the
        per-insert semantic dedup block is skipped and near-duplicate
        paraphrases accumulate as separate nodes.  This method batch-embeds
        all nodes, computes pairwise cosine similarities, clusters near-
        duplicates via union-find, and merges each cluster into a single
        canonical node (earliest created_at wins).

        Tags are unioned across all cluster members; confidence takes the
        maximum.  Edges from/to merged nodes are reassigned to the canonical
        node before the duplicates are deleted.

        Returns the number of nodes removed.
        """
        from engram import embedder
        if not embedder.is_available():
            return 0

        rows = self.conn.execute(
            "SELECT id, fact, tags, confidence, created_at FROM nodes ORDER BY created_at"
        ).fetchall()
        if len(rows) < 2:
            return 0

        ids = [r[0] for r in rows]
        facts = [r[1] for r in rows]
        tags_list = [json.loads(r[2]) for r in rows]
        confs = [r[3] for r in rows]

        blobs = embedder.embed_texts(facts)

        # Build similarity pairs using numpy (required by sentence-transformers anyway)
        dup_pairs: list[tuple[int, int]] = []
        try:
            import struct
            import numpy as np
            n = len(ids)
            dim = embedder.EMBEDDING_DIM
            vecs = np.array(
                [struct.unpack(f"{dim}f", b) for b in blobs], dtype=np.float32
            )
            norms = np.linalg.norm(vecs, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1e-9, norms)
            normalized = vecs / norms
            # Process in blocks to bound peak memory
            for start in range(0, n, batch_size):
                end = min(start + batch_size, n)
                block = normalized[start:end] @ normalized.T  # (block, n)
                ri_arr, ci_arr = np.where(block >= threshold)
                for ri, ci in zip(ri_arr.tolist(), ci_arr.tolist()):
                    i, j = start + int(ri), int(ci)
                    if i < j:
                        dup_pairs.append((i, j))
        except ImportError:
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    if embedder.cosine_similarity(blobs[i], blobs[j]) >= threshold:
                        dup_pairs.append((i, j))

        if not dup_pairs:
            return 0

        # Union-Find: cluster transitively similar nodes
        parent = list(range(len(ids)))

        def _find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for i, j in dup_pairs:
            pi, pj = _find(i), _find(j)
            if pi != pj:
                parent[pj] = pi

        # Group clusters: canonical = lowest index (= earliest created_at)
        from collections import defaultdict
        clusters: dict[int, list[int]] = defaultdict(list)
        for idx in range(len(ids)):
            clusters[_find(idx)].append(idx)

        removed = 0
        for canonical_idx, members in clusters.items():
            if len(members) < 2:
                continue

            canonical_id = ids[canonical_idx]
            dup_ids = [ids[m] for m in members if m != canonical_idx]
            all_member_ids = [ids[m] for m in members]

            # Merge tags (union) and confidence (max)
            merged_tags: set[str] = set()
            merged_conf: float = 0.0
            for m in members:
                merged_tags |= set(tags_list[m])
                merged_conf = max(merged_conf, confs[m])
            self.conn.execute(
                "UPDATE nodes SET tags = ?, confidence = ? WHERE id = ?",
                (json.dumps(sorted(merged_tags)), merged_conf, canonical_id),
            )

            # Reassign edges before deleting duplicates.
            # Edges between cluster members are dropped (both endpoints gone).
            dup_ph = ",".join("?" * len(dup_ids))
            mem_ph = ",".join("?" * len(all_member_ids))
            # Outgoing: dup → external  becomes  canonical → external
            self.conn.execute(
                f"""INSERT OR IGNORE INTO edges (from_id, to_id, relation, weight)
                    SELECT ?, to_id, relation, COALESCE(weight, 1.0)
                    FROM edges
                    WHERE from_id IN ({dup_ph})
                    AND to_id NOT IN ({mem_ph})""",
                [canonical_id] + dup_ids + all_member_ids,
            )
            # Incoming: external → dup  becomes  external → canonical
            self.conn.execute(
                f"""INSERT OR IGNORE INTO edges (from_id, to_id, relation, weight)
                    SELECT from_id, ?, relation, COALESCE(weight, 1.0)
                    FROM edges
                    WHERE to_id IN ({dup_ph})
                    AND from_id NOT IN ({mem_ph})""",
                [canonical_id] + dup_ids + all_member_ids,
            )

            # Delete all edges involving any duplicate, then delete the nodes
            self.conn.execute(
                f"DELETE FROM edges WHERE from_id IN ({dup_ph}) OR to_id IN ({dup_ph})",
                dup_ids + dup_ids,
            )
            self.conn.execute(
                f"DELETE FROM nodes WHERE id IN ({dup_ph})",
                dup_ids,
            )
            removed += len(dup_ids)

        self.conn.commit()
        log.info(
            "Brute-force dedup: removed %d duplicate nodes (threshold=%.2f)",
            removed,
            threshold,
        )
        return removed

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
