"""
Judge spot-check tool — compare verdicts from two LLM judges on the same questions.

Loads a LOCOMO conversation, replays retrieval for a given config, then scores
each QA pair with two judge models. Prints cases where the judges disagree so
you can read them and decide which judge is right.

Usage:
    python -m benchmarks.locomo.spot_check \
        --dataset benchmarks/locomo/data/locomo10.json \
        --config engram_default_topk100 \
        --conv conv-26 \
        --model-a local:qwen/qwen3-8b \
        --model-b local:qwen/qwen3.5-9b \
        --max 30

All judge calls use the cache (~/.cache/engram_judge_cache.json) — if you've
already run both judges on this conversation, this script makes zero new API calls.
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from benchmarks.locomo.loaders.locomo_dataset import LocomoDataset
from benchmarks.locomo.ablation_configs import ABLATION_CONFIGS
from benchmarks.locomo.evaluation.scoring import score_llm_judge
from benchmarks.locomo.harness import _retrieve_context
from engram.store import GraphStore


def _judge_all(
    triples: list[tuple[int, str, str, str]],
    model: str,
    label: str,
) -> dict[int, float | None]:
    """Score all (idx, question, answer, context) triples with one judge model.

    Returns dict[idx → score | None].
    """
    results: dict[int, float | None] = {}

    def _one(args):
        idx, q, a, ctx = args
        try:
            return idx, score_llm_judge(
                question=q,
                ground_truth_answer=a,
                retrieved_context=ctx,
                model=model,
            )
        except Exception as e:
            print(f"  [{label}] Q{idx} failed: {e}", flush=True)
            return idx, None

    # Local models can't handle high concurrency — cap at 4 workers
    _workers = 4 if model.startswith("local:") else 20
    with ThreadPoolExecutor(max_workers=_workers) as pool:
        futures = [pool.submit(_one, t) for t in triples]
        done = 0
        total = len(triples)
        for fut in as_completed(futures):
            idx, score = fut.result()
            results[idx] = score
            done += 1
            print(f"  [{label}] {done}/{total} scored", end="\r", flush=True)
    print()  # newline after progress line
    return results


def _find_relevant_snippet(ctx: str, question: str, answer: str, window: int = 600) -> str:
    """Return the most relevant portion of ctx for this question/answer.

    Tokenizes the answer (and question) into meaningful words, then finds the
    line in ctx with the highest keyword overlap. Returns that line plus
    surrounding lines to fill ~window chars. Falls back to the first `window`
    chars if no match is found.
    """
    import re

    def keywords(text: str) -> set[str]:
        return {w.lower() for w in re.findall(r"[a-zA-Z0-9]+", text) if len(w) >= 3}

    target_kws = keywords(str(answer)) | keywords(str(question))
    lines = ctx.splitlines()

    best_idx = 0
    best_score = -1
    for i, line in enumerate(lines):
        score = len(keywords(line) & target_kws)
        if score > best_score:
            best_score = score
            best_idx = i

    # Expand outward from the best line until we hit the char budget
    snippet_lines = [lines[best_idx]]
    chars = len(lines[best_idx])
    lo, hi = best_idx - 1, best_idx + 1
    while chars < window and (lo >= 0 or hi < len(lines)):
        if lo >= 0:
            snippet_lines.insert(0, lines[lo])
            chars += len(lines[lo]) + 1
            lo -= 1
        if hi < len(lines) and chars < window:
            snippet_lines.append(lines[hi])
            chars += len(lines[hi]) + 1
            hi += 1

    return " | ".join(l.strip() for l in snippet_lines if l.strip())


def main():
    parser = argparse.ArgumentParser(description="Compare two LLM judges on LOCOMO QA pairs")
    parser.add_argument("--dataset", required=True, help="Path to locomo10.json")
    parser.add_argument("--config", required=True, help="Ablation config name (e.g. engram_default_topk100)")
    parser.add_argument("--conv", required=True, help="Conversation ID (e.g. conv-26)")
    parser.add_argument("--model-a", default="local:qwen/qwen3-8b", help="First judge model")
    parser.add_argument("--model-b", default="local:qwen/qwen3.5-9b", help="Second judge model")
    parser.add_argument("--max", type=int, default=30, help="Max disagreements to show (default 30)")
    parser.add_argument("--context-chars", type=int, default=600,
                        help="Characters of retrieved context to show per case (default 600)")
    args = parser.parse_args()

    if args.config not in ABLATION_CONFIGS:
        print(f"Unknown config '{args.config}'. Available: {', '.join(ABLATION_CONFIGS)}")
        sys.exit(1)

    config = ABLATION_CONFIGS[args.config]
    ds = LocomoDataset(args.dataset)

    # Find the requested conversation
    conv = None
    for c in ds.iter_conversations(sample_ids=[args.conv]):
        conv = c
        break
    if conv is None:
        print(f"Conversation '{args.conv}' not found in dataset")
        sys.exit(1)

    print(f"Conversation: {conv.sample_id} — {len(conv.qa_pairs)} QA pairs")

    # Open the checkpoint DB for this config
    db_path = Path.home() / ".engram" / "locomo_cache" / config.name / f"{conv.sample_id}.db"
    if not db_path.exists():
        print(f"No checkpoint found at {db_path}")
        print("Run the harness first to build the DB for this config/conversation.")
        sys.exit(1)

    store = GraphStore(str(db_path))
    print(f"DB: {db_path}")
    print()

    # Retrieval pass
    print("Retrieving context for all QA pairs...", flush=True)
    triples: list[tuple[int, str, str, str]] = []
    for i, qa in enumerate(conv.qa_pairs):
        ctx = _retrieve_context(
            conv=conv,
            question=qa.question,
            store=store,
            config=config,
            relevant_session_ids=None,
        )
        triples.append((i, qa.question, qa.answer, ctx))

    print(f"Retrieval done — {len(triples)} pairs")
    print()

    # Judge both models
    label_a = args.model_a.split("/")[-1]
    label_b = args.model_b.split("/")[-1]

    print(f"Scoring with model A: {args.model_a}")
    scores_a = _judge_all(triples, model=args.model_a, label=label_a)

    print(f"Scoring with model B: {args.model_b}")
    scores_b = _judge_all(triples, model=args.model_b, label=label_b)

    # Find disagreements — A says YES (>=1.0), B says NO (<1.0) or vice versa
    disagree: list[tuple[int, float, float]] = []
    for idx, qa_q, qa_a, ctx in triples:
        sa = scores_a.get(idx)
        sb = scores_b.get(idx)
        if sa is None or sb is None:
            continue
        verdict_a = sa >= 1.0
        verdict_b = sb >= 1.0
        if verdict_a != verdict_b:
            disagree.append((idx, sa, sb))

    total_a = sum(1 for s in scores_a.values() if s is not None)
    total_b = sum(1 for s in scores_b.values() if s is not None)
    acc_a = sum(1 for s in scores_a.values() if s is not None and s >= 1.0) / max(total_a, 1)
    acc_b = sum(1 for s in scores_b.values() if s is not None and s >= 1.0) / max(total_b, 1)

    print(f"{'='*70}")
    print(f"Summary")
    print(f"  {label_a}: {acc_a:.1%}  (n={total_a})")
    print(f"  {label_b}: {acc_b:.1%}  (n={total_b})")
    print(f"  Disagreements: {len(disagree)} / {min(total_a, total_b)}")
    print(f"{'='*70}")
    print()

    if not disagree:
        print("No disagreements found — judges agree on all scored questions.")
        return

    # Show disagreements grouped: A=YES/B=NO first (cases where A is more lenient)
    a_yes_b_no = [(i, sa, sb) for i, sa, sb in disagree if sa >= 1.0]
    a_no_b_yes = [(i, sa, sb) for i, sa, sb in disagree if sb >= 1.0]

    print(f"{label_a}=YES / {label_b}=NO: {len(a_yes_b_no)} cases  (A more lenient)")
    print(f"{label_a}=NO  / {label_b}=YES: {len(a_no_b_yes)} cases  (B more lenient)")
    print()

    shown = 0
    for group_label, group in [
        (f"{label_a}=YES / {label_b}=NO", a_yes_b_no),
        (f"{label_a}=NO  / {label_b}=YES", a_no_b_yes),
    ]:
        if not group:
            continue
        print(f"{'─'*70}")
        print(f"  {group_label}")
        print(f"{'─'*70}")
        for idx, sa, sb in group:
            if shown >= args.max:
                print(f"\n  ... (truncated, showing first {args.max} disagreements)")
                return
            _, qa_q, qa_a, ctx = triples[idx]
            print()
            print(f"  Q{idx}  [{label_a}={sa:.1f}  {label_b}={sb:.1f}]")
            print(f"  Question : {qa_q}")
            print(f"  Answer   : {qa_a}")
            # Find the most relevant snippet: grep context lines for answer/question keywords
            ctx_snippet = _find_relevant_snippet(ctx, qa_q, qa_a, window=args.context_chars)
            print(f"  Context  : {ctx_snippet}  ({len(ctx)} chars total)")
            print()
            print(f"  Your verdict? (type YES/NO/SKIP and press Enter, or Ctrl-C to quit)")
            try:
                verdict = input("  > ").strip().upper()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if verdict == "YES":
                winner = label_a if sa >= 1.0 else label_b
                print(f"  → {winner} was right\n")
            elif verdict == "NO":
                winner = label_b if sa >= 1.0 else label_a
                print(f"  → {winner} was right\n")
            elif verdict == "SKIP":
                print(f"  → skipped\n")
            else:
                print(f"  → unrecognized input, skipped\n")
            shown += 1


if __name__ == "__main__":
    main()
