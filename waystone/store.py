"""SQLite-backed graph store for context nodes and edges."""

import hashlib
import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


def prewarm_sqlite_vec() -> bool:
    """Force the one-time cold load of the sqlite-vec native extension.

    On a fresh install — notably Windows — the *first* ``import sqlite_vec`` +
    ``sqlite_vec.load()`` can stall for a long time (the OS scanning/gating the
    unsigned native binary on first load). After it's loaded once the cost is
    gone for good. Calling this at install time (``waystone configure``) or in a
    background thread at MCP-server startup moves that one-time cost off the
    user's first semantic query. Best-effort and idempotent — returns True on
    success, False if sqlite-vec isn't available.
    """
    try:
        import sqlite_vec
        conn = sqlite3.connect(":memory:")
        try:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
        finally:
            conn.close()
        return True
    except Exception as e:  # noqa: BLE001 — never let warming break the caller
        log.debug("prewarm_sqlite_vec failed (%s: %s)", type(e).__name__, e)
        return False


# Increment when any schema change is made (new table, column, index, trigger).
# init_db() checks PRAGMA user_version and skips all DDL if already current.
SCHEMA_VERSION = 19


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


def compute_fact_hash(fact: str) -> str:
    """Public content-address utility: return the canonical hash for a fact string."""
    return _fact_hash(fact)


_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def _strip_surrogates(s):
    """Replace lone surrogate code points (e.g. '\\udc8f') with U+FFFD.

    LLM extraction output occasionally contains lone surrogates; SQLite/UTF-8
    cannot encode them, so the INSERT in add_node raises UnicodeEncodeError and
    crashes the whole extraction/onboard run. Sanitizing at the storage gate
    protects every path that writes a node.
    """
    if not isinstance(s, str):
        return s
    return _SURROGATE_RE.sub("�", s) if _SURROGATE_RE.search(s) else s


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

    def __init__(self, db_path: str | Path, dedup_threshold: float = 0.95, vec_enabled: bool = True):
        self.db_path = Path(db_path)
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError) as e:
            raise RuntimeError(
                f"Cannot create project database at {self.db_path}: {e}. Check directory permissions."
            ) from e
        try:
            self.conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        except (PermissionError, OSError) as e:
            raise RuntimeError(
                f"Cannot open database at {self.db_path}: {e}. Check file and directory permissions."
            ) from e
        self.conn.row_factory = sqlite3.Row
        # Performance tuning — applied before any schema work
        self.conn.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;
            PRAGMA cache_size=-65536;
            PRAGMA temp_store=MEMORY;
            PRAGMA mmap_size=268435456;
        """)
        # Skip extension load when semantic search is disabled — saves ~49ms per open.
        self._vec_available = self._load_sqlite_vec() if vec_enabled else False
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
            log.warning("sqlite-vec not available (%s: %s) — semantic search disabled", type(e).__name__, e)
            return False

    def init_db(self):
        """Create tables if they don't exist."""
        # Fast path: if schema is already at SCHEMA_VERSION, skip all DDL.
        # PRAGMA user_version is a free integer stored in the DB header —
        # reading it costs ~0.1ms vs ~300ms for re-running all CREATE IF NOT EXISTS.
        current_version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if current_version >= SCHEMA_VERSION:
            return

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
                supersedes TEXT NOT NULL DEFAULT '[]',
                domain TEXT
            );

            CREATE TABLE IF NOT EXISTS edges (
                from_id TEXT NOT NULL,
                to_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                PRIMARY KEY (from_id, to_id, relation)
            );

            CREATE INDEX IF NOT EXISTS idx_nodes_type   ON nodes(type);
            CREATE INDEX IF NOT EXISTS idx_nodes_source ON nodes(source_transcript);
            CREATE INDEX IF NOT EXISTS idx_edges_from   ON edges(from_id);
            CREATE INDEX IF NOT EXISTS idx_edges_to     ON edges(to_id);
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
        # BFS covering index for reverse direction (to_id → from_id, relation, weight).
        # Created after the weight migration so all columns are guaranteed to exist.
        # Forward direction is already covered by the PK (from_id, to_id, relation);
        # this index makes `WHERE to_id IN (...)` fully covering — no table row fetch.
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_edges_to_covering "
            "ON edges(to_id, from_id, relation, weight)"
        )
        self.conn.commit()

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

        # Migration: add domain column for process nodes
        try:
            self.conn.execute("ALTER TABLE nodes ADD COLUMN domain TEXT")
            self.conn.commit()
            log.info("Migrated nodes table: added domain column")
        except sqlite3.OperationalError:
            pass  # Column already exists

        # Migration: bi-temporal schema — valid_to + is_active
        #
        # valid_to (TEXT, ISO 8601): when this fact stopped being true in the
        #   world (NULL = still current).  Set by merge_extraction() when a
        #   superseding node is ingested.  Semantically the end of the "valid
        #   time" window; the start is recorded in occurred_at / created_at.
        #
        # is_active (INTEGER, 0|1): cached `valid_to IS NULL` flag.  Maintained
        #   by merge_extraction() and the backfill below.  Indexed for O(log N)
        #   superseded_pruning without edge traversal.
        for col_def, col_name in [
            ("ALTER TABLE nodes ADD COLUMN valid_to TEXT", "valid_to"),
            ("ALTER TABLE nodes ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1", "is_active"),
        ]:
            try:
                self.conn.execute(col_def)
                self.conn.commit()
                log.info("Migrated nodes table: added %s column", col_name)
            except sqlite3.OperationalError:
                pass  # Column already exists
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_nodes_is_active ON nodes(is_active)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_nodes_valid_range ON nodes(occurred_at, valid_to)"
        )
        self.conn.commit()
        # Backfill: mark nodes that appear in any supersedes[] array as inactive.
        # Uses the superseding node's created_at as the expiry time.
        # Only updates rows where is_active is still 1 (unset) to be idempotent.
        self.conn.execute("""
            UPDATE nodes
            SET is_active = 0,
                valid_to  = COALESCE(valid_to, (
                    SELECT MIN(n2.created_at)
                    FROM nodes n2, json_each(n2.supersedes) je
                    WHERE je.value = nodes.id
                ))
            WHERE id IN (
                SELECT DISTINCT je.value
                FROM nodes n, json_each(n.supersedes) je
                WHERE json_type(n.supersedes) = 'array'
            )
            AND is_active = 1
        """)
        self.conn.commit()

        # EHRAG hyperedge index — pre-computed semantic clusters for sibling injection.
        # Created here (migration-safe) so existing DBs upgrade on first open.
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS hyperedge_members (
                hyperedge_id TEXT NOT NULL,
                node_id      TEXT NOT NULL,
                PRIMARY KEY (hyperedge_id, node_id)
            );
            CREATE INDEX IF NOT EXISTS idx_hm_node ON hyperedge_members(node_id);
        """)
        self.conn.commit()

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

        # WorldDB-style reactive edge programs: typed edge handlers as SQLite triggers.
        #
        # edge_supersedes_expire: fires atomically when a supersedes edge is inserted,
        # closing the validity window of the superseded node without requiring Python to
        # chase the expiry after every add_edge() call.
        #
        # node_delete_cascade: when any node is deleted, auto-clean node_tags, edges,
        # and hyperedge_members.  Fixes a latent bug where vacuum_unused() and prune_nodes()
        # left stale node_tags rows (those callers delete edges then nodes but never node_tags).
        self.conn.executescript("""
            CREATE TRIGGER IF NOT EXISTS edge_supersedes_expire
            AFTER INSERT ON edges
            WHEN new.relation = 'supersedes'
            BEGIN
                UPDATE nodes
                SET valid_to  = COALESCE(valid_to,
                        (SELECT COALESCE(occurred_at, created_at) FROM nodes WHERE id = new.from_id),
                        datetime('now')),
                    is_active = 0
                WHERE id = new.to_id AND is_active = 1;
            END;

            CREATE TRIGGER IF NOT EXISTS node_delete_cascade
            AFTER DELETE ON nodes
            BEGIN
                DELETE FROM node_tags         WHERE node_id = old.id;
                DELETE FROM edges             WHERE from_id = old.id OR to_id = old.id;
                DELETE FROM hyperedge_members WHERE node_id = old.id;
            END;
        """)
        self.conn.commit()

        # Backfill FTS index for any nodes not yet indexed
        self._backfill_fts()

        # Create vec0 virtual table for semantic search (requires sqlite-vec loaded)
        if self._vec_available:
            from waystone.embedder import get_embedding_dim
            self.conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS node_embeddings USING vec0("
                f"node_id TEXT PRIMARY KEY, embedding float[{get_embedding_dim()}])"
            )
            self.conn.commit()

        # node_tags junction table — indexed tag lookup replacing O(N) LIKE scans.
        # Each (tag, node_id) pair gets its own row; the index on `tag` makes
        # tag-based entry node discovery O(log N) regardless of corpus size.
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS node_tags (
                tag     TEXT NOT NULL,
                node_id TEXT NOT NULL,
                PRIMARY KEY (tag, node_id)
            );
            CREATE INDEX IF NOT EXISTS idx_node_tags_tag  ON node_tags(tag);
            CREATE INDEX IF NOT EXISTS idx_node_tags_node ON node_tags(node_id);
        """)
        self.conn.commit()
        # Backfill node_tags for any nodes whose tags haven't been indexed yet.
        self._backfill_node_tags()

        # Migration: add on_query_rewrite column to edges for typed edge handler registry
        try:
            self.conn.execute("ALTER TABLE edges ADD COLUMN on_query_rewrite TEXT")
            self.conn.commit()
            log.info("Migrated edges table: added on_query_rewrite column")
        except sqlite3.OperationalError:
            pass  # Column already exists

        # Migration: create edge_query_rules table for on_query_rewrite rule registry
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS edge_query_rules (
                rule_id   TEXT PRIMARY KEY,
                rule_type TEXT NOT NULL,
                params    TEXT
            )
        """)
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_edge_query_rules_type ON edge_query_rules(rule_type)"
        )
        self.conn.commit()

        # Raw sentence index: per-sentence transcript storage for semantic fallback.
        # Populated by ingestion when sentence_index.enabled=True in config.
        # Intentionally separated from the nodes/edges graph so it can be toggled
        # independently and does not affect existing retrieval paths.
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS raw_sentences (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                speaker      TEXT,
                session_id   TEXT,
                occurred_at  TEXT,
                sequence_num INTEGER NOT NULL,
                text         TEXT    NOT NULL
            )
        """)
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_raw_sentences_session_seq "
            "ON raw_sentences(session_id, sequence_num)"
        )
        self.conn.commit()
        if self._vec_available:
            from waystone.embedder import get_embedding_dim
            self.conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_raw_sentences USING vec0("
                f"sentence_id INTEGER PRIMARY KEY, embedding float[{get_embedding_dim()}])"
            )
            self.conn.commit()

        # Migration: world containers — named context namespaces for scoped retrieval
        try:
            self.conn.execute("ALTER TABLE nodes ADD COLUMN world_id TEXT REFERENCES nodes(id)")
            self.conn.commit()
            log.info("Migrated nodes table: added world_id column")
        except sqlite3.OperationalError:
            pass  # Column already exists
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_nodes_world_id ON nodes(world_id) WHERE world_id IS NOT NULL"
        )
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS worlds (
                world_id    TEXT PRIMARY KEY REFERENCES nodes(id) ON DELETE CASCADE,
                name        TEXT NOT NULL,
                description TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        self.conn.commit()

        # Stamp schema version so future opens skip all DDL above.
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.conn.commit()

    def add_node(self, node: dict) -> str:
        """Insert a node, deduplicating by fact text hash.

        If a node with the same normalized fact text already exists, the
        incoming node is merged into it: tags are unioned and confidence
        takes the maximum of the two values. Returns the canonical node ID
        (either the existing one or the newly inserted one).
        """
        # Strip lone surrogates the LLM may have emitted — SQLite can't encode
        # them and the INSERT below would raise UnicodeEncodeError, crashing the
        # whole run. Sanitize fact + the other stored text fields up front.
        node["fact"] = _strip_surrogates(node["fact"])
        if node.get("source_transcript"):
            node["source_transcript"] = _strip_surrogates(node["source_transcript"])
        tags = [_strip_surrogates(t) for t in _auto_tag_numerics(node["fact"], node.get("tags", []))]
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
            self._upsert_node_tags(existing_id, merged_tags)
            self.conn.commit()
            log.debug("Dedup: merged node %s into existing %s", node["id"], existing_id)
            return existing_id

        # Semantic dedup: check for paraphrase duplicates before inserting
        _new_blob: bytes | None = None
        if self._vec_available:
            from waystone import embedder as _embedder
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
                                self._upsert_node_tags(nid, merged_tags2)
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
                source_message_index, tags, created_at, occurred_at, supersedes, fact_hash, domain,
                valid_to, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                node.get("domain"),
                node.get("valid_to"),        # None = still current
                node.get("is_active", 1),    # default: active
            ),
        )
        self._upsert_node_tags(node["id"], tags)
        self.conn.commit()

        # Expire any nodes listed in supersedes[] — set valid_to + is_active=0
        supersedes_ids = node.get("supersedes", [])
        if supersedes_ids:
            expiry = node.get("occurred_at") or node.get("created_at") or datetime.now(timezone.utc).isoformat()
            for sid in supersedes_ids:
                self.conn.execute(
                    """UPDATE nodes
                       SET valid_to  = COALESCE(valid_to, ?),
                           is_active = 0
                       WHERE id = ? AND is_active = 1""",
                    (expiry, sid),
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
        self.conn.execute("DELETE FROM node_tags WHERE node_id = ?", (node_id,))
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

    # Default per-type half-lives (days) used for proactive staleness detection.
    # Mirrors the RoMem decay table in ARCHITECTURE.md. A node is a temporal-expiry
    # candidate only past half_life × expiry_factor (conservative). Types absent here
    # (e.g. session_summary) are never flagged by the temporal signal.
    _INVALIDATION_HALF_LIVES = {
        "transition": 14, "question": 30, "implementation": 60, "process": 90,
        "preference": 90, "resolved": 90, "decision": 180,
        "lesson_learned": 365, "constraint": 365,
    }

    def detect_stale_candidates(
        self,
        *,
        never_retrieved_days: int = 90,
        min_age_days: int = 30,
        max_confidence: float = 0.95,
        expiry_factor: float = 1.5,
        half_lives: dict | None = None,
    ) -> list[dict]:
        """Proactively find facts likely gone stale WITHOUT an explicit contradiction.

        Deterministic, no LLM. Two conservative signals over active, non-pinned nodes:
          - ``temporal_half_life_expired``: age > (per-type half-life × expiry_factor)
            and confidence < max_confidence.
          - ``never_retrieved_aged``: hit_count = 0, age > never_retrieved_days,
            confidence < 0.75.

        Returns candidate dicts ``{id, type, fact, reason, detail, score}`` — does NOT
        modify the graph (call ``deactivate_node`` to retire). Recall-preserving by
        design: pinned + high-confidence + recent nodes are exempt.
        """
        hl_map = half_lives or self._INVALIDATION_HALF_LIVES
        now = datetime.now(timezone.utc)
        rows = self.conn.execute(
            """SELECT id, type, fact, confidence, hit_count,
                      COALESCE(occurred_at, created_at)
               FROM nodes WHERE is_active = 1 AND pinned = 0"""
        ).fetchall()

        candidates: list[dict] = []
        for nid, ntype, fact, conf, hits, ts in rows:
            try:
                age_days = (now - datetime.fromisoformat(ts)).days
            except (ValueError, TypeError):
                continue
            if age_days < min_age_days:
                continue
            conf = conf if conf is not None else 0.5
            hl = hl_map.get(ntype)
            if hl and age_days > hl * expiry_factor and conf < max_confidence:
                candidates.append({
                    "id": nid, "type": ntype, "fact": fact,
                    "reason": "temporal_half_life_expired",
                    "detail": f"age={age_days}d > {hl}d×{expiry_factor:g}",
                    "score": 0.7,
                })
            elif (hits or 0) == 0 and age_days > never_retrieved_days and conf < 0.75:
                candidates.append({
                    "id": nid, "type": ntype, "fact": fact,
                    "reason": "never_retrieved_aged",
                    "detail": f"hit_count=0, age={age_days}d, conf={conf:.2f}",
                    "score": 0.6,
                })
        return candidates

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
        from waystone import embedder
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
        self._upsert_node_tags(keep_id, merged_tags)
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
        self.conn.execute("DELETE FROM node_tags WHERE node_id = ?", (drop_id,))
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
        self._upsert_node_tags(node_id, new_tags)
        self.conn.commit()
        return True

    def add_edge(self, from_id: str, to_id: str, relation: str):
        """Insert an edge, or increment its weight if it already exists.

        When relation == "supersedes", also closes the validity window of the
        superseded node (to_id): sets valid_to = occurred_at of the superseding
        node (from_id), falling back to its created_at, then now().  This keeps
        add_edge() consistent with merge_extraction() so callers don't have to
        remember to expire superseded nodes manually.

        For supersedes edges, also auto-wires the add_superseded_tags query rewrite rule.
        """
        self.conn.execute(
            """INSERT INTO edges (from_id, to_id, relation, weight)
               VALUES (?, ?, ?, 1.0)
               ON CONFLICT (from_id, to_id, relation) DO UPDATE SET weight = weight + 1.0""",
            (from_id, to_id, relation),
        )
        self.conn.commit()

        if relation == "supersedes":
            superseding = self.get_node(from_id)
            expiry = None
            if superseding:
                expiry = superseding.get("occurred_at") or superseding.get("created_at")
            expiry = expiry or datetime.now(timezone.utc).isoformat()
            self.conn.execute(
                """UPDATE nodes
                   SET valid_to  = COALESCE(valid_to, ?),
                       is_active = 0
                   WHERE id = ? AND is_active = 1""",
                (expiry, to_id),
            )
            self.conn.commit()

            # Auto-wire the add_superseded_tags rule if not already set.
            existing_rule = self.get_edge_query_rule(from_id, to_id)
            if not existing_rule:
                self.set_edge_query_rule(from_id, to_id, "add_superseded_tags")

    def get_node(self, node_id: str) -> dict | None:
        """Fetch a single node by ID."""
        row = self.conn.execute(
            "SELECT * FROM nodes WHERE id = ?", (node_id,)
        ).fetchone()
        return self._row_to_node(row) if row else None

    def get_node_by_hash(self, fact_hash: str) -> dict | None:
        """Content-addressed lookup: fetch a node by its normalized fact hash.

        Use compute_fact_hash(fact_text) to derive the hash from a raw fact string.
        Returns None if no node with that hash exists.
        """
        row = self.conn.execute(
            "SELECT * FROM nodes WHERE fact_hash = ? LIMIT 1", (fact_hash,)
        ).fetchone()
        return self._row_to_node(row) if row else None

    def get_all_nodes(self) -> list[dict]:
        """Fetch all nodes."""
        rows = self.conn.execute("SELECT * FROM nodes").fetchall()
        return [self._row_to_node(r) for r in rows]

    def get_active_nodes(self) -> list[dict]:
        """Fetch only currently active (non-superseded) nodes."""
        rows = self.conn.execute(
            "SELECT * FROM nodes WHERE is_active = 1"
        ).fetchall()
        return [self._row_to_node(r) for r in rows]

    def get_nodes_by_type(self, node_type: str) -> list[dict]:
        """Fetch all active nodes of a given type."""
        rows = self.conn.execute(
            "SELECT * FROM nodes WHERE type = ? AND is_active = 1",
            (node_type,),
        ).fetchall()
        return [self._row_to_node(r) for r in rows]

    def get_nodes_at_time(
        self,
        valid_at: str | None = None,
        transaction_at: str | None = None,
    ) -> list[dict]:
        """Bi-temporal point-in-time query.

        valid_at (ISO 8601): return facts whose valid window covers this
            moment — i.e. occurred_at <= valid_at AND (valid_to IS NULL OR
            valid_to > valid_at).  Approximates "what was true in the world
            at this point in time."

        transaction_at (ISO 8601): return only facts that were ingested on
            or before this timestamp — i.e. created_at <= transaction_at.
            Approximates "what did the system know at this point in time."

        Both filters stack; omit either to skip that dimension.
        """
        clauses: list[str] = []
        params: list[str] = []

        if valid_at:
            clauses.append(
                "(occurred_at IS NULL OR occurred_at <= ?)"
                " AND (valid_to IS NULL OR valid_to > ?)"
            )
            params.extend([valid_at, valid_at])

        if transaction_at:
            clauses.append("created_at <= ?")
            params.append(transaction_at)

        where = " AND ".join(clauses) if clauses else "1"
        rows = self.conn.execute(
            f"SELECT * FROM nodes WHERE {where} ORDER BY occurred_at, created_at",
            params,
        ).fetchall()
        return [self._row_to_node(r) for r in rows]

    def get_revision_history(self, node_id: str) -> list[dict]:
        """Return the belief-revision chain ending at node_id.

        Walks the supersedes[] arrays backwards to find the full lineage:
        [oldest_ancestor, ..., node_id].  Each entry is a full node dict.
        The returned list is ordered from oldest to newest.
        """
        visited: set[str] = set()
        chain: list[dict] = []

        def _walk(nid: str) -> None:
            if nid in visited:
                return
            visited.add(nid)
            node = self.get_node(nid)
            if not node:
                return
            for prior_id in node.get("supersedes", []):
                _walk(prior_id)
            chain.append(node)

        _walk(node_id)
        return chain  # oldest-first

    def get_recent_nodes(self, limit: int = 20) -> list[dict]:
        """Fetch most recent nodes."""
        rows = self.conn.execute(
            "SELECT * FROM nodes ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_node(r) for r in rows]

    def get_nodes_by_tags(self, tags: list[str]) -> list[dict]:
        """Find nodes whose tags overlap with the given list.

        Uses the node_tags junction index (O(log N) per tag) when available,
        falling back to the legacy LIKE scan for DBs that haven't been
        backfilled yet. Falls back further to fact-text search if no tag
        matches are found.
        """
        if not tags:
            return []

        # Fast path: node_tags index exists and is populated
        index_count = self.conn.execute("SELECT COUNT(*) FROM node_tags").fetchone()[0]
        if index_count > 0:
            lower_tags = [str(t).lower() for t in tags if t]
            if not lower_tags:
                return []
            _CHUNK = 900  # stay well under SQLite's 1000-variable limit
            seen_ids: set[str] = set()
            matched_ids: list[str] = []
            for i in range(0, len(lower_tags), _CHUNK):
                chunk = lower_tags[i : i + _CHUNK]
                placeholders = ",".join("?" * len(chunk))
                rows = self.conn.execute(
                    f"SELECT DISTINCT node_id FROM node_tags WHERE tag IN ({placeholders})",
                    chunk,
                ).fetchall()
                for row in rows:
                    nid = row[0]
                    if nid not in seen_ids:
                        seen_ids.add(nid)
                        matched_ids.append(nid)
            if matched_ids:
                return self.get_nodes_by_ids(matched_ids)

        else:
            # Legacy fallback for DBs with empty node_tags (pre-migration open)
            _CHUNK = 200
            seen_ids = set()
            result_rows: list = []
            for i in range(0, len(tags), _CHUNK):
                chunk = tags[i : i + _CHUNK]
                conditions = " OR ".join(["tags LIKE ?" for _ in chunk])
                params = [f'%{tag}%' for tag in chunk]
                rows = self.conn.execute(
                    f"SELECT * FROM nodes WHERE {conditions}", params
                ).fetchall()
                for row in rows:
                    if row[0] not in seen_ids:
                        seen_ids.add(row[0])
                        result_rows.append(row)
            if result_rows:
                return [self._row_to_node(r) for r in result_rows]

        # Fallback: search fact text when no tag matches found
        seen_ids = set()
        result_rows = []
        _CHUNK = 200
        for i in range(0, len(tags), _CHUNK):
            chunk = tags[i : i + _CHUNK]
            fact_conditions = " OR ".join(["fact LIKE ?" for _ in chunk])
            fact_params = [f"%{tag}%" for tag in chunk]
            rows = self.conn.execute(
                f"SELECT * FROM nodes WHERE {fact_conditions}", fact_params
            ).fetchall()
            for row in rows:
                if row[0] not in seen_ids:
                    seen_ids.add(row[0])
                    result_rows.append(row)
        return [self._row_to_node(r) for r in result_rows]

    def get_nodes_by_fact_text(self, keywords: list[str], limit: int = 150) -> list[dict]:
        """Find nodes whose fact text contains any of the given keywords.

        Active nodes only.  ``limit`` caps results per chunk to avoid full-table
        scans on generic keywords (e.g. "work", "week") that match thousands of rows.
        """
        if not keywords:
            return []
        _CHUNK = 200
        seen_ids: set[str] = set()
        result_rows: list = []
        for i in range(0, len(keywords), _CHUNK):
            chunk = keywords[i : i + _CHUNK]
            conditions = " OR ".join(["fact LIKE ?" for _ in chunk])
            params = [f"%{kw}%" for kw in chunk]
            rows = self.conn.execute(
                f"SELECT * FROM nodes WHERE is_active = 1 AND ({conditions}) LIMIT {limit}",
                params,
            ).fetchall()
            for row in rows:
                if row[0] not in seen_ids:
                    seen_ids.add(row[0])
                    result_rows.append(row)
        return [self._row_to_node(r) for r in result_rows]

    def has_hyperedges(self) -> bool:
        """Return True if the hyperedge_members table has at least one row."""
        row = self.conn.execute("SELECT 1 FROM hyperedge_members LIMIT 1").fetchone()
        return row is not None

    def get_hyperedge_siblings(self, node_ids: list[str]) -> list[str]:
        """Return node IDs that share a hyperedge with any of the given nodes (excluding inputs)."""
        if not node_ids:
            return []
        # Step 1: find all hyperedge IDs containing these nodes
        he_ids: list[str] = []
        for chunk in _chunk(node_ids, 900):
            placeholders = ",".join("?" * len(chunk))
            rows = self.conn.execute(
                f"SELECT DISTINCT hyperedge_id FROM hyperedge_members WHERE node_id IN ({placeholders})",
                chunk,
            ).fetchall()
            he_ids.extend(r[0] for r in rows)
        if not he_ids:
            return []
        # Step 2: fetch all members of those hyperedges, excluding the input nodes
        input_set = set(node_ids)
        siblings: list[str] = []
        for chunk in _chunk(he_ids, 900):
            placeholders = ",".join("?" * len(chunk))
            rows = self.conn.execute(
                f"SELECT DISTINCT node_id FROM hyperedge_members WHERE hyperedge_id IN ({placeholders})",
                chunk,
            ).fetchall()
            siblings.extend(r[0] for r in rows if r[0] not in input_set)
        return list(dict.fromkeys(siblings))  # dedupe, preserve first-seen order

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

    def create_world(
        self,
        name: str,
        tags: list[str] | None = None,
        parent_world_id: str | None = None,
        description: str | None = None,
    ) -> str:
        """Create a world container node and return its ID."""
        from uuid import uuid4
        world_node_id = f"n_{uuid4().hex[:8]}"
        world_tags = list(tags or [])
        name_tokens = [t.lower() for t in name.split()]
        world_tags.extend(name_tokens)
        world_tags = sorted(set(world_tags))

        node = {
            "id": world_node_id,
            "fact": name,
            "type": "world",
            "confidence": 1.0,
            "tags": world_tags,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.add_node(node)

        self.conn.execute(
            "INSERT INTO worlds (world_id, name, description, created_at) VALUES (?, ?, ?, ?)",
            (world_node_id, name, description, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

        if parent_world_id is not None:
            self.add_edge(parent_world_id, world_node_id, "contains")

        return world_node_id

    def add_node_to_world(self, node_id: str, world_id: str) -> None:
        """Assign a node to a world."""
        world_exists = self.conn.execute(
            "SELECT world_id FROM worlds WHERE world_id = ?", (world_id,)
        ).fetchone()
        if not world_exists:
            raise ValueError(f"World {world_id} does not exist")

        self.conn.execute(
            "UPDATE nodes SET world_id = ? WHERE id = ?",
            (world_id, node_id),
        )
        self.conn.commit()

    def get_world_nodes(self, world_id: str, recursive: bool = False) -> list[dict]:
        """Get all nodes in a world. If recursive=True, include child worlds."""
        nodes = []

        if recursive:
            world_ids = [world_id]
            visited = {world_id}
            while world_ids:
                current_id = world_ids.pop(0)
                rows = self.conn.execute(
                    "SELECT * FROM nodes WHERE world_id = ?", (current_id,)
                ).fetchall()
                nodes.extend(self._row_to_node(r) for r in rows)

                child_rows = self.conn.execute(
                    "SELECT to_id FROM edges WHERE from_id = ? AND relation = 'contains'",
                    (current_id,),
                ).fetchall()
                for row in child_rows:
                    child_id = row[0]
                    if child_id not in visited:
                        visited.add(child_id)
                        world_ids.append(child_id)
        else:
            rows = self.conn.execute(
                "SELECT * FROM nodes WHERE world_id = ?", (world_id,)
            ).fetchall()
            nodes = [self._row_to_node(r) for r in rows]

        return nodes

    def list_worlds(self, parent_world_id: str | None = None) -> list[dict]:
        """List all worlds, optionally filtered by parent."""
        if parent_world_id is None:
            rows = self.conn.execute(
                "SELECT * FROM worlds ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT w.* FROM worlds w
                   JOIN edges e ON e.to_id = w.world_id
                   WHERE e.from_id = ? AND e.relation = 'contains'
                   ORDER BY w.created_at DESC""",
                (parent_world_id,),
            ).fetchall()

        results = []
        for row in rows:
            world_id = row[0]
            node_count = self.conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE world_id = ?", (world_id,)
            ).fetchone()[0]
            results.append({
                "world_id": world_id,
                "name": row[1],
                "description": row[2],
                "created_at": row[3],
                "node_count": node_count,
            })
        return results

    def get_world(self, world_id: str) -> dict | None:
        """Get world metadata by ID."""
        row = self.conn.execute(
            "SELECT * FROM worlds WHERE world_id = ?", (world_id,)
        ).fetchone()
        if not row:
            return None
        node_count = self.conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE world_id = ?", (world_id,)
        ).fetchone()[0]
        return {
            "world_id": row[0],
            "name": row[1],
            "description": row[2],
            "created_at": row[3],
            "node_count": node_count,
        }

    def get_edges_for_nodes(self, node_ids: list[str]) -> list[dict]:
        """Fetch all edges where from_id or to_id is in node_ids, chunked for SQLite limits.

        Uses two separate IN queries instead of ``from_id IN (...) OR to_id IN (...)``.
        The OR form triggers SQLite's MULTI-INDEX OR path which iterates per element
        (``from_id=?`` × N) rather than doing a single range scan per index.  Splitting
        into two queries lets each index serve a proper range scan, then Python
        deduplicates by (from_id, to_id, relation).

        Index coverage:
        - Forward direction (``from_id IN``): served by PK ``(from_id, to_id, relation)``
          which is a clustered B-tree; ``weight`` fetch is one extra column read.
        - Reverse direction (``to_id IN``): served by ``idx_edges_to_covering``
          ``(to_id, from_id, relation, weight)`` — fully covering, no table fetch needed.
        """
        if not node_ids:
            return []
        seen_key: set[tuple] = set()
        results: list[dict] = []

        def _add_rows(rows) -> None:
            for r in rows:
                key = (r["from_id"], r["to_id"], r["relation"])
                if key not in seen_key:
                    seen_key.add(key)
                    results.append(dict(r))

        for chunk in _chunk(node_ids, 999):
            placeholders = ",".join("?" * len(chunk))
            _add_rows(self.conn.execute(
                f"SELECT * FROM edges WHERE from_id IN ({placeholders})", chunk
            ).fetchall())
            _add_rows(self.conn.execute(
                f"SELECT * FROM edges WHERE to_id IN ({placeholders})", chunk
            ).fetchall())

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

    def set_edge_query_rule(self, from_id: str, to_id: str, rule_type: str, params: dict | None = None) -> None:
        """Set a query rewrite rule on an edge.

        rule_id is auto-generated as f"{from_id}__{to_id}". Writes the rule to
        edge_query_rules and updates the edge's on_query_rewrite column.
        """
        rule_id = f"{from_id}__{to_id}"
        params_json = json.dumps(params) if params else None
        self.conn.execute(
            "INSERT OR REPLACE INTO edge_query_rules (rule_id, rule_type, params) VALUES (?, ?, ?)",
            (rule_id, rule_type, params_json),
        )
        self.conn.execute(
            "UPDATE edges SET on_query_rewrite = ? WHERE from_id = ? AND to_id = ?",
            (rule_id, from_id, to_id),
        )
        self.conn.commit()

    def get_edge_query_rule(self, from_id: str, to_id: str) -> dict | None:
        """Get the query rewrite rule for an edge, if any.

        Returns a dict with keys: rule_id, rule_type, params (parsed JSON or None).
        """
        rule_id = f"{from_id}__{to_id}"
        row = self.conn.execute(
            "SELECT rule_id, rule_type, params FROM edge_query_rules WHERE rule_id = ?",
            (rule_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "rule_id": row[0],
            "rule_type": row[1],
            "params": json.loads(row[2]) if row[2] else None,
        }

    def deactivate_node(self, node_id: str) -> None:
        """Soft-delete a node by setting is_active=0 and valid_to=now.

        The node is retained in the DB for audit / temporal queries.
        """
        expiry = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE nodes SET is_active = 0, valid_to = COALESCE(valid_to, ?) WHERE id = ?",
            (expiry, node_id),
        )
        self.conn.commit()

    def boost_confidence(self, node_id: str, delta: float = 0.02) -> None:
        """Increment a node's confidence score by delta, capped at 1.0."""
        self.conn.execute(
            "UPDATE nodes SET confidence = MIN(1.0, COALESCE(confidence, 0.5) + ?) WHERE id = ?",
            (delta, node_id),
        )
        self.conn.commit()

    def prune_superseded(self) -> int:
        """Deactivate any nodes that appear in a supersedes list but are still active.

        This is a safety net for nodes that slipped through merge_extraction's
        supersedes handling (e.g. nodes merged before a superseding node arrived).

        Returns the number of nodes pruned.
        """
        expiry = datetime.now(timezone.utc).isoformat()
        cursor = self.conn.execute(
            """
            UPDATE nodes
            SET is_active = 0,
                valid_to  = COALESCE(valid_to, ?)
            WHERE id IN (
                SELECT DISTINCT je.value
                FROM nodes n, json_each(n.supersedes) je
                WHERE json_type(n.supersedes) = 'array'
                  AND je.value != ''
            )
            AND is_active = 1
            """,
            (expiry,),
        )
        self.conn.commit()
        return cursor.rowcount

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
                # Bi-temporal: expire the superseded node (valid_to + is_active).
                # Prefer occurred_at of superseding node (when the change happened
                # in the world) over created_at (when we ingested it); fall back
                # to created_at if occurred_at is absent.
                superseding = self.get_node(from_id)
                expiry = None
                if superseding:
                    expiry = superseding.get("occurred_at") or superseding.get("created_at")
                expiry = expiry or datetime.now(timezone.utc).isoformat()
                self.conn.execute(
                    """UPDATE nodes
                       SET valid_to  = COALESCE(valid_to, ?),
                           is_active = 0
                       WHERE id = ? AND is_active = 1""",
                    (expiry, to_id),
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

    def get_sources(self) -> list[dict]:
        """Return all distinct source_transcript values with node counts.

        Useful for discovering which files/sessions have been ingested and
        how many nodes each contributed. Results are sorted by node count
        descending (most content first).

        Returns list of {"source": str, "count": int} dicts.
        """
        rows = self.conn.execute(
            """SELECT source_transcript, COUNT(*) AS cnt
               FROM nodes
               WHERE source_transcript IS NOT NULL AND source_transcript != ''
               GROUP BY source_transcript
               ORDER BY cnt DESC"""
        ).fetchall()
        return [{"source": r[0], "count": r[1]} for r in rows]

    def get_nodes_by_source_prefix(self, prefix: str) -> list[dict]:
        """Return all nodes whose source_transcript starts with prefix.

        Enables sub-project scoping: pass a folder path (e.g. 'health/')
        to retrieve only nodes from notes in that folder.
        """
        rows = self.conn.execute(
            "SELECT * FROM nodes WHERE source_transcript LIKE ?",
            (f"{prefix}%",),
        ).fetchall()
        return [self._row_to_node(r) for r in rows]

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
        from waystone import embedder
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

    def rebuild_embeddings(self) -> int:
        """Drop and rebuild the node_embeddings table at the current backend's dim.

        Required when switching embedding backends (e.g. local 384-dim →
        api 768-dim): the vec0 column dimension is fixed at creation, so the
        table must be recreated before re-embedding. Returns the number of
        nodes embedded. No-op if sqlite-vec is unavailable.
        """
        if not self._vec_available:
            return 0
        from waystone import embedder
        if not embedder.is_available():
            return 0
        self.conn.execute("DROP TABLE IF EXISTS node_embeddings")
        self.conn.execute(
            f"CREATE VIRTUAL TABLE node_embeddings USING vec0("
            f"node_id TEXT PRIMARY KEY, embedding float[{embedder.get_embedding_dim()}])"
        )
        self.conn.commit()
        return self.embed_missing_nodes()

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

    # ------------------------------------------------------------------
    # Raw sentence index (sentence_index feature)
    # ------------------------------------------------------------------

    def add_raw_sentence(
        self,
        speaker: str | None,
        session_id: str | None,
        occurred_at: str | None,
        sequence_num: int,
        text: str,
    ) -> int:
        """Insert a raw sentence and return its auto-assigned row ID."""
        cursor = self.conn.execute(
            "INSERT INTO raw_sentences "
            "(speaker, session_id, occurred_at, sequence_num, text) VALUES (?, ?, ?, ?, ?)",
            (speaker, session_id, occurred_at, sequence_num, text),
        )
        self.conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def embed_raw_sentences(self) -> int:
        """Embed all raw sentences that don't yet have a vector entry.

        Returns number embedded. No-op if sqlite-vec or sentence-transformers
        are unavailable.
        """
        if not self._vec_available:
            return 0
        from waystone import embedder
        if not embedder.is_available():
            return 0
        rows = self.conn.execute(
            "SELECT rs.id, rs.text FROM raw_sentences rs "
            "WHERE NOT EXISTS "
            "  (SELECT 1 FROM vec_raw_sentences vrs WHERE vrs.sentence_id = rs.id)"
        ).fetchall()
        if not rows:
            return 0
        ids = [r[0] for r in rows]
        texts = [r[1] for r in rows]
        blobs = embedder.embed_texts(texts)
        self.conn.executemany(
            "INSERT OR IGNORE INTO vec_raw_sentences(sentence_id, embedding) VALUES (?, ?)",
            list(zip(ids, blobs)),
        )
        self.conn.commit()
        log.debug("Embedded %d raw sentences", len(ids))
        return len(ids)

    def has_raw_sentences(self) -> bool:
        """Return True if the raw_sentences table contains any rows."""
        try:
            row = self.conn.execute("SELECT COUNT(*) FROM raw_sentences").fetchone()
            return bool(row and row[0] > 0)
        except Exception:
            return False

    def semantic_search_raw_sentences(
        self,
        query_blob: bytes,
        top_k: int = 10,
        earlier_neighbors: int = 2,
        later_neighbors: int = 2,
    ) -> list[dict]:
        """Find raw sentences semantically similar to query_blob.

        Returns list of dicts with keys: id, speaker, session_id, occurred_at,
        sequence_num, text, context_before (list[str]), context_after (list[str]),
        distance.

        Falls back gracefully if sqlite-vec is unavailable or the table is empty.
        """
        if not self._vec_available:
            return []
        try:
            rows = self.conn.execute(
                "SELECT sentence_id, distance FROM vec_raw_sentences "
                "WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
                (query_blob, top_k),
            ).fetchall()
        except Exception:
            log.debug("vec_raw_sentences search failed", exc_info=True)
            return []

        results: list[dict] = []
        for row in rows:
            sid, distance = row[0], row[1]
            rs = self.conn.execute(
                "SELECT id, speaker, session_id, occurred_at, sequence_num, text "
                "FROM raw_sentences WHERE id = ?",
                (sid,),
            ).fetchone()
            if not rs:
                continue
            _, speaker, session_id, occurred_at, seq, text = rs

            before_rows = self.conn.execute(
                "SELECT text FROM raw_sentences "
                "WHERE session_id = ? AND sequence_num < ? "
                "ORDER BY sequence_num DESC LIMIT ?",
                (session_id, seq, earlier_neighbors),
            ).fetchall()
            after_rows = self.conn.execute(
                "SELECT text FROM raw_sentences "
                "WHERE session_id = ? AND sequence_num > ? "
                "ORDER BY sequence_num ASC LIMIT ?",
                (session_id, seq, later_neighbors),
            ).fetchall()

            results.append({
                "id": sid,
                "speaker": speaker,
                "session_id": session_id,
                "occurred_at": occurred_at,
                "sequence_num": seq,
                "text": text,
                "context_before": [r[0] for r in reversed(before_rows)],
                "context_after": [r[0] for r in after_rows],
                "distance": distance,
            })
        return results

    def _backfill_fts(self) -> None:
        """Populate FTS index for any existing nodes not yet covered."""
        # Fast pre-check: if FTS row count >= node count, nothing to do (O(1)).
        node_count, fts_count = self.conn.execute(
            "SELECT (SELECT COUNT(*) FROM nodes), (SELECT COUNT(*) FROM nodes_fts)"
        ).fetchone()
        if fts_count >= node_count:
            return
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

    def _backfill_node_tags(self) -> None:
        """Populate node_tags for any nodes not yet covered.

        Compares node IDs in `nodes` against those already in `node_tags` and
        inserts the missing (tag, node_id) pairs in chunks to avoid holding a
        large write lock on the DB during the one-time migration.
        """
        # Fast pre-check: if every node is already in node_tags, nothing to do.
        # DISTINCT node_id count vs node count — both are fast indexed reads (O(1)).
        node_count, indexed_count = self.conn.execute(
            "SELECT (SELECT COUNT(*) FROM nodes), (SELECT COUNT(DISTINCT node_id) FROM node_tags)"
        ).fetchone()
        if indexed_count >= node_count:
            return
        missing_nodes = self.conn.execute(
            """SELECT n.id, n.tags FROM nodes n
               WHERE NOT EXISTS (SELECT 1 FROM node_tags WHERE node_id = n.id)"""
        ).fetchall()
        if not missing_nodes:
            return
        _COMMIT_CHUNK = 2000  # nodes per commit batch — keeps write lock duration short
        total_pairs = 0
        batch: list[tuple[str, str]] = []
        seen_in_batch: set[tuple[str, str]] = set()
        for i, row in enumerate(missing_nodes):
            node_id = row[0]
            try:
                tags = json.loads(row[1]) if row[1] else []
            except (json.JSONDecodeError, TypeError):
                tags = []
            for tag in tags:
                if tag:
                    for pair in self._tag_pairs(tag, node_id):
                        if pair not in seen_in_batch:
                            seen_in_batch.add(pair)
                            batch.append(pair)
            if (i + 1) % _COMMIT_CHUNK == 0 and batch:
                self.conn.executemany(
                    "INSERT OR IGNORE INTO node_tags(tag, node_id) VALUES (?, ?)", batch
                )
                self.conn.commit()
                total_pairs += len(batch)
                batch = []
                seen_in_batch = set()
        if batch:
            self.conn.executemany(
                "INSERT OR IGNORE INTO node_tags(tag, node_id) VALUES (?, ?)", batch
            )
            self.conn.commit()
            total_pairs += len(batch)
        log.debug("node_tags backfill: indexed tags for %d nodes (%d pairs)", len(missing_nodes), total_pairs)

    @staticmethod
    def _tag_pairs(tag: str, node_id: str) -> list[tuple[str, str]]:
        """Return (tag, node_id) pairs for a single tag.

        For multi-word tags (e.g. "screen protector", "iPhone 13 Pro"), emits
        both the full lowercase tag AND each individual word that is 3+ characters
        and not a common stop word.  This ensures single-word query keywords like
        "screen" or "iphone" can find nodes tagged "screen protector" or "iPhone 13 Pro"
        via the exact-match node_tags fast path.
        """
        _TAG_STOP = frozenset({
            "the", "and", "for", "with", "from", "that", "this", "are",
            "was", "not", "but", "has", "had", "have", "its", "their",
        })
        tag_lower = str(tag).lower().strip()
        if not tag_lower:
            return []
        pairs: list[tuple[str, str]] = [(tag_lower, node_id)]
        words = tag_lower.split()
        if len(words) > 1:
            for word in words:
                w = word.strip(".,;:!?\"'()[]{}")
                if len(w) >= 3 and w not in _TAG_STOP:
                    pairs.append((w, node_id))
        return pairs

    def _upsert_node_tags(self, node_id: str, tags: list[str]) -> None:
        """Replace all tag entries for a node in the node_tags index.

        Deletes any existing rows for node_id then inserts the current set.
        Multi-word tags also emit word-split entries so single-word query keywords
        can find them via the exact-match fast path.
        Call after any operation that changes a node's tag list.
        """
        self.conn.execute("DELETE FROM node_tags WHERE node_id = ?", (node_id,))
        pairs: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for t in tags:
            if t:
                for pair in self._tag_pairs(t, node_id):
                    if pair not in seen:
                        seen.add(pair)
                        pairs.append(pair)
        if pairs:
            self.conn.executemany(
                "INSERT OR IGNORE INTO node_tags(tag, node_id) VALUES (?, ?)", pairs
            )

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
        from waystone import embedder
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
