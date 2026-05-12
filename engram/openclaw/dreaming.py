"""Engram OpenClaw dreaming — reflection and reconciliation passes.

Replaces OpenClaw's native LLM-based dreaming with two deterministic passes:

  Reflection:      Boost confidence of hub nodes (high edge degree = load-bearing facts).
                   Optionally clusters nodes by tag co-occurrence (no LLM required).

  Reconciliation:  Prune nodes that are superseded but still marked active.
                   Detect and log genuine conflicts (two active nodes with contradictory facts).

Both passes are zero-cost (no LLM calls). The result is written to
~/.engram/openclaw_conflicts.log and returned as a DreamResult for callers.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engram.store import GraphStore

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ConflictRecord:
    """Two active nodes with potentially contradictory content."""
    node_a_id: str
    node_b_id: str
    node_a_fact: str
    node_b_fact: str
    shared_tags: list[str]
    detected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class DreamResult:
    """Summary of what the dream cycle did."""
    nodes_promoted: int = 0      # confidence boosted
    nodes_pruned: int = 0        # superseded → deactivated
    conflicts_detected: int = 0  # contradictory active nodes logged
    duration_ms: float = 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_dream(store: "GraphStore", cfg: dict) -> DreamResult:
    """Run a full dream cycle: reflection then reconciliation.

    Args:
        store: Open GraphStore instance.
        cfg:   OpenClaw plugin config (from load_openclaw_config()).

    Returns:
        DreamResult with counts of what changed.
    """
    import time
    t0 = time.monotonic()

    result = DreamResult()

    try:
        result.nodes_promoted = _reflect(store)
    except Exception as e:
        log.warning("engram: reflection error: %s", e)

    try:
        pruned, conflicts = _reconcile(store)
        result.nodes_pruned = pruned
        result.conflicts_detected = len(conflicts)
        if conflicts:
            _log_conflicts(conflicts, cfg)
    except Exception as e:
        log.warning("engram: reconciliation error: %s", e)

    result.duration_ms = (time.monotonic() - t0) * 1000
    log.info(
        "engram: dream complete — promoted=%d pruned=%d conflicts=%d (%.0fms)",
        result.nodes_promoted, result.nodes_pruned, result.conflicts_detected, result.duration_ms,
    )
    return result


# ---------------------------------------------------------------------------
# Reflection
# ---------------------------------------------------------------------------

def _reflect(store: "GraphStore") -> int:
    """Boost confidence of hub nodes based on edge degree.

    A hub node is one that participates in >= HUB_DEGREE edges. These are
    load-bearing facts that many other facts depend on — boosting their
    confidence makes them more likely to survive top_k filtering.

    Also clusters nodes by tag co-occurrence: if two nodes share >= 2 tags,
    they're in the same cluster; the higher-confidence one gets a small boost
    as the "anchor" of that cluster.

    Returns the number of nodes whose confidence was boosted.
    """
    HUB_DEGREE = 3
    HUB_BOOST = 0.02
    CLUSTER_BOOST = 0.01
    MIN_SHARED_TAGS = 2

    edges = store.get_all_edges()
    if not edges:
        return 0

    # --- Hub detection via edge degree ---
    degree: Counter = Counter()
    for edge in edges:
        from_id = edge.get("from_id") or edge.get("from")
        to_id = edge.get("to_id") or edge.get("to")
        if from_id:
            degree[from_id] += 1
        if to_id:
            degree[to_id] += 1

    promoted = 0
    hub_ids = {nid for nid, deg in degree.items() if deg >= HUB_DEGREE}
    for node_id in hub_ids:
        try:
            store.boost_confidence(node_id, delta=HUB_BOOST)
            promoted += 1
        except Exception:
            pass

    # --- Tag co-occurrence clustering ---
    active_nodes = store.get_active_nodes()
    if len(active_nodes) < 4:
        return promoted  # Not enough nodes to bother clustering

    # Build tag → [node_ids] index
    tag_index: dict[str, list[str]] = defaultdict(list)
    node_tags: dict[str, list[str]] = {}
    for node in active_nodes:
        tags = node.get("tags") or []
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except Exception:
                tags = []
        node_id = node["id"]
        node_tags[node_id] = [str(t).lower() for t in tags if t]
        for tag in node_tags[node_id]:
            tag_index[tag].append(node_id)

    # Find pairs that share >= MIN_SHARED_TAGS tags
    seen_pairs: set[frozenset] = set()
    for tag, node_ids in tag_index.items():
        if len(node_ids) < 2:
            continue
        for i in range(len(node_ids)):
            for j in range(i + 1, len(node_ids)):
                pair = frozenset([node_ids[i], node_ids[j]])
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                a, b = node_ids[i], node_ids[j]
                shared = set(node_tags.get(a, [])) & set(node_tags.get(b, []))
                if len(shared) >= MIN_SHARED_TAGS:
                    # Boost the higher-confidence node as cluster anchor
                    a_node = next((n for n in active_nodes if n["id"] == a), None)
                    b_node = next((n for n in active_nodes if n["id"] == b), None)
                    if a_node and b_node:
                        anchor = a if (a_node.get("confidence") or 0.5) >= (b_node.get("confidence") or 0.5) else b
                        try:
                            store.boost_confidence(anchor, delta=CLUSTER_BOOST)
                            promoted += 1
                        except Exception:
                            pass

    return promoted


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

def _reconcile(store: "GraphStore") -> tuple[int, list[ConflictRecord]]:
    """Prune superseded nodes and detect genuine conflicts.

    Step 1: Call store.prune_superseded() to deactivate nodes that are in a
            supersedes list but still marked active (safety net).

    Step 2: Scan active nodes for potential conflicts — pairs of active nodes
            that share tags AND have contradictory-looking facts (heuristic:
            different numeric values or explicit negation words).

    Returns (pruned_count, conflicts).
    """
    # Step 1: prune stragglers
    pruned = store.prune_superseded()

    # Step 2: conflict detection
    conflicts: list[ConflictRecord] = []
    active_nodes = store.get_active_nodes()

    if len(active_nodes) < 2:
        return pruned, conflicts

    # Build tag → node lookup
    tag_to_nodes: dict[str, list[dict]] = defaultdict(list)
    node_tags_map: dict[str, set[str]] = {}

    for node in active_nodes:
        tags = node.get("tags") or []
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except Exception:
                tags = []
        normalized = {str(t).lower() for t in tags if t}
        node_tags_map[node["id"]] = normalized
        for tag in normalized:
            tag_to_nodes[tag].append(node)

    # Check candidate pairs (nodes sharing >= 2 tags) for conflict signals
    seen_pairs: set[frozenset] = set()
    for tag, nodes_with_tag in tag_to_nodes.items():
        if len(nodes_with_tag) < 2:
            continue
        for i in range(len(nodes_with_tag)):
            for j in range(i + 1, len(nodes_with_tag)):
                a = nodes_with_tag[i]
                b = nodes_with_tag[j]
                pair = frozenset([a["id"], b["id"]])
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                shared_tags = node_tags_map.get(a["id"], set()) & node_tags_map.get(b["id"], set())
                if len(shared_tags) >= 2 and _looks_conflicting(a["fact"], b["fact"]):
                    conflicts.append(ConflictRecord(
                        node_a_id=a["id"],
                        node_b_id=b["id"],
                        node_a_fact=a["fact"],
                        node_b_fact=b["fact"],
                        shared_tags=sorted(shared_tags),
                    ))

    return pruned, conflicts


_NEGATION_WORDS = frozenset({
    "not", "never", "no", "avoid", "rejected", "deprecated",
    "removed", "disabled", "dropped", "instead", "replaced",
})

_CONFLICT_NUMERIC_RE = None  # lazy-compiled


def _looks_conflicting(fact_a: str, fact_b: str) -> bool:
    """Heuristic: do these two facts look like they might contradict each other?

    We flag a pair as potentially conflicting if:
    - One contains a negation word the other doesn't, or
    - Both contain numeric values but with different numbers on the same unit

    This is intentionally conservative (low false-positive rate).
    """
    import re

    a_lower = fact_a.lower()
    b_lower = fact_b.lower()

    # Negation asymmetry: one uses a negation word, the other doesn't
    a_has_neg = bool(_NEGATION_WORDS & set(a_lower.split()))
    b_has_neg = bool(_NEGATION_WORDS & set(b_lower.split()))
    if a_has_neg != b_has_neg:
        return True

    # Numeric conflict: extract all numbers, flag if they differ significantly
    global _CONFLICT_NUMERIC_RE
    if _CONFLICT_NUMERIC_RE is None:
        _CONFLICT_NUMERIC_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(ms|s|mb|gb|tb|%|rpm|req|k|m)?\b", re.IGNORECASE)

    nums_a = {(m.group(2) or "").lower(): float(m.group(1)) for m in _CONFLICT_NUMERIC_RE.finditer(a_lower)}
    nums_b = {(m.group(2) or "").lower(): float(m.group(1)) for m in _CONFLICT_NUMERIC_RE.finditer(b_lower)}

    for unit in set(nums_a) & set(nums_b):
        if nums_a[unit] != nums_b[unit]:
            return True

    return False


# ---------------------------------------------------------------------------
# Conflict logging
# ---------------------------------------------------------------------------

def _log_conflicts(conflicts: list[ConflictRecord], cfg: dict) -> None:
    """Append detected conflicts to ~/.engram/openclaw_conflicts.log."""
    log_path = Path.home() / ".engram" / "openclaw_conflicts.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n--- Dream cycle {datetime.now(timezone.utc).isoformat()} ---\n")
            for c in conflicts:
                f.write(
                    f"CONFLICT  shared_tags={c.shared_tags}\n"
                    f"  node_a ({c.node_a_id}): {c.node_a_fact}\n"
                    f"  node_b ({c.node_b_id}): {c.node_b_fact}\n"
                )
        log.info("engram: logged %d conflicts to %s", len(conflicts), log_path)
    except Exception as e:
        log.warning("engram: could not write conflict log: %s", e)


def format_dream_summary(result: DreamResult) -> str:
    """Format a DreamResult for display to the user."""
    parts = [
        f"Dream cycle complete ({result.duration_ms:.0f}ms):",
        f"  Promoted {result.nodes_promoted} hub/cluster nodes",
        f"  Pruned {result.nodes_pruned} superseded nodes",
    ]
    if result.conflicts_detected:
        log_path = Path.home() / ".engram" / "openclaw_conflicts.log"
        parts.append(f"  Detected {result.conflicts_detected} potential conflicts → {log_path}")
    else:
        parts.append("  No conflicts detected")
    return "\n".join(parts)
