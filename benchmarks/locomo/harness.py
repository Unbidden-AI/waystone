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
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    submit_judge_batch,
    aggregate,
    ScoringResult,
    _judge_cache_key,
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
    llm_model: str = "gemini-2.5-flash-lite",
    categories: list[int] | None = None,
    db_dir: str | None = None,
    verbose: bool = True,
    sample_ids_override: list[str] | None = None,
    conv_workers: int = 4,
    use_batch: bool = False,
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

    if sample_ids_override:
        sample_ids = sample_ids_override

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
    conv_workers: int = 4,
    use_batch: bool = False,
) -> dict[str, Any]:
    """Run a single ablation config across all (limited) conversations.

    Conversations run in parallel (conv_workers threads). Each conversation's
    retrieval pass is sequential (SQLite), but LLM judge calls within each
    conversation are concurrent. With multiple conv_workers active simultaneously,
    judge calls across conversations also run concurrently.
    """
    if db_dir:
        tmp_dir = db_dir
    else:
        from pathlib import Path as _Path
        tmp_dir = str(_Path.home() / ".engram" / "locomo_cache" / config.name)
        _Path(tmp_dir).mkdir(parents=True, exist_ok=True)
    conversations = list(ds.iter_conversations(limit=limit, sample_ids=sample_ids))

    def _process_one_conv(conv) -> dict[str, Any]:
        """Process a single conversation: ingest (if needed) + retrieve + judge."""
        if verbose:
            print(f"  [{config.name}] {conv.sample_id}: "
                  f"{len(conv.sessions)} sessions, {len(conv.qa_pairs)} QA")

        ingestion_entry: dict | None = None

        if config.full_context:
            store = None
        else:
            from pathlib import Path as _Path
            from engram.store import GraphStore as _GraphStore
            import shutil as _shutil
            import yaml as _yaml
            checkpoint_path = str(_Path(tmp_dir) / f"{conv.sample_id}.db")

            # Load extraction LLM override if the config specifies one
            _llm_override: dict | None = None
            if config.extraction_model_config:
                _mc_path = _Path(config.extraction_model_config)
                if not _mc_path.is_absolute():
                    _mc_path = _Path(__file__).parent.parent.parent / _mc_path
                if _mc_path.exists():
                    with open(_mc_path) as _f:
                        _llm_override = _yaml.safe_load(_f).get("llm", {})
                    if verbose:
                        print(f"  [{config.name}] extraction model: {_llm_override.get('model', '?')}")

            if _Path(checkpoint_path).exists():
                if verbose:
                    print(f"  [{config.name}] {conv.sample_id}: checkpoint found, skipping extraction")
                store = _GraphStore(checkpoint_path)
            elif config.checkpoint_source:
                # Copy checkpoint from the source config's dir instead of re-extracting
                source_dir = str(_Path.home() / ".engram" / "locomo_cache" / config.checkpoint_source)
                source_path = str(_Path(source_dir) / f"{conv.sample_id}.db")
                if _Path(source_path).exists():
                    _Path(tmp_dir).mkdir(parents=True, exist_ok=True)
                    _shutil.copy2(source_path, checkpoint_path)
                    if verbose:
                        print(f"  [{config.name}] {conv.sample_id}: copied checkpoint from {config.checkpoint_source}")
                    store = _GraphStore(checkpoint_path)
                else:
                    if verbose:
                        print(f"  [{config.name}] {conv.sample_id}: source checkpoint not found at {source_path}, extracting")
                    store, ingestion_result = ingest_conversation(
                        conv=conv,
                        db_dir=tmp_dir,
                        verbose=verbose,
                        domain=config.domain,
                        dedup_threshold=config.dedup_threshold,
                        llm_override=_llm_override,
                        config_override={"sentence_index": {"enabled": True}} if config.sentence_index else None,
                    )
                    if ingestion_result:
                        ingestion_entry = {
                            "sample_id": ingestion_result.sample_id,
                            "nodes_created": ingestion_result.nodes_created,
                            "elapsed_seconds": round(ingestion_result.elapsed_seconds, 2),
                            "errors": ingestion_result.errors,
                        }
            else:
                store, ingestion_result = ingest_conversation(
                    conv=conv,
                    db_dir=tmp_dir,
                    verbose=verbose,
                    domain=config.domain,
                    dedup_threshold=config.dedup_threshold,
                    llm_override=_llm_override,
                    config_override={"sentence_index": {"enabled": True}} if config.sentence_index else None,
                )
                if ingestion_result:
                    ingestion_entry = {
                        "sample_id": ingestion_result.sample_id,
                        "nodes_created": ingestion_result.nodes_created,
                        "elapsed_seconds": round(ingestion_result.elapsed_seconds, 2),
                        "errors": ingestion_result.errors,
                    }

            # Post-hoc edge inference: add missing involves edges from tag overlap
            if store is not None and config.infer_edges:
                n_new = store.infer_involves_edges(
                    weight=config.infer_edges_weight,
                    name_only=config.infer_edges_name_only,
                )
                if verbose:
                    print(f"  [{config.name}] {conv.sample_id}: inferred {n_new} new involves edges")

        # Retrieval pass — sequential (reads SQLite store)
        qa_pairs = conv.qa_pairs
        if categories:
            qa_pairs = [q for q in qa_pairs if q.category in categories]

        retrieved: list[tuple[Any, str, float, list, list, int, str]] = []
        tokens_total = 0
        for qa in qa_pairs:
            context = _retrieve_context(
                conv=conv,
                question=qa.question,
                store=store,
                config=config,
                relevant_session_ids=qa.relevant_session_ids if config.session_scoped else None,
            )
            tokens = estimate_tokens(context)
            tokens_total += tokens
            kw_score, matched, missed = score_keyword_recall(context, qa.answer)
            retrieved.append((qa, context, kw_score, matched, missed, tokens, qa.category_label))

        # LLM judge pass — concurrent (pure I/O, no shared state)
        # Skipped in batch mode; batch submission happens after all convs complete.
        llm_scores: dict[int, float | None] = {i: None for i in range(len(retrieved))}
        if use_llm_judge and not use_batch:
            def _judge(idx_qa_ctx):
                idx, qa, context = idx_qa_ctx
                try:
                    return idx, score_llm_judge(
                        question=qa.question,
                        ground_truth_answer=qa.answer,
                        retrieved_context=context,
                        model=llm_model,
                        abstention_mode=config.abstention_mode,
                    )
                except Exception as e:
                    if verbose:
                        print(f"    [warn] LLM judge failed for Q{idx}: {e}")
                    return idx, None

            # OpenAI TPM limit (200K/min) can't handle 20 concurrent judge requests
            # at ~3600 tokens each. Cap to 5 workers for gpt-* models (50 peak concurrent
            # with conv_workers=5 already saturates TPM; judge cache avoids re-scoring).
            _judge_workers = 5 if llm_model.startswith("gpt-") else 20
            with ThreadPoolExecutor(max_workers=_judge_workers) as pool:
                futures = [
                    pool.submit(_judge, (i, qa, ctx))
                    for i, (qa, ctx, *_) in enumerate(retrieved)
                ]
                for fut in as_completed(futures):
                    idx, score = fut.result()
                    llm_scores[idx] = score

        scoring_results = [
            ScoringResult(
                question=qa.question,
                ground_truth_answer=qa.answer,
                retrieved_context=context,
                keyword_score=kw_score,
                llm_score=llm_scores[i],
                matched_keywords=matched,
                missed_keywords=missed,
                tokens_used=tokens,
            )
            for i, (qa, context, kw_score, matched, missed, tokens, _) in enumerate(retrieved)
        ]
        cat_labels = [t[6] for t in retrieved]

        return {
            "scoring_results": scoring_results,
            "cat_labels": cat_labels,
            "ingestion_entry": ingestion_entry,
            "tokens_total": tokens_total,
            "query_count": len(retrieved),
        }

    # Run conversations in parallel
    workers = min(conv_workers, len(conversations)) if conversations else 1
    conv_outputs: list[dict] = [None] * len(conversations)  # type: ignore[list-item]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_idx = {pool.submit(_process_one_conv, conv): i
                         for i, conv in enumerate(conversations)}
        for fut in as_completed(future_to_idx):
            idx = future_to_idx[fut]
            try:
                conv_outputs[idx] = fut.result()
            except Exception as e:
                import traceback
                print(f"  [error] conversation {conversations[idx].sample_id} failed: {e}", flush=True)
                traceback.print_exc()
                conv_outputs[idx] = {
                    "scoring_results": [], "cat_labels": [],
                    "ingestion_entry": None, "tokens_total": 0, "query_count": 0,
                }

    # Batch judge pass — submit all uncached (q, a, ctx) triples in one API call.
    # Runs after all retrieval completes so we send a single batch for the whole config.
    if use_llm_judge and use_batch:
        all_pairs: list[tuple[str, str, str]] = []
        for out in conv_outputs:
            for sr in out["scoring_results"]:
                all_pairs.append((sr.question, sr.ground_truth_answer, sr.retrieved_context))

        if verbose:
            print(f"\n  [batch] Submitting {len(all_pairs)} judge requests via Batch API ...")

        key_to_score = submit_judge_batch(
            requests=all_pairs,
            model=llm_model,
            poll_interval=30,
            verbose=verbose,
            abstention_mode=config.abstention_mode,
        )

        # Assign scores back by recomputing each result's cache key
        for out in conv_outputs:
            for sr in out["scoring_results"]:
                key = _judge_cache_key(
                    sr.question, sr.ground_truth_answer, sr.retrieved_context, llm_model
                )
                sr.llm_score = key_to_score.get(key)

    # Merge results in original conversation order
    all_scoring_results: list[ScoringResult] = []
    all_categories: list[str] = []
    all_ingestion_results: list[dict] = []
    budget = TokenBudget()

    for out in conv_outputs:
        all_scoring_results.extend(out["scoring_results"])
        all_categories.extend(out["cat_labels"])
        if out["ingestion_entry"]:
            all_ingestion_results.append(out["ingestion_entry"])
        budget.retrieval_context += out["tokens_total"]
        budget.query_count += out["query_count"]

    return {
        "config_name": config.name,
        "aggregate": aggregate(all_scoring_results),
        "token_budget": budget.as_dict(),
        "ingestion": all_ingestion_results,
        "per_question": [
            {
                "question": r.question[:80],
                "answer": r.ground_truth_answer,
                "category": cat,
                "keyword_score": r.keyword_score,
                "llm_score": r.llm_score,
                "tokens": r.tokens_used,
            }
            for r, cat in zip(all_scoring_results, all_categories)
        ],
    }


def _build_prior_turns_window(conv, n: int) -> str:
    """Return the last n raw turns across all sessions as a formatted string."""
    all_turns = []
    for session in conv.sessions:
        for turn in session.turns:
            all_turns.append(f"{turn.speaker}: {turn.text}")
    tail = all_turns[-n:] if n < len(all_turns) else all_turns
    return "\n".join(tail)


def _retrieve_context(
    conv,
    question: str,
    store,
    config: AblationConfig,
    relevant_session_ids: list[str] | None = None,
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

    parts: list[str] = []

    if config.use_bfs and store is not None:
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
            "temporal_sort": config.temporal_sort,
            "query_coreference": config.query_coreference,
            "allowed_session_ids": relevant_session_ids if config.session_scoped else None,
            "edge_weight_scoring": config.edge_weight_scoring,
            "semantic_rerank": config.semantic_rerank,
            "semantic_rerank_cap": config.semantic_rerank_cap,
            "cross_encoder_rerank": config.cross_encoder_rerank,
            "cross_encoder_model": config.cross_encoder_model,
            "rrf_rerank": config.rrf_rerank,
            "rrf_weights": config.rrf_weights,
            "sentence_index": config.sentence_index,
            "preference_fanout": config.preference_fanout,
            "preference_fanout_cap": config.preference_fanout_cap,
            "semantic_retrieval": config.semantic_retrieval,
            "semantic_retrieval_k": config.semantic_retrieval_k,
            "temporal_auto_route": config.temporal_auto_route,
            "extend_stop_words": config.extend_stop_words,
            "half_life_by_type": config.half_life_by_type,
            "phase_rotation": config.phase_rotation,
            "soft_supersede": config.soft_supersede,
        }

        try:
            result = retrieve_with_stats(
                store=store,
                task_description=question,
                hops=config.bfs_hops,
                top_k=config.top_k,  # None triggers dynamic scaling in retrieve_with_stats
                strategies=strategies,
            )
            if result.markdown:
                parts.append(result.markdown)
        except Exception:
            pass

    if config.prior_turns_window > 0:
        window_text = _build_prior_turns_window(conv, config.prior_turns_window)
        if window_text:
            parts.append(f"## Recent Conversation\n{window_text}")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Pre-run preflight check
# ---------------------------------------------------------------------------

def _preflight_check(
    dataset_path: str,
    config_names: list[str],
    sample_ids: list[str],
    limit: int | None,
    db_dir_override: str | None,
) -> None:
    """Print extraction model config and which conversations will need extraction vs reuse."""
    from engram.config import load_config

    cfg = load_config()
    llm = cfg.get("llm", {})
    model = llm.get("model", "UNKNOWN")
    max_tokens = llm.get("max_tokens", "UNKNOWN")
    base_url = llm.get("base_url", "")

    print("=" * 60)
    print("PRE-RUN CONFIG CHECK")
    print(f"  Extraction model : {model}")
    print(f"  max_tokens       : {max_tokens}")
    print(f"  base_url         : {base_url}")
    print()

    ds = LocomoDataset(dataset_path)
    convs = list(ds.iter_conversations(limit=limit, sample_ids=sample_ids))

    for config_name in config_names:
        if config_name not in ABLATION_CONFIGS:
            continue
        config = ABLATION_CONFIGS[config_name]
        if config.full_context:
            print(f"  Config: {config_name}  — full_context mode, no extraction")
            continue

        if db_dir_override:
            db_dir = db_dir_override
        else:
            db_dir = str(Path.home() / ".engram" / "locomo_cache" / config_name)

        will_extract = []
        will_reuse = []
        will_copy = []
        for conv in convs:
            cp = Path(db_dir) / f"{conv.sample_id}.db"
            if cp.exists():
                will_reuse.append(conv.sample_id)
            elif config.checkpoint_source and not db_dir_override:
                src = Path.home() / ".engram" / "locomo_cache" / config.checkpoint_source / f"{conv.sample_id}.db"
                if src.exists():
                    will_copy.append(conv.sample_id)
                else:
                    will_extract.append(conv.sample_id)
            else:
                will_extract.append(conv.sample_id)

        print(f"  Config: {config_name}")
        print(f"    db_dir: {db_dir}")
        if will_reuse:
            print(f"    Checkpoint found (skip extraction) : {will_reuse}")
        if will_copy:
            print(f"    Will copy from {config.checkpoint_source}  : {will_copy}")
        if will_extract:
            print(f"    *** NO checkpoint — WILL EXTRACT  : {will_extract}")
        if not will_extract:
            print(f"    All checkpoints present — no extraction needed")

    print("=" * 60)
    print()


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
        "--llm-model", default="gemini-2.5-flash-lite",
        help="Model for LLM judge (default: gemini-2.5-flash-lite; "
             "use 'local:<model>' for a local LM Studio/Ollama server — free, "
             "set LOCAL_LLM_BASE_URL env var if not localhost:1234; "
             "use claude-haiku-4-5-20251001 for final paper numbers)",
    )
    parser.add_argument(
        "--categories", nargs="+", type=int, default=[1, 2, 3, 4],
        help="Filter to QA categories (1=single_hop, 2=temporal, 3=open_domain, "
             "4=multi_hop, 5=adversarial). Default: 1 2 3 4 (official protocol, excludes cat 5). "
             "Pass --categories 1 2 3 4 5 to include adversarial.",
    )
    parser.add_argument(
        "--output", default=None,
        help="Path to write JSON results",
    )
    parser.add_argument(
        "--db-dir", default=None,
        help="Directory for SQLite DBs. Defaults to ~/.engram/locomo_cache/<config_name>/ "
             "so checkpoints persist automatically across runs. Override to use a different "
             "location or share DBs between configs. Different domains or dedup_thresholds "
             "need different dirs.",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress progress output",
    )
    parser.add_argument(
        "--sample-ids", nargs="+", default=None, dest="sample_ids",
        help="Limit to specific conversation IDs (e.g. conv-42). Overrides --split filtering.",
    )
    parser.add_argument(
        "--quick", action="store_true",
        help="Quick single-conversation test on conv-26. Use for change validation "
             "before running the full dev set.",
    )
    parser.add_argument(
        "--conv-workers", type=int, default=4, dest="conv_workers",
        help="Number of conversations to process in parallel (default: 4).",
    )
    parser.add_argument(
        "--use-batch", action="store_true", dest="use_batch",
        help="Use OpenAI Batch API for judging (separate RPD quota, 50%% cheaper, 24h SLA). "
             "Retrieval runs first across all convs, then a single batch is submitted. "
             "Requires OPENAI_API_KEY. Only applies when --llm-judge is also set.",
    )
    args = parser.parse_args()

    # Load .env so API keys (OPENAI_API_KEY, GOOGLE_API_KEY, etc.) are available
    # for both extraction and judging — extractor.py reads os.environ directly.
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass  # python-dotenv not installed; keys must be set in environment

    # Resolve config names
    config_names = args.configs
    if "all" in config_names:
        config_names = list(ABLATION_CONFIGS.keys())
    elif "paper" in config_names:
        config_names = PAPER_CONFIGS

    # --quick: single-conversation change validation on conv-26
    sample_ids_override = args.sample_ids
    limit = args.limit
    if args.quick:
        sample_ids_override = ["conv-26"]
        limit = 1
        if not args.quiet:
            print("[quick mode] Running on conv-26 only. Use full dev set only after approval.")

    if not args.quiet:
        _preflight_check(
            dataset_path=args.dataset,
            config_names=config_names,
            sample_ids=sample_ids_override or (
                DEV if args.split == "dev" else TEST if args.split == "test" else ALL
            ),
            limit=limit,
            db_dir_override=args.db_dir,
        )

    run_benchmark(
        dataset_path=args.dataset,
        config_names=config_names,
        output_path=args.output,
        split=args.split,
        limit=limit,
        use_llm_judge=args.llm_judge,
        llm_model=args.llm_model,
        categories=args.categories,
        db_dir=args.db_dir,
        verbose=not args.quiet,
        sample_ids_override=sample_ids_override,
        conv_workers=args.conv_workers,
        use_batch=args.use_batch,
    )


if __name__ == "__main__":
    main()
