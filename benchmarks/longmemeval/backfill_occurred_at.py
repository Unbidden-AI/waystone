#!/usr/bin/env python3
"""
Backfill occurred_at on existing LME checkpoint DBs.

For each checkpoint, maps source_transcript → session datetime using the LME
dataset, then writes occurred_at into nodes that currently have it as NULL.

This is a one-shot migration — run once after fixing _parse_session_date to
handle LME's '2023/05/20 (Sat) 02:21' format.

Usage:
    python3.13 benchmarks/longmemeval/backfill_occurred_at.py \\
        --dataset benchmarks/longmemeval/data/longmemeval_s_cleaned.json \\
        --checkpoint-dir ~/.waystone/longmemeval_cache/engram_lme_gemini_s_user_patched \\
        [--dry-run] [--verbose]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env", override=False)
except ImportError:
    pass

from benchmarks.longmemeval.loaders.longmemeval_dataset import LongMemEvalDataset


def _parse_lme_date(datetime_str: str | None) -> str | None:
    """Parse LME session date string to ISO-8601 UTC."""
    if not datetime_str:
        return None
    for fmt in (
        "%Y/%m/%d (%a) %H:%M",  # '2023/05/20 (Sat) 02:21'
        "%Y/%m/%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(datetime_str.strip(), fmt)
            return dt.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    return None


def _backfill_one(
    db_path: Path,
    sample_id: str,
    session_date_map: dict[str, str],
    dry_run: bool,
    verbose: bool,
) -> dict:
    """Backfill occurred_at for one checkpoint DB.

    session_date_map: {session_id: iso_occurred_at}
    """
    conn = sqlite3.connect(str(db_path))
    try:
        # Check columns exist
        cols = {r[1] for r in conn.execute("PRAGMA table_info(nodes)").fetchall()}
        if "occurred_at" not in cols or "source_transcript" not in cols:
            return {"sample_id": sample_id, "status": "missing_columns", "updated": 0}

        # Fetch all nodes with null occurred_at and a source_transcript
        rows = conn.execute(
            "SELECT id, source_transcript FROM nodes "
            "WHERE occurred_at IS NULL AND source_transcript IS NOT NULL"
        ).fetchall()

        updates: list[tuple[str, str]] = []
        for node_id, src in rows:
            if not src:
                continue
            # source_transcript format: "{sample_id}/{session_id}"
            parts = src.split("/", 1)
            if len(parts) != 2:
                continue
            session_id = parts[1]
            occ = session_date_map.get(session_id)
            if occ:
                updates.append((occ, node_id))

        if updates and not dry_run:
            conn.executemany(
                "UPDATE nodes SET occurred_at = ? WHERE id = ?",
                updates,
            )
            conn.commit()

        if verbose and updates:
            print(f"  {sample_id}: {len(updates)} nodes updated")

        return {"sample_id": sample_id, "status": "ok", "updated": len(updates)}
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill occurred_at for LME checkpoints")
    parser.add_argument(
        "--dataset",
        default="benchmarks/longmemeval/data/longmemeval_s_cleaned.json",
    )
    parser.add_argument(
        "--checkpoint-dir",
        default=str(Path.home() / ".waystone" / "longmemeval_cache" / "engram_lme_gemini_s_user_patched"),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    if not dataset_path.is_absolute():
        dataset_path = REPO_ROOT / dataset_path
    checkpoint_dir = Path(args.checkpoint_dir)

    print(f"Dataset:        {dataset_path}")
    print(f"Checkpoint dir: {checkpoint_dir}")
    print(f"Dry run:        {args.dry_run}")
    print()

    ds = LongMemEvalDataset(str(dataset_path))
    samples = list(ds.iter_samples())
    print(f"Samples: {len(samples)}")

    total_updated = 0
    processed = 0

    for sample in samples:
        db_path = checkpoint_dir / f"{sample.sample_id}.db"
        if not db_path.exists():
            continue

        # Build session_id → occurred_at map for this sample
        session_date_map: dict[str, str] = {}
        for sess in sample.sessions:
            occ = _parse_lme_date(sess.datetime_str)
            if occ:
                session_date_map[sess.session_id] = occ

        result = _backfill_one(
            db_path, sample.sample_id, session_date_map,
            args.dry_run, args.verbose,
        )
        total_updated += result.get("updated", 0)
        processed += 1

    print()
    print("=" * 40)
    print(f"Processed:     {processed}")
    print(f"Nodes updated: {total_updated}")
    if args.dry_run:
        print("[DRY RUN — no writes]")


if __name__ == "__main__":
    main()
