"""Retrieval engine: tag matching, BFS traversal, strategy pipeline, and context assembly."""

import logging
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .store import GraphStore

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
}


@dataclass
class RetrievalResult:
    """Full retrieval result with metadata for benchmarking."""
    markdown: str
    nodes_before_strategies: int
    nodes_after_strategies: int
    strategies_applied: list[str] = field(default_factory=list)
    tokens_estimated: int = 0


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
    log.debug("Retrieval: task=%r keywords=%s hops=%d top_k=%d pinned=%d", task_description[:80], keywords, hops, top_k, len(pinned_nodes))
    if not keywords and not pinned_nodes:
        log.debug("No keywords extracted and no pinned nodes — returning empty result")
        return RetrievalResult(markdown="No relevant context found.", nodes_before_strategies=0, nodes_after_strategies=0)
    if not keywords:
        markdown = assemble_markdown([], task_description, [], pinned_nodes=pinned_nodes,
                                     resolve_dates=strats.get("resolve_dates", True))
        return RetrievalResult(markdown=markdown, nodes_before_strategies=0, nodes_after_strategies=0)

    entry_nodes = [n for n in store.get_nodes_by_tags(keywords) if n["id"] not in pinned_ids]
    log.debug("Tag search: %d entry nodes matched (excluding %d pinned)", len(entry_nodes), len(pinned_ids))

    # Augment with fact-text matches — catches nodes with sparse tags where keywords
    # appear in the fact itself but weren't extracted as tags by the LLM.
    fact_nodes = store.get_nodes_by_fact_text(keywords)
    if fact_nodes:
        existing_ids = {n["id"] for n in entry_nodes}
        added = 0
        for node in fact_nodes:
            if node["id"] not in existing_ids:
                entry_nodes.append(node)
                existing_ids.add(node["id"])
                added += 1
        log.debug("Fact-text search added %d new entry nodes (total: %d)", added, len(entry_nodes))

    # Semantic augmentation: always union semantic results when available, not just as fallback.
    # This catches nodes whose vocabulary differs from extraction-time tags.
    # Can be disabled per-strategy via semantic flag.
    from engram import embedder
    if strats["semantic"] and embedder.is_available() and store._vec_available:
        query_blob = embedder.embed_text(task_description)
        sem_ids = store.search_by_embedding(query_blob, top_k=top_k)
        if sem_ids:
            existing_ids = {n["id"] for n in entry_nodes}
            sem_nodes = store.get_nodes_by_ids(sem_ids)
            added = sum(1 for n in sem_nodes if n["id"] not in existing_ids)
            entry_nodes.extend(n for n in sem_nodes if n["id"] not in existing_ids)
            log.debug("Semantic augmentation: %d new entry nodes (total: %d)", added, len(entry_nodes))

    if not entry_nodes:
        return RetrievalResult(markdown="No relevant context found.", nodes_before_strategies=0, nodes_after_strategies=0)

    # Strategy: Relevance scoring — rank entry nodes by tag overlap count
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
    if strats["relevance_scoring"]:
        high_overlap = [n for n in entry_nodes if n.get("_relevance", 0) >= 2]
    else:
        high_overlap = [
            n for n in entry_nodes
            if _count_keyword_tag_hits(n.get("tags", []), keyword_set) >= 2
        ]
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

    # Sort by confidence (possibly decayed) descending, then BFS depth ascending
    # as a tiebreaker so nodes closer to entry points rank higher when scores are equal.
    collected_nodes.sort(
        key=lambda n: (n.get("_score", n.get("confidence", 0)), -n.get("_bfs_depth", 0)),
        reverse=True,
    )

    # Seed preservation: ensure directly query-matched seed nodes (depth=0) are not
    # completely displaced by recency-biased BFS-expanded nodes. Reserve up to 40% of
    # top_k slots for seeds, filling remaining slots with the highest-scoring non-seeds.
    # This prevents stable early-conversation facts (allergies, hobbies) from being
    # buried by recent-session nodes when recency decay is active.
    seed_ids = {n["id"] for n in entry_nodes}
    seeds_in_collected = [n for n in collected_nodes if n["id"] in seed_ids]
    non_seeds_in_collected = [n for n in collected_nodes if n["id"] not in seed_ids]
    seed_reserve = min(len(seeds_in_collected), top_k * 2 // 5)  # up to 40% reserved for seeds
    collected_nodes = (
        seeds_in_collected[:seed_reserve]
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
    )
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

    # Seed layer 0
    current_layer_ids: list[str] = []
    for node in entry_nodes:
        if node["id"] not in visited_ids:
            visited_ids.add(node["id"])
            node = {**node, "_bfs_depth": 0}
            collected.append(node)
            current_layer_ids.append(node["id"])

    for depth in range(hops):
        if not current_layer_ids:
            break

        # Batch-fetch all edges touching this layer (1 query for the whole layer)
        all_edges = store.get_edges_for_nodes(current_layer_ids)

        # Collect unvisited neighbor IDs (deduplicated, preserving first-seen order)
        seen_this_pass: set[str] = set()
        neighbor_ids: list[str] = []
        for edge in all_edges:
            for nid in (edge["to_id"], edge["from_id"]):
                if nid not in visited_ids and nid not in seen_this_pass:
                    seen_this_pass.add(nid)
                    neighbor_ids.append(nid)

        if not neighbor_ids:
            break

        # Batch-fetch all new neighbors in one query
        next_layer_ids: list[str] = []
        for node in store.get_nodes_by_ids(neighbor_ids):
            nid = node["id"]
            if nid not in visited_ids:
                visited_ids.add(nid)
                node = {**node, "_bfs_depth": depth + 1}
                collected.append(node)
                next_layer_ids.append(nid)

        current_layer_ids = next_layer_ids

    return collected


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


def assemble_markdown(
    nodes: list[dict],
    task: str,
    strategies_applied: list[str] | None = None,
    pinned_nodes: list[dict] | None = None,
    resolve_dates: bool = True,
) -> str:
    """Assemble nodes into a formatted markdown context block."""
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

    # Group by type
    by_type: dict[str, list[dict]] = {}
    for node in nodes:
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
                    from datetime import datetime, timezone
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
