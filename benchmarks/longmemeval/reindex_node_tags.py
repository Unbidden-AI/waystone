#!/usr/bin/env python3
"""
Re-index node_tags for LME checkpoint DBs to enable word-split tag matching.

The node_tags fast path in store.py uses exact matching, so multi-word tags
like "screen protector" or "iPhone 13 Pro" were never found by single-word
query keywords.  The fix in store.py now emits both the full tag AND each
word >= 3 chars when inserting.  This script re-indexes existing DBs.

Usage:
    python3.13 benchmarks/longmemeval/reindex_node_tags.py [--dir checkpoint_dir_name ...]

    # Re-index a specific checkpoint dir
    python3.13 benchmarks/longmemeval/reindex_node_tags.py --dir engram_lme_gemini_s_pref_v2

    # Re-index all LME checkpoint dirs
    python3.13 benchmarks/longmemeval/reindex_node_tags.py --all
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

CACHE_ROOT = Path.home() / ".engram" / "longmemeval_cache"

_TAG_STOP = frozenset({
    "the", "and", "for", "with", "from", "that", "this", "are",
    "was", "not", "but", "has", "had", "have", "its", "their",
})


def _tag_pairs(tag: str, node_id: str) -> list[tuple[str, str]]:
    """Return (tag, node_id) pairs — full tag + word splits for multi-word tags."""
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


def reindex_db(db_path: Path, verbose: bool = False) -> dict:
    """Clear and rebuild node_tags for a single DB."""
    conn = sqlite3.connect(db_path)
    try:
        before = conn.execute("SELECT COUNT(*) FROM node_tags").fetchone()[0]
        node_count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]

        # Clear existing index
        conn.execute("DELETE FROM node_tags")

        # Rebuild with word-split logic
        rows = conn.execute("SELECT id, tags FROM nodes").fetchall()
        batch: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for node_id, tags_json in rows:
            try:
                tags = json.loads(tags_json) if tags_json else []
            except (json.JSONDecodeError, TypeError):
                tags = []
            for tag in tags:
                if tag:
                    for pair in _tag_pairs(tag, node_id):
                        if pair not in seen:
                            seen.add(pair)
                            batch.append(pair)

        conn.executemany(
            "INSERT OR IGNORE INTO node_tags(tag, node_id) VALUES (?, ?)", batch
        )
        conn.commit()

        after = conn.execute("SELECT COUNT(*) FROM node_tags").fetchone()[0]
        if verbose:
            print(f"  {db_path.name}: {node_count} nodes, {before} → {after} tag entries (+{after - before})")
        return {"nodes": node_count, "before": before, "after": after}
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-index node_tags with word-split support")
    parser.add_argument("--dir", nargs="+", metavar="DIR",
                        help="Checkpoint dir names under ~/.engram/longmemeval_cache/")
    parser.add_argument("--all", action="store_true",
                        help="Re-index all DB dirs under ~/.engram/longmemeval_cache/")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if args.all:
        dirs = [d for d in CACHE_ROOT.iterdir() if d.is_dir()]
    elif args.dir:
        dirs = [CACHE_ROOT / d for d in args.dir]
    else:
        parser.error("Specify --dir DIR [...] or --all")

    t0 = time.monotonic()
    total_dbs = 0
    total_before = 0
    total_after = 0

    for checkpoint_dir in sorted(dirs):
        if not checkpoint_dir.exists():
            print(f"SKIP: {checkpoint_dir} not found")
            continue
        db_files = sorted(checkpoint_dir.glob("*.db"))
        if not db_files:
            continue
        print(f"{checkpoint_dir.name}: {len(db_files)} DBs")
        for db_path in db_files:
            result = reindex_db(db_path, verbose=args.verbose or len(db_files) <= 5)
            total_dbs += 1
            total_before += result["before"]
            total_after += result["after"]
        if not args.verbose:
            print(f"  done ({len(db_files)} DBs)")

    elapsed = time.monotonic() - t0
    print()
    print(f"Re-indexed {total_dbs} DBs in {elapsed:.1f}s")
    print(f"Tag entries: {total_before:,} → {total_after:,} (+{total_after - total_before:,})")


if __name__ == "__main__":
    main()
