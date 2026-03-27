"""
Ingestion pipeline: convert a LOCOMO Conversation into an Engram GraphStore.

Each session's turns are concatenated into a transcript and passed through
Engram's extractor. We store one GraphStore per conversation (isolated DB
per sample_id) so runs are reproducible and parallel-safe.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from engram.extractor import extract_turn, ExtractionBuffer
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

    # Use ExtractionBuffer + extract_turn to avoid max_tokens truncation.
    # Buffer accumulates turns; flushes when threshold met (≥3 turns, >200 words).
    buffer = ExtractionBuffer()
    source_label = conv.sample_id
    ctx_k = 30
    ctx_hops = 2

    def _flush(flush_text: str, session_id: str) -> None:
        nonlocal total_nodes
        keywords = extract_keywords(flush_text)
        context_nodes: list[dict] = []
        if keywords:
            entry_nodes = store.get_nodes_by_tags(keywords)
            if entry_nodes:
                entry_nodes = score_by_relevance(entry_nodes, keywords)
                context_nodes = bfs_collect(store, entry_nodes, ctx_hops)
                context_nodes.sort(key=lambda n: n.get("_relevance", 0), reverse=True)
                context_nodes = context_nodes[:ctx_k]
        try:
            extraction = asyncio.run(extract_turn(flush_text, context_nodes, config, domain_profile))
            nodes = extraction.get("nodes", [])
            edges = extraction.get("edges", [])
            for node in nodes:
                node["source_transcript"] = f"{source_label}/{session_id}"
                node.setdefault("created_at", datetime.now(timezone.utc).isoformat())
            store.merge_extraction(nodes, edges)
            total_nodes += len(nodes)
        except Exception as e:
            errors.append(f"{session_id}: {e}")
            if verbose:
                print(f"  [warn] extract_turn failed for {session_id}: {e}")

    total_turn_count = sum(len(s.turns) for s in conv.sessions)
    turn_idx = 0
    current_session_id: str | None = None
    buffer_nonempty = False
    for session in conv.sessions:
        # Issue #11: flush at session boundary to keep buffer windows session-pure
        if buffer_nonempty and current_session_id != session.session_id:
            _flush(buffer.flush(), current_session_id or session.session_id)
            buffer_nonempty = False

        current_session_id = session.session_id

        # Issue #10: inject session boundary marker so LLM sees temporal context
        if session.datetime_str:
            buffer.add(f"[Session: {session.session_id} | Date: {session.datetime_str}]")
            buffer_nonempty = True

        for turn in session.turns:
            line = f"{turn.speaker}: {turn.text}"
            should_flush = buffer.add(line)
            buffer_nonempty = True
            if should_flush:
                _flush(buffer.flush(), session.session_id)
                buffer_nonempty = False
            turn_idx += 1
            if verbose and turn_idx % 50 == 0:
                print(f"  [ingest] {turn_idx}/{total_turn_count} turns, {total_nodes} nodes so far")

    # Flush any remaining buffered text
    remaining = buffer.flush()
    if remaining.strip():
        _flush(remaining, current_session_id or (conv.sessions[-1].session_id if conv.sessions else "unknown"))

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


def _session_transcript(session: Session, speaker_a: str, speaker_b: str) -> str:
    """Format session turns as a plain-text transcript."""
    lines = []
    if session.datetime_str:
        lines.append(f"[Conversation on {session.datetime_str}]")
    for turn in session.turns:
        lines.append(f"{turn.speaker}: {turn.text}")
    return "\n".join(lines)
