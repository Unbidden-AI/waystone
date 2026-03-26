"""
LOCOMO benchmark harness for Engram.

Usage:
    # Quick dev run (1 conversation, keyword scoring only)
    python -m benchmarks.locomo.harness --dataset /path/to/locomo10.json --limit 1

    # Full run with LLM judge, all ablation configs
    python -m benchmarks.locomo.harness \\
        --dataset /path/to/locomo10.json \\
        --configs engram_default engram_tight full_context \\
        --llm-judge \\
        --output results/locomo_$(date +%Y%m%d).json

    # Single config, all 10 conversations
    python -m benchrams.locomo.harness \\
        --dataset /path/to/locomo10.json \\
        --configs engram_default \\
        --llm-judge \\
        --output results/locomo_default.json
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# Add project root to path when run as __main__
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from benchmarks.locomo.loaders.locomo_dataset import LocomoDataset
from benchmarks.locomo.loaders.ingestion_pipeline import ingest_conversation, IngestionResult
from benchmarks.locomo.evaluation.scoring import (
    score_keyword_recall,
    score_llm_judge,
    aggregate,
    ScoringResult,
)
from benchmarks.locomo.evaluation.token_counter import TokenBudget, estimate_tokens
from benchmarks.locomo.ablation_configs import ABLATION_CONFIGS, PAPER_CONFIGS, AblationConfig
from benchmarks.locomo.splits import DEV, TEST, ALL


def run_benchmark(
    dataset_path: str,
    config_names: list[str],
    output_path: str | None = None,
    split: str = "dev",
    limit: int | None = None,
    use_llm_judge: bool = False,
    llm_model: str = "claude-haiku-4-5-20251001",
    categories: list[int] | None = None,
    db_dir: str | None = None,
    verbose: bool = True,
) -> dict[str, Any]:
    """
    Run the LOCOMO benchmark for one or more ablation configs.

    Returns a results dict that can be written to JSON for the paper.
    """
    if split == "dev":
        sample_ids = DEV
    elif split == "test":
        sample_ids = TEST
    elif split == "all":
        sample_ids = ALL
    else:
        raise ValueError(f"split must be 'dev', 'test', or 'all', got {split!r}")

    ds = LocomoDataset(dataset_path)

    if verbose:
        stats = ds.stats()
        print(f"Dataset: {stats['conversations']} conversations, "
              f"{stats['total_qa_pairs']} QA pairs, "
              f"{stats['total_turns']} turns")
        print(f"Split: {split} ({len(sample_ids)} conversations: {sample_ids})")
        if limit:
            print(f"Limit: {limit} conversations")
        print(f"Configs: {config_names}")
        print(f"LLM judge: {use_llm_judge} ({llm_model})")
        print()

    results: dict[str, Any] = {
        "dataset": str(dataset_path),
        "split": split,
        "sample_ids": sample_ids,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "configs": {},
    }

    for config_name in config_names:
        if config_name not in ABLATION_CONFIGS:
            print(f"[warn] Unknown config '{config_name}', skipping")
            continue

        config = ABLATION_CONFIGS[config_name]
        if verbose:
            print(f"{'='*60}")
            print(f"Config: {config_name}")
            print(f"  {config.description}")
            print()

        config_results = _run_config(
            ds=ds,
            config=config,
            sample_ids=sample_ids,
            limit=limit,
            use_llm_judge=use_llm_judge,
            llm_model=llm_model,
            categories=categories,
            db_dir=db_dir,
            verbose=verbose,
        )
        results["configs"][config_name] = config_results

        if verbose:
            m = config_results["aggregate"]
            print(f"\n  Results for {config_name}:")
            print(f"    keyword accuracy: {m.get('keyword_accuracy', 0):.1%}")
            print(f"    keyword exact:    {m.get('keyword_exact', 0):.1%}")
            print(f"    avg tokens:       {m.get('avg_tokens', 0):.0f}")
            if "llm_accuracy" in m:
                print(f"    llm accuracy:     {m['llm_accuracy']:.1%}")
            print()

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(results, f, indent=2)
        if verbose:
            print(f"Results written to {output_path}")

    return results


def _run_config(
    ds: LocomoDataset,
    config: AblationConfig,
    sample_ids: list[str],
    limit: int | None,
    use_llm_judge: bool,
    llm_model: str,
    categories: list[int] | None,
    db_dir: str | None,
    verbose: bool,
) -> dict[str, Any]:
    """Run a single ablation config across all (limited) conversations."""
    all_scoring_results: list[ScoringResult] = []
    all_ingestion_results: list[dict] = []
    budget = TokenBudget()

    tmp_dir = db_dir or tempfile.mkdtemp(prefix=f"locomo_{config.name}_")

    for conv in ds.iter_conversations(limit=limit, sample_ids=sample_ids):
        if verbose:
            print(f"  [{config.name}] {conv.sample_id}: "
                  f"{len(conv.sessions)} sessions, {len(conv.qa_pairs)} QA")

        if config.full_context:
            store = None
            ingestion_result = None
        else:
            store, ingestion_result = ingest_conversation(
                conv=conv,
                db_dir=tmp_dir,
                verbose=verbose,
                domain=config.domain,
            )
            if ingestion_result:
                all_ingestion_results.append({
                    "sample_id": ingestion_result.sample_id,
                    "nodes_created": ingestion_result.nodes_created,
                    "elapsed_seconds": round(ingestion_result.elapsed_seconds, 2),
                    "errors": ingestion_result.errors,
                })

        # Score each QA pair
        qa_pairs = conv.qa_pairs
        if categories:
            qa_pairs = [q for q in qa_pairs if q.category in categories]

        for qa in qa_pairs:
            context = _retrieve_context(
                conv=conv,
                question=qa.question,
                store=store,
                config=config,
            )

            tokens = estimate_tokens(context)
            budget.retrieval_context += tokens
            budget.query_count += 1

            kw_score, matched, missed = score_keyword_recall(context, qa.answer)

            llm_score = None
            if use_llm_judge:
                try:
                    llm_score = score_llm_judge(
                        question=qa.question,
                        ground_truth_answer=qa.answer,
                        retrieved_context=context,
                        model=llm_model,
                    )
                except Exception as e:
                    if verbose:
                        print(f"    [warn] LLM judge failed: {e}")

            all_scoring_results.append(ScoringResult(
                question=qa.question,
                ground_truth_answer=qa.answer,
                retrieved_context=context,
                keyword_score=kw_score,
                llm_score=llm_score,
                matched_keywords=matched,
                missed_keywords=missed,
                tokens_used=tokens,
            ))

    return {
        "config_name": config.name,
        "aggregate": aggregate(all_scoring_results),
        "token_budget": budget.as_dict(),
        "ingestion": all_ingestion_results,
        "per_question": [
            {
                "question": r.question[:80],
                "answer": r.ground_truth_answer,
                "keyword_score": r.keyword_score,
                "llm_score": r.llm_score,
                "tokens": r.tokens_used,
            }
            for r in all_scoring_results
        ],
    }


def _retrieve_context(
    conv,
    question: str,
    store,
    config: AblationConfig,
) -> str:
    """Retrieve context for a question given the ablation config."""

    if config.full_context:
        # Baseline: concatenate all session transcripts
        lines = []
        for session in conv.sessions:
            if session.datetime_str:
                lines.append(f"[{session.datetime_str}]")
            for turn in session.turns:
                lines.append(f"{turn.speaker}: {turn.text}")
        return "\n".join(lines)

    if store is None or not config.use_bfs:
        return ""

    from engram.retriever import retrieve_with_stats

    strategies: dict[str, Any] = {
        "superseded_pruning": config.superseded_pruning,
        "recency_decay": config.recency_decay,
        "confidence_threshold": config.confidence_threshold or 0.0,
        "token_budget": config.token_budget or 0,
    }

    try:
        result = retrieve_with_stats(
            store=store,
            task_description=question,
            top_k=config.top_k or 10,
            strategies=strategies,
        )
        return result.markdown
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="LOCOMO benchmark harness for Engram",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--dataset", required=True,
        help="Path to locomo10.json",
    )
    parser.add_argument(
        "--configs", nargs="+", default=["engram_default"],
        choices=list(ABLATION_CONFIGS.keys()) + ["all", "paper"],
        help="Ablation configs to run. 'paper' runs all paper configs.",
    )
    parser.add_argument(
        "--split", default="dev", choices=["dev", "test", "all"],
        help="Which split to evaluate. 'dev' (default) for tuning; "
             "'test' for final paper numbers only (conv-44 through conv-50).",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Limit to N conversations within the split (for dev iteration)",
    )
    parser.add_argument(
        "--llm-judge", action="store_true",
        help="Use LLM to judge answer quality (more accurate, uses API credits)",
    )
    parser.add_argument(
        "--llm-model", default="claude-haiku-4-5-20251001",
        help="Model for LLM judge",
    )
    parser.add_argument(
        "--categories", nargs="+", type=int,
        help="Filter to QA categories (1=temporal, 2=explicit, 3=adversarial, "
             "4=multi_session, 5=open_domain)",
    )
    parser.add_argument(
        "--output", default=None,
        help="Path to write JSON results",
    )
    parser.add_argument(
        "--db-dir", default=None,
        help="Directory for SQLite DBs (default: temp dir)",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress progress output",
    )
    args = parser.parse_args()

    # Resolve config names
    config_names = args.configs
    if "all" in config_names:
        config_names = list(ABLATION_CONFIGS.keys())
    elif "paper" in config_names:
        config_names = PAPER_CONFIGS

    run_benchmark(
        dataset_path=args.dataset,
        config_names=config_names,
        output_path=args.output,
        split=args.split,
        limit=args.limit,
        use_llm_judge=args.llm_judge,
        llm_model=args.llm_model,
        categories=args.categories,
        db_dir=args.db_dir,
        verbose=not args.quiet,
    )


if __name__ == "__main__":
    main()
