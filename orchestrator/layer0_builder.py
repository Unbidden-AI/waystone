"""Layer 0 standing world state builder — pre-fetched at conversation start."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from waystone.store import GraphStore

from .llm_adapter import estimate_tokens

log = logging.getLogger(__name__)


def build_layer0(
    store: GraphStore,
    token_budget: int = 1000,
    confidence_threshold: float = 0.7,
    recency_days: int = 30,
    question_limit: int = 5,
    constraint_limit: int = 10,
    decision_limit: int = 8,
) -> str:
    """Build standing world state block from the graph.

    Selects:
    - Open questions (no supersedes edge pointing at them)
    - Active constraints with high confidence
    - Recent/high-confidence decisions (last N days, confidence >= threshold)

    Returns markdown string, possibly empty if graph is empty.

    Parameters
    ----------
    store : GraphStore
        The graph store to fetch nodes from.
    token_budget : int
        Soft limit on total tokens. If adding another item would exceed,
        stop adding items. Default: 1000.
    confidence_threshold : float
        Minimum confidence to include decisions/constraints. Default: 0.7.
    recency_days : int
        Include decisions from the last N days. 0 = all. Default: 30.
    question_limit : int
        Maximum open questions to include. Default: 5.
    constraint_limit : int
        Maximum active constraints to include. Default: 10.
    decision_limit : int
        Maximum recent/high-confidence decisions to include. Default: 8.

    Returns
    -------
    str
        Markdown-formatted Layer 0 block (grouped by type). Empty string if
        the graph is empty or has no matching nodes.
    """
    lines: list[str] = []
    total_tokens = 0

    # Fetch candidate nodes from store
    questions = _fetch_open_questions(store, limit=question_limit)
    constraints = _fetch_active_constraints(store, confidence_threshold, limit=constraint_limit)
    decisions = _fetch_recent_decisions(store, confidence_threshold, recency_days, limit=decision_limit)

    # Questions section
    if questions:
        lines.append("### Open Questions\n")
        for node in questions:
            line = f"- {node['fact']}"
            if _would_exceed_budget(total_tokens, line, token_budget):
                break
            lines.append(line)
            total_tokens += estimate_tokens(line)
        lines.append("")

    # Constraints section
    if constraints:
        lines.append("### Active Constraints\n")
        for node in constraints:
            line = f"- {node['fact']} (confidence: {node['confidence']:.2f})"
            if _would_exceed_budget(total_tokens, line, token_budget):
                break
            lines.append(line)
            total_tokens += estimate_tokens(line)
        lines.append("")

    # Decisions section
    if decisions:
        lines.append("### Recent Decisions\n")
        for node in decisions:
            ts = node.get('occurred_at', 'unknown')
            line = f"- [{ts}] {node['fact']}"
            if _would_exceed_budget(total_tokens, line, token_budget):
                break
            lines.append(line)
            total_tokens += estimate_tokens(line)
        lines.append("")

    result = "\n".join(lines).strip()
    if result:
        log.debug(
            "Layer0Builder: %d questions, %d constraints, %d decisions (~%d tokens)",
            len(questions),
            len(constraints),
            len(decisions),
            total_tokens,
        )

    return result


def _fetch_open_questions(store: GraphStore, limit: int) -> list[dict]:
    """Return open (non-superseded) question nodes.

    Parameters
    ----------
    store : GraphStore
        The graph store.
    limit : int
        Maximum number of questions to fetch.

    Returns
    -------
    list[dict]
        List of question node dicts with 'id', 'fact', 'confidence', 'occurred_at'.
    """
    try:
        query = (
            "SELECT id, fact, confidence, occurred_at FROM nodes "
            "WHERE type='question' AND is_active=1 "
            "ORDER BY occurred_at DESC LIMIT ?"
        )
        rows = store._conn.execute(query, (limit,)).fetchall()
        return [dict(row) for row in rows] if rows else []
    except Exception as e:
        log.warning("Failed to fetch open questions: %s", e)
        return []


def _fetch_active_constraints(store: GraphStore, confidence_threshold: float, limit: int) -> list[dict]:
    """Return active constraint nodes with confidence >= threshold.

    Parameters
    ----------
    store : GraphStore
        The graph store.
    confidence_threshold : float
        Minimum confidence to include.
    limit : int
        Maximum number of constraints to fetch.

    Returns
    -------
    list[dict]
        List of constraint node dicts.
    """
    try:
        query = (
            "SELECT id, fact, confidence, occurred_at FROM nodes "
            "WHERE type='constraint' AND is_active=1 AND confidence >= ? "
            "ORDER BY confidence DESC, occurred_at DESC LIMIT ?"
        )
        rows = store._conn.execute(query, (confidence_threshold, limit)).fetchall()
        return [dict(row) for row in rows] if rows else []
    except Exception as e:
        log.warning("Failed to fetch active constraints: %s", e)
        return []


def _fetch_recent_decisions(
    store: GraphStore,
    confidence_threshold: float,
    recency_days: int,
    limit: int,
) -> list[dict]:
    """Return recent (or high-confidence) decision nodes.

    Parameters
    ----------
    store : GraphStore
        The graph store.
    confidence_threshold : float
        Minimum confidence to include.
    recency_days : int
        Include decisions from the last N days. 0 = all.
    limit : int
        Maximum number of decisions to fetch.

    Returns
    -------
    list[dict]
        List of decision node dicts.
    """
    try:
        cutoff = None
        if recency_days > 0:
            cutoff = (datetime.utcnow() - timedelta(days=recency_days)).isoformat()

        if cutoff:
            query = (
                "SELECT id, fact, confidence, occurred_at FROM nodes "
                "WHERE type='decision' AND is_active=1 AND confidence >= ? AND occurred_at >= ? "
                "ORDER BY confidence DESC, occurred_at DESC LIMIT ?"
            )
            rows = store._conn.execute(query, (confidence_threshold, cutoff, limit)).fetchall()
        else:
            query = (
                "SELECT id, fact, confidence, occurred_at FROM nodes "
                "WHERE type='decision' AND is_active=1 AND confidence >= ? "
                "ORDER BY confidence DESC, occurred_at DESC LIMIT ?"
            )
            rows = store._conn.execute(query, (confidence_threshold, limit)).fetchall()

        return [dict(row) for row in rows] if rows else []
    except Exception as e:
        log.warning("Failed to fetch recent decisions: %s", e)
        return []


def _would_exceed_budget(current_tokens: int, next_line: str, budget: int) -> bool:
    """Check if adding next_line would exceed token budget.

    Parameters
    ----------
    current_tokens : int
        Current token count.
    next_line : str
        The line to potentially add.
    budget : int
        The token budget limit.

    Returns
    -------
    bool
        True if adding the line would exceed budget.
    """
    return estimate_tokens(next_line) + current_tokens >= budget
