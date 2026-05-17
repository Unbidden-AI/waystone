#!/usr/bin/env python3
"""
Augment existing LongMemEval checkpoint DBs with a targeted preference pass.

Processes session-by-session (one API call per session) rather than chunking the
full ~500K char transcript, which caused MAX_TOKENS errors.  Each session is
~1K–5K tokens of input; output is capped at 10 nodes × ~150 tok/node ≈ 1500 tok,
well within the 4096 token output budget.

For each checkpoint DB this script:
  1. Iterates each session in the sample
  2. Calls extract_targeted("preferences", session_text, running_prefs, config)
  3. Accumulates preference nodes across sessions (deduplicates within sample)
  4. Merges new preference nodes/edges back into the checkpoint DB

This is a one-shot augmentation — run it once on existing checkpoints.

Estimated cost (30 preference-type samples × 50 sessions × ~3K tokens input):
  30 × 50 × 3000 = 4.5M input tokens  → ~$0.45
  30 × 50 × 1500 = 2.25M output tokens → ~$0.68
  Total: ~$1.13 at Gemini Flash-Lite pricing

Usage:
    python3.13 benchmarks/longmemeval/preference_pass.py \\
        --dataset benchmarks/longmemeval/data/longmemeval_s_cleaned.json \\
        --checkpoint-dir ~/.waystone/longmemeval_cache/engram_lme_gemini_s_user_patched \\
        [--limit N] [--workers N] [--dry-run] [--question-type single-session-preference]
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env", override=False)
except ImportError:
    pass

from waystone.config import load_config
from waystone.extractor import extract_targeted, extract_implicit_prefs, assign_ids
from waystone.store import GraphStore
from benchmarks.longmemeval.loaders.longmemeval_dataset import LongMemEvalDataset


def _session_text(session: object) -> str:
    """Format a single session as a transcript string."""
    return _turns_to_text(session.turns, getattr(session, "datetime_str", None))


def _turns_to_text(turns: list, datetime_str: str | None) -> str:
    """Format a list of turn objects (with .speaker / .text) as a transcript string."""
    lines: list[str] = []
    if datetime_str:
        lines.append(f"[Session: {datetime_str}]")
    for turn in turns:
        speaker = getattr(turn, "speaker", "User")
        text = getattr(turn, "text", "") or ""
        lines.append(f"{speaker}: {text}")
    return "\n".join(lines)


# min turns per session to bother calling the LLM — very short sessions
# (e.g., a single greeting exchange) are unlikely to contain durable preferences.
_MIN_TURNS = 3

# Maximum bisection depth: 4 → sessions are split at most into 16 chunks (2^4).
# Raised from 3 to handle large phone sessions (~20K chars, 14 turns) that
# previously exhausted depth=3 and were silently skipped.
_BISECT_MAX_DEPTH = 4


def _extract_session_with_bisection(
    turns: list,
    datetime_str: str | None,
    running_prefs: list[dict],
    config: dict,
    depth: int = 0,
) -> dict:
    """Extract preferences from a session's turns, bisecting on MAX_TOKENS.

    If the LLM output is truncated (finish_reason=MAX_TOKENS), the turn list is
    split in half and each half is retried independently.  The first half's nodes
    are passed as running_prefs context to the second half to avoid re-extraction.

    Args:
        turns: List of turn objects (with .speaker / .text).
        datetime_str: Optional session datetime for the transcript header.
        running_prefs: Accumulated preference nodes so far (dedup context).
        config: LLM config dict.
        depth: Current recursion depth (stops at _BISECT_MAX_DEPTH).

    Returns:
        dict with "nodes" and "edges" from this (possibly bisected) session.
    """
    if not turns:
        return {"nodes": [], "edges": []}

    text = _turns_to_text(turns, datetime_str if depth == 0 else None)
    try:
        return _run_async(extract_targeted(text, "preferences", running_prefs, config))
    except Exception as exc:
        exc_str = str(exc)
        is_truncated = (
            "finish_reason=MAX_TOKENS" in exc_str
            or "LLM response was truncated" in exc_str
        )
        if is_truncated and depth < _BISECT_MAX_DEPTH and len(turns) > 1:
            mid = len(turns) // 2
            r1 = _extract_session_with_bisection(
                turns[:mid], datetime_str, running_prefs, config, depth + 1
            )
            # Pass first-half nodes as additional context to the second half
            combined_prefs = running_prefs + r1.get("nodes", [])
            r2 = _extract_session_with_bisection(
                turns[mid:], None, combined_prefs, config, depth + 1
            )
            return {
                "nodes": r1.get("nodes", []) + r2.get("nodes", []),
                "edges": r1.get("edges", []) + r2.get("edges", []),
            }
        raise


def _run_async(coro):
    """Run a coroutine in a fresh event loop (safe inside ThreadPoolExecutor)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(None)


def _process_one(
    sample_id: str,
    sessions: list,
    checkpoint_path: Path,
    config: dict,
    dry_run: bool,
    verbose: bool,
    max_tokens: int = 8192,
    implicit_prefs: bool = False,
) -> dict:
    """Run preference pass on one checkpoint DB, session by session.

    If implicit_prefs=True, runs a second post-pass that infers cross-domain
    preferences from the session-level extraction results (e.g. gardening nodes
    → cooking preferences).
    """
    t0 = time.monotonic()

    if not checkpoint_path.exists():
        return {"sample_id": sample_id, "status": "missing", "new_nodes": 0}

    all_new_nodes: list[dict] = []
    all_new_edges: list[dict] = []

    # Cap output tokens per call: 10 nodes × ~150 tok/node ≈ 1500 tok.
    # 8192 provides headroom for sessions with many preferences without burning budget.
    pref_config = copy.deepcopy(config)
    pref_config["llm"]["max_tokens"] = max_tokens

    # Accumulate preference nodes across sessions to avoid re-extracting the same
    # preferences in later sessions.  Start empty — non-preference DB nodes are
    # not relevant context for this pass.
    running_prefs: list[dict] = []

    skipped = 0
    for session in sessions:
        if len(session.turns) < _MIN_TURNS:
            skipped += 1
            continue
        try:
            result = _extract_session_with_bisection(
                session.turns,
                getattr(session, "datetime_str", None),
                running_prefs,
                pref_config,
            )
        except Exception as e:
            # Log session-level failure but continue — partial writes are better than none
            if verbose:
                print(f"  {sample_id}: session error (skipping): {e}")
            continue
        sess_nodes = result.get("nodes", [])
        sess_edges = result.get("edges", [])
        all_new_nodes.extend(sess_nodes)
        all_new_edges.extend(sess_edges)
        running_prefs = running_prefs + sess_nodes

    n_implicit = 0
    if implicit_prefs:
        # Post-pass: infer cross-domain preferences from explicit ones.
        # Load ALL existing preference nodes from the DB so the inference engine
        # sees both nodes from the initial extraction AND new nodes from this run.
        existing_pref_nodes: list[dict] = []
        if checkpoint_path.exists():
            try:
                _store_ro = GraphStore(str(checkpoint_path))
                existing_pref_nodes = [
                    n for n in _store_ro.get_all_nodes()
                    if n.get("type") == "preference"
                ]
                _store_ro.close()
            except Exception:
                pass
        # Combine: existing DB nodes + newly extracted in this run (dedup by id)
        all_pref_ids = {n["id"] for n in existing_pref_nodes}
        combined_prefs = existing_pref_nodes + [
            n for n in running_prefs if n.get("id") not in all_pref_ids
        ]
        if combined_prefs:
            implicit_config = copy.deepcopy(pref_config)
            # Use same max_tokens as session pass — thinking tokens eat budget fast
            implicit_config["llm"]["max_tokens"] = max_tokens
            try:
                impl_result = _run_async(
                    extract_implicit_prefs(combined_prefs, implicit_config)
                )
                impl_nodes = impl_result.get("nodes", [])
                impl_edges = impl_result.get("edges", [])
                all_new_nodes.extend(impl_nodes)
                all_new_edges.extend(impl_edges)
                n_implicit = len(impl_nodes)
                if verbose and impl_nodes:
                    print(f"  {sample_id}: +{n_implicit} implicit pref nodes")
            except Exception as e:
                if verbose:
                    print(f"  {sample_id}: implicit pass error (skipping): {e}")

    elapsed = time.monotonic() - t0

    if not all_new_nodes:
        return {"sample_id": sample_id, "status": "ok", "new_nodes": 0, "elapsed": elapsed}

    if not dry_run:
        merged = assign_ids({"nodes": all_new_nodes, "edges": all_new_edges})
        store = GraphStore(str(checkpoint_path))
        store.merge_extraction(merged["nodes"], merged["edges"])
        store.close()

    n_sessions = len(sessions) - skipped
    if verbose:
        print(
            f"  {sample_id}: +{len(all_new_nodes)} pref nodes "
            f"({n_implicit} implicit) from {n_sessions} sessions  ({elapsed:.1f}s)"
        )

    return {
        "sample_id": sample_id,
        "status": "ok",
        "new_nodes": len(all_new_nodes),
        "new_edges": len(all_new_edges),
        "new_implicit_nodes": n_implicit,
        "elapsed": round(elapsed, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Augment LME checkpoints with preference pass")
    parser.add_argument(
        "--dataset",
        default="benchmarks/longmemeval/data/longmemeval_s_cleaned.json",
        help="Path to LME S-split JSON",
    )
    parser.add_argument(
        "--checkpoint-dir",
        default=str(Path.home() / ".waystone" / "longmemeval_cache" / "engram_lme_gemini_s_user_patched"),
        help="Directory containing .db checkpoint files",
    )
    parser.add_argument("--limit", type=int, default=None, help="Process only first N samples")
    parser.add_argument(
        "--sample-ids", nargs="+", default=None, metavar="ID",
        help="Only process these sample IDs (space-separated)",
    )
    parser.add_argument(
        "--max-tokens", type=int, default=8192,
        help="Max output tokens per LLM call (default: 8192)",
    )
    parser.add_argument("--workers", type=int, default=4, help="Parallel workers (default: 4)")
    parser.add_argument("--dry-run", action="store_true", help="Run extraction but don't write to DBs")
    parser.add_argument("--verbose", action="store_true", help="Print per-sample progress")
    parser.add_argument(
        "--implicit-prefs",
        action="store_true",
        help=(
            "After session-by-session extraction, run a post-pass that infers "
            "cross-domain preferences (e.g. gardening nodes → cooking preferences). "
            "Adds ~1 LLM call per sample."
        ),
    )
    parser.add_argument(
        "--question-type",
        default="single-session-preference",
        help=(
            "Only process samples of this question type "
            "(default: single-session-preference, set 'all' for all types)"
        ),
    )
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    if not dataset_path.is_absolute():
        dataset_path = REPO_ROOT / dataset_path
    checkpoint_dir = Path(args.checkpoint_dir)

    print(f"Dataset:        {dataset_path}")
    print(f"Checkpoint dir: {checkpoint_dir}")
    print(f"Workers:        {args.workers}")
    print(f"Max tokens:     {args.max_tokens}")
    print(f"Dry run:        {args.dry_run}")
    print(f"Implicit prefs: {args.implicit_prefs}")
    print()

    ds = LongMemEvalDataset(str(dataset_path))
    config = load_config()

    question_types = None if args.question_type == "all" else [args.question_type]

    samples = list(ds.iter_samples(limit=args.limit, question_types=question_types))
    if args.sample_ids:
        target_ids = set(args.sample_ids)
        samples = [s for s in samples if s.sample_id in target_ids]
        print(f"  (filtered to --sample-ids: {sorted(target_ids)})")
    print(f"Samples to process: {len(samples)}")
    if question_types:
        print(f"  (filtered to question_type={args.question_type})")
    print()

    tasks: list[tuple[str, list, Path]] = []
    for sample in samples:
        checkpoint_path = checkpoint_dir / f"{sample.sample_id}.db"
        tasks.append((sample.sample_id, sample.sessions, checkpoint_path))

    # Check how many checkpoints exist
    existing = sum(1 for _, _, p in tasks if p.exists())
    missing = len(tasks) - existing
    total_sessions = sum(len(sessions) for _, sessions, _ in tasks)
    print(f"Checkpoints found: {existing}/{len(tasks)}  (missing: {missing})")
    print(f"Total sessions:    {total_sessions}  (~{total_sessions} API calls)")
    if missing:
        print(f"  [warn] {missing} samples have no checkpoint — they will be skipped")
    print()

    t_start = time.monotonic()
    results: list[dict] = []
    total_new_nodes = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                _process_one,
                sample_id, sessions, cp_path,
                config, args.dry_run, args.verbose, args.max_tokens,
                args.implicit_prefs,
            ): sample_id
            for sample_id, sessions, cp_path in tasks
        }
        done = 0
        for fut in as_completed(futures):
            done += 1
            try:
                r = fut.result()
            except Exception as e:
                sid = futures[fut]
                r = {"sample_id": sid, "status": f"exception: {e}", "new_nodes": 0}
            results.append(r)
            total_new_nodes += r.get("new_nodes", 0)
            if done % 5 == 0 or done == len(tasks):
                elapsed = time.monotonic() - t_start
                print(
                    f"  [{done}/{len(tasks)}]  "
                    f"total new preference nodes so far: {total_new_nodes}  ({elapsed:.0f}s)"
                )

    elapsed_total = time.monotonic() - t_start
    errors = [r for r in results if r["status"].startswith(("error", "exception"))]
    augmented = [r for r in results if r.get("new_nodes", 0) > 0]
    total_implicit = sum(r.get("new_implicit_nodes", 0) for r in results)

    print()
    print("=" * 50)
    print("SUMMARY")
    print("=" * 50)
    print(f"  Processed:          {len(results)}")
    print(f"  Augmented:          {len(augmented)}")
    print(f"  Total new nodes:    {total_new_nodes}")
    if args.implicit_prefs:
        print(f"  Implicit nodes:     {total_implicit}")
    print(f"  Errors:             {len(errors)}")
    print(f"  Total time:         {elapsed_total:.0f}s")
    if args.dry_run:
        print("  [DRY RUN — no writes performed]")
    print()
    if errors:
        print("Errors:")
        for r in errors[:10]:
            print(f"  {r['sample_id']}: {r['status']}")


if __name__ == "__main__":
    main()
