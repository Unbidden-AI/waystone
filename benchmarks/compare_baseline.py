#!/usr/bin/env python3
"""Waystone vs. Raw-Transcript Baseline comparison.

Runs a set of eval questions against two approaches:

  CB approach   — retrieve relevant graph nodes, inject as context, ask LLM
  Raw baseline  — put the full transcript in the system prompt, ask LLM

Both use the same model so the only variable is context quality.

Usage
-----
    # Quick run — 5 questions from all transcripts (cheapest)
    python benchmarks/compare_baseline.py

    # Single transcript, all questions
    python benchmarks/compare_baseline.py --transcript project_api_design --questions all

    # Custom count, explicit model override
    python benchmarks/compare_baseline.py --questions 3 --model gemini/gemini-2.5-flash

    # Skip re-extraction if you already ran this recently
    python benchmarks/compare_baseline.py --skip-extract
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from waystone.config import get_db_path, get_project_dir, load_config
from waystone.extractor import extract, extract_targeted, verify_extraction
from waystone.retriever import retrieve_with_stats
from waystone.store import GraphStore
from orchestrator.llm_adapter import call_llm, estimate_tokens
from orchestrator.types import Message

BENCHMARKS_DIR = Path(__file__).parent
TRANSCRIPTS_DIR = BENCHMARKS_DIR / "transcripts"
EVAL_QUESTIONS_PATH = BENCHMARKS_DIR / "eval_questions.yaml"

# ---------------------------------------------------------------------------
# Scoring (copied from run_benchmark.py to keep this script self-contained)
# ---------------------------------------------------------------------------

STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "and", "or", "for",
    "to", "of", "in", "on", "at", "by", "with", "not", "no", "due",
    "when", "than", "that", "this", "it", "its", "be", "as", "also",
    "from", "but", "so", "if", "we", "our", "they", "their",
}


def score_recall(answer: str, ground_truth_elements: list) -> tuple[float, list, list]:
    """Fraction of ground-truth elements found in *answer* (full phrase or keyword fallback)."""
    text = answer.lower()
    matched, missed = [], []
    for element in ground_truth_elements:
        el_lower = element.lower()
        if el_lower in text:
            matched.append(element)
            continue
        key_words = [
            w.strip(".,;:()\"'—")
            for w in el_lower.split()
            if w.strip(".,;:()\"'—") not in STOP_WORDS and len(w) > 2
        ]
        if key_words and sum(1 for w in key_words if w in text) / len(key_words) >= 0.7:
            matched.append(element)
        else:
            missed.append(element)
    recall = len(matched) / len(ground_truth_elements) if ground_truth_elements else 0.0
    return recall, matched, missed


# ---------------------------------------------------------------------------
# LLM helper
# ---------------------------------------------------------------------------

_SYSTEM_CB = (
    "You are a helpful assistant. Answer the question using ONLY the provided context. "
    "Be concise but include all specific details (names, numbers, rationale) that are present. "
    "If the context does not contain enough information, say so."
)

_SYSTEM_RAW = (
    "You are a helpful assistant with access to the full project transcript below. "
    "Answer the question using ONLY information from the transcript. "
    "Be concise but include all specific details (names, numbers, rationale). "
    "If the transcript does not contain enough information, say so.\n\n"
)


async def ask_llm(system: str, question: str, llm_cfg: dict) -> tuple[str, int]:
    """Call the LLM and return (answer_text, input_tokens_estimated)."""
    messages = [Message(role="user", content=question)]
    input_tokens = estimate_tokens(system) + estimate_tokens(question)
    text, _, _ = await call_llm(messages=messages, system=system, cfg=llm_cfg, tools=None)
    return (text or ""), input_tokens


# ---------------------------------------------------------------------------
# Extraction helper
# ---------------------------------------------------------------------------

def ensure_extracted(
    config: dict,
    project_name: str,
    transcript_paths: list[Path],
    verify: bool = False,
    decisions: bool = False,
) -> None:
    """Extract all transcripts into *project_name* graph (overwrites existing)."""
    db_path = get_db_path(config, project_name)
    project_dir = get_project_dir(config, project_name)
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "transcripts").mkdir(exist_ok=True)

    # Fresh DB each run so we don't accumulate across compare runs
    if db_path.exists():
        db_path.unlink()

    for tp in transcript_paths:
        text = tp.read_text()
        print(f"  Extracting {tp.name} ...", end="", flush=True)
        t0 = time.time()
        extraction = asyncio.run(extract(text, config))
        if verify:
            extra = asyncio.run(verify_extraction(text, extraction["nodes"], config))
            extraction["nodes"].extend(extra["nodes"])
            extraction["edges"].extend(extra["edges"])
        if decisions:
            extra = asyncio.run(extract_targeted(text, "decisions", extraction["nodes"], config))
            extraction["nodes"].extend(extra["nodes"])
            extraction["edges"].extend(extra["edges"])
        store = GraphStore(db_path)
        store.merge_extraction(extraction["nodes"], extraction["edges"])
        n = len(extraction["nodes"])
        e = len(extraction["edges"])
        store.close()
        print(f" {n} nodes, {e} edges [{time.time()-t0:.0f}s]")


# ---------------------------------------------------------------------------
# Main comparison logic
# ---------------------------------------------------------------------------

def run_comparison(
    config: dict,
    project_name: str,
    questions: list[dict],
    transcript_texts: dict[str, str],
    llm_cfg: dict,
) -> list[dict]:
    """For each question run CB and raw baseline and return result rows."""
    db_path = get_db_path(config, project_name)
    rows = []

    for q in questions:
        qid = q["id"]
        question_text = q["question"]
        transcript_name = q["transcript"]
        gt = q["ground_truth_elements"]
        raw_transcript = transcript_texts.get(transcript_name, "")

        # ------------------------------------------------------------------
        # CB approach: retrieve graph context → ask LLM
        # ------------------------------------------------------------------
        store = GraphStore(db_path)
        retrieval = retrieve_with_stats(
            store,
            question_text,
            hops=config.get("defaults", {}).get("hops", 3),
            top_k=config.get("defaults", {}).get("top_k", 10),
            strategies={
                "superseded_pruning": True,
                "confidence_threshold": 0.0,
                "recency_decay": False,
                "token_budget": 2000,
                "relevance_scoring": True,
            },
        )
        store.close()

        cb_context = retrieval.markdown or "_No relevant context found._"
        cb_system = _SYSTEM_CB + f"\n\n## Project Knowledge\n\n{cb_context}\n\n---"

        t0 = time.time()
        cb_answer, cb_input_tokens = asyncio.run(ask_llm(cb_system, question_text, llm_cfg))
        cb_elapsed = time.time() - t0
        cb_recall, cb_matched, cb_missed = score_recall(cb_answer, gt)

        # ------------------------------------------------------------------
        # Raw baseline: full transcript → ask LLM
        # ------------------------------------------------------------------
        # Truncate transcript to a reasonable context window (8k tokens ≈ 32k chars)
        _MAX_TRANSCRIPT_TOKENS = 8000
        transcript_tokens = estimate_tokens(raw_transcript)
        if transcript_tokens > _MAX_TRANSCRIPT_TOKENS:
            # Truncate at the character level (≈4 chars/token)
            max_chars = _MAX_TRANSCRIPT_TOKENS * 4
            truncated = raw_transcript[:max_chars]
            truncation_note = (
                f"\n\n[NOTE: Transcript truncated from ~{transcript_tokens} to "
                f"~{_MAX_TRANSCRIPT_TOKENS} tokens to fit context window]"
            )
            raw_system = _SYSTEM_RAW + truncated + truncation_note
        else:
            raw_system = _SYSTEM_RAW + raw_transcript

        t0 = time.time()
        raw_answer, raw_input_tokens = asyncio.run(ask_llm(raw_system, question_text, llm_cfg))
        raw_elapsed = time.time() - t0
        raw_recall, raw_matched, raw_missed = score_recall(raw_answer, gt)

        delta = cb_recall - raw_recall
        icon = "↑" if delta > 0.05 else ("↓" if delta < -0.05 else "=")

        rows.append({
            "id": qid,
            "question": question_text,
            "gt_count": len(gt),
            # CB
            "cb_recall": cb_recall,
            "cb_matched": cb_matched,
            "cb_missed": cb_missed,
            "cb_nodes": retrieval.nodes_after_strategies,
            "cb_tokens": cb_input_tokens,
            "cb_elapsed": round(cb_elapsed, 1),
            "cb_answer": cb_answer,
            # Raw
            "raw_recall": raw_recall,
            "raw_matched": raw_matched,
            "raw_missed": raw_missed,
            "raw_tokens": raw_input_tokens,
            "raw_elapsed": round(raw_elapsed, 1),
            "raw_answer": raw_answer,
            # Delta
            "delta": round(delta, 3),
            "icon": icon,
        })

        print(
            f"  {icon} {qid:14s}  "
            f"CB={cb_recall:.0%} ({cb_input_tokens:4d}tok)  "
            f"Raw={raw_recall:.0%} ({raw_input_tokens:4d}tok)  "
            f"Δ={delta:+.0%}"
        )

    return rows


def print_summary(rows: list[dict]) -> None:
    """Print a summary table and per-question answer excerpts."""
    cb_recalls = [r["cb_recall"] for r in rows]
    raw_recalls = [r["raw_recall"] for r in rows]
    cb_mean = sum(cb_recalls) / len(cb_recalls) if cb_recalls else 0
    raw_mean = sum(raw_recalls) / len(raw_recalls) if raw_recalls else 0
    cb_tokens_mean = sum(r["cb_tokens"] for r in rows) / len(rows) if rows else 0
    raw_tokens_mean = sum(r["raw_tokens"] for r in rows) / len(rows) if rows else 0

    print("\n" + "=" * 72)
    print(f"{'SUMMARY':^72}")
    print("=" * 72)
    print(f"{'':20s}  {'Waystone':>18s}  {'Raw Baseline':>18s}")
    print(f"{'─'*20}  {'─'*18}  {'─'*18}")
    print(f"{'Mean recall':20s}  {cb_mean:>17.0%}  {raw_mean:>17.0%}")
    print(f"{'≥80% questions':20s}  {sum(1 for r in cb_recalls if r>=0.8):>17d}  {sum(1 for r in raw_recalls if r>=0.8):>17d}  / {len(rows)}")
    print(f"{'Mean input tokens':20s}  {cb_tokens_mean:>17.0f}  {raw_tokens_mean:>17.0f}")
    token_reduction = 1 - cb_tokens_mean / raw_tokens_mean if raw_tokens_mean else 0
    print(f"{'Token reduction':20s}  {token_reduction:>17.0%}  {'(baseline)':>18s}")
    print()

    print(f"{'Question':<16s}  {'CB':>6s}  {'Raw':>6s}  {'Δ':>5s}  Missed (CB)")
    print(f"{'─'*16}  {'─'*6}  {'─'*6}  {'─'*5}  {'─'*30}")
    for r in rows:
        missed_str = ", ".join(r["cb_missed"][:2]) if r["cb_missed"] else "—"
        if len(r["cb_missed"]) > 2:
            missed_str += f" (+{len(r['cb_missed'])-2})"
        print(
            f"{r['id']:<16s}  {r['cb_recall']:>5.0%}  {r['raw_recall']:>5.0%}  "
            f"{r['delta']:>+4.0%}  {missed_str[:40]}"
        )

    print()

    # Print answer excerpts for questions where CB and raw diverge significantly
    divergent = [r for r in rows if abs(r["delta"]) >= 0.25]
    if divergent:
        print(f"\n{'─'*72}")
        print("DIVERGENT QUESTIONS (|Δ| ≥ 25%)")
        print(f"{'─'*72}")
        for r in divergent:
            print(f"\n▶ {r['id']}  (CB={r['cb_recall']:.0%}  Raw={r['raw_recall']:.0%})")
            print(f"  Q: {r['question']}")
            print(f"\n  [CB answer excerpt]")
            print("  " + r["cb_answer"][:400].replace("\n", "\n  "))
            print(f"\n  [Raw answer excerpt]")
            print("  " + r["raw_answer"][:400].replace("\n", "\n  "))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--transcript",
        default=None,
        help="Transcript stem to test (e.g. project_api_design). Default: all three.",
    )
    p.add_argument(
        "--questions",
        default="5",
        help="Number of questions to run per transcript (default: 5), or 'all'.",
    )
    p.add_argument(
        "--model",
        default=None,
        help="LiteLLM model string (e.g. gemini/gemini-2.5-flash). Overrides config.",
    )
    p.add_argument(
        "--verify",
        action="store_true",
        help="Run a second verification pass after extraction (improves recall, ~2x slower).",
    )
    p.add_argument(
        "--decisions",
        action="store_true",
        help="Run a targeted decisions pass after extraction (adds rationale/evolution nodes).",
    )
    p.add_argument(
        "--skip-extract",
        action="store_true",
        help="Skip extraction phase (reuse existing graph from a previous run).",
    )
    p.add_argument(
        "--config",
        default=None,
        help="Path to a model config YAML to merge (same format as benchmarks/model_configs/).",
    )
    return p.parse_args()


def main():
    args = parse_args()

    # Load base config
    config = load_config()

    # Optionally overlay a model config (same pattern as run_benchmark.py)
    if args.config:
        overlay = yaml.safe_load(Path(args.config).read_text())
        for k, v in overlay.items():
            if isinstance(v, dict) and isinstance(config.get(k), dict):
                config[k].update(v)
            else:
                config[k] = v

    # Model override
    llm_cfg = dict(config.get("llm", {}))
    if args.model:
        llm_cfg["model"] = args.model
    # If the model uses a LiteLLM provider prefix (e.g. gemini/..., anthropic/...),
    # strip base_url — it points to an OpenAI-compat endpoint and would break native routing.
    if "/" in llm_cfg.get("model", ""):
        llm_cfg.pop("base_url", None)
    # Keep output short — we're scoring not reading essays
    llm_cfg.setdefault("max_tokens", 512)
    llm_cfg.setdefault("temperature", 0.0)
    # Suppress LiteLLM verbose output
    llm_cfg["log_level"] = "WARNING"

    # Load eval questions
    all_questions = yaml.safe_load(EVAL_QUESTIONS_PATH.read_text())["questions"]

    # Determine which transcripts to test
    available = {p.stem: p for p in sorted(TRANSCRIPTS_DIR.glob("*.md"))}
    if args.transcript:
        if args.transcript not in available:
            print(f"ERROR: transcript {args.transcript!r} not found in {TRANSCRIPTS_DIR}")
            print(f"Available: {', '.join(available)}")
            sys.exit(1)
        selected_transcripts = {args.transcript: available[args.transcript]}
    else:
        selected_transcripts = available

    # Filter questions to selected transcripts
    questions = [q for q in all_questions if q["transcript"] in selected_transcripts]

    # Limit question count
    if args.questions.lower() != "all":
        n = int(args.questions)
        # Take n from each transcript (round-robin to keep coverage even)
        by_transcript: dict[str, list] = {}
        for q in questions:
            by_transcript.setdefault(q["transcript"], []).append(q)
        questions = []
        for t in selected_transcripts:
            questions.extend(by_transcript.get(t, [])[:n])

    if not questions:
        print("No matching questions found.")
        sys.exit(1)

    # Transcript texts (for raw baseline)
    transcript_texts = {stem: path.read_text() for stem, path in selected_transcripts.items()}

    # Temp project name for the comparison graph
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    project_name = f"__compare_{stamp}__"

    print(f"\nWaystone vs. Raw Baseline")
    print(f"Model : {llm_cfg.get('model', '(from config)')}")
    print(f"Questions : {len(questions)} across {len(selected_transcripts)} transcript(s)")
    print()

    # Extraction phase
    if not args.skip_extract:
        print("Extracting transcripts into temporary graph...")
        ensure_extracted(config, project_name, list(selected_transcripts.values()), verify=args.verify, decisions=args.decisions)
    else:
        print("(skipping extraction — using existing graph)")

    # Count nodes
    db_path = get_db_path(config, project_name)
    if db_path.exists():
        store = GraphStore(db_path)
        node_count = store.get_node_count() if hasattr(store, "get_node_count") else "?"
        store.close()
        print(f"Graph: {node_count} nodes\n")

    # Comparison phase
    print(f"{'Question':<16s}  {'CB':>6s}  {'Raw':>6s}  {'Δ':>5s}")
    print(f"{'─'*16}  {'─'*6}  {'─'*6}  {'─'*5}")

    rows = run_comparison(
        config=config,
        project_name=project_name,
        questions=questions,
        transcript_texts=transcript_texts,
        llm_cfg=llm_cfg,
    )

    print_summary(rows)

    # Cleanup temp project
    try:
        project_dir = get_project_dir(config, project_name)
        shutil.rmtree(project_dir, ignore_errors=True)
    except Exception:
        pass

    # Exit with non-zero if CB underperforms raw by more than 10% mean recall
    cb_mean = sum(r["cb_recall"] for r in rows) / len(rows)
    raw_mean = sum(r["raw_recall"] for r in rows) / len(rows)
    if cb_mean < raw_mean - 0.10:
        print(f"\n⚠  CB underperformed raw baseline by {raw_mean - cb_mean:.0%} — check graph quality.")
        sys.exit(1)


if __name__ == "__main__":
    main()
