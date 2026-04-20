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

    # Preference fan-out disabled control: same as pref_fanout configs but with
    # preference_fanout=False. Isolates whether fanout itself hurts multi-session by
    # crowding token budget. If this recovers multi-session to ~54.9%, fanout needs
    # to be conditioned on question type rather than applied universally.
    "engram_lme_s_pref_fanout_off": AblationConfig(
        name="engram_lme_s_pref_fanout_off",
        description=(
            "S-split control: preference_fanout=False. Isolates whether fanout itself "
            "is causing multi-session regression (-9.8pp). Reuses user_patched DBs."
        ),
        superseded_pruning=True,
        recency_decay=True,
        top_k=100,
        semantic_rerank=True,
        dedup_threshold=0.95,
        domain="episodic_personal",
        abstention_mode=True,
        person_anchoring=True,
        preference_fanout=False,
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

    # Preference fan-out with raised cap=60 — tests whether the -11.3pp multi-session
    # regression (pre-cap 54.9% → cap=20 43.6%) is recoverable by allowing more preference
    # nodes through the cosine-similarity gate. p90 of preference node counts is 257, so
    # cap=20 was silently dropping the majority on dense conversations.
    "engram_lme_s_pref_fanout_cap60": AblationConfig(
        name="engram_lme_s_pref_fanout_cap60",
        description=(
            "S-split preference fan-out with cap=60 (vs default 20). "
            "Tests whether raising the preference injection cap recovers multi-session accuracy. "
            "Reuses user_patched DBs."
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
        preference_fanout_cap=60,
        checkpoint_source="engram_lme_gemini_s_user_patched",
    ),

    # Preference fan-out with cap=0 (unlimited) — exact replication of the Apr 15 run
    # that achieved 54.9% multi-session. The Apr 15 codebase had fanout=True but cap
    # enforcement didn't exist yet (added in 22a3bc6). cap=0 disables the cosine gate
    # entirely, replicating that behavior with current code.
    "engram_lme_s_pref_fanout_cap0": AblationConfig(
        name="engram_lme_s_pref_fanout_cap0",
        description=(
            "S-split preference fan-out with cap=0 (unlimited). "
            "Replicates Apr-15 baseline behavior — no cosine gate on preference injection. "
            "Tests whether removing the cap entirely recovers 54.9%% multi-session."
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
        preference_fanout_cap=0,
        checkpoint_source="engram_lme_gemini_s_user_patched",
    ),

    # Preference v2: re-extracted preferences with improved bridging-tag prompt.
    # The original user_patched preferences were tagged with specific brand/product names
    # (e.g. "Zagg", "iPhone 13 Pro") but lacked generic category tags ("phone accessories",
    # "phone") that preference questions use.  This config uses DBs where:
    #   1. All old preference nodes were stripped from the 22 failing samples
    #   2. preference_pass was re-run with the improved prompt requiring category bridging tags
    # Tests whether normal BFS retrieval (no fanout) can find preference nodes via query
    # vocab once the tags are enriched.
    "engram_lme_s_pref_v2": AblationConfig(
        name="engram_lme_s_pref_v2",
        description=(
            "S-split with re-extracted preferences using improved bridging-tag prompt. "
            "22 failing single-session-preference samples re-extracted from scratch. "
            "Normal BFS (no fanout) — tests vocabulary gap fix at extraction time."
        ),
        superseded_pruning=True,
        recency_decay=True,
        top_k=100,
        semantic_rerank=True,
        dedup_threshold=0.95,
        domain="episodic_personal",
        abstention_mode=True,
        person_anchoring=True,
        checkpoint_source="engram_lme_gemini_s_pref_v2",
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

    # Regression fix verification: semantic_rerank_cap=0 (unlimited).
    # Root cause of -9.8pp multi-session regression (54.9%→45.1%): DEFAULT_STRATEGIES
    # semantic_rerank_cap=300 was added in commit 22a3bc6. Multi-session facts have low
    # initial BFS scores → fall outside position 300 in pre-rerank sort → never get cosine
    # similarity computed → eliminated by top_k=100. This config explicitly disables the cap
    # (0 = unlimited, rerank all collected nodes) to restore Apr-15 behavior.
    # Reuses user_patched checkpoints (same as the regressed Apr-15 run).
    "engram_lme_s_rerank_uncapped": AblationConfig(
        name="engram_lme_s_rerank_uncapped",
        description=(
            "S-split semantic rerank cap=0 (unlimited). Fixes -9.8pp multi-session regression "
            "from semantic_rerank_cap=300 default added in 22a3bc6. All BFS-collected nodes "
            "enter cosine reranking. Reuses user_patched checkpoints."
        ),
        superseded_pruning=True,
        recency_decay=True,
        top_k=100,
        semantic_rerank=True,
        semantic_rerank_cap=0,
        dedup_threshold=0.95,
        domain="episodic_personal",
        abstention_mode=True,
        person_anchoring=True,
        checkpoint_source="engram_lme_gemini_s_user_patched",
    ),

    # Full replication of Apr-15 config with the cap fix: cap=0 + preference_fanout=True.
    # Apr-15 had fanout=True, which the verification run above omitted.
    # This config is the exact analog of engram_lme_s_pref_fanout (the Apr-15 run)
    # but with semantic_rerank_cap=0 explicitly set to prevent future silent re-regression.
    "engram_lme_s_apr15_repro": AblationConfig(
        name="engram_lme_s_apr15_repro",
        description=(
            "Full Apr-15 replication — cap=0 + preference_fanout=True + temporal_auto_route=False. "
            "Apples-to-apples against s_pref_fanout_20260415.json (54.9%% multi-session). "
            "temporal_auto_route=False: b6d4f72 was committed AFTER the Apr-15 run (05:08 UTC "
            "vs 17:17 -0800 commit). Reuses user_patched checkpoints."
        ),
        superseded_pruning=True,
        recency_decay=True,
        top_k=100,
        semantic_rerank=True,
        semantic_rerank_cap=0,
        dedup_threshold=0.95,
        domain="episodic_personal",
        abstention_mode=True,
        person_anchoring=True,
        preference_fanout=True,
        temporal_auto_route=False,
        checkpoint_source="engram_lme_gemini_s_user_patched",
    ),

    # Semantic retrieval channel: independent linear scan of all node embeddings.
    # Nodes with no tag match and no BFS connectivity (e.g. preference nodes, obscure
    # facts) are invisible to BFS regardless of cosine similarity. This channel surfaces
    # them by scoring every stored embedding against the query and injecting the top-K
    # into the candidate pool before semantic_rerank unifies and re-ranks.
    # Reuses engram_lme_gemini_s_user_patched checkpoints (same as pref_fanout).
    "engram_lme_s_semantic_retrieval": AblationConfig(
        name="engram_lme_s_semantic_retrieval",
        description=(
            "S-split with independent semantic retrieval channel — linear scan of all "
            "node embeddings, inject top-40 by cosine into BFS pool before semantic_rerank. "
            "Reuses user_patched checkpoints."
        ),
        superseded_pruning=True,
        recency_decay=True,
        top_k=100,
        semantic_rerank=True,
        dedup_threshold=0.95,
        domain="episodic_personal",
        abstention_mode=True,
        person_anchoring=True,
        semantic_retrieval=True,
        semantic_retrieval_k=40,
        checkpoint_source="engram_lme_gemini_s_user_patched",
    ),
}

# Convenience: configs to run in a single paper table row
LME_PAPER_CONFIGS = ["engram_lme_gemini", "engram_lme_gpt4omini"]
