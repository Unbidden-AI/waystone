"""Retrieval engine: tag matching, BFS traversal, strategy pipeline, and context assembly."""

import logging
import math
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
            - superseded_pruning (bool): Drop superseded nodes
            - confidence_threshold (float): Min confidence to include (0.0 = off)
            - recency_decay (bool): Apply time-based decay to scores
            - recency_half_life_days (int): Half-life for decay
            - token_budget (int): Max estimated tokens in output (0 = off)
            - relevance_scoring (bool): Rank entry nodes by tag overlap count
    """
    strats = {**DEFAULT_STRATEGIES, **(strategies or {})}

    keywords = extract_keywords(task_description)
    log.debug("Retrieval: task=%r keywords=%s hops=%d top_k=%d", task_description[:80], keywords, hops, top_k)
    if not keywords:
        log.debug("No keywords extracted — returning empty result")
        return RetrievalResult(markdown="No relevant context found.", nodes_before_strategies=0, nodes_after_strategies=0)

    entry_nodes = store.get_nodes_by_tags(keywords)
    log.debug("Tag search: %d entry nodes matched", len(entry_nodes))

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
    from engram import embedder
    if embedder.is_available() and store._vec_available:
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
    collected_nodes = collected_nodes[:top_k]

    if strats["token_budget"] > 0:
        collected_nodes = apply_token_budget(collected_nodes, strats["token_budget"])
        applied.append(f"token_budget({strats['token_budget']})")

    nodes_after = len(collected_nodes)
    log.debug("Retrieval: %d nodes before strategies, %d after (strategies: %s)", nodes_before, nodes_after, applied)
    markdown = assemble_markdown(collected_nodes, task_description, applied)
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

def _count_keyword_tag_hits(tags: list[str], keyword_set: set) -> int:
    """Count distinct keywords that appear as a substring in any tag.

    Mirrors the SQL LIKE %keyword% behavior so multi-word tags like
    "event format" are counted as a match for keyword "format".
    """
    matched = set()
    for kw in keyword_set:
        if any(kw in tag.lower() for tag in tags):
            matched.add(kw)
    return len(matched)


def score_by_relevance(nodes: list[dict], keywords: list[str]) -> list[dict]:
    """Rank nodes by number of matching tags (not just binary match).

    Nodes with more keyword overlap are placed first, which makes them
    BFS seed priorities. Applies type-based boost to prioritize decisions,
    constraints, and trade-offs.
    """
    TYPE_BOOST = {
        "decision": 1.5,
        "constraint": 1.4,
        "trade_off": 1.3,
        "lesson_learned": 1.2,
    }

    keyword_set = set(keywords)
    for node in nodes:
        relevance = _count_keyword_tag_hits(node.get("tags", []), keyword_set)
        node_type = node.get("type", "").lower()
        boost = TYPE_BOOST.get(node_type, 1.0)
        node["_relevance"] = relevance * boost
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
        created = node.get("created_at", "")
        try:
            created_dt = datetime.fromisoformat(created)
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


def assemble_markdown(nodes: list[dict], task: str, strategies_applied: list[str] | None = None) -> str:
    """Assemble nodes into a formatted markdown context block."""
    if not nodes:
        return "No relevant context found."

    lines = [
        "# Reconstructed Context",
        f"**Task:** {task}",
        f"**Nodes:** {len(nodes)}",
    ]

    if strategies_applied:
        lines.append(f"**Strategies:** {', '.join(strategies_applied)}")

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
            lines.append(f"- {node['fact']}{confidence_str}{source}")
        lines.append("")

    return "\n".join(lines)
