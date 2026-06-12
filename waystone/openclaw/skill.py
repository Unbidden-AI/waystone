"""Waystone OpenClaw skill — hook handlers and @claw commands.

Registered hooks (wired via SKILL.md):
  on_session_start(ctx)                            — bootstrap, context injection
  on_turn_end(ctx, user_content, assistant_content) — buffer turn for extraction
  on_session_end(ctx)                              — flush buffer, write-back MEMORY.md
  on_dream(ctx)                                    — reflection + reconciliation cycle

User commands (triggered by @claw <command>):
  remember <text>    — immediately extract text as a graph node
  recall <query>     — BFS retrieval, returns relevant facts
  forget <topic>     — soft-delete nodes matching topic
  summarize          — full graph summary grouped by type
  sync_now           — force MEMORY.md refresh from live graph
  status             — node count, last sync/dream time, pending errors
  dream              — manually trigger a dream cycle (reflection + reconciliation)
  export [path]      — dump full graph to a markdown file

Session state is stored in a module-level dict keyed by session_id so that
all hooks within a session share the same GraphStore and config.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import get_db_path, get_memory_md_path, get_project, load_openclaw_config
from .errors import DBInitError, LLMExtractionError
from .memory_sync import bootstrap, seed_from_memory_md, write_back

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

class _SessionState:
    """Per-session mutable state shared across all hook calls."""

    def __init__(self, session_id: str, cfg: dict):
        self.session_id = session_id
        self.cfg = cfg
        self.project = get_project(cfg)
        self.store = None  # GraphStore; opened in _open_store()
        self.turn_buffer: list[str] = []
        self.buffer_lock = threading.Lock()
        self.last_sync: datetime | None = None
        self.last_dream: datetime | None = None
        self.last_error: str | None = None
        self.error_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="waystone-oc")

    def open_store(self) -> bool:
        """Open the GraphStore. Returns False if DB doesn't exist yet."""
        from waystone.store import GraphStore
        db_path = get_db_path(self.cfg)
        try:
            self.store = GraphStore(db_path)
            return True
        except Exception as e:
            raise DBInitError(f"Could not open graph store at {db_path}: {e}") from e

    def record_error(self, msg: str) -> None:
        with self.error_lock:
            self.last_error = msg

    def close(self) -> None:
        self._executor.shutdown(wait=False)
        if self.store:
            try:
                self.store.close()
            except Exception:
                pass


# Module-level session registry
_sessions: dict[str, _SessionState] = {}
_sessions_lock = threading.Lock()


def _get_or_create_session(session_id: str) -> _SessionState:
    with _sessions_lock:
        if session_id not in _sessions:
            cfg = load_openclaw_config()
            _sessions[session_id] = _SessionState(session_id, cfg)
        return _sessions[session_id]


def _ctx_session_id(ctx: Any) -> str:
    """Extract session_id from OpenClaw ctx object."""
    return getattr(ctx, "session_id", None) or "default"


# ---------------------------------------------------------------------------
# Lifecycle hooks
# ---------------------------------------------------------------------------

async def on_session_start(ctx: Any) -> None:
    """Called by OpenClaw at the start of each conversation session.

    1. Load config and open GraphStore.
    2. If graph is empty and MEMORY.md exists → seed graph from MEMORY.md.
    3. Inject top-K relevant nodes into system prompt.
    4. Bootstrap MEMORY.md Waystone section from graph.
    """
    session_id = _ctx_session_id(ctx)
    state = _get_or_create_session(session_id)

    try:
        state.open_store()
    except DBInitError as e:
        log.warning("waystone: on_session_start — %s", e)
        state.record_error(str(e))
        return

    try:
        stats = state.store.get_stats()
        graph_empty = stats.get("node_count", 0) == 0

        if graph_empty:
            # Try to seed from existing MEMORY.md (first-time install path)
            memory_path = get_memory_md_path(state.cfg)
            if memory_path.exists() and not state.cfg.get("dry_run"):
                try:
                    n = seed_from_memory_md(state.cfg)
                    if n > 0:
                        log.info("waystone: seeded graph with %d nodes from MEMORY.md", n)
                except LLMExtractionError as e:
                    log.warning("waystone: MEMORY.md seeding failed: %s", e)
                    state.record_error(f"MEMORY.md seed failed: {e}")
        else:
            # Inject top-K relevant nodes into the system prompt
            context_block = _build_context_block(state)
            if context_block and state.cfg.get("context_prefix", True):
                _inject_context(ctx, context_block)

            # Refresh MEMORY.md from live graph
            bootstrap(state.store, state.cfg)
            state.last_sync = datetime.now(timezone.utc)

    except Exception as e:
        log.warning("waystone: on_session_start error: %s", e)
        state.record_error(str(e))


async def on_turn_end(ctx: Any, user_content: str, assistant_content: str) -> None:
    """Called by OpenClaw after each completed user/assistant turn.

    Buffers the turn text. If extract_on_session_end_only is False, triggers
    background extraction when the buffer threshold is reached.
    """
    session_id = _ctx_session_id(ctx)
    state = _get_or_create_session(session_id)

    if not state.store or not state.cfg.get("auto_extract", True):
        return

    turn_text = f"Human: {user_content.strip()}\n\nAssistant: {assistant_content.strip()}"

    with state.buffer_lock:
        state.turn_buffer.append(turn_text)
        buffer_len = len(state.turn_buffer)

    # If extract_on_session_end_only is False, flush when buffer is large
    if not state.cfg.get("extract_on_session_end_only", True):
        max_turns = state.cfg.get("_waystone", {}).get("incremental", {}).get("max_turns", 10)
        if buffer_len >= max_turns:
            await _flush_and_extract(state)


async def on_session_end(ctx: Any) -> None:
    """Called by OpenClaw when the session ends.

    Flushes the turn buffer, runs extraction, and refreshes MEMORY.md.
    """
    session_id = _ctx_session_id(ctx)
    state = _get_or_create_session(session_id)

    if state.store and state.cfg.get("auto_extract", True):
        await _flush_and_extract(state)

    # Clean up session state
    with _sessions_lock:
        if session_id in _sessions:
            _sessions[session_id].close()
            del _sessions[session_id]


async def on_dream(ctx: Any) -> None:
    """Called by OpenClaw's dreaming cycle.

    Delegates to dreaming.run_dream() which runs reflection (hub node confidence
    boosting + tag co-occurrence clustering) and reconciliation (superseded pruning
    + conflict detection), then refreshes MEMORY.md.

    This replaces OpenClaw's native LLM-based dreaming — Waystone's passes are
    deterministic and cost-free.
    """
    session_id = _ctx_session_id(ctx)
    state = _get_or_create_session(session_id)

    if not state.store:
        return

    await _run_dream_cycle(state)


# ---------------------------------------------------------------------------
# @claw commands (Phase 1B)
# ---------------------------------------------------------------------------

async def cmd_remember(ctx: Any, args: str = "") -> str:
    """@claw remember <text> — immediately extract text as a graph node.

    Calls the LLM synchronously (blocks until extraction is done).
    Returns a confirmation with the number of nodes extracted.
    """
    session_id = _ctx_session_id(ctx)
    state = _get_or_create_session(session_id)
    text = args.strip()

    if not text:
        return "Usage: @claw remember <text to remember>"

    if state.cfg.get("dry_run"):
        return f"[dry_run] Would extract: {text[:100]}…"

    if not state.store:
        try:
            state.open_store()
        except DBInitError as e:
            return f"Waystone error: {e}"

    try:
        from waystone.extractor import extract
        waystone_cfg = state.cfg.get("_waystone", {})
        result = await extract(text, waystone_cfg, source_transcript="claw_remember")
        nodes = result.get("nodes", [])
        edges = result.get("edges", [])
        if nodes:
            state.store.merge_extraction(nodes, edges)
            write_back(state.store, state.cfg)
            return f"Remembered: extracted {len(nodes)} fact(s) into '{state.project}'."
        else:
            return "Noted, but no structured facts were extracted from that text."
    except Exception as e:
        return f"Waystone extraction failed: {e}"


async def cmd_recall(ctx: Any, args: str = "") -> str:
    """@claw recall <query> — BFS retrieval, returns relevant facts.

    Returns a formatted markdown block of the most relevant nodes.
    """
    session_id = _ctx_session_id(ctx)
    state = _get_or_create_session(session_id)
    query = args.strip()

    if not state.store:
        try:
            state.open_store()
        except DBInitError as e:
            return f"Waystone error: {e}"

    try:
        from waystone.retriever import retrieve
        waystone_cfg = state.cfg.get("_waystone", {})
        strategies = waystone_cfg.get("strategies", {})
        result = retrieve(
            state.store,
            query or "general context",
            hops=state.cfg.get("hops", 3),
            top_k=state.cfg.get("top_k", 15),
            strategies=strategies,
        )
        return result or "_No relevant facts found._"
    except Exception as e:
        return f"Waystone recall failed: {e}"


async def cmd_forget(ctx: Any, args: str = "") -> str:
    """@claw forget <topic> — soft-delete nodes matching topic.

    Marks matching nodes as inactive (recoverable via the CLI).
    """
    session_id = _ctx_session_id(ctx)
    state = _get_or_create_session(session_id)
    topic = args.strip()

    if not topic:
        return "Usage: @claw forget <topic>"

    if not state.store:
        try:
            state.open_store()
        except DBInitError as e:
            return f"Waystone error: {e}"

    try:
        tags = topic.lower().split()
        nodes = state.store.get_nodes_by_tags(tags)
        if not nodes:
            return f"No facts found matching '{topic}'."
        count = 0
        for node in nodes:
            try:
                state.store.deactivate_node(node["id"])
                count += 1
            except Exception:
                pass
        write_back(state.store, state.cfg)
        return f"Forgot {count} fact(s) about '{topic}'. (Recoverable via `waystone show {state.project}`.)"
    except Exception as e:
        return f"Waystone forget failed: {e}"


async def cmd_summarize(ctx: Any, args: str = "") -> str:
    """@claw summarize — render all active graph nodes as a structured summary."""
    session_id = _ctx_session_id(ctx)
    state = _get_or_create_session(session_id)

    if not state.store:
        try:
            state.open_store()
        except DBInitError as e:
            return f"Waystone error: {e}"

    try:
        from .memory_sync import _get_top_nodes, _render_nodes
        nodes = _get_top_nodes(state.store, top_k=50)
        if not nodes:
            return f"Graph for '{state.project}' is empty."
        return _render_nodes(nodes, f"## {state.project} — Knowledge Graph Summary")
    except Exception as e:
        return f"Waystone summarize failed: {e}"


async def cmd_sync_now(ctx: Any, args: str = "") -> str:
    """@claw sync_now — force MEMORY.md refresh from the live graph."""
    session_id = _ctx_session_id(ctx)
    state = _get_or_create_session(session_id)

    if not state.store:
        try:
            state.open_store()
        except DBInitError as e:
            return f"Waystone error: {e}"

    ok = write_back(state.store, state.cfg)
    if ok:
        state.last_sync = datetime.now(timezone.utc)
        return f"MEMORY.md synced with {state.store.get_stats().get('node_count', 0)} nodes from '{state.project}'."
    else:
        return "MEMORY.md sync failed — check logs."


async def cmd_dream(ctx: Any, args: str = "") -> str:
    """@claw dream — manually trigger a reflection + reconciliation cycle.

    Useful after a long session to promote hub nodes and prune stale facts
    before reviewing the knowledge graph.
    """
    session_id = _ctx_session_id(ctx)
    state = _get_or_create_session(session_id)

    if not state.store:
        try:
            state.open_store()
        except DBInitError as e:
            return f"Waystone error: {e}"

    return await _run_dream_cycle(state)


async def cmd_export(ctx: Any, args: str = "") -> str:
    """@claw export [path] — dump full active graph to a markdown file.

    If no path is given, writes to ~/.waystone/<project>_export.md.
    Returns the path of the written file on success.
    """
    session_id = _ctx_session_id(ctx)
    state = _get_or_create_session(session_id)

    if not state.store:
        try:
            state.open_store()
        except DBInitError as e:
            return f"Waystone error: {e}"

    # Resolve output path
    dest = args.strip()
    if dest:
        out_path = Path(dest).expanduser()
    else:
        out_path = Path.home() / ".waystone" / f"{state.project}_export.md"

    try:
        from .memory_sync import _get_top_nodes, _render_nodes

        nodes = _get_top_nodes(state.store, top_k=1000)  # all nodes
        header = f"# {state.project} — Waystone Full Export\n\n_Generated {datetime.now(timezone.utc).isoformat()}_"

        if not nodes:
            content = header + "\n\n_Graph is empty._"
        else:
            section = _render_nodes(nodes, f"## Knowledge Graph ({len(nodes)} nodes)")
            content = header + "\n\n" + section

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
        return f"Exported {len(nodes)} node(s) to `{out_path}`."
    except Exception as e:
        return f"Waystone export failed: {e}"


async def cmd_status(ctx: Any, args: str = "") -> str:
    """@claw status — show node count, sync time, error state."""
    session_id = _ctx_session_id(ctx)
    state = _get_or_create_session(session_id)

    lines = [f"**Waystone — {state.project}**"]

    if not state.store:
        try:
            state.open_store()
        except DBInitError:
            lines.append("Status: graph not initialized")
            return "\n".join(lines)

    try:
        stats = state.store.get_stats()
        lines.append(f"Nodes: {stats.get('node_count', 0)}  Edges: {stats.get('edge_count', 0)}")
        if stats.get("type_counts"):
            type_str = ", ".join(f"{t}={c}" for t, c in sorted(stats["type_counts"].items()))
            lines.append(f"Types: {type_str}")
    except Exception:
        lines.append("Nodes: (error reading stats)")

    last_sync = state.last_sync.strftime("%Y-%m-%d %H:%M UTC") if state.last_sync else "never"
    last_dream = state.last_dream.strftime("%Y-%m-%d %H:%M UTC") if state.last_dream else "never"
    lines.append(f"Last sync: {last_sync}")
    lines.append(f"Last dream: {last_dream}")

    memory_path = get_memory_md_path(state.cfg)
    lines.append(f"MEMORY.md: {memory_path} ({'exists' if memory_path.exists() else 'missing'})")

    with state.error_lock:
        if state.last_error:
            lines.append(f"⚠ Last error: {state.last_error}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _build_context_block(state: "_SessionState") -> str:
    """BFS retrieval → formatted context block for system prompt injection."""
    try:
        from waystone.retriever import retrieve
        waystone_cfg = state.cfg.get("_waystone", {})
        strategies = waystone_cfg.get("strategies", {})
        return retrieve(
            state.store,
            "",  # empty query = return highest-confidence nodes
            hops=state.cfg.get("hops", 2),
            top_k=state.cfg.get("context_top_k", 15),
            strategies=strategies,
        ) or ""
    except Exception as e:
        log.warning("waystone: context injection failed: %s", e)
        return ""


def _inject_context(ctx: Any, context_block: str) -> None:
    """Inject the context block into OpenClaw's system prompt."""
    header = "[Waystone Project Knowledge]\n"
    full_block = header + context_block

    # Try OpenClaw's documented injection methods (defensive — API may vary)
    for method_name in ("inject_context", "prepend_context", "add_system_context"):
        method = getattr(ctx, method_name, None)
        if callable(method):
            try:
                method(full_block)
                return
            except Exception:
                continue

    # Fallback: mutate ctx.system_prompt directly if it's a string
    if hasattr(ctx, "system_prompt") and isinstance(ctx.system_prompt, str):
        ctx.system_prompt = full_block + "\n\n" + ctx.system_prompt


async def _flush_and_extract(state: "_SessionState") -> None:
    """Flush turn buffer → run extraction → write-back MEMORY.md."""
    with state.buffer_lock:
        if not state.turn_buffer:
            return
        text = "\n\n".join(state.turn_buffer)
        state.turn_buffer.clear()

    if state.cfg.get("dry_run"):
        log.info("waystone: dry_run — would extract %d chars", len(text))
        return

    try:
        from waystone.extractor import extract
        from waystone.store import GraphStore

        waystone_cfg = state.cfg.get("_waystone", {})
        db_path = get_db_path(state.cfg)

        # Open a fresh store in this coroutine (SQLite is not thread-safe across threads)
        store = GraphStore(db_path)
        try:
            result = await asyncio.wait_for(
                extract(text, waystone_cfg, source_transcript=f"session_{state.session_id}"),
                timeout=60.0,
            )
            nodes = result.get("nodes", [])
            edges = result.get("edges", [])
            if nodes:
                store.merge_extraction(nodes, edges)
                log.info("waystone: extracted %d nodes from session buffer", len(nodes))
            # Refresh MEMORY.md with the primary store (already has the new nodes)
            write_back(state.store or store, state.cfg)
            state.last_sync = datetime.now(timezone.utc)
        finally:
            store.close()

    except asyncio.TimeoutError:
        msg = "extraction timed out after 60s — buffer not stored"
        log.warning("waystone: %s", msg)
        state.record_error(msg)
    except Exception as e:
        log.warning("waystone: flush+extract failed: %s", e)
        state.record_error(str(e)[:120])


async def _run_dream_cycle(state: "_SessionState") -> str:
    """Run a full dream cycle and return a human-readable summary string."""
    from .dreaming import format_dream_summary, run_dream

    try:
        result = run_dream(state.store, state.cfg)
        write_back(state.store, state.cfg)
        state.last_dream = datetime.now(timezone.utc)
        return format_dream_summary(result)
    except Exception as e:
        msg = f"dream failed: {e}"
        log.warning("waystone: %s", msg)
        state.record_error(msg)
        return f"Dream cycle error: {e}"
