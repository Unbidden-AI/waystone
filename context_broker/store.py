"""SQLite-backed graph store for context nodes and edges."""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


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
        self.conn.commit()

    def add_node(self, node: dict):
        """Insert or replace a node."""
        self.conn.execute(
            """INSERT OR REPLACE INTO nodes
               (id, fact, type, confidence, source_transcript,
                source_message_index, tags, created_at, supersedes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                node["id"],
                node["fact"],
                node["type"],
                node.get("confidence", 0.5),
                node.get("source_transcript"),
                node.get("source_message_index"),
                json.dumps(node.get("tags", [])),
                node.get("created_at", datetime.now(timezone.utc).isoformat()),
                json.dumps(node.get("supersedes", [])),
            ),
        )
        self.conn.commit()

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
        """
        log.info("Merging %d nodes, %d edges into %s", len(nodes), len(edges), self.db_path.name)
        for node in nodes:
            self.add_node(node)
        for edge in edges:
            self.add_edge(edge["from_id"], edge["to_id"], edge["relation"])
            # If it's a supersedes edge, also record in the node's supersedes list
            if edge["relation"] == "supersedes":
                existing = self.get_node(edge["from_id"])
                if existing:
                    supersedes_list = existing.get("supersedes", [])
                    if edge["to_id"] not in supersedes_list:
                        supersedes_list.append(edge["to_id"])
                        self.conn.execute(
                            "UPDATE nodes SET supersedes = ? WHERE id = ?",
                            (json.dumps(supersedes_list), edge["from_id"]),
                        )
                        self.conn.commit()

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
        return d
