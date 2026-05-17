#!/usr/bin/env python3
"""Waystone Benchmark Runner

Evaluates extraction quality and retrieval accuracy for a given model config.
Only the extraction phase calls the LLM — retrieval is pure local computation.

Usage:
    python benchmarks/run_benchmark.py
    python benchmarks/run_benchmark.py --config benchmarks/model_configs/qwen.yaml
    python benchmarks/run_benchmark.py --config benchmarks/model_configs/gemini.yaml
    python benchmarks/run_benchmark.py --presets baseline default
    python benchmarks/run_benchmark.py --keep-project
"""

import argparse
import asyncio
import json
import shutil
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from waystone.config import get_db_path, get_project_dir, load_config
from waystone.extractor import ExtractionBuffer, extract, extract_targeted, extract_turn, split_transcript_into_turns, split_into_chunks, verify_extraction
from waystone.retriever import (
    bfs_collect,
    extract_keywords,
    retrieve_with_stats,
    score_by_relevance,
)
from waystone.store import GraphStore

BENCHMARKS_DIR = Path(__file__).parent
TRANSCRIPTS_DIR = BENCHMARKS_DIR / "transcripts"
EVAL_QUESTIONS_PATH = BENCHMARKS_DIR / "eval_questions.yaml"
RESULTS_DIR = BENCHMARKS_DIR / "results"

# ---------------------------------------------------------------------------
# Strategy presets
# ---------------------------------------------------------------------------

STRATEGY_PRESETS = {
    "baseline": {
        "semantic": True,
        "superseded_pruning": False,
        "confidence_threshold": 0.0,
        "recency_decay": False,
        "token_budget": 0,
        "relevance_scoring": False,
    },
    "default": {
        "semantic": True,
        "superseded_pruning": True,
        "confidence_threshold": 0.0,
        "recency_decay": False,
        "token_budget": 0,
        "relevance_scoring": True,
    },
    "filtered": {
        "semantic": True,
        "superseded_pruning": True,
        "confidence_threshold": 0.6,
        "recency_decay": False,
        "token_budget": 0,
        "relevance_scoring": True,
    },
    "tight": {
        "semantic": True,
        "superseded_pruning": True,
        "confidence_threshold": 0.6,
        "recency_decay": False,
        "token_budget": 750,
        "relevance_scoring": True,
    },
}

# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "and", "or", "for",
    "to", "of", "in", "on", "at", "by", "with", "not", "no", "due",
    "when", "than", "that", "this", "it", "its", "be", "as", "also",
    "from", "but", "so", "if", "we", "our", "they", "their",
}


def _keyword_variants(word: str) -> list[str]:
    """Return a word plus morphological variants to improve keyword matching.

    Handles:
    - Hyphenated compounds: "ip-level" → also checks "ip" + "level" separately
    - Basic singular/plural: "minutes" ↔ "minute", "containers" ↔ "container"
    - Number+unit compounds: "15-minute" also matches "minutes"
    """
    variants = [word]
    if "-" in word:
        parts = [p for p in word.split("-") if len(p) > 2 and p not in STOP_WORDS]
        variants.extend(parts)
    # Singular ↔ plural
    if word.endswith("s") and len(word) > 3:
        variants.append(word[:-1])   # "minutes" → "minute"
    elif not word.endswith("s"):
        variants.append(word + "s")  # "minute" → "minutes"
    return variants


def score_recall(retrieved_markdown: str, ground_truth_elements: list) -> tuple:
    """Score recall: fraction of ground truth elements found in retrieved output.

    Checks full phrase first, then falls back to keyword overlap (>=70% of
    non-stop-words must be present). Keyword matching handles hyphenated
    compounds and basic singular/plural variants.

    Returns:
        (recall: float, matched: list[str], missed: list[str])
    """
    text = retrieved_markdown.lower()
    matched = []
    missed = []

    for element in ground_truth_elements:
        element_lower = element.lower()

        if element_lower in text:
            matched.append(element)
            continue

        # Keyword overlap fallback — expand each keyword to variants
        raw_words = [
            w.strip(".,;:()\"'—")
            for w in element_lower.split()
        ]
        key_words = [
            w for w in raw_words
            if w not in STOP_WORDS and len(w) > 2
        ]
        if key_words:
            found = sum(
                1 for w in key_words
                if any(v in text for v in _keyword_variants(w))
            )
            if found / len(key_words) >= 0.7:
                matched.append(element)
            else:
                missed.append(element)
        else:
            missed.append(element)

    recall = len(matched) / len(ground_truth_elements) if ground_truth_elements else 0.0
    return recall, matched, missed


# ---------------------------------------------------------------------------
# Extraction phase
# ---------------------------------------------------------------------------

def run_extraction(config: dict, project_name: str, transcripts: list, verify: bool = False, targeted: list | None = None) -> dict:
    """Extract all transcripts into the project graph. Returns per-transcript stats."""
    targeted = targeted or []
    results = {}
    db_path = get_db_path(config, project_name)

    project_dir = get_project_dir(config, project_name)
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "transcripts").mkdir(exist_ok=True)
    (project_dir / "exports").mkdir(exist_ok=True)

    for transcript_path in transcripts:
        transcript_text = transcript_path.read_text()
        name = transcript_path.stem

        print(f"  Extracting {transcript_path.name} ...", end="", flush=True)
        t0 = time.time()

        try:
            _AUTO_CHUNK = 20_000
            chunk_size = config.get("llm", {}).get("chunk_size")
            effective_chunk_size = chunk_size or (_AUTO_CHUNK if len(transcript_text) > _AUTO_CHUNK else None)
            if effective_chunk_size and len(transcript_text) > effective_chunk_size:
                chunk_size = effective_chunk_size
            if chunk_size and len(transcript_text) > chunk_size:
                chunks = split_into_chunks(transcript_text, chunk_size)
                print(f" [{len(chunks)} chunks]", end="", flush=True)

                async def extract_all_chunks():
                    results = await asyncio.gather(*[extract(c, config) for c in chunks])
                    merged_nodes, merged_edges = [], []
                    for r in results:
                        merged_nodes.extend(r["nodes"])
                        merged_edges.extend(r["edges"])
                    return {"nodes": merged_nodes, "edges": merged_edges}

                extraction = asyncio.run(extract_all_chunks())
            else:
                extraction = asyncio.run(extract(transcript_text, config))
            nodes = extraction["nodes"]
            edges = extraction["edges"]

            if verify or targeted:
                print(f" [{len(nodes)} nodes pass1]", end="", flush=True)
            if verify:
                verify_result = asyncio.run(verify_extraction(transcript_text, nodes, config))
                extra_nodes = verify_result["nodes"]
                extra_edges = verify_result["edges"]
                print(f" [+{len(extra_nodes)} pass2]", end="", flush=True)
                nodes = nodes + extra_nodes
                edges = edges + extra_edges
            if targeted:
                async def _run_targeted(cats, snap_nodes, snap_edges):
                    results = await asyncio.gather(
                        *[extract_targeted(transcript_text, cat, snap_nodes, config) for cat in cats],
                        return_exceptions=True,
                    )
                    out_nodes, out_edges = list(snap_nodes), list(snap_edges)
                    for cat, tr in zip(cats, results):
                        if isinstance(tr, Exception):
                            print(f" [--{cat} FAILED: {tr}]", end="", flush=True)
                        else:
                            print(f" [+{len(tr['nodes'])} {cat}]", end="", flush=True)
                            out_nodes = out_nodes + tr["nodes"]
                            out_edges = out_edges + tr["edges"]
                    return out_nodes, out_edges
                nodes, edges = asyncio.run(_run_targeted(targeted, nodes, edges))

            elapsed = time.time() - t0

            for node in nodes:
                node["source_transcript"] = transcript_path.name
                node.setdefault("created_at", datetime.now(timezone.utc).isoformat())

            store = GraphStore(db_path)
            store.merge_extraction(nodes, edges)
            store.close()

            print(f" {len(nodes)} nodes, {len(edges)} edges  [{elapsed:.1f}s]")
            results[name] = {
                "status": "ok",
                "nodes": len(nodes),
                "edges": len(edges),
                "elapsed_s": round(elapsed, 2),
            }

        except Exception as e:
            elapsed = time.time() - t0
            msg = str(e) or repr(e)
            tb = traceback.format_exc()
            print(f" FAILED ({type(e).__name__}): {msg}")
            print(tb)
            results[name] = {
                "status": "error",
                "error": msg,
                "traceback": tb,
                "elapsed_s": round(elapsed, 2),
            }

    return results


# ---------------------------------------------------------------------------
# Incremental extraction phase
# ---------------------------------------------------------------------------

def run_extraction_incremental(config: dict, project_name: str, transcripts: list, turn_size: int = 2) -> dict:
    """Extract all transcripts turn-by-turn using incremental extraction.

    Each turn is given the relevant subgraph built so far as context,
    enabling cross-turn supersedes edges and avoiding re-extraction.
    Returns per-transcript stats including LLM call count and cross-turn edges.
    """
    results = {}
    db_path = get_db_path(config, project_name)

    project_dir = get_project_dir(config, project_name)
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "transcripts").mkdir(exist_ok=True)
    (project_dir / "exports").mkdir(exist_ok=True)

    inc_cfg = config.get("incremental", {})
    ctx_k = inc_cfg.get("context_k", 30)
    ctx_hops = inc_cfg.get("context_hops", 2)

    for transcript_path in transcripts:
        transcript_text = transcript_path.read_text()
        name = transcript_path.stem
        turns = split_transcript_into_turns(transcript_text, turn_size)

        print(f"  Extracting {transcript_path.name} ({len(turns)} turns) ...", flush=True)
        t0 = time.time()

        total_nodes = 0
        total_edges = 0
        total_cross_turn = 0
        llm_calls = 0

        try:
            for turn_text in turns:
                # Gather context from graph built so far
                store = GraphStore(db_path)
                context_nodes = []
                keywords = extract_keywords(turn_text)
                if keywords:
                    entry_nodes = store.get_nodes_by_tags(keywords)
                    if entry_nodes:
                        entry_nodes = score_by_relevance(entry_nodes, keywords)
                        context_nodes = bfs_collect(store, entry_nodes, ctx_hops)
                        context_nodes.sort(key=lambda n: n.get("_relevance", 0), reverse=True)
                        context_nodes = context_nodes[:ctx_k]
                store.close()

                existing_ids = {n["id"] for n in context_nodes}

                extraction = asyncio.run(extract_turn(turn_text, context_nodes, config))
                llm_calls += 1

                nodes = extraction["nodes"]
                edges = extraction["edges"]

                for node in nodes:
                    node["source_transcript"] = transcript_path.name
                    node.setdefault("created_at", datetime.now(timezone.utc).isoformat())

                cross_turn = sum(
                    1 for e in edges if e["from_id"] in existing_ids or e["to_id"] in existing_ids
                )

                store = GraphStore(db_path)
                store.merge_extraction(nodes, edges)
                store.close()

                total_nodes += len(nodes)
                total_edges += len(edges)
                total_cross_turn += cross_turn

            elapsed = time.time() - t0
            print(
                f"    -> {total_nodes} nodes, {total_edges} edges, "
                f"{total_cross_turn} cross-turn, {llm_calls} LLM calls  [{elapsed:.1f}s]"
            )
            results[name] = {
                "status": "ok",
                "nodes": total_nodes,
                "edges": total_edges,
                "elapsed_s": round(elapsed, 2),
                "llm_calls": llm_calls,
                "cross_turn_edges": total_cross_turn,
                "turns": len(turns),
            }

        except Exception as e:
            elapsed = time.time() - t0
            msg = str(e) or repr(e)
            tb = traceback.format_exc()
            print(f"    FAILED ({type(e).__name__}): {msg}")
            print(tb)
            results[name] = {
                "status": "error",
                "error": msg,
                "traceback": tb,
                "elapsed_s": round(elapsed, 2),
            }

    return results


# ---------------------------------------------------------------------------
# Buffered extraction phase
# ---------------------------------------------------------------------------

def run_extraction_buffered(config: dict, project_name: str, transcripts: list, turn_size: int = 2) -> dict:
    """Extract transcripts turn-by-turn with smart buffering.

    Turns accumulate in an ExtractionBuffer and are flushed to the LLM only
    when the buffer threshold is met (≥3 turns AND >200 words) or forced at
    the end of each transcript. Reports LLM calls vs total turns as an
    efficiency metric.
    """
    results = {}
    db_path = get_db_path(config, project_name)

    project_dir = get_project_dir(config, project_name)
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "transcripts").mkdir(exist_ok=True)
    (project_dir / "exports").mkdir(exist_ok=True)

    inc_cfg = config.get("incremental", {})
    ctx_k = inc_cfg.get("context_k", 30)
    ctx_hops = inc_cfg.get("context_hops", 2)

    for transcript_path in transcripts:
        transcript_text = transcript_path.read_text()
        name = transcript_path.stem
        turns = split_transcript_into_turns(transcript_text, turn_size)

        print(f"  Extracting {transcript_path.name} ({len(turns)} turns, buffered) ...", flush=True)
        t0 = time.time()

        total_nodes = 0
        total_edges = 0
        total_cross_turn = 0
        llm_calls = 0
        buffer = ExtractionBuffer()

        def _flush_buffer(flush_text: str):
            nonlocal total_nodes, total_edges, total_cross_turn, llm_calls
            store = GraphStore(db_path)
            keywords = extract_keywords(flush_text)
            context_nodes = []
            if keywords:
                entry_nodes = store.get_nodes_by_tags(keywords)
                if entry_nodes:
                    entry_nodes = score_by_relevance(entry_nodes, keywords)
                    context_nodes = bfs_collect(store, entry_nodes, ctx_hops)
                    context_nodes.sort(key=lambda n: n.get("_relevance", 0), reverse=True)
                    context_nodes = context_nodes[:ctx_k]
            store.close()

            existing_ids = {n["id"] for n in context_nodes}
            extraction = asyncio.run(extract_turn(flush_text, context_nodes, config))
            llm_calls += 1

            nodes = extraction["nodes"]
            edges = extraction["edges"]
            for node in nodes:
                node["source_transcript"] = transcript_path.name
                node.setdefault("created_at", datetime.now(timezone.utc).isoformat())

            cross_turn = sum(
                1 for e in edges if e["from_id"] in existing_ids or e["to_id"] in existing_ids
            )

            store = GraphStore(db_path)
            store.merge_extraction(nodes, edges)
            store.close()

            total_nodes += len(nodes)
            total_edges += len(edges)
            total_cross_turn += cross_turn

        try:
            for turn_text in turns:
                if buffer.add(turn_text):
                    _flush_buffer(buffer.flush())

            remainder = buffer.flush_if_nonempty()
            if remainder:
                _flush_buffer(remainder)

            elapsed = time.time() - t0
            efficiency = f"{llm_calls}/{len(turns)} calls/turns"
            print(
                f"    -> {total_nodes} nodes, {total_edges} edges, "
                f"{total_cross_turn} cross-turn, {efficiency}  [{elapsed:.1f}s]"
            )
            results[name] = {
                "status": "ok",
                "nodes": total_nodes,
                "edges": total_edges,
                "elapsed_s": round(elapsed, 2),
                "llm_calls": llm_calls,
                "turns": len(turns),
                "cross_turn_edges": total_cross_turn,
            }

        except Exception as e:
            elapsed = time.time() - t0
            msg = str(e) or repr(e)
            tb = traceback.format_exc()
            print(f"    FAILED ({type(e).__name__}): {msg}")
            print(tb)
            results[name] = {
                "status": "error",
                "error": msg,
                "traceback": tb,
                "elapsed_s": round(elapsed, 2),
            }

    return results


# ---------------------------------------------------------------------------
# Query / evaluation phase
# ---------------------------------------------------------------------------

def run_queries(config: dict, project_name: str, questions: list, presets: dict) -> dict:
    """Run all eval questions against all strategy presets. Returns nested results.

    No LLM calls here — retrieval is pure local SQLite + Python.

    Merges config-level strategy overrides with presets:
    - config["strategies"][preset_name] overrides values in STRATEGY_PRESETS[preset_name]
    """
    db_path = get_db_path(config, project_name)
    all_results = {}

    # Read per-strategy config overrides (e.g., semantic: false for Flash-Lite)
    config_strategies = config.get("strategies", {})

    for preset_name, strategies in presets.items():
        # Merge config overrides: config wins over preset
        merged_strategies = {**strategies}
        if preset_name in config_strategies:
            merged_strategies.update(config_strategies[preset_name])

        print(f"\n  [{preset_name}]")
        preset_rows = []

        store = GraphStore(db_path)

        for q in questions:
            t0 = time.time()
            result = retrieve_with_stats(
                store,
                q["question"],
                hops=config.get("defaults", {}).get("hops", 3),
                top_k=config.get("defaults", {}).get("top_k", 10),
                strategies=merged_strategies,
            )
            elapsed = time.time() - t0

            recall, matched, missed = score_recall(
                result.markdown, q["ground_truth_elements"]
            )

            icon = "✓" if recall >= 0.8 else ("~" if recall >= 0.5 else "✗")
            print(
                f"    {icon} {q['id']:12s}  recall={recall:.0%}  "
                f"nodes={result.nodes_after_strategies:2d}  "
                f"tokens={result.tokens_estimated:4d}  "
                f"({elapsed * 1000:.0f}ms)"
            )

            preset_rows.append({
                "question_id": q["id"],
                "transcript": q["transcript"],
                "question": q["question"],
                "recall": round(recall, 3),
                "matched": matched,
                "missed": missed,
                "retrieved_context": result.markdown,
                "nodes_before_strategies": result.nodes_before_strategies,
                "nodes_after_strategies": result.nodes_after_strategies,
                "tokens_estimated": result.tokens_estimated,
                "strategies_applied": result.strategies_applied,
                "elapsed_ms": round(elapsed * 1000, 1),
            })

        store.close()

        recalls = [r["recall"] for r in preset_rows]
        mean_recall = sum(recalls) / len(recalls) if recalls else 0.0
        mean_tokens = (
            sum(r["tokens_estimated"] for r in preset_rows) / len(preset_rows)
            if preset_rows else 0
        )

        all_results[preset_name] = {
            "questions": preset_rows,
            "summary": {
                "mean_recall": round(mean_recall, 3),
                "mean_tokens": round(mean_tokens),
                "questions_80pct_plus": sum(1 for r in recalls if r >= 0.8),
                "questions_50pct_plus": sum(1 for r in recalls if r >= 0.5),
                "total_questions": len(recalls),
            },
        }

        s = all_results[preset_name]["summary"]
        print(
            f"\n    Summary: recall={s['mean_recall']:.0%}  "
            f"avg_tokens={s['mean_tokens']}  "
            f"≥80%: {s['questions_80pct_plus']}/{s['total_questions']}"
        )

    return all_results


# ---------------------------------------------------------------------------
# Report writing
# ---------------------------------------------------------------------------

def write_report(data: dict, output_dir: Path) -> tuple:
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "results.json"
    json_path.write_text(json.dumps(data, indent=2))

    show_llm_calls = any(
        r.get("llm_calls") is not None for r in data["extraction"].values()
    )

    lines = [
        "# Benchmark Report",
        "",
        f"**Model:** `{data['model']}`  ",
        f"**Config:** `{data['config_file']}`  ",
        f"**Mode:** {data.get('mode', 'full')}  ",
        f"**Stack runs:** {data.get('stack_runs', 1)}  ",
        f"**Run:** {data['timestamp']}  ",
        "",
        "## Extraction",
        "",
    ]

    if show_llm_calls:
        lines += [
            "| Transcript | Status | Nodes | Edges | LLM Calls | Turns | Time (s) |",
            "|-----------|--------|-------|-------|-----------|-------|----------|",
        ]
    else:
        lines += [
            "| Transcript | Status | Nodes | Edges | Time (s) |",
            "|-----------|--------|-------|-------|----------|",
        ]

    for name, r in data["extraction"].items():
        if r["status"] == "ok":
            if show_llm_calls:
                calls = r.get("llm_calls", "—")
                turns = r.get("turns", "—")
                lines.append(
                    f"| {name} | ✓ | {r['nodes']} | {r['edges']} | {calls} | {turns} | {r['elapsed_s']} |"
                )
            else:
                lines.append(
                    f"| {name} | ✓ | {r['nodes']} | {r['edges']} | {r['elapsed_s']} |"
                )
        else:
            if show_llm_calls:
                lines.append(f"| {name} | ✗ `{r['error'][:40]}` | — | — | — | — | {r['elapsed_s']} |")
            else:
                lines.append(f"| {name} | ✗ `{r['error'][:40]}` | — | — | {r['elapsed_s']} |")

    ok = [r for r in data["extraction"].values() if r["status"] == "ok"]
    total_nodes = sum(r["nodes"] for r in ok)
    total_time = sum(r["elapsed_s"] for r in data["extraction"].values())
    lines += [
        "",
        f"**Total nodes:** {total_nodes}  ",
        f"**Total extraction time:** {total_time:.1f}s  ",
        "",
        "## Retrieval Quality by Strategy",
        "",
    ]

    for preset_name, preset_data in data["query"].items():
        s = preset_data["summary"]
        lines += [
            f"### {preset_name}",
            "",
            f"Mean recall: **{s['mean_recall']:.0%}** | "
            f"Avg tokens: **{s['mean_tokens']}** | "
            f"≥80% recall: **{s['questions_80pct_plus']}/{s['total_questions']}**",
            "",
            "| ID | Recall | Nodes | Tokens | Missed elements |",
            "|----|--------|-------|--------|-----------------|",
        ]
        for q in preset_data["questions"]:
            missed_str = "; ".join(q["missed"][:2])
            if len(q["missed"]) > 2:
                missed_str += f" (+{len(q['missed']) - 2} more)"
            lines.append(
                f"| {q['question_id']} | {q['recall']:.0%} | "
                f"{q['nodes_after_strategies']} | {q['tokens_estimated']} | {missed_str} |"
            )
        lines.append("")

    md_path = output_dir / "report.md"
    md_path.write_text("\n".join(lines))

    return json_path, md_path


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Run Waystone extraction + retrieval benchmark",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--config",
        default=str(BENCHMARKS_DIR / "model_configs" / "qwen.yaml"),
        help="Model config YAML (default: qwen.yaml)",
    )
    parser.add_argument(
        "--presets",
        nargs="+",
        choices=list(STRATEGY_PRESETS.keys()),
        default=list(STRATEGY_PRESETS.keys()),
        help="Strategy presets to evaluate (default: all)",
    )
    parser.add_argument(
        "--keep-project",
        action="store_true",
        help="Keep the temporary benchmark project after run",
    )
    parser.add_argument(
        "--mode",
        choices=["full", "incremental", "buffered"],
        default="full",
        help="Extraction mode: 'full' (one call/transcript), 'incremental' (every turn), 'buffered' (smart flush)",
    )
    parser.add_argument(
        "--turn-size",
        type=int,
        default=2,
        help="Messages per turn for incremental mode (default: 2)",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Run a second verification pass after extraction to catch missed facts",
    )
    parser.add_argument(
        "--lessons",
        action="store_true",
        help="Run a targeted pass hunting for lesson_learned nodes (failed approaches, rejected alternatives)",
    )
    parser.add_argument(
        "--decisions",
        action="store_true",
        help="Run a targeted pass hunting for decision nodes and their rationale",
    )
    parser.add_argument(
        "--questions",
        action="store_true",
        help="Run a targeted pass hunting for open questions and unresolved items",
    )
    parser.add_argument(
        "--constraints",
        action="store_true",
        help="Run a targeted pass hunting for hard constraints and requirements",
    )
    parser.add_argument(
        "--numerics",
        action="store_true",
        help="Run a targeted pass hunting for numeric values, measurements, and quantified facts",
    )
    parser.add_argument(
        "--decisions-constraints-tradeoffs",
        action="store_true",
        help="Run a focused pass hunting for decisions, constraints, and tradeoffs in a single integrated prompt",
    )
    parser.add_argument(
        "--stack-runs",
        type=int,
        default=1,
        metavar="N",
        help="Extract each transcript N times into the same project before evaluating (default: 1)",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Error: config not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    config = load_config(config_path)
    model_name = config["llm"]["model"]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    project_name = f"bench_{timestamp}"

    # Store benchmark projects under results/ so they don't pollute the main projects dir
    config["projects_dir"] = str(RESULTS_DIR / "projects")

    transcripts = sorted(TRANSCRIPTS_DIR.glob("*.md"))
    with open(EVAL_QUESTIONS_PATH) as f:
        questions = yaml.safe_load(f)["questions"]
    presets = {k: STRATEGY_PRESETS[k] for k in args.presets}

    print(f"\n{'='*60}")
    print(f"  Waystone Benchmark")
    print(f"  Model:       {model_name}")
    print(f"  Config:      {config_path.name}")
    print(f"  Transcripts: {len(transcripts)}")
    print(f"  Questions:   {len(questions)}")
    print(f"  Presets:     {', '.join(presets.keys())}")
    print(f"  Mode:        {args.mode}")
    if args.mode in ("incremental", "buffered"):
        print(f"  Turn size:   {args.turn_size}")
    print(f"  Verify pass: {'yes' if args.verify else 'no'}")
    _targeted_categories = [
        c for c, flag in [("lessons", args.lessons), ("decisions", args.decisions),
                          ("questions", args.questions), ("constraints", args.constraints),
                          ("numerics", args.numerics), ("decisions_constraints_tradeoffs", args.decisions_constraints_tradeoffs)]
        if flag
    ]
    if _targeted_categories:
        print(f"  Targeted:    {', '.join(_targeted_categories)}")
    if args.stack_runs > 1:
        print(f"  Stack runs:  {args.stack_runs}")
    print(f"{'='*60}\n")

    # Phase 1: Extraction (calls LLM)
    print("── Phase 1: Extraction (LLM calls) ──")
    t0 = time.time()
    for run_i in range(args.stack_runs):
        if args.stack_runs > 1:
            print(f"\n  [Stack run {run_i + 1}/{args.stack_runs}]")
        if args.mode == "incremental":
            extraction_results = run_extraction_incremental(
                config, project_name, transcripts, turn_size=args.turn_size
            )
        elif args.mode == "buffered":
            extraction_results = run_extraction_buffered(
                config, project_name, transcripts, turn_size=args.turn_size
            )
        else:
            extraction_results = run_extraction(config, project_name, transcripts, verify=args.verify, targeted=_targeted_categories)
    extraction_time = time.time() - t0
    print(f"\nExtraction done in {extraction_time:.1f}s\n")

    # Embed all nodes so semantic search contributes to retrieval during evaluation.
    # This is a no-op when sentence-transformers / sqlite-vec is unavailable.
    try:
        from waystone import embedder
        if embedder.is_available():
            _store = GraphStore(get_db_path(config, project_name))
            if _store._vec_available:
                print("── Embedding nodes for semantic search ──")
                t_emb = time.time()
                n_embedded = _store.embed_missing_nodes()
                print(f"  Embedded {n_embedded} nodes in {time.time() - t_emb:.1f}s\n")
            _store.close()
    except Exception as _emb_err:
        print(f"  Embedding skipped: {_emb_err}\n")

    # Phase 2: Query evaluation (local only, no LLM)
    print("── Phase 2: Retrieval Evaluation (local, no LLM) ──")
    t0 = time.time()
    query_results = run_queries(config, project_name, questions, presets)
    query_time = time.time() - t0
    print(f"\nQuery evaluation done in {query_time:.1f}s\n")

    # Write results
    run_label = f"{config_path.stem}_{timestamp}"
    output_dir = RESULTS_DIR / run_label

    full_data = {
        "model": model_name,
        "config_file": config_path.name,
        "timestamp": timestamp,
        "mode": args.mode,
        "verify": args.verify,
        "stack_runs": args.stack_runs,
        "extraction": extraction_results,
        "query": query_results,
        "timing": {
            "extraction_total_s": round(extraction_time, 2),
            "query_total_s": round(query_time, 2),
            "total_s": round(extraction_time + query_time, 2),
        },
    }

    json_path, md_path = write_report(full_data, output_dir)

    # Cleanup temp project
    if not args.keep_project:
        project_dir = Path(config["projects_dir"]) / project_name
        if project_dir.exists():
            shutil.rmtree(project_dir)

    # Final summary
    print(f"── Results ──")
    print(f"  JSON:     {json_path.relative_to(ROOT)}")
    print(f"  Markdown: {md_path.relative_to(ROOT)}\n")

    print(f"{'='*60}")
    print(f"  FINAL SUMMARY  ({model_name})")
    print(f"{'='*60}")
    for preset_name, preset_data in query_results.items():
        s = preset_data["summary"]
        print(
            f"  {preset_name:10s}  recall={s['mean_recall']:.0%}  "
            f"avg_tokens={s['mean_tokens']:4d}  "
            f"≥80%: {s['questions_80pct_plus']:2d}/{s['total_questions']}"
        )
    print()


if __name__ == "__main__":
    main()
