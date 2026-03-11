#!/usr/bin/env python3
"""Hook Impact Benchmark

Measures the cost/benefit of the context broker hook integration by running
each eval question through the configured LLM twice:

  - Brokered:   system prompt + injected context + question
  - Unbrokered: system prompt + question only

Reports per-question: hook latency, context tokens added, recall delta,
and input token delta. Also shows a break-even table modelling at which
conversation turn the broker starts saving tokens vs carrying full history.

Usage:
    # Extract transcripts first, then benchmark:
    python benchmarks/bench_hook_impact.py --config benchmarks/model_configs/gemini_25_flash.yaml

    # Reuse an existing project (skip extraction):
    python benchmarks/bench_hook_impact.py --config benchmarks/model_configs/gemini_25_flash.yaml \\
        --project bench_myproject

    # Only measure latency + token overhead, skip API calls:
    python benchmarks/bench_hook_impact.py --config benchmarks/model_configs/gemini_25_flash.yaml \\
        --no-api
"""

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from context_broker.config import get_db_path, get_project_dir, load_config
from context_broker.extractor import extract
from context_broker.retriever import estimate_tokens, extract_keywords, retrieve_with_stats
from context_broker.store import GraphStore

BENCHMARKS_DIR = Path(__file__).parent
TRANSCRIPTS_DIR = BENCHMARKS_DIR / "transcripts"
EVAL_QUESTIONS_PATH = BENCHMARKS_DIR / "eval_questions.yaml"
RESULTS_DIR = BENCHMARKS_DIR / "results"

SYSTEM_PROMPT = (
    "You are a knowledgeable assistant. Answer the question directly and concisely "
    "based on any provided context. If context is provided, prioritise it."
)

# Stop words for recall scoring (matches run_benchmark.py)
STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "and", "or", "for",
    "to", "of", "in", "on", "at", "by", "with", "not", "no", "due",
    "when", "than", "that", "this", "it", "its", "be", "as", "also",
    "from", "but", "so", "if", "we", "our", "they", "their",
}


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

async def call_llm_for_answer(question: str, context_md: str | None, config: dict) -> dict:
    """Call the LLM with or without injected context. Returns answer + token usage."""
    llm_cfg = config["llm"]
    headers = {}
    api_key_env = llm_cfg.get("api_key_env")
    if api_key_env:
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise ValueError(f"API key env var '{api_key_env}' is not set")
        headers["Authorization"] = f"Bearer {api_key}"

    user_content = question
    if context_md:
        user_content = f"{context_md}\n\n---\n\n{question}"

    body = {
        "model": llm_cfg["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.1,
        "max_tokens": 512,
    }

    timeout = llm_cfg.get("timeout", 120.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{llm_cfg['base_url']}/chat/completions",
            headers=headers,
            json=body,
        )
        resp.raise_for_status()

    data = resp.json()
    usage = data.get("usage", {})
    return {
        "answer": data["choices"][0]["message"]["content"],
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
    }


# ---------------------------------------------------------------------------
# Recall scoring
# ---------------------------------------------------------------------------

def score_recall(text: str, ground_truth: list[str]) -> tuple[float, list, list]:
    text = text.lower()
    matched, missed = [], []
    for element in ground_truth:
        el_lower = element.lower()
        if el_lower in text:
            matched.append(element)
            continue
        key_words = [
            w.strip(".,;:()\"'—") for w in el_lower.split()
            if w.strip(".,;:()\"'—") not in STOP_WORDS and len(w) > 2
        ]
        if key_words and sum(1 for w in key_words if w in text) / len(key_words) >= 0.7:
            matched.append(element)
        else:
            missed.append(element)
    recall = len(matched) / len(ground_truth) if ground_truth else 0.0
    return recall, matched, missed


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def setup_project(config: dict, project_name: str) -> None:
    db_path = get_db_path(config, project_name)
    project_dir = get_project_dir(config, project_name)
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "transcripts").mkdir(exist_ok=True)

    transcripts = sorted(TRANSCRIPTS_DIR.glob("*.md"))
    print(f"\nExtracting {len(transcripts)} transcripts into '{project_name}'...")

    for transcript_path in transcripts:
        transcript_text = transcript_path.read_text()
        print(f"  {transcript_path.name} ...", end="", flush=True)
        t0 = time.time()
        try:
            result = asyncio.run(extract(transcript_text, config))
            nodes, edges = result["nodes"], result["edges"]
            for node in nodes:
                node["source_transcript"] = transcript_path.name
                node.setdefault("created_at", datetime.now(timezone.utc).isoformat())
            store = GraphStore(db_path)
            store.merge_extraction(nodes, edges)
            store.close()
            print(f" {len(nodes)} nodes [{time.time()-t0:.1f}s]")
        except Exception as e:
            print(f" FAILED: {e}")


# ---------------------------------------------------------------------------
# Main benchmark
# ---------------------------------------------------------------------------

def run_benchmark(config: dict, project_name: str, questions: list, use_api: bool) -> list[dict]:
    db_path = get_db_path(config, project_name)
    defaults = config.get("defaults", {})
    hops = defaults.get("hops", 3)
    top_k = defaults.get("top_k", 10)
    strategies = {**config.get("strategies", {}), "relevance_scoring": True}

    rows = []

    for q in questions:
        qid = q["id"]
        question = q["question"]
        ground_truth = q["ground_truth_elements"]

        # --- Hook latency: time the ctx query ---
        store = GraphStore(db_path)
        t0 = time.time()
        result = retrieve_with_stats(store, question, hops=hops, top_k=top_k, strategies=strategies)
        hook_latency_ms = (time.time() - t0) * 1000
        store.close()

        context_md = result.markdown if result.nodes_after_strategies > 0 else None
        context_tokens = estimate_tokens(context_md) if context_md else 0
        nodes_retrieved = result.nodes_after_strategies

        row = {
            "id": qid,
            "transcript": q["transcript"],
            "question": question,
            "hook_latency_ms": round(hook_latency_ms, 1),
            "context_tokens": context_tokens,
            "nodes_retrieved": nodes_retrieved,
            "context_found": context_md is not None,
        }

        if use_api:
            # Brokered call
            try:
                t0 = time.time()
                brokered = asyncio.run(call_llm_for_answer(question, context_md, config))
                brokered_latency = (time.time() - t0) * 1000
                b_recall, b_matched, b_missed = score_recall(brokered["answer"], ground_truth)
                row.update({
                    "brokered_recall": round(b_recall, 3),
                    "brokered_input_tokens": brokered["input_tokens"],
                    "brokered_output_tokens": brokered["output_tokens"],
                    "brokered_latency_ms": round(brokered_latency, 1),
                    "brokered_missed": b_missed,
                })
            except Exception as e:
                print(f"    [brokered API error for {qid}: {e}]", file=sys.stderr)
                row.update({"brokered_recall": None, "brokered_input_tokens": None, "brokered_error": str(e)})

            # Unbrokered call
            try:
                t0 = time.time()
                unbrokered = asyncio.run(call_llm_for_answer(question, None, config))
                unbrokered_latency = (time.time() - t0) * 1000
                u_recall, u_matched, u_missed = score_recall(unbrokered["answer"], ground_truth)
                row.update({
                    "unbrokered_recall": round(u_recall, 3),
                    "unbrokered_input_tokens": unbrokered["input_tokens"],
                    "unbrokered_output_tokens": unbrokered["output_tokens"],
                    "unbrokered_latency_ms": round(unbrokered_latency, 1),
                    "unbrokered_missed": u_missed,
                })
            except Exception as e:
                print(f"    [unbrokered API error for {qid}: {e}]", file=sys.stderr)
                row.update({"unbrokered_recall": None, "unbrokered_input_tokens": None, "unbrokered_error": str(e)})

            b_rec = row.get("brokered_recall")
            u_rec = row.get("unbrokered_recall")
            b_tok = row.get("brokered_input_tokens", 0) or 0
            u_tok = row.get("unbrokered_input_tokens", 0) or 0
            delta_rec = f"{(b_rec - u_rec):+.0%}" if b_rec is not None and u_rec is not None else "n/a"
            delta_tok = f"{b_tok - u_tok:+d}" if b_tok and u_tok else "n/a"
            icon = "✓" if (b_rec or 0) >= 0.8 else ("~" if (b_rec or 0) >= 0.5 else "✗")
            print(
                f"  {icon} {qid:12s}  "
                f"latency={hook_latency_ms:.0f}ms  "
                f"ctx={context_tokens}tok/{nodes_retrieved}nodes  "
                f"recall: {u_rec:.0%}→{b_rec:.0%} ({delta_rec})  "
                f"tokens: {u_tok}→{b_tok} ({delta_tok})"
            )
        else:
            print(
                f"  {qid:12s}  "
                f"latency={hook_latency_ms:.0f}ms  "
                f"ctx={context_tokens}tok/{nodes_retrieved}nodes"
            )

        rows.append(row)

    return rows


# ---------------------------------------------------------------------------
# Break-even model
# ---------------------------------------------------------------------------

def compute_breakeven(rows: list[dict]) -> dict:
    """Model token savings vs carrying full conversation history."""
    avg_ctx_tokens = sum(r["context_tokens"] for r in rows if r["context_found"]) / max(
        sum(1 for r in rows if r["context_found"]), 1
    )
    # Estimate average turn size: question + answer tokens
    if any(r.get("brokered_input_tokens") for r in rows):
        avg_answer_tokens = sum(
            r.get("brokered_output_tokens", 0) or 0 for r in rows
        ) / len(rows)
    else:
        avg_answer_tokens = 150  # rough estimate

    avg_question_tokens = sum(estimate_tokens(r["question"]) for r in rows) / len(rows)
    avg_turn_tokens = avg_question_tokens + avg_answer_tokens

    # At turn N: history carries (N-1) full turns
    # Context broker always injects avg_ctx_tokens instead
    breakeven = []
    for turn in [1, 2, 3, 5, 10, 20, 50]:
        history_tokens = avg_turn_tokens * (turn - 1)
        overhead = avg_ctx_tokens - 0  # cost of context vs nothing
        savings_vs_history = history_tokens - avg_ctx_tokens
        breakeven.append({
            "turn": turn,
            "history_tokens": round(history_tokens),
            "context_tokens": round(avg_ctx_tokens),
            "net_savings": round(savings_vs_history),
        })

    return {
        "avg_context_tokens": round(avg_ctx_tokens),
        "avg_turn_tokens": round(avg_turn_tokens),
        "table": breakeven,
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def write_report(data: dict, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "hook_impact.json"
    json_path.write_text(json.dumps(data, indent=2))

    rows = data["questions"]
    be = data["breakeven"]
    use_api = any(r.get("brokered_recall") is not None for r in rows)

    lines = [
        "# Hook Impact Benchmark",
        "",
        f"**Model:** `{data['model']}`  ",
        f"**Run:** {data['timestamp']}  ",
        "",
        "## Per-Question Results",
        "",
    ]

    if use_api:
        lines += [
            "| ID | Latency | Ctx tokens | Nodes | Recall (unbrokered→brokered) | Token delta |",
            "|----|---------|------------|-------|------------------------------|-------------|",
        ]
        for r in rows:
            b = r.get("brokered_recall")
            u = r.get("unbrokered_recall")
            b_tok = r.get("brokered_input_tokens", 0) or 0
            u_tok = r.get("unbrokered_input_tokens", 0) or 0
            delta_rec = f"{(b-u):+.0%}" if b is not None and u is not None else "n/a"
            delta_tok = f"{b_tok-u_tok:+d}" if b_tok and u_tok else "n/a"
            b_str = f"{b:.0%}" if b is not None else "err"
            u_str = f"{u:.0%}" if u is not None else "err"
            lines.append(
                f"| {r['id']} | {r['hook_latency_ms']:.0f}ms | {r['context_tokens']} | "
                f"{r['nodes_retrieved']} | {u_str} → {b_str} ({delta_rec}) | {delta_tok} |"
            )
    else:
        lines += [
            "| ID | Hook latency | Context tokens | Nodes retrieved |",
            "|----|-------------|----------------|-----------------|",
        ]
        for r in rows:
            lines.append(
                f"| {r['id']} | {r['hook_latency_ms']:.0f}ms | "
                f"{r['context_tokens']} | {r['nodes_retrieved']} |"
            )

    if use_api:
        b_recalls = [r["brokered_recall"] for r in rows if r.get("brokered_recall") is not None]
        u_recalls = [r["unbrokered_recall"] for r in rows if r.get("unbrokered_recall") is not None]
        mean_b = sum(b_recalls) / len(b_recalls) if b_recalls else 0
        mean_u = sum(u_recalls) / len(u_recalls) if u_recalls else 0
        lines += [
            "",
            f"**Mean recall — unbrokered: {mean_u:.0%} → brokered: {mean_b:.0%} "
            f"({mean_b-mean_u:+.0%})**",
        ]

    avg_latency = sum(r["hook_latency_ms"] for r in rows) / len(rows)
    lines += [
        "",
        f"**Avg hook latency:** {avg_latency:.1f}ms  ",
        f"**Avg context tokens injected:** {be['avg_context_tokens']}  ",
        "",
        "## Break-Even: Broker vs Full History",
        "",
        f"Avg context injected per prompt: **{be['avg_context_tokens']} tokens**  ",
        f"Avg turn size (question + answer): **{be['avg_turn_tokens']} tokens**  ",
        "",
        "| Turn # | History tokens (no broker) | Context tokens (broker) | Net savings |",
        "|--------|---------------------------|------------------------|-------------|",
    ]
    for row in be["table"]:
        sign = "✓" if row["net_savings"] > 0 else "✗"
        lines.append(
            f"| {row['turn']:6d} | {row['history_tokens']:25,d} | "
            f"{row['context_tokens']:22,d} | {sign} {row['net_savings']:+,d} |"
        )

    md_path = output_dir / "hook_impact.md"
    md_path.write_text("\n".join(lines))
    return json_path, md_path


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Benchmark hook integration cost/benefit")
    parser.add_argument(
        "--config",
        default=str(BENCHMARKS_DIR / "model_configs" / "gemini_25_flash.yaml"),
        help="Model config YAML",
    )
    parser.add_argument(
        "--project",
        default=None,
        help="Existing project name to query (skips extraction). "
             "Must live under results/projects/.",
    )
    parser.add_argument(
        "--no-api",
        action="store_true",
        help="Skip LLM calls — only measure hook latency and context token overhead",
    )
    parser.add_argument(
        "--keep-project",
        action="store_true",
        help="Keep the temporary benchmark project after run",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Error: config not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    config = load_config(config_path)
    config["projects_dir"] = str(RESULTS_DIR / "projects")

    model_name = config["llm"]["model"]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    with open(EVAL_QUESTIONS_PATH) as f:
        questions = yaml.safe_load(f)["questions"]

    cleanup_project = False
    if args.project:
        project_name = args.project
        db_path = get_db_path(config, project_name)
        if not db_path.exists():
            print(f"Error: project '{project_name}' not found at {db_path}", file=sys.stderr)
            sys.exit(1)
        print(f"Using existing project: {project_name}")
    else:
        project_name = f"hookbench_{timestamp}"
        cleanup_project = not args.keep_project
        setup_project(config, project_name)

    print(f"\n{'='*60}")
    print(f"  Hook Impact Benchmark")
    print(f"  Model:     {model_name}")
    print(f"  Questions: {len(questions)}")
    print(f"  API calls: {'disabled (--no-api)' if args.no_api else 'enabled (2 per question)'}")
    print(f"{'='*60}\n")

    rows = run_benchmark(config, project_name, questions, use_api=not args.no_api)
    be = compute_breakeven(rows)

    run_label = f"hookbench_{config_path.stem}_{timestamp}"
    output_dir = RESULTS_DIR / run_label

    full_data = {
        "model": model_name,
        "config_file": config_path.name,
        "timestamp": timestamp,
        "questions": rows,
        "breakeven": be,
    }

    json_path, md_path = write_report(full_data, output_dir)

    if cleanup_project:
        project_dir = Path(config["projects_dir"]) / project_name
        if project_dir.exists():
            shutil.rmtree(project_dir)

    print(f"\n── Break-Even Summary ──")
    print(f"  Avg context injected: {be['avg_context_tokens']} tokens")
    print(f"  Avg turn size:        {be['avg_turn_tokens']} tokens")
    print(f"  Break-even at turn:   ", end="")
    breakeven_turn = next(
        (r["turn"] for r in be["table"] if r["net_savings"] > 0), None
    )
    print(breakeven_turn if breakeven_turn else ">50")

    print(f"\n── Results ──")
    print(f"  JSON:     {json_path.relative_to(ROOT)}")
    print(f"  Markdown: {md_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
