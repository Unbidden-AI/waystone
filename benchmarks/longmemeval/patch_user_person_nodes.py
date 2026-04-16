"""
patch_user_person_nodes.py — Inject a synthetic "User" person node into existing
LongMemEval checkpoint DBs, connect it to all user-tagged nodes, and write the
result to a new cache directory.

This is a retrieval-only fix for the SS-user gap: the existing extraction never
created a person node for the "User" speaker label, so person_anchoring had nothing
to anchor to. This script post-processes existing DBs without any LLM calls.

Usage:
    python3.13 -m benchmarks.longmemeval.patch_user_person_nodes \
        [--source engram_lme_gemini_s] \
        [--dest engram_lme_gemini_s_user_patched]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


CACHE_ROOT = Path.home() / ".engram" / "longmemeval_cache"

USER_NODE_ID = "n_user_anchor_00000000"
USER_NODE = {
    "id": USER_NODE_ID,
    "fact": "User is the main speaker in this conversation.",
    "type": "person",
    "confidence": 1.0,
    "source_transcript": None,
    "source_message_index": None,
    "tags": json.dumps(["user", "speaker", "main speaker"]),
    "created_at": datetime.now(timezone.utc).isoformat(),
    "supersedes": "[]",
    "fact_hash": hashlib.sha256(b"user is the main speaker in this conversation.").hexdigest(),
    "pinned": 0,
    "occurred_at": None,
    "hit_count": 0,
    "entry_hit_count": 0,
    "last_used_at": None,
}


def patch_db(src_path: Path, dst_path: Path) -> dict:
    """Copy src → dst, then inject User person node + edges to user-tagged nodes."""
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_path, dst_path)

    conn = sqlite3.connect(dst_path)
    conn.row_factory = sqlite3.Row

    # Skip if a person node already tagged "user" exists
    existing = conn.execute(
        "SELECT id FROM nodes WHERE type='person' AND tags LIKE '%\"user\"%'"
    ).fetchone()
    if existing:
        conn.close()
        return {"skipped": True, "reason": f"existing person node {existing['id']}"}

    # Find all user-tagged nodes (any tag containing the word "user")
    user_nodes = conn.execute(
        "SELECT id FROM nodes WHERE tags LIKE '%\"user\"%'"
    ).fetchall()
    user_node_ids = [r["id"] for r in user_nodes]

    # Insert the synthetic User person node
    conn.execute(
        """INSERT OR IGNORE INTO nodes
           (id, fact, type, confidence, source_transcript, source_message_index,
            tags, created_at, supersedes, fact_hash, pinned, occurred_at,
            hit_count, entry_hit_count, last_used_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            USER_NODE["id"],
            USER_NODE["fact"],
            USER_NODE["type"],
            USER_NODE["confidence"],
            USER_NODE["source_transcript"],
            USER_NODE["source_message_index"],
            USER_NODE["tags"],
            USER_NODE["created_at"],
            USER_NODE["supersedes"],
            USER_NODE["fact_hash"],
            USER_NODE["pinned"],
            USER_NODE["occurred_at"],
            USER_NODE["hit_count"],
            USER_NODE["entry_hit_count"],
            USER_NODE["last_used_at"],
        ),
    )

    # Also insert into FTS table
    conn.execute(
        "INSERT OR IGNORE INTO nodes_fts (node_id, fact) VALUES (?, ?)",
        (USER_NODE_ID, USER_NODE["fact"]),
    )

    # Add edges: User → each user-tagged node (relates_to)
    edges_added = 0
    for nid in user_node_ids:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO edges (from_id, to_id, relation, weight) VALUES (?, ?, ?, ?)",
                (USER_NODE_ID, nid, "relates_to", 1.0),
            )
            edges_added += 1
        except Exception:
            pass

    conn.commit()
    conn.close()
    return {"user_tagged_nodes": len(user_node_ids), "edges_added": edges_added}


def patch_cache_dir(source: str, dest: str) -> None:
    src_dir = CACHE_ROOT / source
    dst_dir = CACHE_ROOT / dest

    if not src_dir.exists():
        raise FileNotFoundError(f"Source cache dir not found: {src_dir}")

    print(f"Patching {src_dir} → {dst_dir}")
    dbs = list(src_dir.glob("**/*.db"))
    print(f"Found {len(dbs)} DBs")

    skipped = 0
    for i, db_path in enumerate(dbs, 1):
        rel = db_path.relative_to(src_dir)
        dst_path = dst_dir / rel
        result = patch_db(db_path, dst_path)
        if result.get("skipped"):
            skipped += 1
            status = f"skip ({result['reason']})"
        else:
            status = f"+1 person node, {result['edges_added']} edges → {result['user_tagged_nodes']} user-tagged nodes"
        if i % 50 == 0 or i <= 5:
            print(f"  [{i}/{len(dbs)}] {rel.name}: {status}")

    print(f"Done. {len(dbs)-skipped} patched, {skipped} skipped (already had person node).")
    print(f"New cache dir: {dst_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="engram_lme_gemini_s")
    parser.add_argument("--dest", default="engram_lme_gemini_s_user_patched")
    args = parser.parse_args()
    patch_cache_dir(args.source, args.dest)
