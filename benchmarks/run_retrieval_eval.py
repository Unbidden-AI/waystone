#!/usr/bin/env python3
"""Durable Retrieval-Quality Benchmark Harness

This is the standard eval gate for every retrieval/strategy/extractor change.
It measures recall, node counts, and token efficiency against a fixed ground-truth set.

KEY TRUSTWORTHINESS FEATURES:
  - Prints waystone module import path at startup (verify it resolves to worktree)
  - Fails loudly if graphs are missing, empty, or questions don't map to projects
  - Reuses cached graphs by default; only extracts when missing
  - Self-check: baseline recall must land in documented ballpark (~85–95%)
  - Reports coverage: which questions were graded, any skipped + why
  - No silent fallbacks — every error is surfaced

STRATEGY VARIANTS:
  The harness supports comparing multiple retrieval strategy configurations.
  Built-in variants: baseline, default, filtered, tight.
  To add a new variant:
    1. Add entry to STRATEGY_PRESETS dict (see below)
    2. Run: python run_retrieval_eval.py --strategies baseline <newname>
  Config-level overrides (e.g., top_k, hops) are merged per-preset.

EXTENSION POINTS (NOT IMPLEMENTED, documented for future):
  - Large/production-scale graph eval: set projects_dir env var before import
  - Extractor-quality eval mode: seed extractor gate with known-good extraction,
    reuse, and measure retrieval deltas (traces extractor → retrieval coupling)

Usage:
    python benchmarks/run_retrieval_eval.py
    python benchmarks/run_retrieval_eval.py --strategies baseline default
    python benchmarks/run_retrieval_eval.py --top-k 20 --hops 2
    python benchmarks/run_retrieval_eval.py --skip-extract (reuse cached graphs)
    python benchmarks/run_retrieval_eval.py --self-check-only (import + sanity checks)

Exit codes:
    0 = success, baseline recall in ballpark
    1 = failure (missing graphs, bad config, baseline recall out of range)
"""

import argparse
import asyncio
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Trustworthiness: print and verify import path BEFORE any other waystone operations
import waystone
print(f"[IMPORT] waystone module: {waystone.__file__}")
print()

from waystone.config import get_db_path, get_project_dir, load_config
from waystone.extractor import extract, split_into_chunks
from waystone.retriever import retrieve_with_stats
from waystone.store import GraphStore

BENCHMARKS_DIR = Path(__file__).parent
TRANSCRIPTS_DIR = BENCHMARKS_DIR / "transcripts"
EVAL_QUESTIONS_PATH = BENCHMARKS_DIR / "eval_questions.yaml"
RESULTS_DIR = BENCHMARKS_DIR / "results"

# ─────────────────────────────────────────────────────────────────────────────
# Strategy Presets
# ─────────────────────────────────────────────────────────────────────────────
# Each preset is a dict of retriever strategy flags.
# Config-level overrides (hops, top_k) are applied on top.

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

# Documented baseline ballpark (from MEMORY.md / Gemini 2.5 Flash + verify)
BASELINE_BALLPARK_MIN = 0.80
BASELINE_BALLPARK_MAX = 0.96

# ─────────────────────────────────────────────────────────────────────────────
# Scoring (Grader)
# ─────────────────────────────────────────────────────────────────────────────

STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "and", "or", "for",
    "to", "of", "in", "on", "at", "by", "with", "not", "no", "due",
    "when", "than", "that", "this", "it", "its", "be", "as", "also",
    "from", "but", "so", "if", "we", "our", "they", "their",
}


def _keyword_variants(word: str) -> list[str]:
    """Return a word plus morphological variants to improve keyword matching."""
    variants = [word]
    if "-" in word:
        parts = [p for p in word.split("-") if len(p) > 2 and p not in STOP_WORDS]
        variants.extend(parts)
    if word.endswith("s") and len(word) > 3:
        variants.append(word[:-1])
    elif not word.endswith("s"):
        variants.append(word + "s")
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


# ─────────────────────────────────────────────────────────────────────────────
# Graph Extraction (cached by default)
# ─────────────────────────────────────────────────────────────────────────────

def load_eval_questions() -> list[dict]:
    """Load and validate eval questions from YAML."""
    if not EVAL_QUESTIONS_PATH.exists():
        raise FileNotFoundError(f"Eval questions file not found: {EVAL_QUESTIONS_PATH}")

    with open(EVAL_QUESTIONS_PATH) as f:
        data = yaml.safe_load(f)

    questions = data.get("questions", [])
    if not questions:
        raise ValueError(f"No questions found in {EVAL_QUESTIONS_PATH}")

    return questions


def get_transcripts_for_project(project_name: str) -> list[Path]:
    """Find all transcripts matching a project name (expect .md files)."""
    patterns = [f"{project_name}.md", f"{project_name}*.md"]
    transcripts = []
    for pattern in patterns:
        transcripts.extend(sorted(TRANSCRIPTS_DIR.glob(pattern)))
    if not transcripts:
        raise FileNotFoundError(
            f"No transcripts found for project {project_name!r} in {TRANSCRIPTS_DIR}"
        )
    return list(dict.fromkeys(transcripts))  # Deduplicate while preserving order


def extract_benchmark_graphs(config: dict, project_name: str) -> bool:
    """Extract benchmark graphs if not already present. Returns True if extraction happened.

    Raises:
        RuntimeError: if extraction fails
        FileNotFoundError: if transcripts not found
    """
    db_path = get_db_path(config, project_name)

    # Check if already extracted
    if db_path.exists():
        store = GraphStore(db_path)
        try:
            stats = store.get_stats()
            node_count = stats.get("node_count", 0)
            if node_count > 0:
                print(f"    ✓ {project_name}: {node_count} nodes (cached)")
                return False
        finally:
            store.close()

    # Find transcripts
    transcript_paths = get_transcripts_for_project(project_name)

    # Extract
    project_dir = get_project_dir(config, project_name)
    project_dir.mkdir(parents=True, exist_ok=True)

    print(f"    ↓ {project_name}: extracting {len(transcript_paths)} transcript(s)...", end="", flush=True)
    t0 = time.time()

    try:
        for transcript_path in transcript_paths:
            text = transcript_path.read_text(encoding="utf-8")

            # Split large transcripts into chunks
            _AUTO_CHUNK = 20_000
            chunk_size = config.get("llm", {}).get("chunk_size")
            effective_chunk_size = chunk_size or (_AUTO_CHUNK if len(text) > _AUTO_CHUNK else None)

            if effective_chunk_size and len(text) > effective_chunk_size:
                chunks = split_into_chunks(text, effective_chunk_size)
                async def extract_all():
                    results = await asyncio.gather(*[extract(c, config) for c in chunks])
                    merged_nodes, merged_edges = [], []
                    for r in results:
                        merged_nodes.extend(r["nodes"])
                        merged_edges.extend(r["edges"])
                    return {"nodes": merged_nodes, "edges": merged_edges}
                extraction = asyncio.run(extract_all())
            else:
                extraction = asyncio.run(extract(text, config))

            nodes = extraction["nodes"]
            edges = extraction["edges"]

            if not nodes:
                raise RuntimeError(f"Extraction returned 0 nodes for {transcript_path.name}")

            for node in nodes:
                node["source_transcript"] = transcript_path.name
                node.setdefault("created_at", datetime.now(timezone.utc).isoformat())

            store = GraphStore(db_path)
            store.merge_extraction(nodes, edges)
            store.close()

        elapsed = time.time() - t0
        store = GraphStore(db_path)
        stats = store.get_stats()
        final_count = stats.get("node_count", 0)
        store.close()

        print(f" {final_count} nodes [{elapsed:.1f}s]")
        return True

    except Exception as e:
        elapsed = time.time() - t0
        print(f" FAILED [{elapsed:.1f}s]: {e}")
        raise


def ensure_all_graphs_extracted(config: dict, questions: list[dict], skip_extract: bool = False) -> dict:
    """Ensure all project graphs needed by eval questions are present.

    Args:
        config: waystone config dict
        questions: list of eval question dicts
        skip_extract: if True, skip extraction phase entirely (reuse cached)

    Returns:
        dict: {project_name: node_count}

    Raises:
        RuntimeError: if any project is missing and extraction fails
    """
    # Collect unique projects referenced in questions
    projects = sorted({q["transcript"] for q in questions})

    if not projects:
        raise ValueError("No questions provided or questions reference no projects")

    print(f"[GRAPHS] Ensuring {len(projects)} project graph(s) exist")
    graph_stats = {}

    if skip_extract:
        # Verify cached graphs exist; fail if missing
        for project_name in projects:
            db_path = get_db_path(config, project_name)
            if not db_path.exists():
                raise FileNotFoundError(
                    f"Graph DB not found for {project_name!r} (--skip-extract requires cached graphs):\n"
                    f"  {db_path}"
                )
            store = GraphStore(db_path)
            stats = store.get_stats()
            count = stats.get("node_count", 0)
            if count == 0:
                raise RuntimeError(
                    f"Graph for {project_name!r} is empty (0 nodes)"
                )
            graph_stats[project_name] = count
            store.close()
            print(f"    ✓ {project_name}: {count} nodes (cached, verified)")
    else:
        # Extract or reuse cached
        for project_name in projects:
            try:
                extract_benchmark_graphs(config, project_name)
                db_path = get_db_path(config, project_name)
                store = GraphStore(db_path)
                stats = store.get_stats()
                count = stats.get("node_count", 0)
                graph_stats[project_name] = count
                store.close()
            except Exception as e:
                print(f"\n[ERROR] Failed to ensure graph for {project_name!r}:")
                print(f"  {e}")
                raise

    return graph_stats


# ─────────────────────────────────────────────────────────────────────────────
# Retrieval Evaluation
# ─────────────────────────────────────────────────────────────────────────────

def run_retrieval_eval(
    config: dict,
    questions: list[dict],
    strategy_presets: dict[str, dict],
    hops: int = 3,
    top_k: int = 30,
) -> dict:
    """Run retrieval evaluation for all questions against all strategy presets.

    Args:
        config: waystone config
        questions: list of eval question dicts
        strategy_presets: dict of {preset_name: strategies_dict}
        hops: BFS depth for retrieval
        top_k: max nodes per retrieval

    Returns:
        dict: {preset_name: {questions: [...], summary: {...}}}
    """
    all_results = {}
    config_strategies = config.get("strategies", {})

    for preset_name, base_strategies in strategy_presets.items():
        # Merge config overrides: config wins over preset
        merged_strategies = {**base_strategies}
        if preset_name in config_strategies:
            merged_strategies.update(config_strategies[preset_name])

        print(f"\n[{preset_name}]")
        preset_rows = []

        for q in questions:
            project = q["transcript"]
            db_path = get_db_path(config, project)

            if not db_path.exists():
                print(f"  ✗ {q['id']:12s}  SKIPPED (graph not found: {project})")
                continue

            store = GraphStore(db_path)
            t0 = time.time()
            result = retrieve_with_stats(
                store,
                q["question"],
                hops=hops,
                top_k=top_k,
                strategies=merged_strategies,
            )
            elapsed = time.time() - t0
            store.close()

            recall, matched, missed = score_recall(
                result.markdown, q["ground_truth_elements"]
            )

            icon = "✓" if recall >= 0.8 else ("~" if recall >= 0.5 else "✗")
            print(
                f"  {icon} {q['id']:12s}  recall={recall:.0%}  "
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

        recalls = [r["recall"] for r in preset_rows]
        mean_recall = sum(recalls) / len(recalls) if recalls else 0.0
        mean_nodes = (
            sum(r["nodes_after_strategies"] for r in preset_rows) / len(preset_rows)
            if preset_rows else 0
        )
        mean_tokens = (
            sum(r["tokens_estimated"] for r in preset_rows) / len(preset_rows)
            if preset_rows else 0
        )

        all_results[preset_name] = {
            "questions": preset_rows,
            "summary": {
                "mean_recall": round(mean_recall, 3),
                "mean_nodes": round(mean_nodes, 1),
                "mean_tokens": round(mean_tokens),
                "questions_80pct_plus": sum(1 for r in recalls if r >= 0.8),
                "questions_50pct_plus": sum(1 for r in recalls if r >= 0.5),
                "total_questions": len(recalls),
            },
        }

        s = all_results[preset_name]["summary"]
        print(
            f"\n  Summary: recall={s['mean_recall']:.0%}  "
            f"nodes={s['mean_nodes']:.1f}  tokens={s['mean_tokens']}  "
            f"≥80%: {s['questions_80pct_plus']}/{s['total_questions']}"
        )

    return all_results


# ─────────────────────────────────────────────────────────────────────────────
# Report Writing
# ─────────────────────────────────────────────────────────────────────────────

def write_results(data: dict, output_dir: Path) -> tuple[Path, Path]:
    """Write JSON and Markdown result reports.

    Returns:
        (json_path, md_path)
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "results.json"
    json_path.write_text(json.dumps(data, indent=2))

    lines = [
        "# Retrieval Quality Benchmark Report",
        "",
        f"**Timestamp:** {data['timestamp']}  ",
        f"**Hops:** {data.get('hops', 3)} | **Top-K:** {data.get('top_k', 30)}  ",
        f"**Strategies tested:** {', '.join(data['query'].keys())}  ",
        "",
        "## Graphs Used",
        "",
    ]

    for project, count in data.get("graphs", {}).items():
        lines.append(f"- {project}: {count} nodes")

    lines += [
        "",
        "## Retrieval Quality by Strategy",
        "",
    ]

    for preset_name, preset_data in data["query"].items():
        s = preset_data["summary"]
        lines += [
            f"### {preset_name}",
            "",
            f"**Mean recall:** {s['mean_recall']:.0%} | "
            f"**Avg nodes:** {s['mean_nodes']:.1f} | "
            f"**Avg tokens:** {s['mean_tokens']} | "
            f"**≥80% recall:** {s['questions_80pct_plus']}/{s['total_questions']}",
            "",
            "| Question ID | Transcript | Recall | Nodes | Tokens | Missed Elements |",
            "|-------------|-----------|--------|-------|--------|-----------------|",
        ]

        for q in preset_data["questions"]:
            missed_str = "; ".join(q["missed"][:2])
            if len(q["missed"]) > 2:
                missed_str += f" (+{len(q['missed']) - 2})"
            if not missed_str:
                missed_str = "—"
            lines.append(
                f"| {q['question_id']} | {q['transcript']} | "
                f"{q['recall']:.0%} | {q['nodes_after_strategies']} | "
                f"{q['tokens_estimated']} | {missed_str} |"
            )
        lines.append("")

    md_path = output_dir / "report.md"
    md_path.write_text("\n".join(lines))

    return json_path, md_path


# ─────────────────────────────────────────────────────────────────────────────
# Self-Check (Ballpark Validation)
# ─────────────────────────────────────────────────────────────────────────────

def check_baseline_ballpark(query_results: dict) -> tuple[bool, str]:
    """Verify baseline recall is in documented ballpark.

    Returns:
        (is_ok: bool, message: str)
    """
    baseline = query_results.get("baseline", {})
    summary = baseline.get("summary", {})
    mean_recall = summary.get("mean_recall", 0.0)

    if not baseline or not summary:
        return False, "Baseline preset not found in results"

    if mean_recall < BASELINE_BALLPARK_MIN:
        return False, (
            f"Baseline recall {mean_recall:.0%} is BELOW documented ballpark "
            f"({BASELINE_BALLPARK_MIN:.0%}–{BASELINE_BALLPARK_MAX:.0%}). "
            "Possible causes: graph extraction issue, retriever regression, config mismatch."
        )

    if mean_recall > BASELINE_BALLPARK_MAX:
        return False, (
            f"Baseline recall {mean_recall:.0%} is ABOVE documented ballpark "
            f"({BASELINE_BALLPARK_MIN:.0%}–{BASELINE_BALLPARK_MAX:.0%}). "
            "Possible causes: eval questions changed, extraction improved, or grader artifact."
        )

    return True, f"Baseline recall {mean_recall:.0%} is in ballpark ✓"


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage:")[1] if "Usage:" in __doc__ else "",
    )
    parser.add_argument(
        "--strategies",
        nargs="+",
        default=["baseline"],
        choices=list(STRATEGY_PRESETS.keys()),
        help="Strategy presets to evaluate (default: baseline)",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=30,
        help="Max nodes to retrieve per query (default: 30)",
    )
    parser.add_argument(
        "--hops",
        type=int,
        default=3,
        help="BFS depth for graph traversal (default: 3)",
    )
    parser.add_argument(
        "--skip-extract",
        action="store_true",
        help="Skip extraction phase; reuse cached graphs (fail if missing)",
    )
    parser.add_argument(
        "--self-check-only",
        action="store_true",
        help="Run import checks and sanity validation only; exit without eval",
    )
    args = parser.parse_args()

    # Load base config
    config = load_config()
    config["defaults"]["top_k"] = args.top_k
    config["defaults"]["hops"] = args.hops

    # Load eval questions
    print("[LOAD] Evaluation questions...")
    try:
        questions = load_eval_questions()
        print(f"  ✓ {len(questions)} questions loaded")
    except Exception as e:
        print(f"  ✗ {e}")
        sys.exit(1)

    print()

    # Self-check: ensure graphs exist and are non-empty
    print("[CHECK] Graph availability...")
    try:
        graph_stats = ensure_all_graphs_extracted(config, questions, skip_extract=args.skip_extract)
        for project, count in graph_stats.items():
            print(f"  ✓ {project}: {count} nodes")
    except Exception as e:
        print(f"  ✗ {e}")
        if args.self_check_only:
            sys.exit(1)
        else:
            sys.exit(1)

    print()

    if args.self_check_only:
        print("[DONE] Self-check passed ✓")
        return

    # Run retrieval evaluation
    print("[EVAL] Running retrieval evaluation...")
    presets_to_test = {k: STRATEGY_PRESETS[k] for k in args.strategies}
    query_results = run_retrieval_eval(
        config,
        questions,
        presets_to_test,
        hops=args.hops,
        top_k=args.top_k,
    )

    print()

    # Ballpark check
    print("[CHECK] Baseline recall ballpark...")
    is_ok, msg = check_baseline_ballpark(query_results)
    if is_ok:
        print(f"  ✓ {msg}")
    else:
        print(f"  ✗ {msg}")

    print()

    # Write results
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_label = f"retrieval_eval_{timestamp}"
    output_dir = RESULTS_DIR / run_label

    print(f"[WRITE] Results to {output_dir.relative_to(ROOT)}")
    full_data = {
        "timestamp": timestamp,
        "hops": args.hops,
        "top_k": args.top_k,
        "graphs": graph_stats,
        "query": query_results,
    }
    json_path, md_path = write_results(full_data, output_dir)
    print(f"  JSON:     {json_path.relative_to(ROOT)}")
    print(f"  Markdown: {md_path.relative_to(ROOT)}")

    print()

    # Final summary
    print("=" * 70)
    print(f"{'BASELINE SUMMARY':^70}")
    print("=" * 70)
    baseline = query_results.get("baseline", {})
    if baseline:
        s = baseline["summary"]
        print(f"  Recall (mean):     {s['mean_recall']:.0%}")
        print(f"  ≥80% questions:    {s['questions_80pct_plus']}/{s['total_questions']}")
        print(f"  Avg nodes:         {s['mean_nodes']:.1f}")
        print(f"  Avg tokens:        {s['mean_tokens']}")
    print()

    # Exit with error if ballpark check failed
    sys.exit(0 if is_ok else 1)


if __name__ == "__main__":
    main()
