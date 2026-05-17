"""
Failure analysis for LOCOMO conv-26 benchmark.

For each failing QA pair (keyword_score=0), determines whether the failure is:
  - extraction gap: answer keywords absent from every graph node
  - retrieval gap:  answer keywords present in graph but not returned by retrieval

Usage:
    python -m benchmarks.locomo.failure_analysis \
        --dataset benchmarks/locomo/data/locomo10.json \
        --config engram_sentence_index
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from benchmarks.locomo.loaders.locomo_dataset import LocomoDataset, QA_CATEGORY_LABELS
from benchmarks.locomo.evaluation.scoring import score_keyword_recall, _tokenize
from benchmarks.locomo.ablation_configs import ABLATION_CONFIGS


def _all_node_text(store) -> list[str]:
    """Return fact text of every node in the store (for extraction-gap check)."""
    conn = store.conn
    rows = conn.execute("SELECT fact FROM nodes").fetchall()
    return [r[0] for r in rows]


def _keywords_in_graph(answer: str, all_facts: list[str]) -> tuple[bool, list[str], list[str]]:
    """
    Check whether answer keywords appear anywhere in the full graph.
    Returns (any_found, found_keywords, missing_keywords).
    """
    answer_kws = set(_tokenize(str(answer).lower()))
    if not answer_kws:
        return False, [], []

    graph_words: set[str] = set()
    for fact in all_facts:
        graph_words.update(_tokenize(fact.lower()))

    found = [w for w in answer_kws if w in graph_words]
    missing = [w for w in answer_kws if w not in graph_words]
    return len(found) > 0, found, missing


def run_failure_analysis(
    dataset_path: str,
    config_name: str,
    conv_id: str = "conv-26",
    min_overlap: float = 0.7,
    verbose: bool = True,
) -> list[dict[str, Any]]:
    """Run retrieval for every QA pair, classify failures."""
    from waystone.store import GraphStore
    from waystone.retriever import retrieve_with_stats

    config = ABLATION_CONFIGS[config_name]
    db_dir = Path.home() / ".waystone" / "locomo_cache" / config_name
    checkpoint_path = db_dir / f"{conv_id}.db"

    if not checkpoint_path.exists():
        raise FileNotFoundError(
            f"Checkpoint not found: {checkpoint_path}\n"
            f"Run the harness with --configs {config_name} first."
        )

    ds = LocomoDataset(dataset_path)
    convs = list(ds.iter_conversations(sample_ids=[conv_id]))
    if not convs:
        raise ValueError(f"Conversation {conv_id} not found in dataset")
    conv = convs[0]

    store = GraphStore(str(checkpoint_path))
    all_facts = _all_node_text(store)

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
        "allowed_session_ids": None,
        "edge_weight_scoring": config.edge_weight_scoring,
        "semantic_rerank": config.semantic_rerank,
        "cross_encoder_rerank": config.cross_encoder_rerank,
        "cross_encoder_model": config.cross_encoder_model,
        "rrf_rerank": config.rrf_rerank,
        "rrf_weights": config.rrf_weights,
        "sentence_index": config.sentence_index,
    }

    results = []
    failures = []
    n_pass = n_fail = 0

    for qa in conv.qa_pairs:
        if qa.category == 5:  # adversarial — excluded from accuracy scoring
            continue

        try:
            ret = retrieve_with_stats(
                store=store,
                task_description=qa.question,
                hops=config.bfs_hops,
                top_k=config.top_k,
                strategies=strategies,
            )
            context = ret.markdown or ""
        except Exception as e:
            context = ""

        score, matched, missed = score_keyword_recall(context, qa.answer, min_overlap)

        if score > 0:
            n_pass += 1
            results.append({
                "question": qa.question,
                "answer": qa.answer,
                "category": qa.category_label,
                "score": score,
                "gap_type": "pass",
                "matched": matched,
                "missed": missed,
            })
        else:
            # Classify the failure
            any_found, graph_found, graph_missing = _keywords_in_graph(qa.answer, all_facts)
            gap_type = "retrieval_gap" if any_found else "extraction_gap"
            n_fail += 1
            entry = {
                "question": qa.question,
                "answer": qa.answer,
                "category": qa.category_label,
                "score": 0.0,
                "gap_type": gap_type,
                "answer_kws": list(_tokenize(str(qa.answer).lower())),
                "graph_found": graph_found,
                "graph_missing": graph_missing,
                "retrieved_tokens": len(context),
            }
            failures.append(entry)
            results.append(entry)

    if verbose:
        print(f"\n{'='*60}")
        print(f"FAILURE ANALYSIS — {conv_id} / {config_name}")
        print(f"  Total QA (cat 1-4): {n_pass + n_fail}")
        print(f"  Pass: {n_pass}  Fail: {n_fail}  Accuracy: {n_pass/(n_pass+n_fail):.1%}")
        print(f"\n  Failures by gap type:")

        ext_gaps = [f for f in failures if f["gap_type"] == "extraction_gap"]
        ret_gaps = [f for f in failures if f["gap_type"] == "retrieval_gap"]

        print(f"    extraction gaps : {len(ext_gaps)}")
        print(f"    retrieval gaps  : {len(ret_gaps)}")

        if ext_gaps:
            print(f"\n{'─'*60}")
            print(f"EXTRACTION GAPS ({len(ext_gaps)}) — answer facts never extracted:")
            for i, f in enumerate(ext_gaps, 1):
                print(f"\n  [{i}] [{f['category']}] {f['question']}")
                print(f"       Answer: {f['answer']}")
                print(f"       Missing kws from graph: {f['graph_missing']}")

        if ret_gaps:
            print(f"\n{'─'*60}")
            print(f"RETRIEVAL GAPS ({len(ret_gaps)}) — fact in graph but not retrieved:")
            for i, f in enumerate(ret_gaps, 1):
                print(f"\n  [{i}] [{f['category']}] {f['question']}")
                print(f"       Answer: {f['answer']}")
                print(f"       Found in graph: {f['graph_found']}")
                print(f"       Missing from retrieval: {f['graph_missing']}")

        # Category breakdown of failures
        print(f"\n{'─'*60}")
        print("Failure breakdown by category:")
        from collections import Counter
        cat_counts = Counter(f["category"] for f in failures)
        for cat, cnt in sorted(cat_counts.items()):
            print(f"    {cat}: {cnt}")

    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--config", default="engram_sentence_index")
    parser.add_argument("--conv", default="conv-26")
    args = parser.parse_args()

    run_failure_analysis(
        dataset_path=args.dataset,
        config_name=args.config,
        conv_id=args.conv,
        verbose=True,
    )


if __name__ == "__main__":
    main()
