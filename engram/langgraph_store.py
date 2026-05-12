"""LangGraph BaseStore adapter for Engram.

Exposes Engram's DAG-based knowledge graph as a LangGraph-compatible
long-term memory store. Agents call ``store.search()`` to retrieve typed
subgraph context and ``store.put()`` to persist structured facts — all
backed by Engram's local SQLite graph.

Usage::

    from engram.store import GraphStore
    from engram.langgraph_store import EngramStore
    from langgraph.graph import StateGraph

    graph_store = GraphStore("/path/to/context.db")
    memory = EngramStore(graph_store)

    builder = StateGraph(...)
    graph = builder.compile(store=memory)

Namespace convention:
    LangGraph namespaces map to Engram tags.
    ``("user_alice", "decisions")`` → tags ``["user_alice", "decisions"]``
    ``("project_api", "constraints")`` → tags ``["project_api", "constraints"]``

Storage encoding:
    Each ``put()`` call stores one node in the Engram graph with:
    - ``source_transcript`` = ``"lg:{ns1}|{ns2}|...:{key}"``  (for exact lookup)
    - ``tags``              = ``list(namespace)``
    - ``fact``              = ``value["content"]`` or ``json.dumps(value)``
    - ``type``              = ``value.get("type", namespace[-1] or "preference")``

Delete semantics:
    ``put(ns, key, None)`` (a PutOp with ``value=None``) marks the node
    inactive (``is_active=0``) rather than hard-deleting it, preserving
    historical context.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Iterable

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy LangGraph imports — optional dependency
# ---------------------------------------------------------------------------

def _langgraph_available() -> bool:
    try:
        import langgraph.store.base  # noqa: F401
        return True
    except ImportError:
        return False


def _get_base_store():
    from langgraph.store.base import BaseStore
    return BaseStore


def _get_op_types():
    from langgraph.store.base import GetOp, PutOp, SearchOp, ListNamespacesOp
    return GetOp, PutOp, SearchOp, ListNamespacesOp


def _make_item(namespace, key, value, created_at, updated_at):
    from langgraph.store.base import Item
    return Item(
        namespace=namespace,
        key=key,
        value=value,
        created_at=created_at,
        updated_at=updated_at,
    )


def _make_search_item(namespace, key, value, created_at, updated_at, score=None):
    from langgraph.store.base import SearchItem
    return SearchItem(
        namespace=namespace,
        key=key,
        value=value,
        created_at=created_at,
        updated_at=updated_at,
        score=score,
    )


# ---------------------------------------------------------------------------
# Source-transcript key encoding
# ---------------------------------------------------------------------------

_PREFIX = "lg:"
_NS_SEP = "|"
_KEY_SEP = ":"


def _encode_source(namespace: tuple[str, ...], key: str) -> str:
    """Encode a (namespace, key) pair as a source_transcript value."""
    ns_str = _NS_SEP.join(namespace)
    return f"{_PREFIX}{ns_str}{_KEY_SEP}{key}"


def _decode_source(source: str) -> tuple[tuple[str, ...], str] | None:
    """Decode a source_transcript value back to (namespace, key).

    Returns None if the source was not encoded by EngramStore.
    """
    if not source.startswith(_PREFIX):
        return None
    inner = source[len(_PREFIX):]
    # Split on the LAST ":" to separate namespace from key
    try:
        ns_str, key = inner.rsplit(_KEY_SEP, 1)
    except ValueError:
        return None
    namespace = tuple(ns_str.split(_NS_SEP)) if ns_str else ()
    return namespace, key


# ---------------------------------------------------------------------------
# EngramStore
# ---------------------------------------------------------------------------

class EngramStore(_get_base_store()):
    """LangGraph BaseStore implementation backed by an Engram GraphStore.

    Args:
        graph_store: An open ``engram.store.GraphStore`` instance.
        hops: BFS traversal depth for ``search()`` queries (default: 2).
        top_k: Maximum nodes returned per ``search()`` query (default: 10).
        strategies: Engram strategy overrides passed to ``retrieve()``.

    Thread safety:
        - Concurrent reads are safe (SQLite WAL mode enabled by default)
        - Concurrent writes serialize at the SQLite level
        - For high-concurrency write workloads, consider using the REST API server
          mode instead of direct SQLite access
    """

    supports_ttl = False

    def __init__(
        self,
        graph_store: Any,  # engram.store.GraphStore — typed loosely to avoid circular import
        hops: int = 2,
        top_k: int = 10,
        strategies: dict | None = None,
    ) -> None:
        self._store = graph_store
        self._hops = hops
        self._top_k = top_k
        self._strategies = strategies or {}

    # ------------------------------------------------------------------
    # Required abstract methods
    # ------------------------------------------------------------------

    def batch(self, ops: Iterable[Any]) -> list[Any]:
        """Execute a batch of LangGraph store operations synchronously."""
        GetOp, PutOp, SearchOp, ListNamespacesOp = _get_op_types()
        results = []
        for op in ops:
            if isinstance(op, GetOp):
                results.append(self._get(op.namespace, op.key))
            elif isinstance(op, PutOp):
                self._put(op.namespace, op.key, op.value)
                results.append(None)
            elif isinstance(op, SearchOp):
                results.append(self._search(
                    op.namespace_prefix,
                    query=op.query,
                    filter=op.filter,
                    limit=op.limit,
                    offset=op.offset,
                ))
            elif isinstance(op, ListNamespacesOp):
                results.append(self._list_namespaces(
                    match_conditions=op.match_conditions,
                    max_depth=op.max_depth,
                    limit=op.limit,
                    offset=op.offset,
                ))
            else:
                raise NotImplementedError(
                    f"Unsupported op type: {type(op).__name__}. Update EngramStore.batch() to handle it."
                )
        return results

    async def abatch(self, ops: Iterable[Any]) -> list[Any]:
        """Execute a batch of LangGraph store operations asynchronously.

        Opens a fresh GraphStore connection in the worker thread so SQLite's
        single-thread restriction is respected across async boundaries.
        """
        ops_list = list(ops)  # consume iterator before crossing thread boundary
        hops, top_k, strategies = self._hops, self._top_k, self._strategies
        db_path = self._store.db_path

        def _run_in_thread():
            from engram.store import GraphStore
            store = GraphStore(db_path)
            adapter = EngramStore(store, hops, top_k, strategies)
            try:
                return adapter.batch(ops_list)
            finally:
                store.close()

        return await asyncio.to_thread(_run_in_thread)

    # ------------------------------------------------------------------
    # Operation implementations
    # ------------------------------------------------------------------

    def _get(self, namespace: tuple[str, ...], key: str) -> Any | None:
        source = _encode_source(namespace, key)
        try:
            row = self._store.conn.execute(
                "SELECT * FROM nodes WHERE source_transcript = ? AND is_active = 1 LIMIT 1",
                (source,),
            ).fetchone()
        except Exception as e:
            log.warning("EngramStore._get failed: %s", e)
            return None

        if row is None:
            return None

        node = self._store._row_to_node(row)
        return self._node_to_item(node, namespace, key)

    def _put(
        self,
        namespace: tuple[str, ...],
        key: str,
        value: dict | None,
    ) -> None:
        source = _encode_source(namespace, key)

        # Deactivate any existing node for this (namespace, key) pair
        try:
            self._store.conn.execute(
                "UPDATE nodes SET is_active = 0, valid_to = ? "
                "WHERE source_transcript = ? AND is_active = 1",
                (datetime.now(timezone.utc).isoformat(), source),
            )
            self._store.conn.commit()
        except Exception as e:
            log.warning("EngramStore._put deactivation failed: %s", e)

        if value is None:
            # value=None signals delete — deactivation above is sufficient
            return

        # Derive node fields from the value dict
        fact = value.get("content") or value.get("fact")
        if not fact:
            fact = json.dumps(value)

        node_type = value.get("type") or (namespace[-1] if namespace else "preference")
        # Map LangGraph-style type names to valid Engram types where possible
        _type_map = {
            "memory": "preference",
            "fact": "implementation",
            "note": "implementation",
        }
        node_type = _type_map.get(node_type, node_type)

        tags = list(namespace)
        if "tags" in value and isinstance(value["tags"], list):
            tags = list(dict.fromkeys(tags + value["tags"]))  # dedupe, preserve order

        import uuid
        now = datetime.now(timezone.utc).isoformat()
        node = {
            "id": f"n_{uuid.uuid4().hex[:8]}",
            "fact": fact,
            "type": node_type,
            "confidence": float(value.get("confidence", 1.0)),
            "tags": tags,
            "source_transcript": source,
            "created_at": now,
            "occurred_at": value.get("occurred_at", now),
            "is_active": 1,
        }

        try:
            self._store.add_node(node)
        except Exception as e:
            log.error("EngramStore._put add_node failed: %s", e)
            raise

    def _search(
        self,
        namespace_prefix: tuple[str, ...],
        query: str | None,
        filter: dict | None,
        limit: int,
        offset: int,
    ) -> list[Any]:
        from engram.retriever import retrieve_with_stats

        results: list[Any] = []

        if query:
            # BFS retrieval — primary path
            try:
                retrieval = retrieve_with_stats(
                    self._store,
                    task_description=query,
                    hops=self._hops,
                    top_k=limit + offset,
                    strategies=self._strategies,
                )
                nodes = retrieval.nodes[offset:offset + limit] if retrieval.nodes else []
            except Exception as e:
                log.warning("EngramStore._search retrieval failed: %s", e)
                nodes = []

            # Filter to namespace prefix if provided
            if namespace_prefix:
                ns_tags = set(namespace_prefix)
                nodes = [n for n in nodes if ns_tags.issubset(set(n.get("tags", [])))]

            for node in nodes:
                ns, key = self._source_to_namespace_key(node)
                item = _make_search_item(
                    namespace=ns,
                    key=key,
                    value={"content": node["fact"], "type": node.get("type"), "tags": node.get("tags", [])},
                    created_at=_parse_dt(node.get("created_at")),
                    updated_at=_parse_dt(node.get("created_at")),
                    score=node.get("_score"),
                )
                results.append(item)
        else:
            # No query — return all nodes in namespace prefix, applying filter if given
            try:
                if not namespace_prefix:
                    # Empty prefix: return all EngramStore-managed nodes
                    rows = self._store.conn.execute(
                        "SELECT * FROM nodes WHERE source_transcript LIKE ? AND is_active = 1 "
                        "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                        (f"{_PREFIX}%", limit, offset),
                    ).fetchall()
                else:
                    # Build prefix pattern: "lg:a|b" matches both "lg:a|b:key" (exact ns)
                    # and "lg:a|b|c:key" (deeper ns). Use two LIKE patterns combined with OR.
                    ns_str = _NS_SEP.join(namespace_prefix)
                    base = f"{_PREFIX}{ns_str}"
                    # Matches exact namespace: "lg:a|b:key" and deeper: "lg:a|b|c:key"
                    rows = self._store.conn.execute(
                        "SELECT * FROM nodes WHERE "
                        "(source_transcript LIKE ? OR source_transcript LIKE ?) "
                        "AND is_active = 1 "
                        "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                        (f"{base}{_KEY_SEP}%", f"{base}{_NS_SEP}%", limit, offset),
                    ).fetchall()
            except Exception as e:
                log.warning("EngramStore._search prefix fetch failed: %s", e)
                rows = []

            for row in rows:
                node = self._store._row_to_node(row)
                if filter and not _matches_filter(node, filter):
                    continue
                ns, key = self._source_to_namespace_key(node)
                item = _make_search_item(
                    namespace=ns,
                    key=key,
                    value={"content": node["fact"], "type": node.get("type"), "tags": node.get("tags", [])},
                    created_at=_parse_dt(node.get("created_at")),
                    updated_at=_parse_dt(node.get("created_at")),
                    score=None,
                )
                results.append(item)

        return results

    def _list_namespaces(
        self,
        match_conditions: Any,
        max_depth: int | None,
        limit: int,
        offset: int,
    ) -> list[tuple[str, ...]]:
        try:
            # Optimize: apply max_depth constraint to SQL query to reduce rows materialized.
            # Build WHERE clause to filter on source_transcript structure.
            conditions = "source_transcript LIKE ? AND is_active = 1"
            params: list[Any] = [f"{_PREFIX}%"]

            # Execute the query with LIMIT/OFFSET to avoid materializing all namespaces.
            # We fetch more rows than needed to account for dedup and filtering, then trim.
            fetch_limit = (limit + offset) * 3 if limit > 0 else 10000
            rows = self._store.conn.execute(
                f"SELECT DISTINCT source_transcript FROM nodes "
                f"WHERE {conditions} "
                f"ORDER BY source_transcript "
                f"LIMIT ? OFFSET 0",
                params + [fetch_limit],
            ).fetchall()
        except Exception as e:
            log.warning("EngramStore._list_namespaces failed: %s", e)
            return []

        seen: set[tuple[str, ...]] = set()
        for (source,) in rows:
            decoded = _decode_source(source)
            if decoded is None:
                continue
            ns, _ = decoded
            if max_depth is not None:
                ns = ns[:max_depth]
            seen.add(ns)

        namespaces = sorted(seen)

        # Apply match_conditions
        if match_conditions:
            from langgraph.store.base import MatchCondition
            filtered = []
            for ns in namespaces:
                if _namespace_matches(ns, match_conditions):
                    filtered.append(ns)
            namespaces = filtered

        # Apply LIMIT and OFFSET to the final filtered result
        return namespaces[offset:offset + limit]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _node_to_item(self, node: dict, namespace: tuple[str, ...], key: str) -> Any:
        created_at = _parse_dt(node.get("created_at"))
        return _make_item(
            namespace=namespace,
            key=key,
            value={
                "content": node["fact"],
                "type": node.get("type"),
                "tags": node.get("tags", []),
                "confidence": node.get("confidence"),
            },
            created_at=created_at,
            updated_at=created_at,
        )

    def _source_to_namespace_key(self, node: dict) -> tuple[tuple[str, ...], str]:
        """Decode a node's source_transcript back to (namespace, key).

        Falls back to a synthetic namespace built from the node's tags.
        """
        source = node.get("source_transcript", "")
        decoded = _decode_source(source) if source else None
        if decoded:
            return decoded
        # Fallback: reconstruct from tags
        tags = node.get("tags", [])
        ns = tuple(tags) if tags else ("engram",)
        return ns, node.get("id", "unknown")


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _parse_dt(value: str | None) -> datetime:
    if value:
        try:
            return datetime.fromisoformat(value)
        except (ValueError, TypeError):
            pass
    return datetime.now(timezone.utc)


def _matches_filter(node: dict, filter: dict) -> bool:
    """Apply a LangGraph filter dict against an Engram node's value fields."""
    value = {"content": node["fact"], "type": node.get("type"), "tags": node.get("tags", [])}
    for k, v in filter.items():
        actual = value.get(k)
        if isinstance(v, dict):
            for op, operand in v.items():
                if op == "$eq" and actual != operand:
                    return False
                elif op == "$ne" and actual == operand:
                    return False
                elif op == "$gt" and not (actual > operand):
                    return False
                elif op == "$gte" and not (actual >= operand):
                    return False
                elif op == "$lt" and not (actual < operand):
                    return False
                elif op == "$lte" and not (actual <= operand):
                    return False
        else:
            if actual != v:
                return False
    return True


def _namespace_matches(ns: tuple[str, ...], conditions: Any) -> bool:
    from langgraph.store.base import MatchCondition
    for cond in conditions:
        if not isinstance(cond, MatchCondition):
            continue
        path = tuple(cond.path)
        match_type = cond.match_type
        if match_type == "prefix" and not ns[:len(path)] == path:
            return False
        elif match_type == "suffix" and not ns[-len(path):] == path:
            return False
    return True


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------

def make_engram_store(
    db_path: str | None = None,
    project: str | None = None,
    hops: int = 2,
    top_k: int = 10,
) -> "EngramStore":
    """Create an EngramStore from a project name or explicit db path.

    Args:
        db_path: Explicit path to a ``context.db`` file.
        project: Engram project name (resolves via default config).
        hops: BFS traversal depth for search queries.
        top_k: Maximum nodes returned per search query.

    Returns:
        A ready-to-use EngramStore instance.

    Thread safety:
        - SQLite WAL mode is enabled by default, allowing concurrent reads
        - Concurrent writes serialize at the SQLite level
        - For high-concurrency write workloads, consider the REST API server mode

    Example::

        store = make_engram_store(project="my-agent")
        graph = builder.compile(store=store)
    """
    from engram.config import get_db_path, load_config
    from engram.store import GraphStore
    from pathlib import Path

    if db_path:
        path = Path(db_path).expanduser()
    elif project:
        cfg = load_config(None)
        path = get_db_path(cfg, project)
    else:
        raise ValueError("Provide either db_path or project")

    return EngramStore(GraphStore(path), hops=hops, top_k=top_k)
