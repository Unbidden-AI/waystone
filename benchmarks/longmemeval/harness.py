"""
LongMemEval benchmark harness for Engram.

Usage:
    # Download data first (oracle + S split):
    python -m benchmarks.longmemeval.loaders.longmemeval_dataset --download

    # Quick dev run (5 samples, oracle split, keyword scoring only):
    python -m benchmarks.longmemeval.harness \\
        --dataset benchmarks/longmemeval/data/longmemeval_oracle.json \\
        --configs engram_lme_gemini \\
        --limit 5

    # Full oracle run with LLM judge:
    python -m benchmarks.longmemeval.harness \\
        --dataset benchmarks/longmemeval/data/longmemeval_oracle.json \\
        --configs engram_lme_gemini engram_lme_gpt4omini \\
        --llm-judge \\
        --output benchmarks/longmemeval/results/oracle_$(date +%Y%m%d).json

    # Full S-split run (500 samples, ~53 sessions each — expensive):
    python -m benchmarks.longmemeval.harness \\
        --dataset benchmarks/longmemeval/data/longmemeval_s_cleaned.json \\
        --configs engram_lme_gemini \\
        --llm-judge \\
        --conv-workers 2 \\
        --output benchmarks/longmemeval/results/s_split_$(date +%Y%m%d).json

Rate limit notes:
    - Extraction: Gemini Flash-Lite handles ~4 concurrent sessions fine.
      For S split (53 sessions/sample), keep --conv-workers 2 to avoid
      exceeding ~8 concurrent Gemini extraction calls.
    - Judge: gpt-4o-mini judge is capped at 5 workers within each sample
      (LME has only 1 QA pair/sample, so this is effectively a no-op).
    - Total cost estimate:
        oracle (3 sessions × 500 samples = 1500 extractions): ~$0.30
        S split (53 sessions × 500 samples = 26,500 extractions): ~$5.30
        Judge (500 questions × $0.001): ~$0.50
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from benchmarks.longmemeval.loaders.longmemeval_dataset import LongMemEvalDataset
from benchmarks.longmemeval.evaluation.scoring import (
    score_keyword_recall,
    score_llm_judge,
    submit_judge_batch,
    aggregate,
    ScoringResult,
    _judge_cache_key,
)
from benchmarks.longmemeval.ablation_configs import LME_ABLATION_CONFIGS, LME_PAPER_CONFIGS, AblationConfig
from benchmarks.locomo.loaders.ingestion_pipeline import ingest_conversation
from benchmarks.locomo.evaluation.token_counter import TokenBudget, estimate_tokens


def run_benchmark(
    dataset_path: str,
    config_names: list[str],
    output_path: str | None = None,
    limit: int | None = None,
    question_types: list[str] | None = None,
    use_llm_judge: bool = False,
    llm_model: str = "gpt-4o-mini",
    db_dir: str | None = None,
    verbose: bool = True,
    sample_ids_override: list[str] | None = None,
    conv_workers: int = 2,
    use_batch: bool = False,
) -> dict[str, Any]:
    """
    Run the LongMemEval benchmark for one or more ablation configs.

    Returns a results dict that can be written to JSON.
    """
    ds = LongMemEvalDataset(dataset_path)

    if verbose:
        stats = ds.stats()
        print(f"Dataset: {stats['samples']} samples, "
              f"avg {stats['avg_sessions_per_sample']} sessions/sample, "
              f"{stats['total_turns']} total turns")
        print(f"Question types: {stats['by_question_type']}")
        if limit:
            print(f"Limit: {limit} samples")
        print(f"Configs: {config_names}")
        print(f"LLM judge: {use_llm_judge} ({llm_model})")
        print()

    results: dict[str, Any] = {
        "dataset": str(dataset_path),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "configs": {},
    }

    for config_name in config_names:
        if config_name not in LME_ABLATION_CONFIGS:
            print(f"[warn] Unknown config '{config_name}', skipping. "
                  f"Available: {list(LME_ABLATION_CONFIGS.keys())}")
            continue

        config = LME_ABLATION_CONFIGS[config_name]
        if verbose:
            print(f"{'='*60}")
            print(f"Config: {config_name}")
            print(f"  {config.description}")
            print()

        config_results = _run_config(
            ds=ds,
            config=config,
            limit=limit,
            question_types=question_types,
            use_llm_judge=use_llm_judge,
            llm_model=llm_model,
            db_dir=db_dir,
            verbose=verbose,
            sample_ids_override=sample_ids_override,
            conv_workers=conv_workers,
            use_batch=use_batch,
        )
        results["configs"][config_name] = config_results

        if verbose:
            m = config_results["aggregate"]
            print(f"\n  Results for {config_name}:")
            print(f"    keyword accuracy: {m.get('keyword_accuracy', 0):.1%}")
            print(f"    keyword exact:    {m.get('keyword_exact', 0):.1%}")
            print(f"    avg tokens:       {m.get('avg_tokens', 0):.0f}")
            if "llm_accuracy" in m:
                llm_n = m.get("llm_n", "?")
                print(f"    llm accuracy:     {m['llm_accuracy']:.1%}  (n={llm_n})")
            # Per question-type breakdown
            by_type = config_results.get("by_question_type", {})
            if by_type:
                print(f"    by question type:")
                for qtype, type_stats in sorted(by_type.items()):
                    n = type_stats.get("n", 0)
                    kw = type_stats.get("keyword_accuracy", 0)
                    llm = type_stats.get("llm_accuracy")
                    llm_str = f"  llm={llm:.1%}" if llm is not None else ""
                    print(f"      {qtype}: n={n}  kw={kw:.1%}{llm_str}")
            print()

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(results, f, indent=2)
        if verbose:
            print(f"Results written to {output_path}")

    return results


def _run_config(
    ds: LongMemEvalDataset,
    config: AblationConfig,
    limit: int | None,
    question_types: list[str] | None,
    use_llm_judge: bool,
    llm_model: str,
    db_dir: str | None,
    verbose: bool,
    sample_ids_override: list[str] | None,
    conv_workers: int,
    use_batch: bool,
) -> dict[str, Any]:
    """Run a single ablation config across all (limited) LME samples."""

    base_db_dir = (
        db_dir
        if db_dir
        else str(Path.home() / ".engram" / "longmemeval_cache" / config.name)
    )
    Path(base_db_dir).mkdir(parents=True, exist_ok=True)

    samples = list(ds.iter_samples(
        limit=limit,
        question_types=question_types,
        sample_ids=sample_ids_override,
    ))

    def _process_one_sample(conv) -> dict[str, Any]:
        """Ingest (if needed) + retrieve + judge for one LME sample."""
        qa = conv.qa_pairs[0]  # LME always has exactly 1 QA pair per sample

        if verbose:
            print(f"  [{config.name}] {conv.sample_id} ({qa.category_label}): "
                  f"{len(conv.sessions)} sessions")

        store = None
        ingestion_entry = None

        import shutil as _shutil
        import yaml as _yaml
        from engram.store import GraphStore

        checkpoint_path = str(Path(base_db_dir) / f"{conv.sample_id}.db")
        done_path = str(Path(base_db_dir) / f"{conv.sample_id}.done")

        _llm_override: dict | None = None
        if config.extraction_model_config:
            mc_path = Path(config.extraction_model_config)
            if not mc_path.is_absolute():
                mc_path = Path(__file__).parent.parent.parent / mc_path
            if mc_path.exists():
                with open(mc_path) as f:
                    _llm_override = _yaml.safe_load(f).get("llm", {})
                if verbose:
                    print(f"  [{config.name}] extraction model: {_llm_override.get('model', '?')}")

        def _checkpoint_is_complete() -> bool:
            """Return True if the checkpoint DB is definitively complete.

            A .done sentinel is written after successful extraction. For older
            checkpoints that predate the sentinel, fall back to a node-count
            heuristic: if the DB contains any nodes at all, treat it as complete
            and retroactively write .done so future runs skip the count query.
            """
            if Path(done_path).exists():
                return True
            if not Path(checkpoint_path).exists():
                return False
            # Legacy or partially-written DB: check node count.
            import sqlite3 as _sqlite3
            try:
                _conn = _sqlite3.connect(checkpoint_path)
                _nc = _conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
                _conn.close()
            except Exception:
                return True  # can't query — assume complete, don't re-extract
            if _nc > 0:
                Path(done_path).touch()  # retroactively mark complete
                return True
            # Empty DB from a crashed run — delete it so we re-extract cleanly.
            Path(checkpoint_path).unlink(missing_ok=True)
            return False

        if _checkpoint_is_complete():
            if verbose:
                print(f"  [{config.name}] {conv.sample_id}: checkpoint found, skipping extraction")
            store = GraphStore(checkpoint_path)
        elif config.checkpoint_source:
            source_dir = str(Path.home() / ".engram" / "longmemeval_cache" / config.checkpoint_source)
            source_path = str(Path(source_dir) / f"{conv.sample_id}.db")
            if Path(source_path).exists():
                Path(base_db_dir).mkdir(parents=True, exist_ok=True)
                _shutil.copy2(source_path, checkpoint_path)
                Path(done_path).touch()
                if verbose:
                    print(f"  [{config.name}] {conv.sample_id}: copied from {config.checkpoint_source}")
                store = GraphStore(checkpoint_path)
            else:
                if verbose:
                    print(f"  [{config.name}] {conv.sample_id}: source checkpoint missing, extracting")
                store, ingestion_result = ingest_conversation(
                    conv=conv,
                    db_dir=base_db_dir,
                    verbose=verbose,
                    domain=config.domain,
                    dedup_threshold=config.dedup_threshold,
                    llm_override=_llm_override,
                    config_override={"sentence_index": {"enabled": True}} if config.sentence_index else None,
                )
                if ingestion_result:
                    Path(done_path).touch()
                    ingestion_entry = {
                        "sample_id": ingestion_result.sample_id,
                        "nodes_created": ingestion_result.nodes_created,
                        "elapsed_seconds": round(ingestion_result.elapsed_seconds, 2),
                        "errors": ingestion_result.errors,
                    }
        else:
            store, ingestion_result = ingest_conversation(
                conv=conv,
                db_dir=base_db_dir,
                verbose=verbose,
                domain=config.domain,
                dedup_threshold=config.dedup_threshold,
                llm_override=_llm_override,
                config_override={"sentence_index": {"enabled": True}} if config.sentence_index else None,
            )
            if ingestion_result:
                Path(done_path).touch()
                ingestion_entry = {
                    "sample_id": ingestion_result.sample_id,
                    "nodes_created": ingestion_result.nodes_created,
                    "elapsed_seconds": round(ingestion_result.elapsed_seconds, 2),
                    "errors": ingestion_result.errors,
                }

        # Retrieval
        context = _retrieve_context(conv=conv, question=qa.question, store=store, config=config)
        tokens = estimate_tokens(context)
        kw_score, matched, missed = score_keyword_recall(context, qa.answer)

        # LLM judge (sync, not batched — 1 QA per sample)
        llm_score: float | None = None
        if use_llm_judge and not use_batch:
            try:
                llm_score = score_llm_judge(
                    question=qa.question,
                    ground_truth_answer=qa.answer,
                    retrieved_context=context,
                    model=llm_model,
                    abstention_mode=config.abstention_mode,
                )
            except Exception as e:
                if verbose:
                    print(f"    [warn] LLM judge failed for {conv.sample_id}: {e}")

        sr = ScoringResult(
            question=qa.question,
            ground_truth_answer=qa.answer,
            retrieved_context=context,
            keyword_score=kw_score,
            llm_score=llm_score,
            matched_keywords=matched,
            missed_keywords=missed,
            tokens_used=tokens,
        )

        return {
            "sample_id": conv.sample_id,
            "question_type": qa.category_label,
            "scoring_result": sr,
            "ingestion_entry": ingestion_entry,
            "tokens": tokens,
        }

    workers = min(conv_workers, len(samples)) if samples else 1
    sample_outputs: list[dict] = [None] * len(samples)  # type: ignore[list-item]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_idx = {pool.submit(_process_one_sample, s): i for i, s in enumerate(samples)}
        for fut in as_completed(future_to_idx):
            idx = future_to_idx[fut]
            try:
                sample_outputs[idx] = fut.result()
            except Exception as e:
                import traceback
                sid = samples[idx].sample_id if idx < len(samples) else "unknown"
                print(f"  [error] sample {sid} failed: {e}", flush=True)
                traceback.print_exc()
                # Placeholder so indexing stays intact
                qa = samples[idx].qa_pairs[0]
                sample_outputs[idx] = {
                    "sample_id": sid,
                    "question_type": qa.category_label,
                    "scoring_result": ScoringResult(
                        question=qa.question,
                        ground_truth_answer=qa.answer,
                        retrieved_context="",
                        keyword_score=0.0,
                        llm_score=None,
                        matched_keywords=[],
                        missed_keywords=[],
                        tokens_used=0,
                    ),
                    "ingestion_entry": None,
                    "tokens": 0,
                }

    # Batch judge: submit after all retrieval is done
    if use_llm_judge and use_batch:
        all_pairs = [
            (o["scoring_result"].question,
             o["scoring_result"].ground_truth_answer,
             o["scoring_result"].retrieved_context)
            for o in sample_outputs
        ]
        if verbose:
            print(f"\n  [batch] Submitting {len(all_pairs)} judge requests via Batch API ...")
        key_to_score = submit_judge_batch(
            requests=all_pairs,
            model=llm_model,
            poll_interval=30,
            verbose=verbose,
            abstention_mode=config.abstention_mode,
        )
        for out in sample_outputs:
            sr = out["scoring_result"]
            key = _judge_cache_key(
                sr.question, sr.ground_truth_answer, sr.retrieved_context, llm_model
            )
            sr.llm_score = key_to_score.get(key)

    # Aggregate
    all_scoring = [o["scoring_result"] for o in sample_outputs]
    all_ingestion = [o["ingestion_entry"] for o in sample_outputs if o["ingestion_entry"]]
    budget = TokenBudget()
    budget.query_count = len(sample_outputs)
    budget.retrieval_context = sum(o["tokens"] for o in sample_outputs)

    # Per-question-type breakdown
    from collections import defaultdict
    by_type: dict[str, list[ScoringResult]] = defaultdict(list)
    for out in sample_outputs:
        by_type[out["question_type"]].append(out["scoring_result"])

    by_type_agg: dict[str, dict] = {}
    for qtype, srs in by_type.items():
        agg = aggregate(srs)
        by_type_agg[qtype] = agg

    return {
        "config_name": config.name,
        "aggregate": aggregate(all_scoring),
        "by_question_type": by_type_agg,
        "token_budget": budget.as_dict(),
        "ingestion": all_ingestion,
        "per_sample": [
            {
                "sample_id": o["sample_id"],
                "question_type": o["question_type"],
                "question": o["scoring_result"].question[:100],
                "answer": o["scoring_result"].ground_truth_answer,
                "keyword_score": o["scoring_result"].keyword_score,
                "llm_score": o["scoring_result"].llm_score,
                "tokens": o["scoring_result"].tokens_used,
            }
            for o in sample_outputs
        ],
    }


def _retrieve_context(conv, question: str, store, config: AblationConfig) -> str:
    """Retrieve context for a question given the ablation config."""
    if store is None:
        return ""

    from engram.retriever import retrieve_with_stats

    strategies: dict[str, Any] = {
        "superseded_pruning": config.superseded_pruning,
        "recency_decay": config.recency_decay,
        "recency_half_life_days": config.recency_half_life_days,
        "confidence_threshold": config.confidence_threshold or 0.0,
        "token_budget": config.token_budget or 0,
        "semantic": config.semantic,
        "relevance_scoring": config.relevance_scoring,
        "resolve_dates": config.resolve_dates,
        "topk_formula": config.topk_formula,
        "query_expansion": config.query_expansion,
        "person_anchoring": config.person_anchoring,
        "temporal_proximity": config.temporal_proximity,
        "query_coreference": config.query_coreference,
        "allowed_session_ids": None,
        "edge_weight_scoring": config.edge_weight_scoring,
        "semantic_rerank": config.semantic_rerank,
        "cross_encoder_rerank": config.cross_encoder_rerank,
        "cross_encoder_model": config.cross_encoder_model,
        "rrf_rerank": config.rrf_rerank,
        "rrf_weights": config.rrf_weights,
        "sentence_index": config.sentence_index,
        "preference_fanout": config.preference_fanout,
        "preference_fanout_cap": config.preference_fanout_cap,
        "semantic_retrieval": config.semantic_retrieval,
        "semantic_retrieval_k": config.semantic_retrieval_k,
    }

    try:
        result = retrieve_with_stats(
            store=store,
            task_description=question,
            hops=config.bfs_hops,
            top_k=config.top_k,
            strategies=strategies,
        )
        return result.markdown or ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Preflight check
# ---------------------------------------------------------------------------

def _preflight_check(
    dataset_path: str,
    config_names: list[str],
    limit: int | None,
    db_dir_override: str | None,
) -> None:
    from engram.config import load_config
    cfg = load_config()
    llm = cfg.get("llm", {})
    print("=" * 60)
    print("PRE-RUN CONFIG CHECK (LongMemEval)")
    print(f"  Extraction model : {llm.get('model', 'UNKNOWN')}")
    print(f"  max_tokens       : {llm.get('max_tokens', 'UNKNOWN')}")
    print(f"  base_url         : {llm.get('base_url', '')}")
    print()

    ds = LongMemEvalDataset(dataset_path)
    samples = list(ds.iter_samples(limit=limit))
    stats = ds.stats()
    print(f"  Dataset: {stats['samples']} samples, "
          f"avg {stats['avg_sessions_per_sample']} sessions/sample")
    print(f"  Processing: {len(samples)} samples")

    for config_name in config_names:
        if config_name not in LME_ABLATION_CONFIGS:
            continue
        config = LME_ABLATION_CONFIGS[config_name]
        db_dir = db_dir_override or str(
            Path.home() / ".engram" / "longmemeval_cache" / config_name
        )
        will_extract = []
        will_reuse = []
        will_copy = []
        for s in samples:
            cp = Path(db_dir) / f"{s.sample_id}.db"
            if cp.exists():
                will_reuse.append(s.sample_id)
            elif config.checkpoint_source and not db_dir_override:
                src = (Path.home() / ".engram" / "longmemeval_cache"
                       / config.checkpoint_source / f"{s.sample_id}.db")
                if src.exists():
                    will_copy.append(s.sample_id)
                else:
                    will_extract.append(s.sample_id)
            else:
                will_extract.append(s.sample_id)

        print(f"  Config: {config_name}")
        print(f"    db_dir: {db_dir}")
        if will_reuse:
            print(f"    Checkpoints found (skip): {len(will_reuse)} samples")
        if will_copy:
            print(f"    Will copy from {config.checkpoint_source}: {len(will_copy)} samples")
        if will_extract:
            avg_sess = stats['avg_sessions_per_sample']
            est_calls = len(will_extract) * avg_sess
            print(f"    *** Will extract: {len(will_extract)} samples "
                  f"(~{est_calls:.0f} LLM calls @ ~{avg_sess:.0f} sessions/sample)")
        else:
            print(f"    All checkpoints present — no extraction needed")

    print("=" * 60)
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="LongMemEval benchmark harness for Engram",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--dataset", required=True,
        help=(
            "Path to LME JSON file. Use longmemeval_oracle.json for fast dev runs; "
            "longmemeval_s_cleaned.json for full evaluation."
        ),
    )
    parser.add_argument(
        "--configs", nargs="+", default=["engram_lme_gemini"],
        help=f"Ablation configs to run. Available: {list(LME_ABLATION_CONFIGS.keys())}",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Limit to N samples (for rapid dev iteration)",
    )
    parser.add_argument(
        "--question-types", nargs="+", default=None, dest="question_types",
        help=(
            "Filter to specific question types: knowledge-update, temporal-reasoning, "
            "multi-session, single-session-user, single-session-assistant, "
            "single-session-preference, absent-information"
        ),
    )
    parser.add_argument(
        "--llm-judge", action="store_true",
        help="Use LLM to judge answer quality",
    )
    parser.add_argument(
        "--llm-model", default="gpt-4o-mini",
        help="Model for LLM judge (default: gpt-4o-mini for cost; "
             "use gemini-2.5-flash-lite for faster judging)",
    )
    parser.add_argument(
        "--output", default=None,
        help="Path to write JSON results",
    )
    parser.add_argument(
        "--db-dir", default=None,
        help="Directory for SQLite DBs. Defaults to ~/.engram/longmemeval_cache/<config>/",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress progress output",
    )
    parser.add_argument(
        "--sample-ids", nargs="+", default=None, dest="sample_ids",
        help="Limit to specific question_id values.",
    )
    parser.add_argument(
        "--conv-workers", type=int, default=2, dest="conv_workers",
        help=(
            "Samples to process in parallel (default: 2). "
            "Keep at 2 for S split (~53 sessions/sample) to avoid rate limits. "
            "Up to 4 is safe for oracle split (~3 sessions/sample)."
        ),
    )
    parser.add_argument(
        "--use-batch", action="store_true", dest="use_batch",
        help="Use OpenAI Batch API for judging (50%% cheaper, 24h SLA).",
    )
    parser.add_argument(
        "--paper", action="store_true",
        help=f"Run paper configs: {LME_PAPER_CONFIGS}",
    )
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    config_names = args.configs
    if args.paper:
        config_names = LME_PAPER_CONFIGS

    if not args.quiet:
        _preflight_check(
            dataset_path=args.dataset,
            config_names=config_names,
            limit=args.limit,
            db_dir_override=args.db_dir,
        )

    run_benchmark(
        dataset_path=args.dataset,
        config_names=config_names,
        output_path=args.output,
        limit=args.limit,
        question_types=args.question_types,
        use_llm_judge=args.llm_judge,
        llm_model=args.llm_model,
        db_dir=args.db_dir,
        verbose=not args.quiet,
        sample_ids_override=args.sample_ids,
        conv_workers=args.conv_workers,
        use_batch=args.use_batch,
    )


if __name__ == "__main__":
    main()
