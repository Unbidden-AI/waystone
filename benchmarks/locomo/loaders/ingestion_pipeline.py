"""
Ingestion pipeline: convert a LOCOMO Conversation into an Engram GraphStore.

Each session is processed as a self-contained transcript — one extraction call
per session, matching how the Stop hook works in real-time use. If a session is
too large for a single extraction call (finish_reason=length), it is bisected
and each half retried recursively.

Context carry-forward: to resolve co-references that span session boundaries or
bisection boundaries, a small tail of prior raw text is prepended as a read-only
header. The LLM is instructed not to re-extract from it — it exists only so that
"she mentioned her new job" in turn 2 of session 3 can be resolved to the entity
established at the end of session 2.

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
    dedup_threshold: float = 0.95,
    llm_override: dict | None = None,
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
        llm_override: Optional dict to deep-merge into config["llm"], overriding
                the extraction model. Use to run with a specific model (e.g.
                gpt-4o-mini) for apples-to-apples comparisons.

    Returns:
        (store, result) — caller is responsible for closing store when done.
    """
    if db_dir is None:
        db_dir = tempfile.mkdtemp(prefix="locomo_")
    db_path = str(Path(db_dir) / f"{conv.sample_id}.db")

    config = load_config()
    if domain is not None:
        config = {**config, "domain": {"name": domain}}
    if llm_override:
        config["llm"] = {**config.get("llm", {}), **llm_override}
    domain_profile = get_domain_profile(config)
    store = GraphStore(db_path, dedup_threshold=dedup_threshold)

    from engram.llm import get_provider as _get_llm_provider
    _provider = _get_llm_provider(config)

    t0 = time.perf_counter()
    errors = []
    total_nodes = 0
    source_label = conv.sample_id
    ctx_k = 30
    ctx_hops = 2
    # Number of lines to carry forward as read-only context across bisection and
    # session boundaries. 8 lines covers ~4 speaker turns — enough to resolve
    # most cross-boundary co-references ("she finally told me" → entity from
    # prior turns) without bloating the prompt significantly (~150–250 tokens).
    CONTEXT_TAIL_LINES = 8

    def _build_prompt_text(main_text: str, prior_tail: str | None) -> str:
        """Prepend a read-only prior-context header when available."""
        if not prior_tail:
            return main_text
        return (
            "[Prior context — already extracted. Use only to resolve co-references "
            "in the current segment. Do NOT re-extract facts from this block.]\n"
            + prior_tail
            + "\n\n[Current segment — extract from this:]\n"
            + main_text
        )

    def _extract_chunk(
        text: str,
        session_id: str,
        depth: int = 0,
        prior_tail: str | None = None,
        occurred_at: str | None = None,
    ) -> None:
        """Extract a text chunk, bisecting on output-length truncation."""
        nonlocal total_nodes
        prompt_text = _build_prompt_text(text, prior_tail)

        # Pre-flight token check: if native SDK supports count_tokens, estimate
        # whether the extraction output will exceed max_tokens before spending an
        # API call.  Extraction JSON grows roughly 0.8 output tokens per input
        # token for this task; bisect immediately when the budget is tight.
        if depth < 4 and _provider is not None and _provider.supports_count_tokens:
            max_tok = config.get("llm", {}).get("max_tokens", 4096)
            try:
                n_input = _provider.count_tokens(prompt_text)
                if n_input * 0.8 > max_tok * 0.9:
                    lines = text.splitlines()
                    if len(lines) >= 2:
                        mid = len(lines) // 2
                        if verbose:
                            print(
                                f"  [pre-bisect] {session_id}: {n_input} input tokens "
                                f"exceeds threshold, splitting {len(lines)} lines at "
                                f"{mid} (depth={depth})"
                            )
                        first_half = "\n".join(lines[:mid])
                        second_half = "\n".join(lines[mid:])
                        bisect_tail = "\n".join(lines[max(0, mid - CONTEXT_TAIL_LINES):mid])
                        _extract_chunk(first_half, session_id, depth + 1, prior_tail=prior_tail, occurred_at=occurred_at)
                        _extract_chunk(second_half, session_id, depth + 1, prior_tail=bisect_tail, occurred_at=occurred_at)
                        return
            except Exception:
                pass  # count_tokens failure is non-fatal — proceed with API call

        keywords = extract_keywords(text)  # keywords from main text only
        context_nodes: list[dict] = []
        if keywords:
            entry_nodes = store.get_nodes_by_tags(keywords)
            if entry_nodes:
                entry_nodes = score_by_relevance(entry_nodes, keywords)
                context_nodes = bfs_collect(store, entry_nodes, ctx_hops)
                context_nodes.sort(key=lambda n: n.get("_relevance", 0), reverse=True)
                context_nodes = context_nodes[:ctx_k]

        # Retry policy for extraction API calls — mirrors GeminiNativeProvider but
        # also covers the OpenAI-compatible path (gpt-4o-mini, local models).
        # The native Gemini provider already retries internally; this outer loop
        # is a safety net for errors that propagate through the async wrapper or
        # from non-native providers.
        #
        # Three error classes:
        #   transient  (503/500/502): short backoff (5s, 15s, 45s) — server hiccup
        #   rate_limit (429 RPM/TPM): longer backoff (15s, 30s, 60s) — burst clears
        #   quota_exhausted (daily/billing cap): fail immediately with clear message

        _QUOTA_PHRASES = (
            "per day", "daily limit", "monthly", "billing",
            "quota exceeded", "project quota",
        )
        _RATE_PHRASES = (
            "429", "rate limit", "resource exhausted",
            "too many requests", "overloaded",
        )
        _TRANSIENT_PHRASES = (
            "503", "502", "500", "service unavailable", "internal server",
        )
        _TRANSIENT_BACKOFF = (5, 15, 45)
        _RATE_LIMIT_BACKOFF = (15, 30, 60)

        def _classify(exc: Exception) -> str:
            s = str(exc).lower()
            # quota_exhausted wins — retrying is useless
            if any(p in s for p in _QUOTA_PHRASES):
                return "quota_exhausted"
            if any(p in s for p in _TRANSIENT_PHRASES):
                return "transient"
            if any(p in s for p in _RATE_PHRASES):
                return "rate_limit"
            return "fatal"

        last_exc: Exception | None = None
        extraction: dict | None = None
        _attempt = 0
        while True:
            try:
                extraction = asyncio.run(extract_turn(prompt_text, context_nodes, config, domain_profile))
                last_exc = None
                break
            except ValueError as e:
                if "finish_reason=length" in str(e) and depth < 4:
                    # Output window exceeded — bisect on a newline boundary and retry.
                    lines = text.splitlines()
                    if len(lines) < 2:
                        errors.append(f"{session_id}: chunk too small to bisect: {e}")
                        return
                    mid = len(lines) // 2
                    if verbose:
                        print(f"  [bisect] {session_id}: splitting {len(lines)} lines at {mid} (depth={depth})")
                    first_half = "\n".join(lines[:mid])
                    second_half = "\n".join(lines[mid:])
                    bisect_tail = "\n".join(lines[max(0, mid - CONTEXT_TAIL_LINES):mid])
                    _extract_chunk(first_half, session_id, depth + 1, prior_tail=prior_tail, occurred_at=occurred_at)
                    _extract_chunk(second_half, session_id, depth + 1, prior_tail=bisect_tail, occurred_at=occurred_at)
                    return
                else:
                    errors.append(f"{session_id}: {e}")
                    if verbose:
                        print(f"  [warn] extract_turn failed for {session_id}: {e}")
                    return
            except RuntimeError as e:
                # quota_exhausted is re-raised as RuntimeError by GeminiNativeProvider
                if "quota exhausted" in str(e).lower():
                    errors.append(f"{session_id}: {e}")
                    if verbose:
                        print(f"  [error] {session_id}: {e}")
                    return
                last_exc = e
                break
            except Exception as e:
                kind = _classify(e)
                if kind == "quota_exhausted":
                    msg = (
                        f"{session_id}: API quota exhausted (daily/billing/project cap). "
                        f"Check your billing console. Original: {e}"
                    )
                    errors.append(msg)
                    if verbose:
                        print(f"  [error] {msg}")
                    return

                backoff = _TRANSIENT_BACKOFF if kind == "transient" else (
                    _RATE_LIMIT_BACKOFF if kind == "rate_limit" else ()
                )
                if _attempt < len(backoff):
                    wait = backoff[_attempt]
                    if verbose:
                        print(
                            f"  [retry] {session_id}: {kind} error "
                            f"(attempt {_attempt + 1}/{len(backoff)}), "
                            f"retrying in {wait}s: {type(e).__name__}: {e}"
                        )
                    time.sleep(wait)
                    _attempt += 1
                    last_exc = e
                else:
                    last_exc = e
                    break

        if last_exc is not None:
            errors.append(f"{session_id}: {last_exc}")
            if verbose:
                print(f"  [warn] extract_turn failed for {session_id}: {last_exc}")
            return

        if extraction is None:
            return

        nodes = extraction.get("nodes", [])
        edges = extraction.get("edges", [])
        for node in nodes:
            node["source_transcript"] = f"{source_label}/{session_id}"
            node.setdefault("created_at", datetime.now(timezone.utc).isoformat())
            if occurred_at:
                node.setdefault("occurred_at", occurred_at)
        store.merge_extraction(nodes, edges)
        total_nodes += len(nodes)

    def _session_text(session: Session) -> str:
        """Format a session as a self-contained transcript with date header.

        Includes both the raw datetime string and a parsed ISO date so the
        extraction model can reliably follow temporal resolution instructions
        (replacing 'last week', 'yesterday', etc. with absolute dates).
        """
        lines = []
        if session.datetime_str:
            iso = _parse_session_date(session.datetime_str)
            if iso:
                # ISO date gives the model an unambiguous reference for resolving
                # relative phrases — "last week" → week before this date.
                lines.append(f"[Session: {session.session_id} | Date: {iso[:10]}]")
            else:
                lines.append(f"[Session: {session.session_id} | Date: {session.datetime_str}]")
        for turn in session.turns:
            lines.append(f"{turn.speaker}: {turn.text}")
        return "\n".join(lines)

    def _parse_session_date(datetime_str: str | None) -> str | None:
        """Parse LOCOMO session date string to ISO-8601 UTC.

        Input format: '7:31 pm on 21 January, 2022'
        Returns ISO string or None if unparseable.
        """
        if not datetime_str:
            return None
        for fmt in ("%I:%M %p on %d %B, %Y", "%I:%M %p on %d %B %Y"):
            try:
                dt = datetime.strptime(datetime_str.strip(), fmt)
                return dt.replace(tzinfo=timezone.utc).isoformat()
            except ValueError:
                continue
        return None

    prev_session_tail: str | None = None
    for i, session in enumerate(conv.sessions):
        text = _session_text(session)
        session_occurred_at = _parse_session_date(session.datetime_str)
        _extract_chunk(text, session.session_id, prior_tail=prev_session_tail,
                       occurred_at=session_occurred_at)
        # Capture the last N raw turns of this session (excluding the date header)
        # for the next session to use as co-reference context.
        session_lines = [f"{t.speaker}: {t.text}" for t in session.turns]
        prev_session_tail = "\n".join(session_lines[-CONTEXT_TAIL_LINES:])
        if verbose:
            print(f"  [ingest] {conv.sample_id} session {i+1}/{len(conv.sessions)} "
                  f"({session.session_id}): {total_nodes} nodes so far")

    # Embed all nodes for semantic retrieval
    store.embed_missing_nodes()

    # When sqlite-vec is unavailable (e.g. Python 3.14 macOS), the per-insert
    # semantic dedup is skipped, causing paraphrase variants to accumulate as
    # separate nodes.  Run a post-ingest brute-force dedup pass to recover.
    if not store._vec_available:
        from engram import embedder as _emb
        if _emb.is_available():
            dedup_removed = store.dedup_nodes_brute_force()
            if verbose and dedup_removed:
                print(
                    f"  [dedup] {conv.sample_id}: removed {dedup_removed} duplicate nodes "
                    f"({total_nodes} → {total_nodes - dedup_removed})"
                )
            total_nodes -= dedup_removed

    # Remove edges whose endpoints were deleted by semantic dedup
    orphaned = store.vacuum_orphaned_edges()
    if verbose and orphaned:
        print(f"  [vacuum] {conv.sample_id}: removed {orphaned} orphaned edges")

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
