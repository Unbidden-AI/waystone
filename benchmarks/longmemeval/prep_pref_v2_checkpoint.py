#!/usr/bin/env python3
"""
Prepare engram_lme_gemini_s_pref_v2 checkpoint for preference re-extraction.

Copies the specified sample DBs from engram_lme_gemini_s_user_patched into a new
checkpoint dir, then strips all preference-type nodes (and their edges) from each
copy so that a fresh preference_pass run with the improved bridging-tag prompt can
re-extract them from scratch.

Usage:
    python3.13 benchmarks/longmemeval/prep_pref_v2_checkpoint.py [--sample-ids ID ...]
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import json
from pathlib import Path

# 22 samples that scored < 1.0 on single-session-preference in the pref_fanout run
FAILING_SAMPLE_IDS = [
    # score=0.0
    "8a2466db", "75832dbd", "0edc2aef", "32260d93", "195a1a1b",
    "06f04340", "38146c39", "d6233ab6", "fca70973", "a89d7624", "07b6f563",
    # score=0.5
    "06878be2", "35a27287", "afdc33df", "caf03d32", "6b7dfb22",
    "1a1907b4", "09d032c9", "d24813b1", "1da05512", "b6025781", "1d4e3b97",
]

CACHE_ROOT = Path.home() / ".engram" / "longmemeval_cache"
SOURCE_DIR = CACHE_ROOT / "engram_lme_gemini_s_user_patched"
DEST_DIR = CACHE_ROOT / "engram_lme_gemini_s_pref_v2"


def _strip_preferences(db_path: Path) -> tuple[int, int]:
    """Remove all preference-type nodes and their edges from a DB.

    Returns (nodes_deleted, edges_deleted).
    """
    conn = sqlite3.connect(db_path)
    try:
        # Get preference node IDs to delete
        pref_ids = [
            row[0]
            for row in conn.execute(
                "SELECT id FROM nodes WHERE type = 'preference'"
            ).fetchall()
        ]
        if not pref_ids:
            conn.close()
            return 0, 0

        placeholders = ",".join("?" * len(pref_ids))

        # Delete edges involving preference nodes
        edges_deleted = 0
        for table in ["edges"]:
            c = conn.execute(
                f"DELETE FROM {table} WHERE from_id IN ({placeholders}) OR to_id IN ({placeholders})",
                pref_ids + pref_ids,
            )
            edges_deleted += c.rowcount

        # Delete preference nodes
        c = conn.execute(
            f"DELETE FROM nodes WHERE id IN ({placeholders})", pref_ids
        )
        nodes_deleted = c.rowcount

        # Delete from node_tags index
        if conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='node_tags'"
        ).fetchone()[0]:
            conn.execute(
                f"DELETE FROM node_tags WHERE node_id IN ({placeholders})", pref_ids
            )

        conn.commit()
        return nodes_deleted, edges_deleted
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Prep pref_v2 checkpoint")
    parser.add_argument(
        "--sample-ids", nargs="+", default=None, metavar="ID",
        help="Only prep these sample IDs (default: all 22 failing samples)",
    )
    args = parser.parse_args()

    sample_ids = args.sample_ids or FAILING_SAMPLE_IDS

    DEST_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Source: {SOURCE_DIR}")
    print(f"Dest:   {DEST_DIR}")
    print(f"Samples: {len(sample_ids)}")
    print()

    total_nodes = 0
    total_edges = 0

    for sid in sample_ids:
        src_db = SOURCE_DIR / f"{sid}.db"
        dst_db = DEST_DIR / f"{sid}.db"

        if not src_db.exists():
            print(f"  {sid}: MISSING in source — skipping")
            continue

        # Copy DB
        shutil.copy2(src_db, dst_db)

        # Strip preference nodes
        n_nodes, n_edges = _strip_preferences(dst_db)
        total_nodes += n_nodes
        total_edges += n_edges

        # Write .done marker (preference_pass checks this)
        done_marker = DEST_DIR / f"{sid}.done"
        # Do NOT create .done — preference_pass should run on these

        print(f"  {sid}: copied, stripped {n_nodes} pref nodes, {n_edges} edges")

    print()
    print(f"Total stripped: {total_nodes} preference nodes, {total_edges} edges")
    print(f"Ready to run preference_pass on {DEST_DIR}")
    print()
    print("Next step:")
    print(f"  python3.13 benchmarks/longmemeval/preference_pass.py \\")
    print(f"      --checkpoint-dir {DEST_DIR} \\")
    print(f"      --sample-ids {' '.join(sample_ids)} \\")
    print(f"      --workers 4 --verbose --implicit-prefs")


if __name__ == "__main__":
    main()
