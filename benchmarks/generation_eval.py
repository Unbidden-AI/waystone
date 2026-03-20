#!/usr/bin/env python3
"""Generation Quality Eval — three-way comparison of LLM answer quality.

Extracts a transcript into a temp project, then for each selected question asks
the LLM three ways and grades each answer against ground_truth_elements:
  1. CB context   — CB-retrieved graph subgraph (~1-2K tokens)
  2. Full transcript — raw transcript stuffed into system prompt (naive upper bound)
  3. No context   — parametric knowledge only (lower bound)

Usage:
    python benchmarks/generation_eval.py
    python benchmarks/generation_eval.py --config benchmarks/model_configs/gemini_25_flash.yaml
    python benchmarks/generation_eval.py --transcript project_api_design --questions q_api_01 q_api_02 q_api_03
    python benchmarks/generation_eval.py --project /path/to/existing/context.db  # skip extraction
    python benchmarks/generation_eval.py --verify
    python benchmarks/generation_eval.py --no-full-transcript  # skip full-transcript condition
"""

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from context_broker.config import get_db_path, get_project_dir, load_config
from context_broker.extractor import extract, verify_extraction, extract_targeted
from context_broker.cli import _split_at_paragraphs
from context_broker.retriever import retrieve_with_stats
from context_broker.store import GraphStore

BENCHMARKS_DIR = Path(__file__).parent
TRANSCRIPTS_DIR = BENCHMARKS_DIR / "transcripts"
EVAL_QUESTIONS_PATH = BENCHMARKS_DIR / "eval_questions.yaml"

# ---------------------------------------------------------------------------
# Scoring (same logic as run_benchmark.py)
# ---------------------------------------------------------------------------

STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "and", "or", "for",
    "to", "of", "in", "on", "at", "by", "with", "not", "no", "due",
    "when", "than", "that", "this", "it", "its", "be", "as", "also",
    "from", "but", "so", "if", "we", "our", "they", "their",
}


def _element_matched(text_lower: str, element: str) -> bool:
    """Return True if the ground truth element is semantically covered by the answer.

    Handles compound elements like:
      "PII scrubbing (mask emails and phone numbers)"
      "Redis rejected for Flink — network overhead added 20ms per event"

    Strategy:
    1. Verbatim substring match on the full element.
    2. Split at " — " and check each clause independently (all must match).
    3. Strip parenthetical into a separate clause and check main text + each clause.
    4. Keyword-overlap fallback (≥60% of non-stop-words must be present).
    """
    el_lower = element.lower()

    # 1. Exact substring
    if el_lower in text_lower:
        return True

    # 2. Split at em-dash " — ": all clauses must be keyword-matched
    clauses = [c.strip() for c in el_lower.split(" — ") if c.strip()]
    if len(clauses) > 1:
        return all(_clause_keyword_match(text_lower, c, threshold=0.6) for c in clauses)

    # 3. Extract parenthetical as extra clause
    import re
    paren_match = re.search(r"\(([^)]+)\)", el_lower)
    if paren_match:
        main = re.sub(r"\([^)]*\)", "", el_lower).strip()
        paren = paren_match.group(1)
        return (
            _clause_keyword_match(text_lower, main, threshold=0.6)
            and _clause_keyword_match(text_lower, paren, threshold=0.6)
        )

    # 4. Keyword overlap fallback
    return _clause_keyword_match(text_lower, el_lower, threshold=0.6)


def _clause_keyword_match(text_lower: str, clause: str, threshold: float = 0.6) -> bool:
    """Return True if ≥threshold fraction of clause keywords appear in text."""
    keywords = [
        w.strip(".,;:()\"'—")
        for w in clause.split()
        if w.strip(".,;:()\"'—") not in STOP_WORDS and len(w.strip(".,;:()\"'—")) > 2
    ]
    if not keywords:
        return True  # nothing to check
    return sum(1 for w in keywords if w in text_lower) / len(keywords) >= threshold


def score_recall(text: str, ground_truth_elements: list) -> tuple:
    """Returns (recall: float, matched: list, missed: list)."""
    text_lower = text.lower()
    matched, missed = [], []

    for element in ground_truth_elements:
        if _element_matched(text_lower, element):
            matched.append(element)
        else:
            missed.append(element)

    recall = len(matched) / len(ground_truth_elements) if ground_truth_elements else 0.0
    return recall, matched, missed


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

def call_llm_sync(question: str, context_markdown: str | None, config: dict, context_label: str = "Project Knowledge") -> str:
    """Call LLM with or without context and return the answer string."""
    import litellm
    import logging
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

    if context_markdown:
        system = (
            "You are a knowledgeable assistant. "
            "Use the project context below to answer the question accurately and concisely.\n\n"
            f"## {context_label}\n\n"
            + context_markdown
            + "\n\n---"
        )
    else:
        system = (
            "You are a knowledgeable assistant. "
            "Answer the question as best you can. "
            "If you don't know, say so clearly — do not guess."
        )

    model = llm_cfg.get("model", "anthropic/claude-sonnet-4-6")
    # For Google API, strip "models/" prefix since we provide the OpenAI-compatible endpoint
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
        # Use custom provider to force OpenAI-compatible endpoint
        kwargs["custom_llm_provider"] = "openai"

    response = litellm.completion(**kwargs)
    return response.choices[0].message.content or ""


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def run_extraction(config: dict, project_name: str, transcript_path: Path, verify: bool, targeted: list[str] | None = None) -> int:
    """Extract transcript into project. Returns node count."""
    transcript_text = transcript_path.read_text()

    project_dir = get_project_dir(config, project_name)
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "transcripts").mkdir(exist_ok=True)
    (project_dir / "exports").mkdir(exist_ok=True)

    _AUTO_CHUNK = 20_000
    chunk_size = config.get("llm", {}).get("chunk_size")
    if not chunk_size and len(transcript_text) > _AUTO_CHUNK:
        chunk_size = _AUTO_CHUNK

    print(f"  Extracting {transcript_path.name}", end="", flush=True)
    t0 = time.time()

    if chunk_size and len(transcript_text) > chunk_size:
        chunks = _split_at_paragraphs(transcript_text, chunk_size)
        print(f" [{len(chunks)} chunks]", end="", flush=True)

        async def extract_chunks():
            results = await asyncio.gather(*[extract(c, config) for c in chunks])
            nodes, edges = [], []
            for r in results:
                nodes.extend(r["nodes"])
                edges.extend(r["edges"])
            return {"nodes": nodes, "edges": edges}

        extraction = asyncio.run(extract_chunks())
    else:
        extraction = asyncio.run(extract(transcript_text, config))

    nodes, edges = extraction["nodes"], extraction["edges"]

    if verify and nodes:
        print(f" [{len(nodes)}n pass1]", end="", flush=True)
        db_path = get_db_path(config, project_name)
        store = GraphStore(db_path)
        store.merge_extraction(nodes, edges)

        extra = asyncio.run(verify_extraction(transcript_text, nodes, config))
        print(f" [{len(extra['nodes'])}n verify]", end="", flush=True)
        nodes = extra["nodes"] or nodes
        edges = extra["edges"] or edges

    for category in (targeted or []):
        db_path = get_db_path(config, project_name)
        store = GraphStore(db_path)
        store.merge_extraction(nodes, edges)
        current_nodes = store.get_all_nodes()
        extra = asyncio.run(extract_targeted(transcript_text, category, current_nodes, config))
        print(f" [{len(extra['nodes'])}n {category}]", end="", flush=True)
        if extra["nodes"]:
            nodes = extra["nodes"]
            edges = extra["edges"]

    db_path = get_db_path(config, project_name)
    store = GraphStore(db_path)
    store.merge_extraction(nodes, edges)

    elapsed = time.time() - t0
    total = len(store.get_all_nodes())
    print(f" → {total} nodes ({elapsed:.0f}s)")
    return total


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generation quality eval for Context Broker")
    parser.add_argument("--config", default="benchmarks/model_configs/gemini_25_flash.yaml",
                        help="Model config YAML (default: gemini_25_flash)")
    parser.add_argument("--transcript", default="project_api_design",
                        help="Which benchmark transcript to use (default: project_api_design)")
    parser.add_argument("--questions", nargs="+", default=None,
                        help="Question IDs to evaluate (default: first 5 for the transcript)")
    parser.add_argument("--project", default=None,
                        help="Path to existing context.db — skip extraction")
    parser.add_argument("--verify", action="store_true",
                        help="Run verification pass during extraction")
    parser.add_argument("--decisions", action="store_true",
                        help="Run targeted decisions pass during extraction")
    parser.add_argument("--hops", type=int, default=3, help="BFS hops for retrieval")
    parser.add_argument("--top-k", type=int, default=30, help="Max nodes to retrieve")
    parser.add_argument("--save", action="store_true",
                        help="Save results JSON to benchmarks/results/generation_eval_<timestamp>/")
    parser.add_argument("--no-full-transcript", action="store_true",
                        help="Skip the full-transcript baseline condition")
    args = parser.parse_args()

    # ── Load config ──────────────────────────────────────────────────────────
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = ROOT / config_path
    config = load_config(config_path)

    # ── Load eval questions ──────────────────────────────────────────────────
    with open(EVAL_QUESTIONS_PATH) as f:
        all_questions = yaml.safe_load(f)["questions"]

    transcript_name = args.transcript
    transcript_qs = [q for q in all_questions if q["transcript"] == transcript_name]
    if not transcript_qs:
        print(f"No questions found for transcript '{transcript_name}'", file=sys.stderr)
        sys.exit(1)

    if args.questions:
        selected = [q for q in transcript_qs if q["id"] in args.questions]
        if not selected:
            print(f"None of {args.questions} found for transcript '{transcript_name}'", file=sys.stderr)
            sys.exit(1)
    else:
        selected = transcript_qs[:5]

    print(f"\nGeneration Eval — {transcript_name}")
    print(f"Model: {config['llm'].get('model', '?')}")
    print(f"Questions: {[q['id'] for q in selected]}\n")

    # ── Locate transcript text (needed for full-transcript baseline) ──────────
    run_full_transcript = not args.no_full_transcript
    transcript_path = TRANSCRIPTS_DIR / f"{transcript_name}.md"
    transcript_text: str | None = None
    if run_full_transcript:
        if not transcript_path.exists():
            print(f"Transcript not found: {transcript_path} — disabling full-transcript condition", file=sys.stderr)
            run_full_transcript = False
        else:
            transcript_text = transcript_path.read_text()

    # ── Set up project ────────────────────────────────────────────────────────
    tmp_dir = None
    if args.project:
        db_path = Path(args.project)
        if not db_path.exists():
            print(f"Project DB not found: {db_path}", file=sys.stderr)
            sys.exit(1)
        print(f"Using existing project: {db_path}\n")
    else:
        tmp_dir = tempfile.mkdtemp(prefix="ctx_geneval_")
        project_name = "geneval_project"
        # Override projects_dir to temp
        config = dict(config)
        config["projects_dir"] = tmp_dir

        if not transcript_path.exists():
            print(f"Transcript not found: {transcript_path}", file=sys.stderr)
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            sys.exit(1)

        print("Extracting transcript...")
        targeted = ["decisions"] if args.decisions else None
        run_extraction(config, project_name, transcript_path, verify=args.verify, targeted=targeted)
        db_path = get_db_path(config, project_name)
        print()

    # ── Run generation eval ───────────────────────────────────────────────────
    store = GraphStore(db_path)
    total_with = 0
    total_full = 0
    total_without = 0
    total_elements = 0
    question_results = []

    conditions = ["CB context", "full transcript", "no context"] if run_full_transcript else ["CB context", "no context"]
    print(f"Conditions: {' | '.join(conditions)}\n")
    print("=" * 78)
    for q in selected:
        qid = q["id"]
        question = q["question"]
        ground_truth = q["ground_truth_elements"]

        # Retrieve CB context
        context_result = retrieve_with_stats(
            store=store,
            task_description=question,
            hops=args.hops,
            top_k=args.top_k,
            strategies={
                "superseded_pruning": True,
                "confidence_threshold": 0.0,
                "recency_decay": False,
                "token_budget": 0,
                "relevance_scoring": True,
            },
        )
        context_md = context_result.markdown
        node_count = context_result.nodes_after_strategies

        print(f"\n[{qid}] {question}")
        print(f"  Retrieved: {node_count} nodes")

        # Answer WITH CB context
        print("  Calling LLM [CB context]...", end="", flush=True)
        t0 = time.time()
        answer_with = call_llm_sync(question, context_md, config)
        print(f" ({time.time()-t0:.1f}s)")

        # Answer WITH full transcript
        answer_full: str | None = None
        if run_full_transcript:
            print("  Calling LLM [full transcript]...", end="", flush=True)
            t0 = time.time()
            answer_full = call_llm_sync(question, transcript_text, config, context_label="Full Project Transcript")
            print(f" ({time.time()-t0:.1f}s)")

        # Answer WITHOUT context
        print("  Calling LLM [no context]...", end="", flush=True)
        t0 = time.time()
        answer_without = call_llm_sync(question, None, config)
        print(f" ({time.time()-t0:.1f}s)")

        # Grade
        recall_with, matched_with, missed_with = score_recall(answer_with, ground_truth)
        recall_without, matched_without, missed_without = score_recall(answer_without, ground_truth)
        recall_full, matched_full, missed_full = (
            score_recall(answer_full, ground_truth) if answer_full is not None else (None, [], [])
        )

        total_with += len(matched_with)
        total_without += len(matched_without)
        if recall_full is not None:
            total_full += len(matched_full)
        total_elements += len(ground_truth)

        result = {
            "id": qid,
            "question": question,
            "node_count": node_count,
            "recall_with": recall_with,
            "recall_without": recall_without,
            "matched_with": matched_with,
            "missed_with": missed_with,
            "matched_without": matched_without,
            "missed_without": missed_without,
            "answer_with": answer_with,
            "answer_without": answer_without,
        }
        if answer_full is not None:
            result["recall_full"] = recall_full
            result["matched_full"] = matched_full
            result["missed_full"] = missed_full
            result["answer_full"] = answer_full
        question_results.append(result)

        # Print results
        if run_full_transcript:
            print(f"\n  ── Recall: CB={recall_with:.0%}  |  full-transcript={recall_full:.0%}  |  no-ctx={recall_without:.0%} ──")
        else:
            print(f"\n  ── Recall: CB={recall_with:.0%}  |  no-ctx={recall_without:.0%} ──")

        print(f"\n  ANSWER [CB CONTEXT]:")
        for line in answer_with.strip().splitlines():
            print(f"    {line}")

        if answer_full is not None:
            print(f"\n  ANSWER [FULL TRANSCRIPT]:")
            for line in answer_full.strip().splitlines():
                print(f"    {line}")

        print(f"\n  ANSWER [NO CONTEXT]:")
        for line in answer_without.strip().splitlines():
            print(f"    {line}")

        print(f"\n  Ground truth ({len(ground_truth)} elements):")
        for el in ground_truth:
            with_hit = el in matched_with
            full_hit = el in matched_full if answer_full is not None else None
            without_hit = el in matched_without
            cb_mark = "✓" if with_hit else "✗"
            no_mark = "✓" if without_hit else "✗"
            if full_hit is not None:
                ft_mark = "✓" if full_hit else "✗"
                print(f"    cb:{cb_mark}  ft:{ft_mark}  no-ctx:{no_mark}  \"{el}\"")
            else:
                print(f"    cb:{cb_mark}  no-ctx:{no_mark}  \"{el}\"")

        print("=" * 78)

    # ── Summary ───────────────────────────────────────────────────────────────
    pct_with = total_with / total_elements * 100 if total_elements else 0
    pct_without = total_without / total_elements * 100 if total_elements else 0
    pct_full = total_full / total_elements * 100 if (total_elements and run_full_transcript) else None

    print(f"\n{'='*78}")
    print(f"SUMMARY: {len(selected)} questions, {total_elements} ground truth elements")
    print(f"  CB context:       {total_with}/{total_elements} = {pct_with:.0f}%")
    if pct_full is not None:
        print(f"  Full transcript:  {total_full}/{total_elements} = {pct_full:.0f}%")
        print(f"  CB vs full-xscr:  {pct_with - pct_full:+.0f}pp")
    print(f"  No context:       {total_without}/{total_elements} = {pct_without:.0f}%")
    print(f"  CB vs no-ctx:     {pct_with - pct_without:+.0f}pp")
    print(f"{'='*78}\n")

    # ── Save results ──────────────────────────────────────────────────────────
    if args.save:
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_dir = BENCHMARKS_DIR / "results" / f"generation_eval_{ts}"
        out_dir.mkdir(parents=True, exist_ok=True)
        summary = {
            "questions": len(selected),
            "elements": total_elements,
            "cb_context": total_with,
            "without_context": total_without,
            "recall_cb_pct": round(pct_with, 1),
            "recall_without_pct": round(pct_without, 1),
            "gain_cb_vs_no_ctx_pp": round(pct_with - pct_without, 1),
        }
        if pct_full is not None:
            summary["full_transcript"] = total_full
            summary["recall_full_pct"] = round(pct_full, 1)
            summary["gain_cb_vs_full_pp"] = round(pct_with - pct_full, 1)

        payload = {
            "timestamp": ts,
            "transcript": transcript_name,
            "model": config["llm"].get("model", "?"),
            "config_file": str(args.config),
            "verify": args.verify,
            "decisions": args.decisions,
            "hops": args.hops,
            "top_k": args.top_k,
            "full_transcript_condition": run_full_transcript,
            "summary": summary,
            "questions": question_results,
        }
        results_path = out_dir / "results.json"
        with open(results_path, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"Results saved to {results_path}")

    # ── Cleanup ───────────────────────────────────────────────────────────────
    if tmp_dir:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
