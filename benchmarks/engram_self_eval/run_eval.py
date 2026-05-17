#!/usr/bin/env python3
"""Waystone self-eval: run a curated question set against the local graph and score context quality.

Usage:
    python benchmarks/engram_self_eval/run_eval.py [--project PROJECT] [--auto] [--category CAT]
    python benchmarks/engram_self_eval/run_eval.py --list

Options:
    --project PROJECT   Waystone project to query (default: Waystone)
    --auto              Auto-grade only (keyword check, no interactive prompts)
    --category CAT      Run only questions from this category
    --list              Print all questions and exit
    --hops N            BFS depth (default: 3)
    --top-k N           Max nodes returned (default: 25)
    --output PATH       Save results JSON to this path (default: auto-named in results/)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Load .env before any waystone imports
try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env", override=False)
except ImportError:
    pass

import yaml

from waystone.config import get_db_path, load_config
from waystone.retriever import retrieve_with_stats
from waystone.store import GraphStore

QUESTIONS_FILE = Path(__file__).parent / "questions.yaml"
RESULTS_DIR = Path(__file__).parent / "results"

# ── ANSI colours ─────────────────────────────────────────────────────────────

_USE_COLOR = sys.stdout.isatty()


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def green(t):  return _c(t, "32")
def yellow(t): return _c(t, "33")
def red(t):    return _c(t, "31")
def bold(t):   return _c(t, "1")
def dim(t):    return _c(t, "2")


# ── Auto-grade ────────────────────────────────────────────────────────────────

def auto_grade(context_md: str, expected_keywords: list[str]) -> tuple[str, list[str]]:
    """Return (grade, matched_keywords). Grade: 'yes' | 'partial' | 'no'."""
    lower = context_md.lower()
    matched = [kw for kw in expected_keywords if str(kw).lower() in lower]
    ratio = len(matched) / len(expected_keywords) if expected_keywords else 1.0
    if ratio >= 0.5:
        grade = "yes"
    elif ratio > 0:
        grade = "partial"
    else:
        grade = "no"
    return grade, matched


# ── Interactive prompt ────────────────────────────────────────────────────────

def ask_grade(question_id: str) -> str:
    """Prompt user for a manual grade. Returns 'yes', 'partial', 'no', or 'skip'."""
    while True:
        raw = input(f"  Useful? [y]es / [p]artial / [n]o / [s]kip > ").strip().lower()
        if raw in ("y", "yes"):    return "yes"
        if raw in ("p", "partial"): return "partial"
        if raw in ("n", "no"):     return "no"
        if raw in ("s", "skip"):   return "skip"
        print("  Please enter y, p, n, or s.")


# ── Main runner ───────────────────────────────────────────────────────────────

def run_eval(
    project: str,
    questions: list[dict],
    hops: int,
    top_k: int,
    auto: bool,
) -> list[dict]:
    config = load_config()
    db_path = get_db_path(config, project)

    if not db_path.exists():
        print(red(f"No graph found for project '{project}' at {db_path}"))
        sys.exit(1)

    strategies = {
        "superseded_pruning": True,
        "confidence_threshold": 0.0,
        "recency_decay": False,
        "token_budget": 0,
        "relevance_scoring": True,
    }

    results: list[dict] = []

    for i, q in enumerate(questions, 1):
        qid = q["id"]
        question = q["question"]
        expected = q.get("expected_keywords", [])
        notes = q.get("notes", "")

        print()
        print(bold(f"[{i}/{len(questions)}] {qid}  ({q.get('category','')})"))
        print(f"  {question}")
        if notes:
            print(dim(f"  Note: {notes}"))
        print()

        t0 = time.monotonic()
        try:
            store = GraphStore(db_path)
            result = retrieve_with_stats(
                store, question,
                hops=hops,
                top_k=top_k,
                strategies=strategies,
            )
            store.close()
            elapsed = time.monotonic() - t0
            context_md = result.markdown
            nodes_before = result.nodes_before_strategies
            nodes_after = result.nodes_after_strategies
            tokens_est = result.tokens_estimated
            error = None
        except Exception as e:
            elapsed = time.monotonic() - t0
            context_md = ""
            nodes_before = nodes_after = tokens_est = 0
            error = str(e)
            print(red(f"  ERROR: {e}"))

        # Show retrieved context
        if context_md:
            print(dim("─" * 70))
            # Truncate for readability — first 60 lines
            lines = context_md.splitlines()
            shown = lines[:60]
            print("\n".join(shown))
            if len(lines) > 60:
                print(dim(f"  … ({len(lines) - 60} more lines)"))
            print(dim("─" * 70))
            print(dim(f"  {nodes_before}→{nodes_after} nodes  ~{tokens_est} tokens  {elapsed:.1f}s"))
        else:
            print(yellow("  (no context retrieved)"))

        # Grade
        auto_g, matched = auto_grade(context_md, expected)
        if expected:
            kw_display = ", ".join(
                green(str(kw)) if kw in matched else red(str(kw))
                for kw in expected
            )
            print(f"  Keywords: {kw_display}")
            print(f"  Auto-grade: {_grade_label(auto_g)}")

        if auto:
            grade = auto_g
        else:
            grade = ask_grade(qid)

        results.append({
            "id": qid,
            "category": q.get("category", ""),
            "question": question,
            "grade": grade,
            "auto_grade": auto_g,
            "nodes_before": nodes_before,
            "nodes_after": nodes_after,
            "tokens_estimated": tokens_est,
            "elapsed_s": round(elapsed, 2),
            "matched_keywords": matched,
            "expected_keywords": expected,
            "error": error,
        })

    return results


def _grade_label(grade: str) -> str:
    return {"yes": green("yes"), "partial": yellow("partial"), "no": red("no"), "skip": dim("skip")}.get(grade, grade)


# ── Summary ───────────────────────────────────────────────────────────────────

def print_summary(results: list[dict]) -> None:
    graded = [r for r in results if r["grade"] != "skip"]
    if not graded:
        print("\nNo graded questions.")
        return

    yes     = sum(1 for r in graded if r["grade"] == "yes")
    partial = sum(1 for r in graded if r["grade"] == "partial")
    no      = sum(1 for r in graded if r["grade"] == "no")
    total   = len(graded)

    score = (yes + 0.5 * partial) / total * 100 if total else 0

    print()
    print(bold("=" * 50))
    print(bold("SUMMARY"))
    print(bold("=" * 50))
    print(f"  Questions graded : {total}")
    print(f"  {green('Useful')}           : {yes}")
    print(f"  {yellow('Partial')}          : {partial}")
    print(f"  {red('Not useful')}      : {no}")
    print(f"  Score            : {bold(f'{score:.0f}%')}  (yes=1pt, partial=0.5pt)")
    print()

    # Per-category breakdown
    categories: dict[str, list[dict]] = {}
    for r in graded:
        cat = r.get("category", "other")
        categories.setdefault(cat, []).append(r)
    print("  By category:")
    for cat, rs in sorted(categories.items()):
        cat_yes     = sum(1 for r in rs if r["grade"] == "yes")
        cat_partial = sum(1 for r in rs if r["grade"] == "partial")
        cat_score   = (cat_yes + 0.5 * cat_partial) / len(rs) * 100
        print(f"    {cat:<16}  {cat_score:5.0f}%  ({cat_yes}y {cat_partial}p {len(rs)-cat_yes-cat_partial}n / {len(rs)})")

    # Failures
    failures = [r for r in graded if r["grade"] == "no"]
    if failures:
        print()
        print(red("  Not useful:"))
        for r in failures:
            print(f"    [{r['id']}] {r['question'][:80]}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Waystone context quality eval")
    parser.add_argument("--project",  default="Waystone")
    parser.add_argument("--auto",     action="store_true", help="Auto-grade only (no interactive prompts)")
    parser.add_argument("--category", help="Filter to one category")
    parser.add_argument("--list",     action="store_true", help="Print all questions and exit")
    parser.add_argument("--hops",     type=int, default=3)
    parser.add_argument("--top-k",    type=int, default=25, dest="top_k")
    parser.add_argument("--output",   help="Path to save results JSON")
    args = parser.parse_args()

    with open(QUESTIONS_FILE) as f:
        data = yaml.safe_load(f)
    questions = data["questions"]

    if args.list:
        for q in questions:
            print(f"[{q['id']}] ({q.get('category','')}) {q['question']}")
        return

    if args.category:
        questions = [q for q in questions if q.get("category") == args.category]
        if not questions:
            print(f"No questions found for category '{args.category}'.")
            sys.exit(1)

    print(bold(f"\nEngram self-eval  —  project: {args.project}  —  {len(questions)} questions"))
    print(dim(f"hops={args.hops}  top_k={args.top_k}  auto={'yes' if args.auto else 'no (interactive)'}"))

    results = run_eval(
        project=args.project,
        questions=questions,
        hops=args.hops,
        top_k=args.top_k,
        auto=args.auto,
    )

    print_summary(results)

    # Save results
    RESULTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(args.output) if args.output else RESULTS_DIR / f"eval_{args.project}_{ts}.json"
    out_path.write_text(json.dumps({
        "project": args.project,
        "timestamp": ts,
        "hops": args.hops,
        "top_k": args.top_k,
        "results": results,
    }, indent=2))
    print(f"\n  Results saved → {out_path}")


if __name__ == "__main__":
    main()
