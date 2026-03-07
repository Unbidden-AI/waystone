"""Retrieval engine: tag matching, BFS traversal, strategy pipeline, and context assembly."""

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .store import GraphStore

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
    if not keywords:
        return RetrievalResult(markdown="No relevant context found.", nodes_before_strategies=0, nodes_after_strategies=0)

    entry_nodes = store.get_nodes_by_tags(keywords)
    if not entry_nodes:
        return RetrievalResult(markdown="No relevant context found.", nodes_before_strategies=0, nodes_after_strategies=0)

    # Strategy: Relevance scoring — rank entry nodes by tag overlap count
    if strats["relevance_scoring"]:
        entry_nodes = score_by_relevance(entry_nodes, keywords)

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

    # Sort by confidence (possibly decayed) descending, limit to top_k
    collected_nodes.sort(key=lambda n: n.get("_score", n.get("confidence", 0)), reverse=True)
    collected_nodes = collected_nodes[:top_k]

    if strats["token_budget"] > 0:
        collected_nodes = apply_token_budget(collected_nodes, strats["token_budget"])
        applied.append(f"token_budget({strats['token_budget']})")

    nodes_after = len(collected_nodes)
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

def score_by_relevance(nodes: list[dict], keywords: list[str]) -> list[dict]:
    """Rank nodes by number of matching tags (not just binary match).

    Nodes with more keyword overlap are placed first, which makes them
    BFS seed priorities.
    """
    keyword_set = set(keywords)
    for node in nodes:
        tag_set = set(node.get("tags", []))
        node["_relevance"] = len(tag_set & keyword_set)
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
    """BFS from entry nodes up to `hops` depth, collecting all reachable nodes."""
    visited_ids = set()
    collected = []
    queue = deque()

    for node in entry_nodes:
        if node["id"] not in visited_ids:
            visited_ids.add(node["id"])
            collected.append(node)
            queue.append((node["id"], 0))

    while queue:
        node_id, depth = queue.popleft()
        if depth >= hops:
            continue

        for edge in store.get_edges_from(node_id):
            neighbor_id = edge["to_id"]
            if neighbor_id not in visited_ids:
                visited_ids.add(neighbor_id)
                neighbor = store.get_node(neighbor_id)
                if neighbor:
                    collected.append(neighbor)
                    queue.append((neighbor_id, depth + 1))

        for edge in store.get_edges_to(node_id):
            neighbor_id = edge["from_id"]
            if neighbor_id not in visited_ids:
                visited_ids.add(neighbor_id)
                neighbor = store.get_node(neighbor_id)
                if neighbor:
                    collected.append(neighbor)
                    queue.append((neighbor_id, depth + 1))

    return collected


def extract_keywords(text: str) -> list[str]:
    """Extract keywords from text by tokenizing and filtering stop words."""
    words = text.lower().split()
    # Strip punctuation
    words = [w.strip(".,;:!?\"'()[]{}") for w in words]
    return [w for w in words if w and w not in STOP_WORDS and len(w) > 1]


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 characters per token for English text."""
    return max(1, len(text) // 4)


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

    # Order: decisions first, then constraints, implementations, others
    type_order = ["decision", "constraint", "implementation", "resolved", "preference", "question"]
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
