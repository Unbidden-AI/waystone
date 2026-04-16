"""
Ablation configurations for the LongMemEval benchmark.

Mirrors the LOCOMO ablation config pattern. LME configs live in a separate
namespace so they don't pollute the LOCOMO config registry.

Question type categories (for --categories filtering):
    1 = single-session-user / single-session-assistant / single-session-preference
    2 = temporal-reasoning
    3 = multi-session
    4 = knowledge-update  ← Engram's key differentiator (supersedes mechanism)
    5 = absent-information (M split only)
"""

from __future__ import annotations

# Re-use AblationConfig dataclass from LOCOMO — it's generic enough.
from benchmarks.locomo.ablation_configs import AblationConfig


# ---------------------------------------------------------------------------
# LongMemEval configs
# ---------------------------------------------------------------------------

LME_ABLATION_CONFIGS: dict[str, AblationConfig] = {

    # Primary config — Gemini 2.5 Flash-Lite extraction, semantic rerank.
    # This is our best LOCOMO config applied to LME without change.
    "engram_lme_gemini": AblationConfig(
        name="engram_lme_gemini",
        description=(
            "Primary LME config — Gemini 2.5 Flash-Lite extraction, "
            "semantic rerank top_k=100, dedup_threshold=0.95"
        ),
        superseded_pruning=True,
        recency_decay=True,
        top_k=100,
        semantic_rerank=True,
        dedup_threshold=0.95,
        domain="episodic_personal",
        abstention_mode=True,   # LME has absent-info questions
        person_anchoring=True,
    ),

    # Apples-to-apples comparison — gpt-4o-mini extraction (matches Zep/Mem0's model).
    # Copies checkpoint from engram_lme_gemini if available; otherwise extracts fresh.
    "engram_lme_gpt4omini": AblationConfig(
        name="engram_lme_gpt4omini",
        description=(
            "LME apples-to-apples — gpt-4o-mini extraction (same as Zep/Mem0), "
            "semantic rerank top_k=100"
        ),
        superseded_pruning=True,
        recency_decay=True,
        top_k=100,
        semantic_rerank=True,
        dedup_threshold=0.95,
        domain="episodic_personal",
        abstention_mode=True,
        person_anchoring=True,
        extraction_model_config="benchmarks/model_configs/gpt_4o_mini.yaml",
    ),

    # Keyword-only baseline (no semantic rerank) — diagnostic.
    "engram_lme_keyword": AblationConfig(
        name="engram_lme_keyword",
        description=(
            "LME keyword-only baseline — Gemini extraction, no semantic rerank, top_k=50"
        ),
        superseded_pruning=True,
        recency_decay=True,
        top_k=50,
        semantic_rerank=False,
        dedup_threshold=0.95,
        domain="episodic_personal",
        abstention_mode=True,
        person_anchoring=True,
        checkpoint_source="engram_lme_gemini",  # reuse Gemini checkpoint
    ),

    # Cross-encoder rerank — same extraction as engram_lme_gemini, swap reranker.
    # ms-marco-MiniLM-L-6-v2 cross-encoder sees (query, fact) jointly; on LOCOMO
    # this scored 84.1% LLM accuracy vs 85.7% for semantic rerank. Running here
    # to see if the domain gap closes or widens on LME's episodic-personal corpus.
    "engram_lme_cross_encoder": AblationConfig(
        name="engram_lme_cross_encoder",
        description=(
            "LME cross-encoder rerank — Gemini extraction, ms-marco-MiniLM cross-encoder, "
            "top_k=100. Reuses engram_lme_gemini checkpoints (no re-extraction)."
        ),
        superseded_pruning=True,
        recency_decay=True,
        top_k=100,
        semantic_rerank=False,
        cross_encoder_rerank=True,
        dedup_threshold=0.95,
        domain="episodic_personal",
        abstention_mode=True,
        person_anchoring=True,
        checkpoint_source="engram_lme_gemini",
    ),

    # RRF fusion of bi-encoder (semantic) + cross-encoder (BGE v2-m3) rankings.
    # Neither signal replaces _score; both are rank-fused via RRF(k=60).
    # Addresses the "CE replaces score entirely" problem — CE and semantic act
    # as complementary rankers with neither dominating.
    "engram_lme_rrf_rerank": AblationConfig(
        name="engram_lme_rrf_rerank",
        description=(
            "LME RRF rerank — bi-encoder (BGE-small) + cross-encoder (BGE v2-m3) fused via RRF(k=60), "
            "top_k=100. Reuses engram_lme_gemini checkpoints (no re-extraction)."
        ),
        superseded_pruning=True,
        recency_decay=True,
        top_k=100,
        semantic_rerank=False,
        cross_encoder_rerank=False,
        rrf_rerank=True,
        cross_encoder_model="BAAI/bge-reranker-v2-m3",
        dedup_threshold=0.95,
        domain="episodic_personal",
        abstention_mode=True,
        person_anchoring=True,
        checkpoint_source="engram_lme_gemini",
    ),

    # BGE reranker v2-m3 — trained on diverse retrieval tasks (not web-search-only).
    # Better domain fit for conversational episodic memory vs ms-marco cross-encoder.
    # Reuses engram_lme_gemini checkpoints; only swaps the reranker model.
    "engram_lme_bge_reranker": AblationConfig(
        name="engram_lme_bge_reranker",
        description=(
            "LME BGE reranker v2-m3 — Gemini extraction, BAAI/bge-reranker-v2-m3 cross-encoder, "
            "top_k=100. Reuses engram_lme_gemini checkpoints (no re-extraction)."
        ),
        superseded_pruning=True,
        recency_decay=True,
        top_k=100,
        semantic_rerank=False,
        cross_encoder_rerank=True,
        cross_encoder_model="BAAI/bge-reranker-v2-m3",
        dedup_threshold=0.95,
        domain="episodic_personal",
        abstention_mode=True,
        person_anchoring=True,
        checkpoint_source="engram_lme_gemini",
    ),

    # Dynamic-weight RRF — per-query-type CE weight tuned from oracle results:
    #   temporal → α_ce=0.7 (RRF gained +5.6pp vs semantic on temporal)
    #   preference → α_ce=0.1 (semantic won 13.8% vs CE 6.7%; keep CE minimal)
    #   default → α_ce=0.5 (balanced; covers multi-session, knowledge-update, ss-user)
    # Keyword heuristics classify at retrieval time — no LLM call required.
    "engram_lme_rrf_dynamic": AblationConfig(
        name="engram_lme_rrf_dynamic",
        description=(
            "LME dynamic-weight RRF — keyword-classified query type drives CE weight: "
            "temporal=0.7, preference=0.1, default=0.5. BGE v2-m3 cross-encoder. "
            "Reuses engram_lme_gemini checkpoints."
        ),
        superseded_pruning=True,
        recency_decay=True,
        top_k=100,
        semantic_rerank=False,
        cross_encoder_rerank=False,
        rrf_rerank=True,
        cross_encoder_model="BAAI/bge-reranker-v2-m3",
        rrf_weights={"temporal": 0.7, "preference": 0.1, "default": 0.5},
        dedup_threshold=0.95,
        domain="episodic_personal",
        abstention_mode=True,
        person_anchoring=True,
        checkpoint_source="engram_lme_gemini",
    ),

    # S-split primary config — full 53-session haystack per question.
    # Separate checkpoint dir from oracle so ablations can reference either split.
    # Subsequent S-split ablations should use checkpoint_source="engram_lme_gemini_s".
    "engram_lme_gemini_s": AblationConfig(
        name="engram_lme_gemini_s",
        description=(
            "Primary S-split config — Gemini 2.5 Flash-Lite extraction, "
            "full 53-session haystack, semantic rerank top_k=100, dedup_threshold=0.95"
        ),
        superseded_pruning=True,
        recency_decay=True,
        top_k=100,
        semantic_rerank=True,
        dedup_threshold=0.95,
        domain="episodic_personal",
        abstention_mode=True,
        person_anchoring=True,
    ),

    # S-split ablations — reuse engram_lme_gemini_s checkpoints, no re-extraction.

    # RRF 50/50: bi-encoder (BGE-small) + cross-encoder (BGE v2-m3), equal weights.
    "engram_lme_s_rrf_50": AblationConfig(
        name="engram_lme_s_rrf_50",
        description=(
            "S-split RRF 50/50 — bi-encoder + cross-encoder (BGE v2-m3) fused via RRF(k=60), "
            "equal weights, top_k=100. Reuses engram_lme_gemini_s checkpoints."
        ),
        superseded_pruning=True,
        recency_decay=True,
        top_k=100,
        semantic_rerank=False,
        cross_encoder_rerank=False,
        rrf_rerank=True,
        cross_encoder_model="BAAI/bge-reranker-v2-m3",
        dedup_threshold=0.95,
        domain="episodic_personal",
        abstention_mode=True,
        person_anchoring=True,
        checkpoint_source="engram_lme_gemini_s",
    ),

    # Dynamic-weight RRF: per-query-type CE weight from oracle analysis.
    "engram_lme_s_rrf_dynamic": AblationConfig(
        name="engram_lme_s_rrf_dynamic",
        description=(
            "S-split dynamic-weight RRF — query type drives CE weight: "
            "temporal=0.7, preference=0.1, default=0.5. BGE v2-m3. "
            "Reuses engram_lme_gemini_s checkpoints."
        ),
        superseded_pruning=True,
        recency_decay=True,
        top_k=100,
        semantic_rerank=False,
        cross_encoder_rerank=False,
        rrf_rerank=True,
        cross_encoder_model="BAAI/bge-reranker-v2-m3",
        rrf_weights={"temporal": 0.7, "preference": 0.1, "default": 0.5},
        dedup_threshold=0.95,
        domain="episodic_personal",
        abstention_mode=True,
        person_anchoring=True,
        checkpoint_source="engram_lme_gemini_s",
    ),

    # Dynamic top_k: node-count-based scaling (sqrt formula), semantic rerank.
    # At ~1600 nodes avg per S-split sample: sqrt(1600)*2.5 ≈ 100 (similar to fixed 100).
    # Adapts to sparser samples (fewer nodes → smaller top_k → tighter context).
    "engram_lme_s_dynamic_topk": AblationConfig(
        name="engram_lme_s_dynamic_topk",
        description=(
            "S-split dynamic top_k — sqrt-scaled top_k (no fixed ceiling), semantic rerank. "
            "Reuses engram_lme_gemini_s checkpoints."
        ),
        superseded_pruning=True,
        recency_decay=True,
        top_k=None,
        topk_formula="sqrt",
        semantic_rerank=True,
        dedup_threshold=0.95,
        domain="episodic_personal",
        abstention_mode=True,
        person_anchoring=True,
        checkpoint_source="engram_lme_gemini_s",
    ),

    # Person anchoring ablation — isolates the impact of person_anchoring on S-split.
    # Reuses engram_lme_gemini_s checkpoints (no re-extraction); only enables
    # person_anchoring=True vs the original engram_lme_gemini_s which had it False.
    # Expected lift: ss-user (+?pp) and multi-session (+?pp) where named people
    # appear in queries. Cross-checks whether the 52.9% ss-user gap is retrieval-side.
    "engram_lme_s_person_anchor": AblationConfig(
        name="engram_lme_s_person_anchor",
        description=(
            "S-split person anchoring — semantic rerank top_k=100 + person_anchoring=True. "
            "Promotes User and named-person nodes to high-confidence BFS seeds. "
            "Reuses engram_lme_gemini_s checkpoints."
        ),
        superseded_pruning=True,
        recency_decay=True,
        top_k=100,
        semantic_rerank=True,
        dedup_threshold=0.95,
        domain="episodic_personal",
        abstention_mode=True,
        person_anchoring=True,
        checkpoint_source="engram_lme_gemini_s",
    ),

    # Person anchoring + query expansion — stacks person_anchoring with query_expansion
    # so "user" queries also expand to "speaker" synonyms (and vice versa).
    # Tests whether the expansion recovers cases where the extraction model tagged
    # user facts with "speaker" instead of "user" in the existing checkpoint.
    "engram_lme_s_person_anchor_qx": AblationConfig(
        name="engram_lme_s_person_anchor_qx",
        description=(
            "S-split person anchoring + query expansion — person_anchoring=True + "
            "query_expansion=True ('user'↔'speaker' synonyms). "
            "Reuses engram_lme_gemini_s checkpoints."
        ),
        superseded_pruning=True,
        recency_decay=True,
        top_k=100,
        semantic_rerank=True,
        dedup_threshold=0.95,
        domain="episodic_personal",
        abstention_mode=True,
        person_anchoring=True,
        query_expansion=True,
        checkpoint_source="engram_lme_gemini_s",
    ),

    # Oracle variant: only answer-bearing sessions ingested (upper-bound retrieval).
    # Uses the longmemeval_oracle split rather than full haystack.
    # Useful for measuring extraction quality independently from retrieval recall.
    "engram_lme_oracle": AblationConfig(
        name="engram_lme_oracle",
        description=(
            "LME oracle — Gemini extraction of oracle sessions only (upper bound), "
            "semantic rerank top_k=100"
        ),
        superseded_pruning=True,
        recency_decay=True,
        top_k=100,
        semantic_rerank=True,
        dedup_threshold=0.95,
        domain="episodic_personal",
        abstention_mode=True,
        person_anchoring=True,
    ),

    # User-node patched: synthetic "User" person node injected post-hoc into
    # engram_lme_gemini_s DBs (see patch_user_person_nodes.py). Person anchoring
    # now has a hub node to BFS from — tests whether the extraction gap (no User
    # person node) was the sole reason person_anchoring did nothing.
    # Run against --db-dir ~/.engram/longmemeval_cache/engram_lme_gemini_s_user_patched
    "engram_lme_s_user_patched": AblationConfig(
        name="engram_lme_s_user_patched",
        description=(
            "S-split patched User node — synthetic person node injected into existing "
            "gemini_s checkpoints, person_anchoring=True, semantic rerank top_k=100."
        ),
        superseded_pruning=True,
        recency_decay=True,
        top_k=100,
        semantic_rerank=True,
        dedup_threshold=0.95,
        domain="episodic_personal",
        abstention_mode=True,
        person_anchoring=True,
        checkpoint_source="engram_lme_gemini_s_user_patched",
    ),

    # Preference fan-out: same as user_patched but with preference_fanout=True.
    # Injects all preference-type nodes when the query contains recommendation/preference
    # signal verbs (recommend, suggest, prefer, like, etc.) — addresses the keyword
    # mismatch gap where preference nodes are tagged with product/activity names but
    # questions use generic action verbs.  Reuses the same checkpoint DBs (already
    # augmented with 7,574 preference nodes via preference_pass.py).
    "engram_lme_s_pref_fanout": AblationConfig(
        name="engram_lme_s_pref_fanout",
        description=(
            "S-split with preference fan-out — injects all preference nodes when query "
            "contains recommendation/preference signal verbs. Reuses user_patched DBs."
        ),
        superseded_pruning=True,
        recency_decay=True,
        top_k=100,
        semantic_rerank=True,
        dedup_threshold=0.95,
        domain="episodic_personal",
        abstention_mode=True,
        person_anchoring=True,
        preference_fanout=True,
        checkpoint_source="engram_lme_gemini_s_user_patched",
    ),

    # Sentence index: raw per-sentence vectors as semantic fallback for queries whose
    # keywords generate zero BFS entry nodes (the SS-user gap root cause).
    # Requires fresh extraction — does NOT reuse engram_lme_gemini_s checkpoints because
    # the sentence index is built at ingest time by ingestion_pipeline.add_raw_sentence().
    # Expected lift: ss-user category (+?pp) where "What degree did I graduate with?"
    # returns no keyword hits but semantically matches "I graduated from MIT with a
    # computer science degree." in the raw transcript.
    "engram_lme_s_sentence_index": AblationConfig(
        name="engram_lme_s_sentence_index",
        description=(
            "S-split sentence index — raw per-sentence vectors as semantic fallback "
            "when BFS entry_nodes < 3. Requires fresh extraction (builds raw_sentences "
            "at ingest time). Semantic rerank top_k=100, person_anchoring=True."
        ),
        superseded_pruning=True,
        recency_decay=True,
        top_k=100,
        semantic_rerank=True,
        dedup_threshold=0.95,
        domain="episodic_personal",
        abstention_mode=True,
        person_anchoring=True,
        sentence_index=True,   # enables sentence_index.enabled at ingest + retrieval
    ),
}

# Convenience: configs to run in a single paper table row
LME_PAPER_CONFIGS = ["engram_lme_gemini", "engram_lme_gpt4omini"]
