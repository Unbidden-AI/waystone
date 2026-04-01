"""
Ablation configurations for the LOCOMO benchmark.

Each config maps to a named retrieval strategy preset. These drive the ablation
study section of the arXiv paper — showing which components of Engram's
strategy pipeline contribute to accuracy and token efficiency.

Config names mirror the STRATEGY_PRESETS in run_benchmark.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AblationConfig:
    name: str
    description: str
    # Retrieval strategy flags
    superseded_pruning: bool = True
    confidence_threshold: float | None = None   # None = disabled
    recency_decay: bool = True
    top_k: int | None = None                    # None = no limit
    token_budget: int | None = None             # None = no limit
    # Retrieval mode
    use_bfs: bool = True                        # False = flat vector/keyword only
    # For baseline comparisons
    full_context: bool = False                  # Inject raw transcript instead
    # Domain profile for extraction
    domain: str = "episodic_personal"           # Profile name from domain_profiles.py
    # Semantic/vector augmentation
    semantic: bool = False                      # True = embed nodes + hybrid retrieval
    # Recency decay tuning (days). Default 30 for real-time use; increase for long-span corpora.
    recency_half_life_days: int = 30
    # Seed relevance scoring: rank entry nodes by tag overlap before BFS
    relevance_scoring: bool = True
    # BFS hop depth (retriever default: 3)
    bfs_hops: int = 3
    # Resolve relative date phrases at display time (e.g. "last week" → absolute date)
    resolve_dates: bool = True
    # Dynamic top_k scaling formula when top_k=None: "sqrt" or "log2"
    topk_formula: str = "sqrt"
    # Semantic dedup threshold at ingest time (per-insert cosine sim, Python 3.13 path)
    # Default 0.92 merges paraphrases; raise to 0.95-0.97 to reduce over-pruning with bge-small
    dedup_threshold: float = 0.92
    # Number of raw conversation turns to append after BFS results as short-term context.
    # 0 = disabled (BFS only). When use_bfs=False, acts as a sliding-window baseline.
    prior_turns_window: int = 0


# ---------------------------------------------------------------------------
# Named configurations used in the paper
# ---------------------------------------------------------------------------

ABLATION_CONFIGS: dict[str, AblationConfig] = {
    # ---- Baselines ----
    "full_context": AblationConfig(
        name="full_context",
        description="Entire conversation transcript injected (no memory system). "
                    "Upper bound on context; lower bound on token efficiency.",
        full_context=True,
        use_bfs=False,
    ),
    "no_memory": AblationConfig(
        name="no_memory",
        description="No context injected at all. Tests LLM's parametric knowledge only.",
        full_context=False,
        use_bfs=False,
        superseded_pruning=False,
        recency_decay=False,
    ),

    # ---- Engram ablations (pipeline component removal) ----
    "engram_all_off": AblationConfig(
        name="engram_all_off",
        description="Engram retrieval with ALL strategy pipeline components disabled. "
                    "Raw BFS graph traversal output.",
        superseded_pruning=False,
        confidence_threshold=None,
        recency_decay=False,
        top_k=None,
        token_budget=None,
        relevance_scoring=False,
    ),
    "engram_superseded_only": AblationConfig(
        name="engram_superseded_only",
        description="Only superseded_pruning enabled. Isolates contribution of "
                    "contradiction resolution.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=False,
        top_k=None,
        token_budget=None,
        relevance_scoring=False,
    ),
    "engram_no_superseded": AblationConfig(
        name="engram_no_superseded",
        description="All pipeline components EXCEPT superseded_pruning. Shows cost of "
                    "removing contradiction resolution.",
        superseded_pruning=False,
        confidence_threshold=0.6,
        recency_decay=True,
        top_k=50,
        token_budget=2000,
        recency_half_life_days=3650,
    ),
    "engram_default": AblationConfig(
        name="engram_default",
        description="Default Engram pipeline: superseded_pruning + recency_decay + "
                    "relevance scoring. No hard token budget. half_life=3650d tuned for "
                    "long-span episodic corpora where all events are years old.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=50,
        token_budget=None,
        recency_half_life_days=3650,
    ),
    "engram_filtered": AblationConfig(
        name="engram_filtered",
        description="Default + confidence threshold 0.6. Filters low-confidence nodes.",
        superseded_pruning=True,
        confidence_threshold=0.6,
        recency_decay=True,
        top_k=50,
        token_budget=None,
        recency_half_life_days=3650,
    ),
    "engram_tight": AblationConfig(
        name="engram_tight",
        description="All pipeline components + aggressive 750-token budget. "
                    "Maximizes token efficiency at potential accuracy cost.",
        superseded_pruning=True,
        confidence_threshold=0.6,
        recency_decay=True,
        top_k=20,
        token_budget=750,
        recency_half_life_days=3650,
    ),
    "engram_semantic": AblationConfig(
        name="engram_semantic",
        description="Default pipeline + semantic search augmentation. "
                    "Isolates vector retrieval contribution. relevance_scoring=False "
                    "to avoid conflating seed-ranking with embedding signal.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=50,
        token_budget=None,
        semantic=True,
        relevance_scoring=False,
        recency_half_life_days=3650,
    ),
    "engram_temporal": AblationConfig(
        name="engram_temporal",
        description="Default pipeline with temporal resolution at ingest: relative date "
                    "references resolved to absolute dates using the session date header. "
                    "Isolates contribution of temporal resolution on LOCOMO date queries.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=50,
        token_budget=None,
        recency_half_life_days=3650,
    ),
    "engram_context_tail": AblationConfig(
        name="engram_context_tail",
        description="Default pipeline + context carry-forward at ingest: last 4 raw turns "
                    "of the previous session (or bisection half) are prepended as a read-only "
                    "co-reference header. Resolves cross-boundary references like 'she finally "
                    "told me about the job' without re-extracting prior facts.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=50,
        token_budget=None,
        recency_half_life_days=3650,
    ),
    "engram_dynamic_topk": AblationConfig(
        name="engram_dynamic_topk",
        description="Default pipeline with dynamic top_k scaling: top_k = min(50, max(10, "
                    "sqrt(graph_node_count))). Prevents retrieval precision from being diluted "
                    "by graph inflation in long-span corpora. Isolates the contribution of "
                    "adaptive budget sizing.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=None,          # None triggers dynamic scaling in retrieve_with_stats
        token_budget=None,
        recency_half_life_days=3650,
    ),
    "engram_dynamic_topk_log": AblationConfig(
        name="engram_dynamic_topk_log",
        description="Default pipeline with log2-based dynamic top_k: top_k = min(100, max(10, "
                    "int(log2(node_count) * 5))). Gives top_k≈50 at LOCOMO scale (1085 nodes) "
                    "and scales gracefully from small (100 nodes→33) to large (5000 nodes→61) "
                    "graphs. Comparison point for sqrt-based engram_dynamic_topk.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=None,          # None triggers dynamic scaling; formula selected by topk_formula
        token_budget=None,
        recency_half_life_days=3650,
        topk_formula="log2",
    ),
    "engram_combined": AblationConfig(
        name="engram_combined",
        description="Full stack: semantic search (bge-small-en-v1.5) + keyword relevance scoring "
                    "+ log2 dynamic top_k. Tests whether vector and keyword signals are additive. "
                    "Unlike engram_semantic (relevance_scoring=False), both signals are active here.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=None,
        token_budget=None,
        semantic=True,
        relevance_scoring=True,
        recency_half_life_days=3650,
        topk_formula="log2",
    ),
    "engram_bfs4": AblationConfig(
        name="engram_bfs4",
        description="Default pipeline with BFS hops=4 instead of 3. Tests whether deeper "
                    "graph traversal recovers multi-hop chains that hops=3 misses, at the "
                    "cost of broader (potentially noisier) retrieval.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=50,
        token_budget=None,
        recency_half_life_days=3650,
        bfs_hops=4,
    ),
    "engram_dedup95": AblationConfig(
        name="engram_dedup95",
        description="Default pipeline with dedup threshold raised to 0.95. Reduces over-pruning "
                    "from bge-small's tighter embedding clusters (default 0.92 merged 712 nodes "
                    "vs 1017 with all-MiniLM). Isolates the accuracy impact of dedup aggressiveness.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=50,
        token_budget=None,
        recency_half_life_days=3650,
        dedup_threshold=0.95,
    ),
    "engram_dedup97": AblationConfig(
        name="engram_dedup97",
        description="Default pipeline with dedup threshold raised to 0.97. More conservative "
                    "merging than engram_dedup95 — only near-identical paraphrases are merged. "
                    "Comparison point for finding the optimal bge-small dedup threshold.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=50,
        token_budget=None,
        recency_half_life_days=3650,
        dedup_threshold=0.97,
    ),
    "engram_prior20": AblationConfig(
        name="engram_prior20",
        description="Default pipeline + last 20 raw turns appended as short-term context. "
                    "Tests whether a recency window complements graph retrieval for "
                    "questions about very recent events not yet well-represented in the graph.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=50,
        token_budget=None,
        recency_half_life_days=3650,
        prior_turns_window=20,
    ),
    "sliding_window20": AblationConfig(
        name="sliding_window20",
        description="No memory system — last 20 raw turns only. Sliding-window baseline that "
                    "tests short-term recency without any graph retrieval. Comparison point "
                    "for engram_prior20 to isolate the marginal value of BFS on top of a window.",
        full_context=False,
        use_bfs=False,
        superseded_pruning=False,
        recency_decay=False,
        prior_turns_window=20,
    ),
    "engram_no_temporal": AblationConfig(
        name="engram_no_temporal",
        description="Default pipeline WITHOUT temporal resolution: ingest uses "
                    "episodic_personal_no_dates (relative date phrases kept as-is in facts), "
                    "and display-time date resolution is disabled. Baseline for measuring the "
                    "accuracy gain from date resolution on temporal QA categories.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=50,
        token_budget=None,
        recency_half_life_days=3650,
        domain="episodic_personal_no_dates",
        resolve_dates=False,
    ),
}


# Configs to run in a standard benchmark sweep (ordered for the paper table)
PAPER_CONFIGS = [
    "no_memory",
    "full_context",
    "engram_all_off",
    "engram_superseded_only",
    "engram_no_superseded",
    "engram_default",
    "engram_filtered",
    "engram_tight",
    "engram_semantic",
]
