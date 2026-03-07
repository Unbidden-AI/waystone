"""Retrieval engine: tag matching, BFS traversal, and context assembly."""

from collections import deque

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


def retrieve(store: GraphStore, task_description: str, hops: int = 3, top_k: int = 10) -> str:
    """Retrieve relevant context for a task description.

    1. Tokenize task into keywords
    2. Find entry nodes via tag matching
    3. BFS traversal up to `hops` depth
    4. Assemble markdown context block

    Returns:
        Formatted markdown string with relevant context.
    """
    keywords = extract_keywords(task_description)
    if not keywords:
        return "No relevant context found."

    entry_nodes = store.get_nodes_by_tags(keywords)
    if not entry_nodes:
        return "No relevant context found."

    # BFS from entry nodes
    visited_ids = set()
    collected_nodes = []
    queue = deque()

    for node in entry_nodes:
        if node["id"] not in visited_ids:
            visited_ids.add(node["id"])
            collected_nodes.append(node)
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
                    collected_nodes.append(neighbor)
                    queue.append((neighbor_id, depth + 1))

        for edge in store.get_edges_to(node_id):
            neighbor_id = edge["from_id"]
            if neighbor_id not in visited_ids:
                visited_ids.add(neighbor_id)
                neighbor = store.get_node(neighbor_id)
                if neighbor:
                    collected_nodes.append(neighbor)
                    queue.append((neighbor_id, depth + 1))

    # Sort by confidence descending, limit to top_k
    collected_nodes.sort(key=lambda n: n.get("confidence", 0), reverse=True)
    collected_nodes = collected_nodes[:top_k]

    return assemble_markdown(collected_nodes, task_description)


def extract_keywords(text: str) -> list[str]:
    """Extract keywords from text by tokenizing and filtering stop words."""
    words = text.lower().split()
    # Strip punctuation
    words = [w.strip(".,;:!?\"'()[]{}") for w in words]
    return [w for w in words if w and w not in STOP_WORDS and len(w) > 1]


def assemble_markdown(nodes: list[dict], task: str) -> str:
    """Assemble nodes into a formatted markdown context block."""
    if not nodes:
        return "No relevant context found."

    lines = [
        "# Reconstructed Context",
        f"**Task:** {task}",
        f"**Nodes:** {len(nodes)}",
        "",
    ]

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
