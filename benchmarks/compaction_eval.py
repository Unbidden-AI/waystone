#!/usr/bin/env python3
"""Compaction Eval — CB compaction vs. naive truncation knowledge preservation test.

CB compaction extracts facts from dropped messages into the graph before removing them.
Naive truncation just drops messages, permanently losing those facts.

Test flow:
  1. Inject a synthetic multi-turn conversation with specific facts planted in early turns.
  2. CB condition   : compact() extracts the early turns into the graph; retrieval
                     injects those facts back when answering questions.
  3. Naive condition: the same early turns simply never existed — no graph, no extraction.
  4. Ask questions about the planted facts in both conditions.
  5. Score both answers against ground truth elements.

Usage:
    python benchmarks/compaction_eval.py
    python benchmarks/compaction_eval.py --config benchmarks/model_configs/gemini_25_flash.yaml
    python benchmarks/compaction_eval.py --save
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from engram.config import get_db_path, load_config
from engram.store import GraphStore
from orchestrator.context_manager import ContextManager
from orchestrator.types import CompactionTrigger, Message

BENCHMARKS_DIR = Path(__file__).parent

# ---------------------------------------------------------------------------
# Context manager config — small window to trigger compaction quickly
# ---------------------------------------------------------------------------

_CTX_CFG = {
    "window_size": 10,               # fire at 10 messages in history
    "compaction_batch": 6,           # compact the first 6 messages (3 seed turns)
    "token_budget": 0,               # disable token-budget trigger
    "idle_seconds_before_compact": 0, # disable idle trigger
    "retrieve": {
        "hops": 2,
        "top_k": 20,
        "strategies": {
            "superseded_pruning": True,
            "confidence_threshold": 0.0,
        },
    },
}

# ---------------------------------------------------------------------------
# Synthetic conversation scenario
# ---------------------------------------------------------------------------

# seed_turns  — planted facts that will be compacted out of raw history.
#               Each turn is a user+assistant message pair.
# filler_turns — unrelated turns added after the seed facts to push history
#               past window_size. These remain in history post-compaction.
# questions   — asked after compaction; can only be answered via the graph
#               (CB) or not at all (naive).

SCENARIO = {
    "name": "streaming_backend",
    "description": (
        "Early design decisions for a real-time streaming backend. "
        "These facts are compacted out of raw history; CB preserves them via graph extraction."
    ),
    "seed_turns": [
        {
            "user": "We need to finalize the message broker for the real-time ingestion pipeline.",
            "assistant": (
                "After evaluating RabbitMQ and Pulsar, we're going with Apache Kafka. "
                "Our throughput requirement is 800K events per second, which Kafka handles "
                "with 16 partitions per topic. RabbitMQ was ruled out due to throughput "
                "limitations under sustained load at our scale."
            ),
        },
        {
            "user": "What's the plan for message retention and long-term archival?",
            "assistant": (
                "Kafka retention is set to 72 hours — enough for replay on transient "
                "failures. After that, a compaction job archives messages to S3 in Parquet "
                "format. Cold data older than 90 days is automatically tiered to S3 Glacier "
                "for cost savings."
            ),
        },
        {
            "user": "How are we configuring consumer groups for the downstream services?",
            "assistant": (
                "Each service gets its own consumer group to allow independent scaling and "
                "replay. The fraud detection service consumer group runs with 8 replicas "
                "to handle peak load without lag. The analytics consumer group uses 4 "
                "replicas — it's lower priority and can fall behind without affecting the "
                "critical payment path."
            ),
        },
    ],
    "filler_turns": [
        {
            "user": "What frontend framework are we using for the admin dashboard?",
            "assistant": (
                "The team settled on React with TypeScript for the admin dashboard. "
                "Vue and Svelte were evaluated but React was chosen for team familiarity "
                "and the breadth of available component libraries."
            ),
        },
        {
            "user": "Should we use CSS modules or Tailwind for the dashboard styling?",
            "assistant": (
                "Tailwind CSS was chosen. It speeds up iteration and the utility-first "
                "approach works well for our small frontend team. We'll use shadcn/ui "
                "for component primitives to keep things consistent."
            ),
        },
    ],
    "questions": [
        {
            "id": "comp_01",
            "question": (
                "What message broker was chosen for the ingestion pipeline, "
                "how many partitions per topic, and why was RabbitMQ rejected?"
            ),
            "ground_truth_elements": [
                "Apache Kafka",
                "16 partitions per topic",
                "RabbitMQ rejected due to throughput limitations",
            ],
        },
        {
            "id": "comp_02",
            "question": (
                "What is the Kafka retention period and where does archived data go long-term?"
            ),
            "ground_truth_elements": [
                "72 hours retention",
                "archived to S3 in Parquet format",
                "S3 Glacier after 90 days",
            ],
        },
        {
            "id": "comp_03",
            "question": (
                "How many replicas does the fraud detection consumer group run with, "
                "and why does the analytics consumer group use fewer?"
            ),
            "ground_truth_elements": [
                "fraud detection consumer group",
                "8 replicas",
                "analytics uses 4 replicas",
                "analytics is lower priority",
            ],
        },
    ],
}


# ---------------------------------------------------------------------------
# Scoring (same logic as generation_eval.py)
# ---------------------------------------------------------------------------

STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "and", "or", "for",
    "to", "of", "in", "on", "at", "by", "with", "not", "no", "due",
    "when", "than", "that", "this", "it", "its", "be", "as", "also",
    "from", "but", "so", "if", "we", "our", "they", "their",
}


def _clause_keyword_match(text_lower: str, clause: str, threshold: float = 0.6) -> bool:
    keywords = [
        w.strip(".,;:()\"'—")
        for w in clause.split()
        if w.strip(".,;:()\"'—") not in STOP_WORDS and len(w.strip(".,;:()\"'—")) > 2
    ]
    if not keywords:
        return True
    return sum(1 for w in keywords if w in text_lower) / len(keywords) >= threshold


def _element_matched(text_lower: str, element: str) -> bool:
    import re
    el_lower = element.lower()
    if el_lower in text_lower:
        return True
    clauses = [c.strip() for c in el_lower.split(" — ") if c.strip()]
    if len(clauses) > 1:
        return all(_clause_keyword_match(text_lower, c) for c in clauses)
    paren_match = re.search(r"\(([^)]+)\)", el_lower)
    if paren_match:
        main = re.sub(r"\([^)]*\)", "", el_lower).strip()
        paren = paren_match.group(1)
        return (
            _clause_keyword_match(text_lower, main)
            and _clause_keyword_match(text_lower, paren)
        )
    return _clause_keyword_match(text_lower, el_lower)


def score_recall(text: str, elements: list[str]) -> tuple[float, list, list]:
    text_lower = text.lower()
    matched, missed = [], []
    for el in elements:
        (matched if _element_matched(text_lower, el) else missed).append(el)
    recall = len(matched) / len(elements) if elements else 0.0
    return recall, matched, missed


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

def _call_llm_sync(question: str, context_md: str | None, config: dict) -> str:
    import litellm
    litellm.set_verbose = False
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)

    llm_cfg = config.get("llm", {})
    api_key = None
    env_name = llm_cfg.get("api_key_env")
    if env_name:
        api_key = os.environ.get(env_name)
    if not api_key:
        for env in ("CTX_API_KEY", "OPENAI_API_KEY"):
            api_key = os.environ.get(env)
            if api_key:
                break

    if context_md:
        system = (
            "You are a knowledgeable assistant. "
            "Use the project knowledge below to answer the question accurately and concisely.\n\n"
            "## Project Knowledge\n\n"
            + context_md
            + "\n\n---"
        )
    else:
        system = (
            "You are a knowledgeable assistant. "
            "Answer the question as best you can based on what is in the conversation history. "
            "If you don't know, say so clearly — do not guess."
        )

    model = llm_cfg.get("model", "gemini-2.5-flash")
    if model.startswith("models/"):
        model = model[7:]

    kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": question},
        ],
        "temperature": llm_cfg.get("temperature", 0.1),
        "max_tokens": min(llm_cfg.get("max_tokens", 4096), 1024),
    }
    if api_key:
        kwargs["api_key"] = api_key
    base_url = llm_cfg.get("base_url")
    if base_url:
        kwargs["api_base"] = base_url
        kwargs["custom_llm_provider"] = "openai"

    import litellm as _ll
    response = _ll.completion(**kwargs)
    return response.choices[0].message.content or ""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run_eval(config: dict, save: bool) -> dict:
    scenario = SCENARIO
    print(f"\nCompaction Eval — {scenario['name']}")
    print(f"Model: {config['llm'].get('model', '?')}")
    print(f"Scenario: {scenario['description']}\n")

    seed_turns = scenario["seed_turns"]
    filler_turns = scenario["filler_turns"]
    questions = scenario["questions"]

    # seed_turns × 2 + filler_turns × 2 should equal _CTX_CFG["window_size"]
    expected = len(seed_turns) * 2 + len(filler_turns) * 2
    window = _CTX_CFG["window_size"]
    if expected != window:
        print(
            f"WARNING: seed={len(seed_turns)*2} + filler={len(filler_turns)*2} = {expected} "
            f"messages but window_size={window}. Compaction may not fire as expected.",
            file=sys.stderr,
        )

    # ── CB condition setup ────────────────────────────────────────────────────
    cb_tmp = tempfile.mkdtemp(prefix="ctx_compeval_cb_")
    cfg_cb = dict(config)
    cfg_cb["projects_dir"] = cb_tmp
    db_path_cb = Path(cb_tmp) / "compeval.db"

    store_cb = GraphStore(db_path_cb)
    ctx_cb = ContextManager(
        cfg=_CTX_CFG,
        store=store_cb,
        project_name="compeval_cb",
        extractor_config=cfg_cb,
    )

    # Inject seed turns
    for turn in seed_turns:
        ctx_cb.add_message(Message(role="user", content=turn["user"]))
        ctx_cb.add_message(Message(role="assistant", content=turn["assistant"]))

    # Inject filler turns
    for turn in filler_turns:
        ctx_cb.add_message(Message(role="user", content=turn["user"]))
        ctx_cb.add_message(Message(role="assistant", content=turn["assistant"]))

    history_before = len(ctx_cb.get_history())
    print(f"CB: {history_before} messages in history — running compaction...")

    trigger = ctx_cb.should_compact()
    if trigger is None:
        print(
            f"WARNING: compaction did not trigger (depth={history_before}, window={window}). "
            "Seed facts will still be in raw history.",
            file=sys.stderr,
        )

    t0 = time.time()
    compaction_result = await ctx_cb.compact(trigger or CompactionTrigger.HISTORY_DEPTH)
    compact_elapsed = time.time() - t0

    history_after = len(ctx_cb.get_history())
    print(
        f"CB: compaction complete in {compact_elapsed:.0f}s — "
        f"removed {compaction_result.messages_removed} messages, "
        f"extracted {compaction_result.nodes_extracted} nodes. "
        f"History: {history_before} → {history_after} messages.\n"
    )

    # ── Naive condition setup ─────────────────────────────────────────────────
    # Naive = filler turns only, empty graph (seed facts were never extracted)
    naive_tmp = tempfile.mkdtemp(prefix="ctx_compeval_naive_")
    db_path_naive = Path(naive_tmp) / "compeval.db"
    store_naive = GraphStore(db_path_naive)

    print("Naive: injecting only filler turns (seed facts lost — not extracted).")
    print(f"Naive: {len(filler_turns) * 2} messages in history.\n")

    # ── Run questions ─────────────────────────────────────────────────────────
    total_cb = 0
    total_naive = 0
    total_elements = 0
    question_results = []

    print("=" * 78)
    for q in questions:
        qid = q["id"]
        question = q["question"]
        ground_truth = q["ground_truth_elements"]

        # CB: retrieve graph context for this question
        context_md = ctx_cb.retrieve_context(question)
        node_count = len([l for l in context_md.splitlines() if l.strip().startswith("-")])

        print(f"\n[{qid}] {question}")
        print(f"  CB graph context: ~{node_count} bullet points retrieved")

        # CB answer
        print("  Calling LLM [CB — graph context]...", end="", flush=True)
        t0 = time.time()
        answer_cb = _call_llm_sync(question, context_md or None, config)
        print(f" ({time.time()-t0:.1f}s)")

        # Naive answer — no context (seed facts are gone)
        print("  Calling LLM [naive — no graph, filler history only]...", end="", flush=True)
        t0 = time.time()
        answer_naive = _call_llm_sync(question, None, config)
        print(f" ({time.time()-t0:.1f}s)")

        # Score
        recall_cb, matched_cb, missed_cb = score_recall(answer_cb, ground_truth)
        recall_naive, matched_naive, missed_naive = score_recall(answer_naive, ground_truth)

        total_cb += len(matched_cb)
        total_naive += len(matched_naive)
        total_elements += len(ground_truth)

        question_results.append({
            "id": qid,
            "question": question,
            "recall_cb": recall_cb,
            "recall_naive": recall_naive,
            "matched_cb": matched_cb,
            "missed_cb": missed_cb,
            "matched_naive": matched_naive,
            "missed_naive": missed_naive,
            "answer_cb": answer_cb,
            "answer_naive": answer_naive,
        })

        print(f"\n  ── Recall: CB={recall_cb:.0%}  |  naive={recall_naive:.0%} ──")
        print(f"\n  ANSWER [CB — after compaction, with graph retrieval]:")
        for line in answer_cb.strip().splitlines():
            print(f"    {line}")

        print(f"\n  ANSWER [NAIVE — seed facts lost, filler history only]:")
        for line in answer_naive.strip().splitlines():
            print(f"    {line}")

        print(f"\n  Ground truth ({len(ground_truth)} elements):")
        for el in ground_truth:
            cb_mark = "✓" if el in matched_cb else "✗"
            naive_mark = "✓" if el in matched_naive else "✗"
            print(f"    cb:{cb_mark}  naive:{naive_mark}  \"{el}\"")

        print("=" * 78)

    # ── Summary ───────────────────────────────────────────────────────────────
    pct_cb = total_cb / total_elements * 100 if total_elements else 0
    pct_naive = total_naive / total_elements * 100 if total_elements else 0
    gain = pct_cb - pct_naive

    print(f"\n{'='*78}")
    print(f"SUMMARY: {len(questions)} questions, {total_elements} ground truth elements")
    print(f"  CB (post-compaction + retrieval): {total_cb}/{total_elements} = {pct_cb:.0f}%")
    print(f"  Naive (no extraction, no graph):  {total_naive}/{total_elements} = {pct_naive:.0f}%")
    print(f"  Knowledge preserved by CB:        {gain:+.0f}pp")
    print(f"  Nodes extracted during compaction: {compaction_result.nodes_extracted}")
    print(f"{'='*78}\n")

    result = {
        "scenario": scenario["name"],
        "model": config["llm"].get("model", "?"),
        "compaction": {
            "messages_removed": compaction_result.messages_removed,
            "nodes_extracted": compaction_result.nodes_extracted,
            "elapsed_seconds": round(compact_elapsed, 1),
        },
        "summary": {
            "questions": len(questions),
            "elements": total_elements,
            "cb_matched": total_cb,
            "naive_matched": total_naive,
            "recall_cb_pct": round(pct_cb, 1),
            "recall_naive_pct": round(pct_naive, 1),
            "gain_pp": round(gain, 1),
        },
        "questions": question_results,
    }

    # ── Cleanup ───────────────────────────────────────────────────────────────
    shutil.rmtree(cb_tmp, ignore_errors=True)
    shutil.rmtree(naive_tmp, ignore_errors=True)

    return result


def main():
    parser = argparse.ArgumentParser(description="CB compaction vs naive truncation eval")
    parser.add_argument(
        "--config",
        default="benchmarks/model_configs/gemini_25_flash.yaml",
        help="Model config YAML (default: gemini_25_flash)",
    )
    parser.add_argument("--save", action="store_true",
                        help="Save results JSON to benchmarks/results/compaction_eval_<timestamp>/")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = ROOT / config_path
    config = load_config(config_path)

    result = asyncio.run(run_eval(config, save=args.save))

    if args.save:
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_dir = BENCHMARKS_DIR / "results" / f"compaction_eval_{ts}"
        out_dir.mkdir(parents=True, exist_ok=True)
        result["timestamp"] = ts
        results_path = out_dir / "results.json"
        with open(results_path, "w") as f:
            json.dump(result, f, indent=2)
        print(f"Results saved to {results_path}")


if __name__ == "__main__":
    main()
