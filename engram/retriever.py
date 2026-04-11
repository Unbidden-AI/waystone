"""Retrieval engine: tag matching, BFS traversal, strategy pipeline, and context assembly."""

import logging
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .store import GraphStore

# ---------------------------------------------------------------------------
# LOCOMO query expansion: synonym dict for common personal-fact question verbs
# ---------------------------------------------------------------------------

LOCOMO_VERB_SYNONYMS: dict[str, list[str]] = {
    "eat":          ["food", "meal", "ate", "eating", "dinner", "lunch", "breakfast", "snack"],
    "ate":          ["eat", "food", "meal", "eating"],
    "eating":       ["eat", "food", "meal"],
    "drink":        ["drank", "drinking", "beverage"],
    "drank":        ["drink", "drinking", "beverage"],
    "visit":        ["visited", "visiting", "came", "trip", "traveled"],
    "visited":      ["visit", "visiting", "came", "trip"],
    "visiting":     ["visit", "visited", "trip"],
    "work":         ["job", "employed", "employment", "career", "profession", "worked", "working"],
    "worked":       ["work", "job", "career", "profession"],
    "working":      ["work", "job", "career"],
    "job":          ["work", "career", "profession", "occupation", "employed"],
    "live":         ["home", "location", "moved", "living", "house", "apartment", "lived"],
    "lived":        ["live", "home", "location", "house", "apartment"],
    "living":       ["live", "home", "location", "house", "apartment"],
    "date":         ["dating", "relationship", "together", "couple"],
    "dating":       ["relationship", "together", "couple", "date"],
    "relationship": ["dating", "together", "couple", "partner"],
    "marry":        ["married", "marriage", "wedding", "engaged"],
    "married":      ["marriage", "wedding", "spouse", "husband", "wife"],
    "marriage":     ["married", "wedding", "spouse"],
    "study":        ["school", "college", "university", "degree", "course", "studied", "studying"],
    "studied":      ["study", "school", "college", "university"],
    "studying":     ["study", "school", "college"],
    "meet":         ["met", "meeting", "encounter", "introduced"],
    "met":          ["meet", "meeting", "introduced"],
    "move":         ["moved", "moving", "relocated", "relocation"],
    "moved":        ["move", "moving", "relocation"],
    "travel":       ["traveled", "trip", "visit", "flight"],
    "traveled":     ["travel", "trip", "visit"],
    "trip":         ["travel", "visited", "visit"],
    "feel":         ["feeling", "felt", "emotion", "mood"],
    "felt":         ["feel", "feeling", "emotion", "mood"],
    "like":         ["love", "enjoy", "prefer", "favorite", "favourite"],
    "love":         ["like", "enjoy", "prefer", "favorite"],
    "enjoy":        ["like", "love", "prefer", "hobby"],
    "hobby":        ["enjoy", "like", "love", "interest", "activity"],
    "tell":         ["told", "said", "mention", "mentioned", "share", "shared"],
    "told":         ["tell", "say", "mention", "share"],
    "said":         ["tell", "told", "mention", "say"],
    "plan":         ["planned", "planning", "intend", "intention", "goal"],
    "planned":      ["plan", "planning", "intention", "goal"],
    "sick":         ["illness", "health", "hospital", "doctor", "disease", "diagnosis"],
    "ill":          ["sick", "illness", "health", "hospital", "doctor"],
    "die":          ["died", "death", "passed", "funeral"],
    "died":         ["die", "death", "passed", "funeral"],
    "born":         ["birth", "birthday", "child", "baby", "pregnant"],
    "pregnant":     ["born", "birth", "baby", "child"],
    "adopt":        ["adopted", "adoption", "child", "foster"],
    "adopted":      ["adopt", "adoption", "child"],
    "graduate":     ["graduated", "graduation", "degree", "university", "school"],
    "graduated":    ["graduate", "graduation", "degree"],
    "hire":         ["hired", "job", "work", "employed"],
    "hired":        ["hire", "job", "work", "employed"],
    "fire":         ["fired", "job", "unemployed", "quit"],
    "fired":        ["fire", "job", "unemployed"],
    "quit":         ["quitting", "fired", "job", "resign", "resigned", "left"],
    "break":        ["broke", "broken", "breakup", "separated", "split"],
    "broke":        ["break", "breakup", "separated"],
    "adopt":        ["adopted", "adoption", "child"],
    # Speaker-role synonyms — for episodic memory where the main speaker is labeled
    # "User" in the transcript. Ensures "user" queries also match "speaker"-tagged nodes
    # and vice versa when query_expansion is enabled.
    "user":         ["speaker", "main speaker"],
    "speaker":      ["user", "main speaker"],
}

# Relationship terms used for query coreference resolution (#4).
# When a query mentions a relationship term (e.g. "sister") and we have a person
# node tagged with that term + another named keyword, we resolve the referent's
# name and add it to the keyword set so BFS can find that person's fact neighborhood.
RELATIONSHIP_TERMS = {
    "sister", "brother", "mother", "father", "mom", "dad", "parents",
    "husband", "wife", "spouse", "partner", "boyfriend", "girlfriend",
    "friend", "colleague", "coworker", "boss", "manager", "mentor",
    "aunt", "uncle", "cousin", "grandmother", "grandfather", "grandma",
    "grandpa", "daughter", "son", "child", "children", "kid", "kids",
    "roommate", "neighbor", "classmate", "teammate",
}

# Common sentence-starter / question words to exclude from named-entity detection
_QUERY_START_WORDS = {
    "what", "when", "where", "who", "why", "how", "did", "does", "has",
    "have", "is", "was", "were", "can", "could", "would", "should",
    "the", "a", "an", "in", "on", "at", "to", "from", "for", "of",
    "do", "tell", "give", "find", "show", "list", "describe",
}

log = logging.getLogger(__name__)

# Common words to exclude from keyword extraction
STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "to", "of", "in",
    "for", "on", "with", "at", "by", "from", "as", "into", "about",
    "that", "this", "it", "its", "and", "or", "but", "not", "no", "if",
    "then", "than", "so", "up", "out", "just", "also", "how", "what",
    "when", "where", "which", "who", "why", "all", "each", "every",
    "both", "few", "more", "most", "some", "any", "i", "me", "my", "we",
    "our", "you", "your", "he", "she", "they", "them", "their",
    # Common query verbs and prepositions that pass through tokenization
    # but are not meaningful retrieval keywords — they match unrelated tags
    # (e.g. "over" hits "failover", "chosen" hits nothing useful).
    "use", "used", "using", "used", "set", "get", "got", "let", "put",
    "over", "under", "between", "through", "against", "within", "along",
    "chosen", "decided", "called", "based", "made", "used", "given",
    "via", "per", "vs", "versus",
}

# Default strategy settings (all reductions off)
DEFAULT_STRATEGIES = {
    "semantic": True,
    "superseded_pruning": False,
    "confidence_threshold": 0.0,
    "recency_decay": False,
    "recency_half_life_days": 30,
    "token_budget": 0,
    "relevance_scoring": False,
    "semantic_rerank": False,  # Post-BFS re-ranking: multiply _score by cosine similarity to query
    "cross_encoder_rerank": False,  # Post-BFS re-ranking: score (query, fact) pairs via cross-encoder
}


@dataclass
class RetrievalResult:
    """Full retrieval result with metadata for benchmarking."""
    markdown: str
    nodes_before_strategies: int
    nodes_after_strategies: int
    strategies_applied: list[str] = field(default_factory=list)
    tokens_estimated: int = 0


def reciprocal_rank_fusion(
    ranked_lists: list[list[str]],
    k: int = 60,
) -> list[tuple[str, float]]:
    """Merge multiple ranked lists into one via Reciprocal Rank Fusion.

    RRF score for a node = sum(1 / (k + rank_i)) across all lists that
    contain the node, where rank_i is 1-based. k=60 is the standard default
    (Cormack et al., 2009). Higher score = more relevant.

    Args:
        ranked_lists: Each list is a ranking of node IDs, most relevant first.
        k: RRF smoothing constant (default 60).

    Returns:
        List of (node_id, rrf_score) sorted descending by score.
    """
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, node_id in enumerate(ranked, start=1):
            scores[node_id] = scores.get(node_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def retrieve(
    store: GraphStore,
    task_description: str,
    hops: int = 3,
    top_k: int = 10,
    strategies: dict | None = None,
) -> str:
    """Retrieve relevant context for a task description.

    Returns formatted markdown string with relevant context.
    """
    result = retrieve_with_stats(store, task_description, hops, top_k, strategies)
    return result.markdown


def retrieve_with_stats(
    store: GraphStore,
    task_description: str,
    hops: int = 3,
    top_k: int = 10,
    strategies: dict | None = None,
) -> RetrievalResult:
    """Retrieve context with full stats for benchmarking.

    Args:
        store: Graph store to query
        task_description: Natural language task description
        hops: BFS traversal depth
        top_k: Max nodes to return
        strategies: Dict of strategy toggles. Keys:
            - semantic (bool): Include semantic embedding-based augmentation (default: True)
            - superseded_pruning (bool): Drop superseded nodes
            - confidence_threshold (float): Min confidence to include (0.0 = off)
            - recency_decay (bool): Apply time-based decay to scores
            - recency_half_life_days (int): Half-life for decay
            - token_budget (int): Max estimated tokens in output (0 = off)
            - relevance_scoring (bool): Rank entry nodes by tag overlap count
    """
    strats = {**DEFAULT_STRATEGIES, **(strategies or {})}

    # Dynamic top_k: scale with graph size when top_k is None.
    # Formula selected by strats["topk_formula"]: "sqrt" or "log2".
    # "sqrt" = min(50, max(10, sqrt(n))) — aggressive, good for small graphs
    # "log2" = min(100, max(10, int(log2(n)*5))) — ~50 at LOCOMO scale, sane at all sizes
    if top_k is None:
        node_count = store.get_stats()["node_count"]
        formula = strats.get("topk_formula", "sqrt")
        if formula == "log2":
            top_k = min(100, max(10, int(math.log2(max(node_count, 2)) * 5)))
        else:
            top_k = min(50, max(10, int(math.sqrt(node_count))))
        log.debug("Dynamic top_k (%s): %d (graph has %d nodes)", formula, top_k, node_count)

    # Pinned nodes always inject regardless of query relevance
    pinned_nodes = store.get_pinned_nodes()
    pinned_ids = {n["id"] for n in pinned_nodes}

    keywords = extract_keywords(task_description)
    if strats.get("query_expansion"):
        keywords = expand_keywords(task_description, keywords)
    if strats.get("query_coreference"):
        keywords = resolve_coreferences(store, keywords)
    log.debug("Retrieval: task=%r keywords=%s hops=%d top_k=%d pinned=%d", task_description[:80], keywords, hops, top_k, len(pinned_nodes))
    if not keywords and not pinned_nodes:
        log.debug("No keywords extracted and no pinned nodes — returning empty result")
        return RetrievalResult(markdown="No relevant context found.", nodes_before_strategies=0, nodes_after_strategies=0)
    if not keywords:
        markdown = assemble_markdown([], task_description, [], pinned_nodes=pinned_nodes,
                                     resolve_dates=strats.get("resolve_dates", True))
        return RetrievalResult(markdown=markdown, nodes_before_strategies=0, nodes_after_strategies=0)

    # --- Multi-channel entry node retrieval with Reciprocal Rank Fusion ---
    # Three parallel ranked lists: tag-overlap, BM25 (FTS5), semantic (cosine).
    # RRF merges them into a single relevance score without scale calibration.

    # Channel 1: Tag-overlap (existing approach)
    tag_nodes_raw = [n for n in store.get_nodes_by_tags(keywords) if n["id"] not in pinned_ids]
    tag_ranked: list[str] = [n["id"] for n in tag_nodes_raw]
    log.debug("Tag search: %d entry nodes matched (excluding %d pinned)", len(tag_nodes_raw), len(pinned_ids))

    # Channel 2: BM25 full-text search on fact text (FTS5, no new deps)
    # Quote each keyword to handle any special FTS5 chars (e.g. hyphens, dots)
    fts_query = " OR ".join(f'"{kw}"' for kw in keywords)
    fts_hits = store.search_by_fts(fts_query, top_k=top_k * 3)
    fts_ranked: list[str] = [nid for nid, _ in fts_hits if nid not in pinned_ids]
    log.debug("FTS BM25: %d hits", len(fts_ranked))

    # Channel 3: Semantic embedding (cosine via sqlite-vec)
    sem_ranked: list[str] = []
    query_blob: bytes | None = None
    from engram import embedder
    if (strats["semantic"] or strats.get("semantic_rerank")) and embedder.is_available() and store._vec_available:
        query_blob = embedder.embed_text(task_description)
    if strats["semantic"] and query_blob is not None:
        sem_ranked = [nid for nid in store.search_by_embedding(query_blob, top_k=top_k)
                      if nid not in pinned_ids]
        log.debug("Semantic: %d hits", len(sem_ranked))

    # Merge via RRF then hydrate nodes (fetch in one query)
    rrf_results = reciprocal_rank_fusion([tag_ranked, fts_ranked, sem_ranked])
    all_entry_ids_ordered = [nid for nid, _ in rrf_results]
    rrf_score_map = {nid: score for nid, score in rrf_results}

    # Also include any fact-text matches not already in tag results
    fact_nodes = store.get_nodes_by_fact_text(keywords)
    fact_only_ids = [n["id"] for n in fact_nodes
                     if n["id"] not in pinned_ids and n["id"] not in rrf_score_map]
    if fact_only_ids:
        # Append at end of RRF list (no RRF score contribution — treat as tail)
        all_entry_ids_ordered.extend(fact_only_ids)
        log.debug("Fact-text fallback added %d nodes not in RRF results", len(fact_only_ids))

    # Hydrate all entry node dicts in one pass
    node_by_id = {n["id"]: n for n in tag_nodes_raw}
    missing_ids = [nid for nid in all_entry_ids_ordered if nid not in node_by_id]
    if missing_ids:
        for n in store.get_nodes_by_ids(missing_ids):
            node_by_id[n["id"]] = n

    entry_nodes = [node_by_id[nid] for nid in all_entry_ids_ordered if nid in node_by_id]
    # Attach RRF score as _rrf for downstream use
    for n in entry_nodes:
        n["_rrf"] = rrf_score_map.get(n["id"], 0.0)

    # Raw sentence fallback (#sentence_index): when the primary BFS entry-node pool
    # is sparse (< fallback_threshold), search the raw_sentences vector table for
    # semantically similar utterances.  Results are appended to the output markdown
    # as a separate section — they are raw transcript snippets, not graph nodes,
    # so they bypass BFS and strategy filtering entirely.
    raw_sentence_snippets: list[dict] = []
    if (
        strats.get("sentence_index")
        and query_blob is not None
        and store.has_raw_sentences()
    ):
        fallback_threshold = strats.get("sentence_fallback_threshold", 3)
        if len(entry_nodes) < fallback_threshold:
            earlier_n = strats.get("sentence_earlier_neighbors", 2)
            later_n = strats.get("sentence_later_neighbors", 2)
            sent_top_k = strats.get("sentence_top_k", 10)
            raw_sentence_snippets = store.semantic_search_raw_sentences(
                query_blob,
                top_k=sent_top_k,
                earlier_neighbors=earlier_n,
                later_neighbors=later_n,
            )
            log.debug(
                "Raw sentence fallback: %d snippets (entry_nodes=%d < threshold=%d)",
                len(raw_sentence_snippets), len(entry_nodes), fallback_threshold,
            )

    # Session-scoped filtering (#6): restrict entry nodes to sessions that contain
    # evidence for this question. source_transcript stores "conv-XX/session_N" —
    # extract the session part and match against allowed_session_ids.
    allowed_sessions = strats.get("allowed_session_ids")
    if allowed_sessions:
        allowed_set = set(allowed_sessions)
        before_filter = len(entry_nodes)
        entry_nodes = [
            n for n in entry_nodes
            if n.get("source_transcript", "").split("/")[-1] in allowed_set
        ]
        log.debug(
            "Session filter: %d → %d entry nodes (allowed: %s)",
            before_filter, len(entry_nodes), sorted(allowed_set),
        )

    if not entry_nodes:
        return RetrievalResult(markdown="No relevant context found.", nodes_before_strategies=0, nodes_after_strategies=0)

    # Strategy: Relevance scoring — rank entry nodes by tag overlap count
    # When RRF is active and multiple channels fired, RRF ordering already encodes
    # multi-signal relevance. Relevance scoring still augments _relevance for the
    # high_overlap split below, but doesn't reorder (order is RRF-determined).
    if strats["relevance_scoring"]:
        entry_nodes = score_by_relevance(entry_nodes, keywords)

    # Build a seed set: prioritize nodes with ≥2 keyword-tag matches (high relevance),
    # then fill remaining slots with other candidates sorted by relevance score.
    # This avoids the previous failure mode where a strict ≥2 filter excluded the
    # most directly relevant node (e.g. the JWT decision node scoring only 1) while
    # still capping total seeds to prevent top_k saturation with off-topic nodes.
    keyword_set = set(keywords)
    max_seeds = max(1, top_k // 2)

    # Use _relevance score (tags + fact-text hits) when it has been computed,
    # otherwise fall back to raw tag count.  This ensures the same signal that
    # drove ordering also drives the high/low-overlap partition.
    # Compute RRF threshold for high-overlap promotion: top-25% of scored nodes
    # qualify regardless of tag overlap count. This promotes nodes that ranked
    # well via BM25 or semantic but have sparse tags.
    rrf_scores = [n.get("_rrf", 0.0) for n in entry_nodes if n.get("_rrf", 0.0) > 0]
    rrf_top_threshold = sorted(rrf_scores, reverse=True)[len(rrf_scores) // 4] if rrf_scores else 0.0

    if strats["relevance_scoring"]:
        high_overlap = [
            n for n in entry_nodes
            if n.get("_relevance", 0) >= 2 or n.get("_rrf", 0.0) >= rrf_top_threshold > 0
        ]
    else:
        high_overlap = [
            n for n in entry_nodes
            if _count_keyword_tag_hits(n.get("tags", []), keyword_set) >= 2
            or n.get("_rrf", 0.0) >= rrf_top_threshold > 0
        ]

    # Person anchoring: person nodes with ≥1 keyword-tag match are automatic high-confidence
    # seeds because they gate all person-scoped facts downstream in the BFS graph.
    _person_anchor_tags: list[str] = []  # names to use for exhaustive tag-scan after BFS
    if strats.get("person_anchoring"):
        high_overlap_ids = {n["id"] for n in high_overlap}
        person_anchors = [
            n for n in entry_nodes
            if n.get("type") == "person"
            and n["id"] not in high_overlap_ids
            and _count_keyword_tag_hits(n.get("tags", []), keyword_set) >= 1
        ]
        if person_anchors:
            log.debug("Person anchoring: promoting %d person nodes to high-overlap seeds", len(person_anchors))
            # Collect person name tags for exhaustive fan-out after BFS
            for pa in person_anchors:
                _person_anchor_tags.extend(pa.get("tags", []))
        high_overlap = high_overlap + person_anchors

    low_overlap_ids = {n["id"] for n in high_overlap}
    low_overlap = [n for n in entry_nodes if n["id"] not in low_overlap_ids]

    # Source restriction: when ≥5 high-overlap seeds all share the same source_transcript,
    # restrict low-overlap seeds to that same source. This prevents cross-project BFS flooding
    # in multi-project stores where generic keywords match nodes from the wrong project.
    # Threshold is 5 (not 3) to reduce false triggers from noise keywords hitting unrelated nodes.
    if len(high_overlap) >= 5:
        src_counts = Counter(n.get("source_transcript", "") for n in high_overlap)
        dominant_src, dominant_count = src_counts.most_common(1)[0]
        if dominant_src and dominant_count == len(high_overlap):
            low_overlap = [n for n in low_overlap if n.get("source_transcript") == dominant_src]

    # High-overlap nodes first (already relevance-sorted), fill remaining with low-overlap
    entry_nodes = (high_overlap + low_overlap)[:max_seeds]

    # Adaptive seed expansion: if average seed score is low, expand the seed set
    # This helps queries where keywords don't match well but related context exists
    SEED_CONFIDENCE_FLOOR = 0.8
    if entry_nodes and strats["relevance_scoring"]:
        avg_relevance = sum(n.get("_relevance", 0) for n in entry_nodes) / len(entry_nodes)
        log.debug("Seed set average relevance: %.3f (floor: %.3f)", avg_relevance, SEED_CONFIDENCE_FLOOR)
        if avg_relevance < SEED_CONFIDENCE_FLOOR:
            expanded_k = min(len(entry_nodes) * 2, len(high_overlap + low_overlap))
            original_size = len(entry_nodes)
            entry_nodes = (high_overlap + low_overlap)[:expanded_k]
            log.debug(
                "Adaptive expansion: avg relevance %.3f < floor %.3f, expanding from %d to %d seeds",
                avg_relevance, SEED_CONFIDENCE_FLOOR, original_size, len(entry_nodes)
            )

    # BFS from entry nodes
    collected_nodes = bfs_collect(store, entry_nodes, hops)
    nodes_before = len(collected_nodes)

    # Apply post-retrieval strategy pipeline
    applied = []

    if strats["superseded_pruning"]:
        collected_nodes = prune_superseded(collected_nodes, store)
        applied.append("superseded_pruning")

    if strats["confidence_threshold"] > 0:
        collected_nodes = filter_by_confidence(collected_nodes, strats["confidence_threshold"])
        applied.append(f"confidence_threshold({strats['confidence_threshold']})")

    if strats["recency_decay"]:
        collected_nodes = apply_recency_decay(collected_nodes, strats["recency_half_life_days"])
        applied.append(f"recency_decay(half_life={strats['recency_half_life_days']}d)")

    if strats.get("temporal_proximity"):
        collected_nodes = apply_temporal_proximity(collected_nodes, task_description)
        applied.append("temporal_proximity")

    if strats.get("edge_weight_scoring"):
        for node in collected_nodes:
            w = node.get("_max_edge_weight", 1.0)
            if w < 1.0:
                node["_score"] = node.get("_score", node.get("confidence", 0.0)) * w
        applied.append("edge_weight_scoring")

    if strats.get("semantic_rerank") and query_blob is not None:
        # Fetch stored embeddings for all collected nodes in one batch query
        node_ids = [n["id"] for n in collected_nodes]
        if node_ids:
            placeholders = ",".join("?" * len(node_ids))
            rows = store.conn.execute(
                f"SELECT node_id, embedding FROM node_embeddings WHERE node_id IN ({placeholders})",
                node_ids,
            ).fetchall()
            emb_map = {r[0]: bytes(r[1]) for r in rows}
            for node in collected_nodes:
                emb = emb_map.get(node["id"])
                if emb is not None:
                    sim = embedder.cosine_similarity(query_blob, emb)
                    # Clamp to [0, 1]: cosine can be negative, treat negatives as 0
                    sim = max(0.0, sim)
                    node["_score"] = node.get("_score", node.get("confidence", 0.0)) * (0.5 + 0.5 * sim)
            applied.append("semantic_rerank")

    if strats.get("cross_encoder_rerank") and collected_nodes:
        # Score each (query, fact) pair via cross-encoder — replaces _score entirely
        # since cross-encoder logits are on a different scale than confidence [0,1].
        facts = [n["fact"] for n in collected_nodes]
        ce_model = strats.get("cross_encoder_model") or None
        ce_kwargs = {"model_name": ce_model} if ce_model else {}
        ce_scores = embedder.cross_encode_scores(task_description, facts, **ce_kwargs)
        for node, score in zip(collected_nodes, ce_scores):
            node["_score"] = score
        applied.append("cross_encoder_rerank")

    if strats.get("rrf_rerank") and query_blob is not None and collected_nodes:
        # Reciprocal Rank Fusion of bi-encoder (semantic) and cross-encoder signals.
        # Neither score replaces the existing _score — both are converted to ranks and fused.
        # RRF constant k=60 (standard; higher k reduces the influence of top-rank gaps).
        rrf_k = strats.get("rrf_k", 60)

        # --- semantic rank list ---
        node_ids = [n["id"] for n in collected_nodes]
        placeholders = ",".join("?" * len(node_ids))
        rows = store.conn.execute(
            f"SELECT node_id, embedding FROM node_embeddings WHERE node_id IN ({placeholders})",
            node_ids,
        ).fetchall()
        emb_map = {r[0]: bytes(r[1]) for r in rows}
        sem_scores = []
        for node in collected_nodes:
            emb = emb_map.get(node["id"])
            sim = max(0.0, embedder.cosine_similarity(query_blob, emb)) if emb else 0.0
            sem_scores.append(sim)
        # rank sem descending (rank 0 = best)
        sem_order = sorted(range(len(collected_nodes)), key=lambda i: sem_scores[i], reverse=True)
        sem_rank = [0] * len(collected_nodes)
        for rank, idx in enumerate(sem_order):
            sem_rank[idx] = rank

        # --- cross-encoder rank list ---
        facts = [n["fact"] for n in collected_nodes]
        ce_model = strats.get("cross_encoder_model") or None
        ce_kwargs = {"model_name": ce_model} if ce_model else {}
        ce_scores_list = embedder.cross_encode_scores(task_description, facts, **ce_kwargs)
        ce_order = sorted(range(len(collected_nodes)), key=lambda i: ce_scores_list[i], reverse=True)
        ce_rank = [0] * len(collected_nodes)
        for rank, idx in enumerate(ce_order):
            ce_rank[idx] = rank

        # --- dynamic per-query-type weights ---
        rrf_weights = strats.get("rrf_weights") or {}
        if rrf_weights:
            query_type = _classify_query_type(task_description)
            alpha_ce = rrf_weights.get(query_type, rrf_weights.get("default", 0.5))
        else:
            alpha_ce = 0.5  # 50/50 balanced
        alpha_sem = 1.0 - alpha_ce

        # --- fuse ---
        for i, node in enumerate(collected_nodes):
            node["_score"] = alpha_sem / (rrf_k + sem_rank[i]) + alpha_ce / (rrf_k + ce_rank[i])
        applied.append("rrf_rerank")

    # Sort by confidence (possibly decayed) descending, then BFS depth ascending
    # as a tiebreaker so nodes closer to entry points rank higher when scores are equal.
    collected_nodes.sort(
        key=lambda n: (n.get("_score", n.get("confidence", 0)), -n.get("_bfs_depth", 0)),
        reverse=True,
    )

    # Person-scoped exhaustive fan-out: when person_anchoring identified person anchors,
    # fetch ALL nodes tagged with the person's name and inject any that BFS missed.
    # These aggregative queries ("What activities has Melanie done?") need full coverage
    # of person-tagged facts, not just what BFS happened to traverse.
    if _person_anchor_tags:
        collected_ids = {n["id"] for n in collected_nodes}
        person_hub_nodes: list[dict] = []
        for tag in set(_person_anchor_tags):
            for n in store.get_nodes_by_tags([tag]):
                if n["id"] not in collected_ids and n["id"] not in pinned_ids:
                    n["_person_hub"] = True
                    person_hub_nodes.append(n)
                    collected_ids.add(n["id"])
                elif n["id"] in collected_ids:
                    # Mark already-collected person-tagged nodes for top_k protection
                    for cn in collected_nodes:
                        if cn["id"] == n["id"]:
                            cn["_person_hub"] = True
                            break
        if person_hub_nodes:
            log.debug("Person hub fan-out: injected %d additional person-tagged nodes", len(person_hub_nodes))
        collected_nodes = collected_nodes + person_hub_nodes

    # Seed preservation: ensure directly query-matched seed nodes (depth=0) are not
    # completely displaced by recency-biased BFS-expanded nodes. Reserve up to 40% of
    # top_k slots for seeds, filling remaining slots with the highest-scoring non-seeds.
    # This prevents stable early-conversation facts (allergies, hobbies) from being
    # buried by recent-session nodes when recency decay is active.
    # Person-hub nodes (from exhaustive fan-out) bypass the top_k cut but remain subject
    # to token_budget — they are the primary retrieval target for aggregative queries.
    person_hub_nodes_in_collected = [n for n in collected_nodes if n.get("_person_hub")]
    non_hub_collected = [n for n in collected_nodes if not n.get("_person_hub")]
    seed_ids = {n["id"] for n in entry_nodes}
    seeds_in_collected = [n for n in non_hub_collected if n["id"] in seed_ids]
    non_seeds_in_collected = [n for n in non_hub_collected if n["id"] not in seed_ids]
    seed_reserve = min(len(seeds_in_collected), top_k * 2 // 5)  # up to 40% reserved for seeds
    collected_nodes = (
        person_hub_nodes_in_collected
        + seeds_in_collected[:seed_reserve]
        + non_seeds_in_collected[:top_k - seed_reserve]
    )
    # Re-sort the final set by score so output is coherently ordered
    collected_nodes.sort(
        key=lambda n: (n.get("_score", n.get("confidence", 0)), -n.get("_bfs_depth", 0)),
        reverse=True,
    )

    if strats["token_budget"] > 0:
        collected_nodes = apply_token_budget(collected_nodes, strats["token_budget"])
        applied.append(f"token_budget({strats['token_budget']})")

    # Exclude pinned node IDs from the relevance pool (they appear separately)
    collected_nodes = [n for n in collected_nodes if n["id"] not in pinned_ids]

    nodes_after = len(collected_nodes)
    log.debug("Retrieval: %d nodes before strategies, %d after (strategies: %s)", nodes_before, nodes_after, applied)

    # Record usage for hit-count tracking (frequency-based deprioritization / vacuum)
    entry_ids = {n["id"] for n in entry_nodes}
    store.record_hits(node_ids=[n["id"] for n in collected_nodes], entry_ids=entry_ids)

    markdown = assemble_markdown(
        collected_nodes, task_description, applied,
        pinned_nodes=pinned_nodes,
        resolve_dates=strats.get("resolve_dates", True),
        temporal_sort=strats.get("temporal_sort", False),
    )
    if raw_sentence_snippets:
        markdown = markdown + "\n\n" + _format_raw_sentence_snippets(raw_sentence_snippets)
    tokens_est = estimate_tokens(markdown)

    return RetrievalResult(
        markdown=markdown,
        nodes_before_strategies=nodes_before,
        nodes_after_strategies=nodes_after,
        strategies_applied=applied,
        tokens_estimated=tokens_est,
    )


# ---------------------------------------------------------------------------
# Strategy implementations
# ---------------------------------------------------------------------------

_TEMPORAL_TOKENS = frozenset({
    "when", "first", "last", "before", "after", "how long", "how many times",
    "what time", "what date", "how old", "how often", "since", "ago", "recently",
    "latest", "earliest", "start", "began", "ended", "duration",
})
_PREFERENCE_TOKENS = frozenset({
    "would", "suit", "like", "prefer", "recommend", "enjoy", "interest",
    "favorite", "favourite", "might like", "could enjoy", "want",
})


def _classify_query_type(query: str) -> str:
    """Classify a retrieval query as 'temporal', 'preference', or 'default'.

    Used by dynamic-weight RRF to adjust the CE/semantic balance per query type.
    Keyword heuristics; no model required.
    """
    q = query.lower()
    if any(tok in q for tok in _TEMPORAL_TOKENS):
        return "temporal"
    if any(tok in q for tok in _PREFERENCE_TOKENS):
        return "preference"
    return "default"


def _stem(word: str) -> str:
    """Minimal suffix-stripping for English words.

    Targets plurals and common inflections only — conservative to avoid
    over-stemming. E.g.: "pets" → "pet", "allergies" → "allergy",
    "swimming" → "swim", "worked" → "work".
    """
    if len(word) <= 3:
        return word
    # -ies → -y  ("allergies" → "allergy", "hobbies" → "hobby")
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    # -ing → base, collapsing double consonant  ("swimming" → "swim")
    if word.endswith("ing") and len(word) > 5:
        stem = word[:-3]
        if len(stem) >= 2 and stem[-1] == stem[-2] and stem[-1] not in "aeiou":
            stem = stem[:-1]
        return stem
    # -ed → base, collapsing double consonant  ("stopped" → "stop")
    if word.endswith("ed") and len(word) > 4:
        stem = word[:-2]
        if len(stem) >= 2 and stem[-1] == stem[-2] and stem[-1] not in "aeiou":
            stem = stem[:-1]
        return stem
    # -es → base  ("dishes" → "dish", "goes" → "go") — handled before -s
    if word.endswith("es") and len(word) >= 4:
        return word[:-2]
    # -s → base  ("pets" → "pet", "cats" → "cat") — skip -ss/-us/-is/-as endings
    if word.endswith("s") and len(word) >= 4 and not word.endswith(("ss", "us", "is", "as")):
        return word[:-1]
    return word


def _tag_matches_keyword(tags: list[str], kw: str) -> bool:
    """True if keyword matches any tag via substring or stemmed-token comparison."""
    kw_stem = _stem(kw)
    for tag in tags:
        tag_lower = tag.lower()
        if kw in tag_lower:
            return True
        if any(kw_stem == _stem(tok) for tok in re.findall(r"[a-z]+", tag_lower)):
            return True
    return False


def _count_keyword_tag_hits(tags: list[str], keyword_set: set) -> int:
    """Count distinct keywords that match any tag (substring or stemmed-token).

    Extends the original SQL LIKE %keyword% behavior with stem-based matching
    so morphological variants like "pets"/"pet" or "allergies"/"allergy"
    count as a hit even when only one form appears in the stored tags.
    """
    return sum(1 for kw in keyword_set if _tag_matches_keyword(tags, kw))


def score_by_relevance(nodes: list[dict], keywords: list[str]) -> list[dict]:
    """Rank nodes by number of matching tags (not just binary match).

    Nodes with more keyword overlap are placed first, which makes them
    BFS seed priorities. Applies type-based boost to prioritize decisions,
    constraints, and trade-offs.

    Fact-text hits are included alongside tag hits so that vocabulary
    mismatches between query and stored tags don't bury relevant nodes.
    For example, a query keyword "allergic" matches the fact text
    "Joanna is allergic to most reptiles" even when the stored tag is
    "allergy" (which wouldn't match via substring).
    """
    TYPE_BOOST = {
        "decision": 1.5,
        "constraint": 1.4,
        "trade_off": 1.3,
        "lesson_learned": 1.2,
    }

    keyword_set = set(keywords)
    for node in nodes:
        node_tags = node.get("tags", [])
        tag_hits = _count_keyword_tag_hits(node_tags, keyword_set)
        # Count keywords found in fact text but not already matched by a tag.
        # Uses stemmed comparison so "allergic"/"allergy", "pet"/"pets" etc. count.
        fact_lower = node.get("fact", "").lower()
        fact_tokens = set(re.findall(r"[a-z]+", fact_lower))
        fact_stems = {_stem(t) for t in fact_tokens}
        fact_only_hits = sum(
            1 for kw in keyword_set
            if (kw in fact_lower or _stem(kw) in fact_stems)
            and not _tag_matches_keyword(node_tags, kw)
        )
        node_type = node.get("type", "").lower()
        boost = TYPE_BOOST.get(node_type, 1.0)
        node["_relevance"] = (tag_hits + fact_only_hits) * boost
    nodes.sort(key=lambda n: n["_relevance"], reverse=True)
    return nodes


def prune_superseded(nodes: list[dict], store: GraphStore) -> list[dict]:
    """Remove nodes that have been superseded by other nodes in the graph."""
    # Collect all node IDs that are targets of supersedes edges or listed in supersedes arrays
    superseded_ids = set()
    for node in nodes:
        for sid in node.get("supersedes", []):
            superseded_ids.add(sid)

    # Also check: if a node is the target of a supersedes edge, it's been superseded
    node_ids = {n["id"] for n in nodes}
    for node in nodes:
        for edge in store.get_edges_to(node["id"]):
            if edge["relation"] == "supersedes" and edge["from_id"] in node_ids:
                superseded_ids.add(node["id"])

    return [n for n in nodes if n["id"] not in superseded_ids]


def filter_by_confidence(nodes: list[dict], threshold: float) -> list[dict]:
    """Remove nodes below the confidence threshold."""
    return [n for n in nodes if n.get("confidence", 0) >= threshold]


def apply_recency_decay(nodes: list[dict], half_life_days: int) -> list[dict]:
    """Apply exponential decay to node scores based on age.

    Score = confidence * 2^(-age_days / half_life_days)

    Stores the decayed score in node["_score"] for sorting.
    """
    now = datetime.now(timezone.utc)
    for node in nodes:
        # Prefer occurred_at (actual conversation date) over created_at (ingestion time)
        ts = node.get("occurred_at") or node.get("created_at", "")
        try:
            created_dt = datetime.fromisoformat(ts)
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
            age_days = (now - created_dt).total_seconds() / 86400
        except (ValueError, TypeError):
            age_days = 0

        confidence = node.get("confidence", 0.5)
        decay = math.pow(2, -age_days / max(half_life_days, 1))
        node["_score"] = confidence * decay
    return nodes


def apply_token_budget(nodes: list[dict], budget: int) -> list[dict]:
    """Pack nodes into a token budget, dropping the lowest-scored ones.

    Uses a rough estimate of ~4 characters per token for the fact text
    plus overhead per node.
    """
    result = []
    used = 0
    overhead_per_node = 20  # tokens for bullet, confidence, source ref, etc.

    for node in nodes:
        fact_tokens = estimate_tokens(node["fact"]) + overhead_per_node
        if used + fact_tokens > budget:
            continue
        used += fact_tokens
        result.append(node)
    return result


# ---------------------------------------------------------------------------
# Core retrieval helpers
# ---------------------------------------------------------------------------

def bfs_collect(store: GraphStore, entry_nodes: list[dict], hops: int) -> list[dict]:
    """BFS from entry nodes up to `hops` depth, collecting all reachable nodes.

    Uses batch queries per hop (2 queries per layer instead of 2+N per node),
    which reduces SQLite round-trips from O(nodes) to O(hops).

    Sets `_bfs_depth` on each node (0 = entry node, 1 = one hop away, etc.)
    so callers can use depth as a ranking signal.
    """
    visited_ids: set[str] = set()
    collected: list[dict] = []

    # Seed layer 0 — seed nodes are always natively matched (weight=1.0)
    current_layer_ids: list[str] = []
    for node in entry_nodes:
        if node["id"] not in visited_ids:
            visited_ids.add(node["id"])
            node = {**node, "_bfs_depth": 0, "_max_edge_weight": 1.0}
            collected.append(node)
            current_layer_ids.append(node["id"])

    for depth in range(hops):
        if not current_layer_ids:
            break

        # Batch-fetch all edges touching this layer (1 query for the whole layer)
        all_edges = store.get_edges_for_nodes(current_layer_ids)

        # Build neighbor_id → max edge weight map (best path quality wins)
        neighbor_max_weight: dict[str, float] = {}
        for edge in all_edges:
            w = edge.get("weight", 1.0)
            for nid in (edge["to_id"], edge["from_id"]):
                if nid not in visited_ids:
                    if nid not in neighbor_max_weight or w > neighbor_max_weight[nid]:
                        neighbor_max_weight[nid] = w

        neighbor_ids = list(neighbor_max_weight.keys())
        if not neighbor_ids:
            break

        # Batch-fetch all new neighbors in one query
        next_layer_ids: list[str] = []
        for node in store.get_nodes_by_ids(neighbor_ids):
            nid = node["id"]
            if nid not in visited_ids:
                visited_ids.add(nid)
                w = neighbor_max_weight.get(nid, 1.0)
                node = {**node, "_bfs_depth": depth + 1, "_max_edge_weight": w}
                collected.append(node)
                next_layer_ids.append(nid)

        current_layer_ids = next_layer_ids

    return collected


def _extract_named_entities(text: str) -> list[str]:
    """Extract likely named entities from original-case query text.

    Simple heuristic: non-first-word capitalized tokens that aren't common
    question/sentence-starter words. Returns lowercase for tag matching.
    """
    tokens = text.split()
    entities: list[str] = []
    seen: set[str] = set()
    for i, tok in enumerate(tokens):
        clean = tok.strip(".,;:!?\"'()[]{}")
        if not clean or len(clean) < 2:
            continue
        # Must be capitalized and not a known sentence-starter
        if not clean[0].isupper():
            continue
        cl = clean.lower()
        if cl in _QUERY_START_WORDS or cl in STOP_WORDS:
            continue
        if cl not in seen:
            seen.add(cl)
            entities.append(cl)
    return entities


def expand_keywords(text: str, base_keywords: list[str]) -> list[str]:
    """Expand base keywords with LOCOMO-specific synonyms and named entities.

    Called when strats["query_expansion"] is True. Adds:
    - Verb synonyms for common personal-fact question patterns
    - Named entity detection (capitalized tokens)
    """
    seen = set(base_keywords)
    result = list(base_keywords)

    # Named entity detection from original-case text
    for entity in _extract_named_entities(text):
        if entity not in seen:
            seen.add(entity)
            result.append(entity)

    # Verb synonym expansion — only expand the original base keywords, not
    # named entities. Expanding entities causes cascades when common words
    # like "live" or "work" appear as entity matches.
    for kw in base_keywords:
        for syn in LOCOMO_VERB_SYNONYMS.get(kw, []):
            if syn not in seen:
                seen.add(syn)
                result.append(syn)

    return result


def resolve_coreferences(store: "GraphStore", keywords: list[str]) -> list[str]:
    """Expand keywords by resolving relationship-term coreferences.

    When the query contains a relationship term (e.g. "sister") alongside a
    named keyword, look for person nodes whose tags include both that relationship
    term and the other keyword. Extract the person's name tags and add them to
    the keyword set so BFS traversal can find that person's full fact neighborhood.

    Example: query "where does [name]'s sister live?" → keywords ["sister", "live"].
    If a person node is tagged ["sister", "Sarah"] we add "Sarah" to keywords.

    This is a pure graph lookup — no extra extraction, works on cached DBs.
    """
    kw_set = set(keywords)
    relationship_matches = kw_set & RELATIONSHIP_TERMS
    if not relationship_matches:
        return keywords

    # Find person-typed nodes tagged with at least one relationship term
    # and at least one non-relationship keyword
    other_keywords = [kw for kw in keywords if kw not in RELATIONSHIP_TERMS]
    if not other_keywords:
        return keywords

    new_terms: list[str] = []
    seen = set(keywords)

    for rel_term in relationship_matches:
        # Get nodes tagged with this relationship term
        candidate_nodes = store.get_nodes_by_tags([rel_term])
        for node in candidate_nodes:
            if node.get("type") != "person":
                continue
            node_tags = {t.lower() for t in node.get("tags", [])}
            # Node must also match at least one of the other query keywords
            if not (node_tags & set(other_keywords)):
                continue
            # Extract name-like tags: tags that aren't relationship terms or stop words
            name_tags = [
                t for t in node_tags
                if t not in RELATIONSHIP_TERMS
                and t not in STOP_WORDS
                and t not in kw_set
                and len(t) >= 2
            ]
            for name in name_tags:
                if name not in seen:
                    seen.add(name)
                    new_terms.append(name)
                    log.debug("Coreference: resolved '%s' → '%s' via node %s", rel_term, name, node["id"])

    return keywords + new_terms


def _extract_query_dates(query: str) -> list[datetime]:
    """Extract date/time references from query text as datetime objects.

    Handles ISO dates, "Month Day Year", and bare 4-digit years.
    Returns datetime objects in UTC, used for temporal proximity scoring.
    """
    MONTH_MAP = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
    }
    dates: list[datetime] = []
    ql = query.lower()

    # ISO date: 2023-05-14
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", ql):
        try:
            dates.append(datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc))
        except ValueError:
            pass

    month_pat = r"(january|february|march|april|may|june|july|august|september|october|november|december)"

    # "Month Year": e.g. "March 2023" — try this first to avoid partial matches
    for m in re.finditer(month_pat + r"\s+(\d{4})\b", ql):
        month = MONTH_MAP[m.group(1)]
        year = int(m.group(2))
        try:
            dates.append(datetime(year, month, 15, tzinfo=timezone.utc))  # mid-month approx
        except ValueError:
            pass

    # "Month Day, Year": e.g. "March 14, 2023" — require explicit 4-digit year
    for m in re.finditer(month_pat + r"\s+(\d{1,2})(?!\d),?\s+(\d{4})\b", ql):
        month = MONTH_MAP[m.group(1)]
        day = int(m.group(2))
        year = int(m.group(3))
        try:
            dates.append(datetime(year, month, day, tzinfo=timezone.utc))
        except ValueError:
            pass

    # Bare year: 2021, 2022 …2029 (LOCOMO spans 2020-2024 roughly)
    for m in re.finditer(r"\b(20[12]\d)\b", ql):
        year = int(m.group(1))
        if not any(d.year == year for d in dates):  # don't double-add if already found above
            dates.append(datetime(year, 6, 15, tzinfo=timezone.utc))  # mid-year approx

    return dates


def apply_temporal_proximity(nodes: list[dict], query: str, half_life_days: float = 180.0) -> list[dict]:
    """Boost scores of nodes whose occurred_at is temporally close to query date references.

    Computes an additive proximity bonus: 1.0 at exact match, decaying with a
    half-life of `half_life_days`. Final score = original_score * (1 + bonus).
    Only activates when the query contains a parseable date/year reference.
    """
    date_refs = _extract_query_dates(query)
    if not date_refs:
        return nodes

    for node in nodes:
        ts = node.get("occurred_at") or node.get("created_at", "")
        if not ts:
            continue
        try:
            node_dt = datetime.fromisoformat(ts)
            if node_dt.tzinfo is None:
                node_dt = node_dt.replace(tzinfo=timezone.utc)
            min_days = min(
                abs((node_dt - ref_dt).total_seconds() / 86400)
                for ref_dt in date_refs
            )
            # Use raw confidence as bonus base — not the already-decayed _score.
            # Recency decay may have crushed _score to near-zero; multiplying
            # by (1 + bonus) can't rescue it. Instead compute an additive bonus
            # proportional to confidence and add it to whatever score exists.
            confidence = node.get("confidence", 0.5)
            proximity_bonus = confidence * math.pow(2, -min_days / max(half_life_days, 1))
            existing_score = node.get("_score", confidence)
            node["_score"] = existing_score + proximity_bonus
        except (ValueError, TypeError):
            pass

    return nodes


def extract_keywords(text: str) -> list[str]:
    """Extract keywords from text by tokenizing and filtering stop words.

    Numeric compound tokens like "15-minute" or "1000/min" are preserved as-is
    in addition to their split parts, so they can match auto-tagged node entries.
    Non-numeric hyphenated tokens ("hot-path") are still split into parts.
    """
    result: list[str] = []
    seen: set[str] = set()

    def _emit(w: str) -> None:
        w = w.strip(".,;:!?\"'()[]{}")
        if w and w not in STOP_WORDS and len(w) > 1 and w not in seen:
            seen.add(w)
            result.append(w)

    for raw in text.lower().split():
        word = raw.strip(".,;:!?\"'()[]{}")
        if not word:
            continue
        if "-" in word and any(c.isdigit() for c in word):
            # Numeric compound: keep whole token AND each split part
            _emit(word)
            for part in word.split("-"):
                _emit(part)
        else:
            if "-" in word:
                # Non-numeric hyphenated: emit whole compound AND each part
                # so "hot-path" matches nodes tagged "hot-path" as well as "hot"/"path"
                _emit(word)
            for part in word.replace("-", " ").split():
                _emit(part)

    return result


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 characters per token for English text."""
    return max(1, len(text) // 4)


def cluster_by_tags(nodes: list[dict], max_cluster_size: int = 20) -> list[list[dict]]:
    """Group nodes into clusters where members share at least one tag.

    Uses union-find on tag overlap. Clusters larger than max_cluster_size are
    split into windows sorted by created_at (oldest first) so the LLM sees
    temporal progression within each window.
    """
    if not nodes:
        return []

    # Map tag → node IDs
    tag_to_ids: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        for tag in node.get("tags", []):
            tag_to_ids[tag.lower()].append(node["id"])

    # Union-find
    parent = {n["id"]: n["id"] for n in nodes}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    for nids in tag_to_ids.values():
        for i in range(1, len(nids)):
            union(nids[0], nids[i])

    # Group by root
    groups: dict[str, list[dict]] = defaultdict(list)
    for node in nodes:
        groups[find(node["id"])].append(node)

    # Sort each group by created_at ascending, then chunk if oversized
    result = []
    for group in groups.values():
        group.sort(key=lambda n: n.get("created_at", ""))
        for i in range(0, len(group), max_cluster_size):
            chunk = group[i : i + max_cluster_size]
            if len(chunk) >= 2:
                result.append(chunk)

    return result


def _resolve_relative_dates(fact: str, occurred_at: str | None) -> str:
    """Rewrite relative date phrases in a fact string using the node's occurred_at timestamp.

    E.g. "Nate lost his job last week" -> "Nate lost his job the week before January 21, 2022"
    when occurred_at is set. Leaves the fact unchanged if occurred_at is missing or unparseable.
    """
    if not occurred_at:
        return fact
    try:
        import re as _re
        from datetime import datetime as _datetime, timezone as _timezone
        dt = _datetime.fromisoformat(occurred_at)
        date_label = dt.strftime("%B %-d, %Y")
        substitutions = [
            (r"\bthe day before yesterday\b", f"two days before {date_label}"),
            (r"\byesterday\b", f"the day before {date_label}"),
            (r"\bearlier this week\b", f"earlier in the week of {date_label}"),
            (r"\bearlier this month\b", f"earlier in the month of {date_label}"),
            (r"\ba few days ago\b", f"a few days before {date_label}"),
            (r"\ba few weeks ago\b", f"a few weeks before {date_label}"),
            (r"\ba few months ago\b", f"a few months before {date_label}"),
            (r"\blast week\b", f"the week before {date_label}"),
            (r"\blast month\b", f"the month before {date_label}"),
            (r"\blast year\b", f"the year before {date_label}"),
            (r"\bnext week\b", f"the week after {date_label}"),
            (r"\bnext month\b", f"the month after {date_label}"),
            (r"\bthis week\b", f"the week of {date_label}"),
            (r"\bthis month\b", f"the month of {date_label}"),
            (r"\bthis year\b", f"the year {dt.year}"),
            (r"\brecently\b", f"around {date_label}"),
        ]
        result = fact
        for pattern, replacement in substitutions:
            result = _re.sub(pattern, replacement, result, flags=_re.IGNORECASE)
        return result
    except (ValueError, TypeError):
        return fact


def _format_raw_sentence_snippets(snippets: list[dict]) -> str:
    """Format raw sentence search results as a markdown section.

    Each snippet includes the matched sentence and its neighbor context window.
    Used as a semantic fallback when primary BFS retrieval is sparse.
    """
    lines = ["## Raw Transcript Matches", ""]
    seen_texts: set[str] = set()
    for s in snippets:
        # Deduplicate by matched text — same sentence can appear at different distances
        if s["text"] in seen_texts:
            continue
        seen_texts.add(s["text"])

        session_info = f" ({s['session_id']})" if s.get("session_id") else ""
        lines.append(f"**Match{session_info}:**")
        for before in s.get("context_before", []):
            lines.append(f"> {before}")
        speaker_prefix = f"**{s['speaker']}**: " if s.get("speaker") else ""
        lines.append(f"> {speaker_prefix}_{s['text']}_")
        for after in s.get("context_after", []):
            lines.append(f"> {after}")
        lines.append("")
    return "\n".join(lines)


def assemble_markdown(
    nodes: list[dict],
    task: str,
    strategies_applied: list[str] | None = None,
    pinned_nodes: list[dict] | None = None,
    resolve_dates: bool = True,
    temporal_sort: bool = False,
) -> str:
    """Assemble nodes into a formatted markdown context block.

    When temporal_sort=True and nodes have occurred_at values, a "## Timeline"
    section is prepended listing all dated nodes in chronological order. This
    helps temporal QA where the answer is a date — facts surface prominently
    with their dates rather than buried inside type-grouped sections.
    Undated nodes fall through to the usual type-grouped sections below.
    """
    pinned_nodes = pinned_nodes or []
    if not nodes and not pinned_nodes:
        return "No relevant context found."

    lines = [
        "# Reconstructed Context",
        f"**Task:** {task}",
        f"**Nodes:** {len(nodes)}",
    ]

    if pinned_nodes:
        lines.append(f"**Pinned:** {len(pinned_nodes)}")

    if strategies_applied:
        lines.append(f"**Strategies:** {', '.join(strategies_applied)}")

    lines.append("")

    # Pinned nodes first — always-active context, exempt from relevance filtering
    if pinned_nodes:
        lines.append("## Always-Active Context")
        lines.append("")
        for node in pinned_nodes:
            lines.append(f"- {node['fact']}")
        lines.append("")

    # Timeline section: when temporal_sort is active, emit all dated nodes in
    # chronological order before the type-grouped sections. This surfaces
    # date-bearing facts prominently for temporal QA categories.
    undated_nodes = list(nodes)
    if temporal_sort:
        dated: list[tuple[datetime, dict]] = []
        undated_nodes = []
        for node in nodes:
            ts = node.get("occurred_at")
            if ts:
                try:
                    dt = datetime.fromisoformat(ts)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    dated.append((dt, node))
                except (ValueError, TypeError):
                    undated_nodes.append(node)
            else:
                undated_nodes.append(node)

        if dated:
            dated.sort(key=lambda x: x[0])
            lines.append("## Timeline")
            lines.append("")
            for dt, node in dated:
                date_label = dt.strftime("%B %-d, %Y")
                resolved_fact = (
                    _resolve_relative_dates(node["fact"], node.get("occurred_at"))
                    if resolve_dates else node["fact"]
                )
                lines.append(f"- **{date_label}** — {resolved_fact}")
            lines.append("")

    # Group remaining nodes by type
    by_type: dict[str, list[dict]] = {}
    for node in undated_nodes:
        by_type.setdefault(node["type"], []).append(node)

    # Order: decisions first, then constraints, implementations, resolved, lessons, preferences, others
    type_order = ["decision", "transition", "constraint", "implementation", "resolved", "lesson_learned", "preference", "question"]
    sorted_types = sorted(by_type.keys(), key=lambda t: type_order.index(t) if t in type_order else 99)

    for node_type in sorted_types:
        type_nodes = by_type[node_type]
        lines.append(f"## {node_type.title()}s")
        lines.append("")
        for node in type_nodes:
            confidence_str = f" (confidence: {node['confidence']:.1f})" if node.get("confidence") else ""
            source = ""
            if node.get("source_transcript") and node.get("source_message_index") is not None:
                source = f" [source: {node['source_transcript']}:{node['source_message_index']}]"
            elif node.get("source_message_index") is not None:
                source = f" [source: msg {node['source_message_index']}]"
            date_str = ""
            if node.get("occurred_at"):
                try:
                    dt = datetime.fromisoformat(node["occurred_at"])
                    date_str = f" [date: {dt.strftime('%B %-d, %Y')}]"
                except (ValueError, TypeError):
                    pass
            resolved_fact = (
                _resolve_relative_dates(node["fact"], node.get("occurred_at"))
                if resolve_dates else node["fact"]
            )
            lines.append(f"- {resolved_fact}{confidence_str}{date_str}{source}")
        lines.append("")

    return "\n".join(lines)
