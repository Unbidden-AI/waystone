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
    # 0.95 is optimal for bge-small — reduces over-pruning vs 0.92 without accumulating noise at 0.97
    dedup_threshold: float = 0.95
    # Number of raw conversation turns to append after BFS results as short-term context.
    # 0 = disabled (BFS only). When use_bfs=False, acts as a sliding-window baseline.
    prior_turns_window: int = 0
    # Accuracy improvement features (LOCOMO plan improvements 1–3)
    query_expansion: bool = False        # #1: synonym + named-entity keyword expansion
    person_anchoring: bool = False       # #2: person nodes always become high-confidence BFS seeds
    temporal_proximity: bool = False     # #3: boost nodes whose occurred_at is near query date
    # Accuracy improvement features (LOCOMO plan improvements 4, 6)
    query_coreference: bool = False      # #4: resolve relationship terms → person names via graph lookup
    session_scoped: bool = False         # #6: restrict entry nodes to evidence sessions for each QA pair
    # Temporal output ordering: prepend a chronological "Timeline" section to retrieval output.
    # All nodes with occurred_at are sorted by date and listed first before type-grouped sections.
    # Targets temporal QA category where answers are dates — surfaces the date-bearing fact prominently.
    temporal_sort: bool = False
    # Post-hoc edge inference: scan graph and add missing `involves` edges
    infer_edges: bool = False            # #7: infer involves edges from person-tag overlap
    infer_edges_weight: float = 1.0      # weight assigned to inferred edges (< 1.0 = deprioritized)
    infer_edges_name_only: bool = False  # if True, match only on person's first name, not all tags
    # Edge-weight scoring: penalize nodes reached only via low-weight (inferred) edges
    edge_weight_scoring: bool = False    # multiply _score by _max_edge_weight after recency_decay
    # Post-BFS semantic re-ranking: multiply _score by cosine similarity to query embedding
    semantic_rerank: bool = False        # re-rank BFS-collected nodes by embedding similarity to query
    # Cap on how many nodes (by pre-rerank score) enter semantic reranking. 0 = unlimited (rerank all).
    # Default 0 preserves Apr-15 behavior: all BFS-collected nodes are cosine-scored.
    # Set >0 only when O(n) embedding lookups are a bottleneck; risk: multi-session facts
    # with low BFS score but high cosine sim fall outside the cap and miss reranking.
    semantic_rerank_cap: int = 0
    # Post-BFS cross-encoder re-ranking: score (query, fact) pairs via cross-encoder
    cross_encoder_rerank: bool = False   # re-rank BFS-collected nodes via cross-encoder relevance scores
    cross_encoder_model: str | None = None  # override cross-encoder model (default: ms-marco-MiniLM-L-6-v2)
    rrf_rerank: bool = False             # fuse bi-encoder + cross-encoder rankings via Reciprocal Rank Fusion
    rrf_weights: dict | None = None      # per-query-type CE weight: {"temporal": 0.7, "preference": 0.1, "default": 0.5}
    # Preference fan-out: inject all preference-type nodes when the query contains
    # recommendation/preference-signal verbs (recommend, suggest, prefer, like, etc.).
    # Addresses the keyword-mismatch gap: preference nodes are tagged with specific
    # product/activity names, but preference questions use generic action verbs.
    preference_fanout: bool = False
    # Max preference nodes to inject during fan-out (ranked by cosine sim; 0 = unlimited).
    # Default 20 prevents polluting multi-session retrieval with low-relevance pref nodes.
    preference_fanout_cap: int = 20
    # Absent-information handling: when True and retrieval returns no context, judge prompt
    # is flipped — empty context scores YES if the ground truth is "info not available".
    # Keep False for LOCOMO (no abstention category); set True for LongMemEval.
    abstention_mode: bool = False
    # Optional: copy checkpoint DB from another config's dir instead of re-extracting
    checkpoint_source: str | None = None  # config name whose checkpoint dir to copy from
    # Optional: path to a model config YAML (e.g. benchmarks/model_configs/gpt_4o_mini.yaml)
    # to override the extraction LLM for this config. When set, fresh extraction (no checkpoint
    # available) uses this model instead of the system config.yaml LLM. Enables apples-to-apples
    # comparison against Zep/Mem0 which use gpt-4o-mini for extraction.
    extraction_model_config: str | None = None
    # Sentence index: raw per-sentence transcript vectors for semantic fallback retrieval.
    # When True, config.sentence_index.enabled is set before ingestion, and the retriever
    # uses raw_sentences as a fallback when BFS entry_nodes < sentence_fallback_threshold.
    sentence_index: bool = False
    # Independent semantic retrieval channel: linear scan of all node embeddings, inject
    # top-K by cosine similarity into the BFS candidate pool before semantic_rerank.
    # Surfaces nodes with no tag match and no graph connectivity.
    semantic_retrieval: bool = False
    semantic_retrieval_k: int = 40
    # Temporal auto-routing: classify query type and auto-enable temporal_sort + temporal_proximity
    # for queries classified as "temporal". Default True preserves the current retriever default.
    # Set False to match pre-b6d4f72 (Apr-15) behavior where this feature didn't exist.
    temporal_auto_route: bool = True
    # Extend stop word list with counting/quantity/temporal words ("many", "last", "month", etc.).
    # Helps LOCOMO by suppressing noise seeds from _tag_pairs compound-tag splitting.
    # MUST be False for LongMemEval — these words are legitimate BFS seeds for multi-session
    # "how many X in the last Y" counting questions. Regression confirmed (a2e1077, Apr-19).
    extend_stop_words: bool = False


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
    # ---- LOCOMO accuracy improvements 1–3 (ablations) ----
    "engram_query_expansion": AblationConfig(
        name="engram_query_expansion",
        description="Default pipeline + query expansion (#1): synonym dict for common personal-fact "
                    "verbs (eat→food/meal, visit→trip/came, work→job/career) and named-entity "
                    "detection from capitalized query tokens. Isolates the lift from broadening "
                    "the keyword search space.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=50,
        token_budget=None,
        recency_half_life_days=3650,
        query_expansion=True,
    ),
    "engram_person_anchor": AblationConfig(
        name="engram_person_anchor",
        description="Default pipeline + person anchoring (#2): person-typed nodes with ≥1 "
                    "keyword-tag match become automatic high-confidence BFS seeds, ensuring "
                    "every query that names a person unlocks that person's full fact neighborhood. "
                    "Isolates the lift from entity-anchored seeding.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=50,
        token_budget=None,
        recency_half_life_days=3650,
        person_anchoring=True,
    ),
    "engram_temporal_boost": AblationConfig(
        name="engram_temporal_boost",
        description="Default pipeline + temporal proximity boosting (#3): nodes whose occurred_at "
                    "is near the query's implied date reference get a score multiplier (half-life "
                    "180d). Isolates the lift on the 20% temporal QA category.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=50,
        token_budget=None,
        recency_half_life_days=3650,
        temporal_proximity=True,
    ),
    "engram_improvements_1_3": AblationConfig(
        name="engram_improvements_1_3",
        description="All three LOCOMO accuracy improvements combined: query expansion (#1) + "
                    "person anchoring (#2) + temporal proximity boosting (#3). Tests whether "
                    "the improvements are additive or have diminishing returns when stacked.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=50,
        token_budget=None,
        recency_half_life_days=3650,
        query_expansion=True,
        person_anchoring=True,
        temporal_proximity=True,
    ),
    "engram_person_temporal": AblationConfig(
        name="engram_person_temporal",
        description="Person anchoring (#2) + temporal proximity boosting (#3), without query "
                    "expansion (#1). Tests whether the 2-feature stack outperforms the 3-way "
                    "stack by dropping the noisy synonym expansion.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=50,
        token_budget=None,
        recency_half_life_days=3650,
        person_anchoring=True,
        temporal_proximity=True,
    ),
    "engram_bm25_rrf": AblationConfig(
        name="engram_bm25_rrf",
        description="Default pipeline with BM25 FTS5 + RRF multi-channel retrieval active. "
                    "BM25 runs on full fact text (porter-stemmed); RRF merges tag-overlap, "
                    "BM25, and cosine channels without scale calibration. Isolates the lift "
                    "from full-text retrieval over tag-only matching.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=50,
        token_budget=None,
        recency_half_life_days=3650,
    ),
    # ---- LOCOMO accuracy improvements 4, 6 ----
    "engram_coreference": AblationConfig(
        name="engram_coreference",
        description="Default pipeline + query coreference resolution (#4): relationship terms "
                    "(sister, husband, colleague, etc.) in the query trigger a graph lookup that "
                    "resolves the referent person's name and adds it to the keyword set. Isolates "
                    "the lift from resolving 'her sister' → 'Sarah' without re-extraction.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=50,
        token_budget=None,
        recency_half_life_days=3650,
        query_coreference=True,
    ),
    "engram_session_scoped": AblationConfig(
        name="engram_session_scoped",
        description="Default pipeline + session-scoped filtering (#6): entry node candidates are "
                    "restricted to the LOCOMO sessions cited in each QA pair's evidence field. "
                    "Reduces cross-session noise for questions with a known temporal anchor. "
                    "Isolates the precision lift from evidence-aware scoping.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=50,
        token_budget=None,
        recency_half_life_days=3650,
        session_scoped=True,
    ),
    "engram_coreference_temporal": AblationConfig(
        name="engram_coreference_temporal",
        description="Query coreference (#4) + temporal proximity boosting (#3) on the default "
                    "pipeline. Tests whether resolving person referents and boosting by date "
                    "proximity are additive — coreference helps person-scoped queries, temporal "
                    "boost helps time-anchored queries, so they should not conflict.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=50,
        token_budget=None,
        recency_half_life_days=3650,
        query_coreference=True,
        temporal_proximity=True,
    ),
    "engram_improvements_4_6": AblationConfig(
        name="engram_improvements_4_6",
        description="Query coreference (#4) + session-scoped filtering (#6) combined. Tests "
                    "whether orthogonal precision improvements (resolving person referents, "
                    "restricting to evidence sessions) are additive.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=50,
        token_budget=None,
        recency_half_life_days=3650,
        query_coreference=True,
        session_scoped=True,
    ),
    # ---- Budget ablation ----
    "engram_default_topk100": AblationConfig(
        name="engram_default_topk100",
        description="Default pipeline with top_k raised to 100 but NO edge inference. "
                    "Isolation ablation for engram_edge_infer_topk100: separates the "
                    "accuracy gain from budget increase vs. inferred edges.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=100,
        token_budget=None,
        recency_half_life_days=3650,
        checkpoint_source="engram_dedup95",
    ),
    "engram_semantic_rerank_topk100": AblationConfig(
        name="engram_semantic_rerank_topk100",
        description="Post-BFS semantic re-ranking with top_k=100. After BFS collects up to 100 "
                    "nodes, each node's _score is multiplied by 0.5 + 0.5*cos(query, node_emb). "
                    "Tests whether embedding-based re-ranking recovers precision lost from the "
                    "larger budget without discarding recall. Comparable to engram_default_topk100.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=100,
        token_budget=None,
        recency_half_life_days=3650,
        semantic_rerank=True,
        checkpoint_source="engram_dedup95",
    ),
    "engram_cross_encoder_topk100": AblationConfig(
        name="engram_cross_encoder_topk100",
        description="Post-BFS cross-encoder re-ranking with top_k=100. After BFS collects up to "
                    "100 nodes, each (query, fact) pair is scored by cross-encoder/ms-marco-MiniLM-L-6-v2. "
                    "Cross-encoder sees query+fact jointly (vs. bi-encoder cosine which scores them "
                    "independently), providing interaction-aware relevance. Speculative decoding "
                    "analog: BFS = draft stage, cross-encoder = verify stage.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=100,
        token_budget=None,
        recency_half_life_days=3650,
        cross_encoder_rerank=True,
        checkpoint_source="engram_dedup95",
    ),

    # ---- Post-hoc edge inference (#7) ----
    "engram_edge_infer": AblationConfig(
        name="engram_edge_infer",
        description="Default pipeline + post-hoc edge inference (#7): scans the extracted graph "
                    "and adds missing `involves` edges wherever a fact node's tags overlap with "
                    "a person anchor node's tags. Repairs extractor omissions without re-extraction. "
                    "Improves BFS reachability for multi-hop person-scoped questions.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=50,
        token_budget=None,
        recency_half_life_days=3650,
        infer_edges=True,
        checkpoint_source="engram_dedup95",
    ),
    "engram_edge_infer_anchored": AblationConfig(
        name="engram_edge_infer_anchored",
        description="Post-hoc edge inference (#7) + person anchoring (#2). Tests whether the "
                    "denser involves-edge graph amplifies the benefit of person-anchor seeding: "
                    "anchored seeds now have more neighbors reachable in BFS.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=50,
        token_budget=None,
        recency_half_life_days=3650,
        person_anchoring=True,
        infer_edges=True,
        checkpoint_source="engram_dedup95",
    ),
    "engram_edge_infer_nameonly": AblationConfig(
        name="engram_edge_infer_nameonly",
        description="Post-hoc edge inference with name-only matching (#7b): instead of matching "
                    "on all person-node tags (which caused BFS flooding), infers `involves` edges "
                    "only when a fact node's tags contain the person's first name. Reduces false "
                    "positive edges from relationship terms (sister, friend) shared across people.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=50,
        token_budget=None,
        recency_half_life_days=3650,
        infer_edges=True,
        infer_edges_name_only=True,
        checkpoint_source="engram_dedup95",
    ),
    "engram_edge_infer_topk100": AblationConfig(
        name="engram_edge_infer_topk100",
        description="Post-hoc edge inference (#7) with top_k raised to 100. Addresses BFS "
                    "flooding from 14k inferred edges by providing a larger result budget so "
                    "signal nodes aren't crowded out. Tests whether a bigger retrieval window "
                    "recovers accuracy lost to inference noise.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=100,
        token_budget=None,
        recency_half_life_days=3650,
        infer_edges=True,
        checkpoint_source="engram_dedup95",
    ),
    "engram_edge_infer_weighted": AblationConfig(
        name="engram_edge_infer_weighted",
        description="Post-hoc edge inference (#7) with weighted edges + score penalty. Inferred "
                    "`involves` edges are stored at weight=0.3. BFS tracks the max edge weight "
                    "on the path to each node (_max_edge_weight). After recency_decay, "
                    "edge_weight_scoring multiplies each node's _score by its _max_edge_weight, "
                    "causing nodes reachable only via inferred edges to rank below natively-linked "
                    "nodes. Preserves graph connectivity while deprioritizing inference noise.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=50,
        token_budget=None,
        recency_half_life_days=3650,
        infer_edges=True,
        infer_edges_weight=0.3,
        edge_weight_scoring=True,
        checkpoint_source="engram_dedup95",
    ),

    # ---- Apples-to-apples comparison against Zep/Mem0 (gpt-4o-mini extraction) ----
    "engram_gpt4o_mini_extraction": AblationConfig(
        name="engram_gpt4o_mini_extraction",
        description="Baseline extraction config using gpt-4o-mini as the extraction LLM — "
                    "the same model used by Zep and Mem0 in their LOCOMO evaluations. "
                    "Matches engram_dedup95 in all other respects (dedup_threshold=0.95, "
                    "domain=episodic_personal). Checkpoint source for apples-to-apples configs.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=50,
        token_budget=None,
        recency_half_life_days=3650,
        extraction_model_config="benchmarks/model_configs/gpt_4o_mini.yaml",
    ),
    "engram_semantic_rerank_gpt4omini": AblationConfig(
        name="engram_semantic_rerank_gpt4omini",
        description="Semantic re-ranking with gpt-4o-mini extraction — apples-to-apples comparison "
                    "against Zep/Mem0 which use gpt-4o-mini for their memory systems. Identical "
                    "retrieval pipeline to engram_semantic_rerank_topk100 (top_k=100, semantic "
                    "rerank) but with gpt-4o-mini-extracted checkpoints instead of "
                    "gemini-2.5-flash-lite. The delta between this and engram_semantic_rerank_topk100 "
                    "isolates the extraction model contribution vs architectural contribution.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=100,
        token_budget=None,
        recency_half_life_days=3650,
        semantic_rerank=True,
        checkpoint_source="engram_gpt4o_mini_extraction",
    ),
    # Sentence index: raw per-sentence vectors as semantic fallback when BFS entry_nodes < 3.
    # Requires fresh extraction (builds raw_sentences at ingest time).
    # Reuses engram_semantic_rerank_topk100 retrieval settings to isolate sentence_index impact.
    "engram_sentence_index": AblationConfig(
        name="engram_sentence_index",
        description=(
            "Sentence index — raw per-sentence vectors as semantic fallback when "
            "BFS entry_nodes < 3. Gemini extraction, semantic rerank top_k=100, "
            "person_anchoring=True. Requires fresh extraction."
        ),
        superseded_pruning=True,
        recency_decay=True,
        top_k=100,
        recency_half_life_days=3650,
        semantic_rerank=True,
        person_anchoring=True,
        sentence_index=True,
    ),
    # Sentence index + temporal proximity: adds temporal_proximity boosting (#3) on top of
    # engram_sentence_index. Reuses the existing sentence_index checkpoint — no re-extraction.
    # Isolates the lift from temporal proximity scoring over person_anchoring+sentence_index baseline.
    "engram_sentence_temporal": AblationConfig(
        name="engram_sentence_temporal",
        description=(
            "Sentence index + temporal proximity (#3). Reuses engram_sentence_index checkpoint "
            "(sentence_index=True, person_anchoring=True, semantic rerank top_k=100) and adds "
            "temporal_proximity boosting: nodes whose occurred_at is near the query date get a "
            "score bonus (half-life 180d). Tests the additive lift on temporal QA category."
        ),
        superseded_pruning=True,
        recency_decay=True,
        top_k=100,
        recency_half_life_days=3650,
        semantic_rerank=True,
        person_anchoring=True,
        temporal_proximity=True,
        sentence_index=True,
        checkpoint_source="engram_sentence_index",
    ),
    # Sentence index + temporal sort: chronological "Timeline" section prepended to retrieval output.
    # All dated nodes (occurred_at set) sorted by date appear first; undated nodes follow in type groups.
    # Tests whether surfacing the date-bearing fact prominently lifts temporal QA category recall.
    # Reuses engram_sentence_index checkpoint — no re-extraction needed.
    "engram_sentence_timeline": AblationConfig(
        name="engram_sentence_timeline",
        description=(
            "Sentence index + timeline output ordering. Reuses engram_sentence_index checkpoint "
            "(sentence_index=True, person_anchoring=True, semantic rerank top_k=100) and prepends "
            "a chronological Timeline section: all nodes with occurred_at are sorted by date and "
            "listed first. Targets temporal QA by surfacing the date-bearing fact at the top."
        ),
        superseded_pruning=True,
        recency_decay=True,
        top_k=100,
        recency_half_life_days=3650,
        semantic_rerank=True,
        person_anchoring=True,
        temporal_sort=True,
        sentence_index=True,
        checkpoint_source="engram_sentence_index",
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
