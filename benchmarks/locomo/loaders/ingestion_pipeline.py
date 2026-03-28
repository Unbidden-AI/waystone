"""
Ingestion pipeline: convert a LOCOMO Conversation into an Engram GraphStore.

Each session is processed as a self-contained transcript — one extraction call
per session, matching how the Stop hook works in real-time use. If a session is
too large for a single extraction call (finish_reason=length), it is bisected
and each half retried recursively.

We store one GraphStore per conversation (isolated DB per sample_id) so runs
are reproducible and parallel-safe.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from engram.extractor import extract_turn
from engram.retriever import bfs_collect, score_by_relevance, extract_keywords
from engram.store import GraphStore
from engram.config import load_config, get_domain_profile

from benchmarks.locomo.loaders.locomo_dataset import Conversation, Session


@dataclass
class IngestionResult:
    sample_id: str
    db_path: str
    sessions_ingested: int
    turns_ingested: int
    nodes_created: int
    elapsed_seconds: float
    errors: list[str] = field(default_factory=list)


def ingest_conversation(
    conv: Conversation,
    db_dir: str | Path | None = None,
    verbose: bool = False,
    domain: str | None = None,
) -> tuple[GraphStore, IngestionResult]:
    """
    Ingest a LOCOMO conversation into a fresh GraphStore.

    Each session is processed as a self-contained unit (one extraction call),
    matching real-time episodic use. Sessions that exceed the model's output
    window are bisected and retried.

    Args:
        conv: Parsed Conversation from LocomoDataset.
        db_dir: Directory to write the SQLite DB. Uses a temp dir if None.
        verbose: Print progress.
        domain: Domain profile name (e.g. "episodic_personal"). If None, reads
                from config.yaml domain.name (default: "software_dev").

    Returns:
        (store, result) — caller is responsible for closing store when done.
    """
    if db_dir is None:
        db_dir = tempfile.mkdtemp(prefix="locomo_")
    db_path = str(Path(db_dir) / f"{conv.sample_id}.db")

    config = load_config()
    if domain is not None:
        config = {**config, "domain": {"name": domain}}
    domain_profile = get_domain_profile(config)
    store = GraphStore(db_path)

    t0 = time.perf_counter()
    errors = []
    total_nodes = 0
    source_label = conv.sample_id
    ctx_k = 30
    ctx_hops = 2

    def _extract_chunk(text: str, session_id: str, depth: int = 0) -> None:
        """Extract a text chunk, bisecting on output-length truncation."""
        nonlocal total_nodes
        keywords = extract_keywords(text)
        context_nodes: list[dict] = []
        if keywords:
            entry_nodes = store.get_nodes_by_tags(keywords)
            if entry_nodes:
                entry_nodes = score_by_relevance(entry_nodes, keywords)
                context_nodes = bfs_collect(store, entry_nodes, ctx_hops)
                context_nodes.sort(key=lambda n: n.get("_relevance", 0), reverse=True)
                context_nodes = context_nodes[:ctx_k]
        try:
            extraction = asyncio.run(extract_turn(text, context_nodes, config, domain_profile))
            nodes = extraction.get("nodes", [])
            edges = extraction.get("edges", [])
            for node in nodes:
                node["source_transcript"] = f"{source_label}/{session_id}"
                node.setdefault("created_at", datetime.now(timezone.utc).isoformat())
            store.merge_extraction(nodes, edges)
            total_nodes += len(nodes)
        except ValueError as e:
            if "finish_reason=length" in str(e) and depth < 4:
                # Output window exceeded — bisect on a newline boundary and retry
                lines = text.splitlines()
                if len(lines) < 2:
                    errors.append(f"{session_id}: chunk too small to bisect: {e}")
                    return
                mid = len(lines) // 2
                if verbose:
                    print(f"  [bisect] {session_id}: splitting {len(lines)} lines at {mid} (depth={depth})")
                _extract_chunk("\n".join(lines[:mid]), session_id, depth + 1)
                _extract_chunk("\n".join(lines[mid:]), session_id, depth + 1)
            else:
                errors.append(f"{session_id}: {e}")
                if verbose:
                    print(f"  [warn] extract_turn failed for {session_id}: {e}")
        except Exception as e:
            errors.append(f"{session_id}: {e}")
            if verbose:
                print(f"  [warn] extract_turn failed for {session_id}: {e}")

    def _session_text(session: Session) -> str:
        """Format a session as a self-contained transcript with date header."""
        lines = []
        if session.datetime_str:
            lines.append(f"[Session: {session.session_id} | Date: {session.datetime_str}]")
        for turn in session.turns:
            lines.append(f"{turn.speaker}: {turn.text}")
        return "\n".join(lines)

    for i, session in enumerate(conv.sessions):
        text = _session_text(session)
        _extract_chunk(text, session.session_id)
        if verbose:
            print(f"  [ingest] {conv.sample_id} session {i+1}/{len(conv.sessions)} "
                  f"({session.session_id}): {total_nodes} nodes so far")

    # Embed all nodes for semantic retrieval
    store.embed_missing_nodes()

    elapsed = time.perf_counter() - t0

    result = IngestionResult(
        sample_id=conv.sample_id,
        db_path=db_path,
        sessions_ingested=len(conv.sessions),
        turns_ingested=conv.total_turns,
        nodes_created=total_nodes,
        elapsed_seconds=elapsed,
        errors=errors,
    )

    if verbose:
        print(
            f"[ingest] {conv.sample_id}: {result.sessions_ingested} sessions, "
            f"{result.nodes_created} nodes in {elapsed:.1f}s"
        )

    return store, result
