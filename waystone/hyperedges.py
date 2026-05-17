"""EHRAG: offline hyperedge index builder for semantic cluster-based retrieval injection.

Clusters active nodes by pairwise cosine similarity (greedy agglomerative) and writes
the result to the ``hyperedge_members`` SQLite table.  Retriever reads this table at
query time to inject sibling nodes from the same cluster that BFS missed.

Usage::

    from waystone.hyperedges import rebuild_hyperedges
    n = rebuild_hyperedges(store)  # returns number of hyperedges written

Complexity: O(n²) in node count — fast for LOCOMO-scale DBs (n ≤ 600) but may need
batching or ANN approximation for corpora above ~10 K active nodes.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def rebuild_hyperedges(
    store,
    threshold: float = 0.80,
    min_size: int = 2,
    max_size: int = 10,
) -> int:
    """Build the hyperedge index for *store* and write it to ``hyperedge_members``.

    Args:
        store: A :class:`~waystone.store.GraphStore` instance (open, writable).
        threshold: Cosine similarity floor for two nodes to share a hyperedge.
        min_size: Minimum cluster size to emit as a hyperedge (singleton clusters discarded).
        max_size: Maximum cluster size (early-stops per-node scan once reached).

    Returns:
        Number of hyperedges written (0 if prerequisites missing).
    """
    try:
        import numpy as np
    except ImportError:
        log.warning("numpy not available — EHRAG hyperedge build skipped")
        return 0

    if not store._vec_available:
        log.warning("sqlite-vec not available — EHRAG hyperedge build skipped")
        return 0

    # Fetch all active nodes that have embeddings
    rows = store.conn.execute(
        "SELECT n.id, e.embedding FROM nodes n "
        "JOIN node_embeddings e ON n.id = e.node_id WHERE n.is_active = 1"
    ).fetchall()

    if len(rows) < min_size:
        log.debug("EHRAG: only %d active nodes with embeddings — too few to cluster", len(rows))
        return 0

    node_ids = [r[0] for r in rows]
    n = len(node_ids)

    # Build normalised embedding matrix (n × dim)
    embs = np.array([np.frombuffer(bytes(r[1]), dtype=np.float32) for r in rows])
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    embs_norm = embs / norms

    # Greedy agglomerative clustering: for each unassigned node (in fetch order),
    # batch-compute cosine sim against all subsequent unassigned nodes and collect
    # neighbours above threshold (up to max_size - 1 additional members).
    assigned = [False] * n
    hyperedges: list[list[int]] = []

    for i in range(n):
        if assigned[i]:
            continue
        # Cosine similarities from node i to nodes i..n-1 (upper triangle only)
        tail = embs_norm[i:]           # (n-i) × dim
        sims = (embs_norm[i] @ tail.T).tolist()  # length = n-i

        cluster_indices = [i]
        for offset in range(1, len(sims)):
            if len(cluster_indices) >= max_size:
                break
            j = i + offset
            if not assigned[j] and sims[offset] >= threshold:
                cluster_indices.append(j)

        if len(cluster_indices) >= min_size:
            hyperedges.append(cluster_indices)
            for idx in cluster_indices:
                assigned[idx] = True

    # Write to DB (full replace)
    store.conn.execute("DELETE FROM hyperedge_members")
    he_rows: list[tuple[str, str]] = []
    for idx, cluster_indices in enumerate(hyperedges):
        he_id = f"he_{idx:06d}"
        for ci in cluster_indices:
            he_rows.append((he_id, node_ids[ci]))

    if he_rows:
        store.conn.executemany(
            "INSERT INTO hyperedge_members (hyperedge_id, node_id) VALUES (?, ?)",
            he_rows,
        )
    store.conn.commit()

    log.info(
        "EHRAG index: %d hyperedges (%d member slots) written to %s",
        len(hyperedges),
        len(he_rows),
        store.db_path,
    )
    return len(hyperedges)
