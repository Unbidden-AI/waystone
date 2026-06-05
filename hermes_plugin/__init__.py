"""Waystone memory provider plugin for Hermes Agent.

Implements the Hermes MemoryProvider interface to provide structured,
DAG-based long-term memory backed by a Waystone knowledge graph.

Installation (into a Hermes Agent installation):
    cp -r hermes_plugin/ /path/to/hermes-agent/plugins/memory/waystone/
    pip install waystone  # or pip install -e /path/to/waystone

Configuration (hermes memory setup, or set env vars):
    WAYSTONE_PROJECT   — project name (required)
    WAYSTONE_DB_PATH   — explicit path to context.db (optional; overrides project lookup)
    WAYSTONE_CONFIG    — path to Waystone config.yaml (optional)
    WAYSTONE_TOP_K     — max nodes per retrieval (default: 10)
    WAYSTONE_HOPS      — BFS traversal depth (default: 3)
    WAYSTONE_EXTRACT   — set to "0" to disable auto-extraction on sync_turn (default: enabled)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy MemoryProvider base — import from hermes at runtime so the plugin file
# is importable even outside a Hermes installation (e.g. during tests).
# ---------------------------------------------------------------------------

def _get_base():
    try:
        from agent.memory_provider import MemoryProvider
        return MemoryProvider
    except ImportError:
        # Running outside Hermes (tests, standalone); use a no-op base class.
        class _Stub:
            pass
        return _Stub


# ---------------------------------------------------------------------------
# WaystoneMemoryProvider
# ---------------------------------------------------------------------------

class WaystoneMemoryProvider(_get_base()):
    """Hermes MemoryProvider backed by a Waystone knowledge graph.

    - prefetch(): BFS retrieval injects structured context before each LLM call
    - sync_turn(): incremental extraction runs in background after each turn
    - Tools: waystone_query (semantic search), waystone_recall (full node dump by tags)
    """

    @property
    def name(self) -> str:
        return "waystone"

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def _load_waystone_config(self) -> dict:
        """Load Waystone config from file or fall back to defaults."""
        from waystone.config import load_config
        config_path = os.environ.get("WAYSTONE_CONFIG") or self._config.get("config_path")
        return load_config(config_path)

    def _resolve_db_path(self) -> Path:
        """Resolve the SQLite DB path for the configured project."""
        explicit = os.environ.get("WAYSTONE_DB_PATH") or self._config.get("db_path")
        if explicit:
            return Path(explicit).expanduser()
        from waystone.config import get_db_path
        return get_db_path(self._waystone_config, self._project)

    # ------------------------------------------------------------------
    # Required abstract methods
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Return True if the configured Waystone project DB exists."""
        try:
            self._load_waystone_config()
        except Exception:
            return False
        project = os.environ.get("WAYSTONE_PROJECT") or self._config.get("project", "")
        if not project:
            return False
        try:
            from waystone.config import load_config, get_db_path
            cfg = load_config(os.environ.get("WAYSTONE_CONFIG") or self._config.get("config_path"))
            db = get_db_path(cfg, project)
            return db.exists()
        except Exception:
            return False

    def initialize(self, session_id: str, **kwargs) -> None:
        """Set up the provider for the session.

        Called once at agent startup. Reads config, opens the GraphStore,
        and initialises the extraction buffer and background thread machinery.
        """
        hermes_home = kwargs.get("hermes_home", str(Path.home() / ".hermes"))
        self._hermes_home = Path(hermes_home)
        self._session_id = session_id
        self._platform = kwargs.get("platform", "cli")

        # Load persisted config from $HERMES_HOME/waystone.json (or env vars)
        config_file = self._hermes_home / "waystone.json"
        self._config: dict = {}
        if config_file.exists():
            try:
                self._config = json.loads(config_file.read_text())
            except Exception as e:
                log.warning("waystone: failed to read %s: %s", config_file, e)

        # Resolve project name
        self._project = (
            os.environ.get("WAYSTONE_PROJECT")
            or self._config.get("project")
            or ""
        )
        if not self._project:
            log.warning("waystone: WAYSTONE_PROJECT not set — provider disabled")
            self._store = None
            return

        # Load Waystone config and open store
        try:
            self._waystone_config = self._load_waystone_config()
            db_path = self._resolve_db_path()
            from waystone.store import GraphStore
            self._store = GraphStore(db_path)
        except Exception as e:
            log.error("waystone: failed to open graph store: %s", e)
            self._store = None
            return

        # Retrieval settings
        self._top_k = int(
            os.environ.get("WAYSTONE_TOP_K")
            or self._config.get("top_k")
            or self._waystone_config.get("defaults", {}).get("top_k", 10)
        )
        self._hops = int(
            os.environ.get("WAYSTONE_HOPS")
            or self._config.get("hops")
            or self._waystone_config.get("defaults", {}).get("hops", 3)
        )
        self._auto_extract = (
            os.environ.get("WAYSTONE_EXTRACT", "1") != "0"
            and self._config.get("auto_extract", True)
        )

        # Extraction buffer and threading
        from waystone.extractor import ExtractionBuffer
        self._buffer = ExtractionBuffer(
            min_turns=self._waystone_config.get("incremental", {}).get("min_turns", 3),
            min_words=self._waystone_config.get("incremental", {}).get("min_words", 200),
            max_turns=self._waystone_config.get("incremental", {}).get("max_turns", 10),
        )
        self._buffer_lock = threading.Lock()
        self._extract_thread: threading.Thread | None = None
        self._shutdown_event = threading.Event()

        # Persist buffer to disk for crash recovery
        self._buffer_dir = Path.home() / ".waystone" / "buffer" / self._project
        self._buffer_dir.mkdir(parents=True, exist_ok=True)
        self._buffer_file = self._buffer_dir / f"{session_id}.txt"
        self._load_persisted_buffer()

        # Last prefetch result (written by background thread, read on next turn)
        self._prefetch_result: str = ""
        self._prefetch_lock = threading.Lock()
        self._prefetch_thread: threading.Thread | None = None

        # Extraction error tracking (for system prompt injection)
        self._last_error: str | None = None
        self._error_lock = threading.Lock()

        # Thread pool for extraction with timeout enforcement
        self._extraction_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="waystone-extract")

        log.info(
            "waystone: initialised — project=%s top_k=%d hops=%d auto_extract=%s",
            self._project, self._top_k, self._hops, self._auto_extract,
        )

    def get_tool_schemas(self) -> list[dict]:
        """Return OpenAI-format tool definitions exposed to Hermes."""
        return [
            {
                "name": "waystone_query",
                "description": (
                    "Search the Waystone knowledge graph for facts relevant to a task or question. "
                    "Returns structured context: decisions, constraints, implementations, and more. "
                    "Use this when you need to recall project-specific facts, past decisions, or "
                    "architectural context."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Natural language description of what you want to recall.",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Max nodes to return (default: provider default).",
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "waystone_recall",
                "description": (
                    "Retrieve all active knowledge graph nodes matching one or more tags. "
                    "Useful for 'show me everything about X' queries where BFS traversal "
                    "isn't needed. Returns raw node list."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Tags to match (OR logic — any match returns the node).",
                        },
                    },
                    "required": ["tags"],
                },
            },
        ]

    # ------------------------------------------------------------------
    # Optional lifecycle hooks
    # ------------------------------------------------------------------

    def system_prompt_block(self) -> str:
        """Inject a brief note into the system prompt about Waystone."""
        if not self._store:
            return ""
        try:
            stats = self._store.get_stats()
            block = (
                f"[Waystone memory: {stats['node_count']} nodes, project '{self._project}'. "
                f"Call waystone_query to search project knowledge.]"
            )
            # Add error warning if extraction failed recently
            with self._error_lock:
                if self._last_error:
                    block += f"\n[Waystone warning: last extraction failed — {self._last_error}. Context may be incomplete.]"
                    self._last_error = None  # Clear after injection (show once)
            return block
        except Exception:
            return "[Waystone memory: active]"

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Retrieve relevant context before the LLM call.

        Returns formatted markdown. Called synchronously but must complete
        within the Hermes 5-second deadline. BFS retrieval is sub-second.

        Keyword-only ``session_id`` to match the MemoryProvider base class.
        """
        if not self._store:
            return ""
        try:
            from waystone.retriever import retrieve
            strategies = self._waystone_config.get("strategies", {})
            result = retrieve(
                self._store,
                query,
                hops=self._hops,
                top_k=self._top_k,
                strategies=strategies,
            )
            return result or ""
        except Exception as e:
            log.warning("waystone: prefetch failed: %s", e)
            return ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """Kick off a background prefetch for the next turn."""
        if not self._store:
            return

        def _run():
            result = self.prefetch(query, session_id=session_id)
            with self._prefetch_lock:
                self._prefetch_result = result

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        with self._prefetch_lock:
            self._prefetch_thread = t

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
    ) -> None:
        """Persist a completed turn. MUST be non-blocking.

        Buffers user + assistant content and triggers background extraction
        when the buffer reaches the flush threshold. Persists buffer to disk.
        """
        if not self._store or not self._auto_extract:
            return

        turn_text = f"Human: {user_content}\n\nAssistant: {assistant_content}"

        with self._buffer_lock:
            should_flush = self._buffer.add(turn_text)
            # Persist buffer after each turn for crash recovery
            self._persist_buffer()
            if should_flush:
                text_to_extract = self._buffer.flush()
            else:
                text_to_extract = None

        if text_to_extract:
            self._spawn_extraction(text_to_extract)

    def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        """Flush remaining buffered turns on session end.

        Signature matches the MemoryProvider base class (required ``messages``).
        Hermes always passes the full history list; we flush our own buffer and
        don't read ``messages`` directly.
        """
        if not self._store or not self._auto_extract:
            return
        with self._buffer_lock:
            text = self._buffer.flush_if_nonempty()
        if text:
            self._spawn_extraction(text)
        # Wait briefly for any in-flight extraction to finish
        if self._extract_thread and self._extract_thread.is_alive():
            self._extract_thread.join(timeout=30)

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
        """Dispatch Waystone tool calls from the LLM.

        Returns a JSON string (per Hermes contract).
        """
        try:
            if tool_name == "waystone_query":
                return self._handle_query(args)
            elif tool_name == "waystone_recall":
                return self._handle_recall(args)
            else:
                return json.dumps({"error": f"Unknown tool: {tool_name}"})
        except Exception as e:
            log.error("waystone: tool call %s failed: %s", tool_name, e)
            return json.dumps({"error": str(e)})

    def shutdown(self) -> None:
        """Clean shutdown: flush buffer and close store."""
        self._shutdown_event.set()
        # Flush remaining buffer
        if self._store and self._auto_extract:
            with self._buffer_lock:
                text = self._buffer.flush_if_nonempty()
            if text:
                self._spawn_extraction(text)
        # Wait for in-flight extraction
        if self._extract_thread and self._extract_thread.is_alive():
            self._extract_thread.join(timeout=15)
        # Shut down thread pool
        if hasattr(self, '_extraction_executor'):
            self._extraction_executor.shutdown(wait=True, timeout=5)
        if self._store:
            try:
                self._store.close()
            except Exception:
                pass

    def get_config_schema(self) -> list[dict]:
        """Config fields for 'hermes memory setup'."""
        return [
            {
                "key": "project",
                "description": "Waystone project name",
                "required": True,
                "secret": False,
                "env_var": "WAYSTONE_PROJECT",
            },
            {
                "key": "db_path",
                "description": "Explicit path to context.db (optional — overrides project lookup)",
                "required": False,
                "secret": False,
                "env_var": "WAYSTONE_DB_PATH",
            },
            {
                "key": "config_path",
                "description": "Path to Waystone config.yaml (optional)",
                "required": False,
                "secret": False,
                "env_var": "WAYSTONE_CONFIG",
            },
            {
                "key": "top_k",
                "description": "Max nodes per retrieval query (default: 10)",
                "required": False,
                "secret": False,
                "env_var": "WAYSTONE_TOP_K",
                "default": 10,
            },
            {
                "key": "hops",
                "description": "BFS traversal depth (default: 3)",
                "required": False,
                "secret": False,
                "env_var": "WAYSTONE_HOPS",
                "default": 3,
            },
            {
                "key": "auto_extract",
                "description": "Extract facts from turns into the graph automatically (default: true)",
                "required": False,
                "secret": False,
                "env_var": "WAYSTONE_EXTRACT",
                "default": True,
            },
        ]

    def save_config(self, values: dict, hermes_home: str) -> None:
        """Write provider config to $HERMES_HOME/waystone.json."""
        config_file = Path(hermes_home) / "waystone.json"
        config_file.write_text(json.dumps(values, indent=2))
        log.info("waystone: config saved to %s", config_file)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_persisted_buffer(self) -> None:
        """Load extraction buffer from disk if session crashed."""
        if self._buffer_file.exists():
            try:
                content = self._buffer_file.read_text()
                if content.strip():
                    # Restore buffer state
                    turns = content.split("\n---TURN_SEP---\n")
                    for turn in turns:
                        if turn.strip():
                            self._buffer.add(turn)
                    log.info("waystone: restored %d buffered turns from %s", len(turns), self._buffer_file)
            except Exception as e:
                log.warning("waystone: failed to restore buffer from %s: %s", self._buffer_file, e)

    def _persist_buffer(self) -> None:
        """Save in-memory buffer to disk."""
        if not hasattr(self, '_buffer_file'):
            return
        try:
            content = "\n---TURN_SEP---\n".join(self._buffer.buffer)
            self._buffer_file.write_text(content)
        except Exception as e:
            log.warning("waystone: failed to persist buffer to %s: %s", self._buffer_file, e)

    def _clear_persisted_buffer(self) -> None:
        """Delete buffer file after successful extraction."""
        if not hasattr(self, '_buffer_file'):
            return
        try:
            if self._buffer_file.exists():
                self._buffer_file.unlink()
        except Exception as e:
            log.warning("waystone: failed to delete buffer file %s: %s", self._buffer_file, e)

    def _handle_query(self, args: dict) -> str:
        query = args.get("query", "")
        top_k = int(args.get("top_k") or self._top_k)
        from waystone.retriever import retrieve
        strategies = self._waystone_config.get("strategies", {})
        result = retrieve(self._store, query, hops=self._hops, top_k=top_k, strategies=strategies)
        return json.dumps({"context": result or "(no results)", "project": self._project})

    def _handle_recall(self, args: dict) -> str:
        tags = args.get("tags", [])
        if not tags:
            return json.dumps({"error": "tags array is required"})
        nodes = self._store.get_nodes_by_tags(tags)[: self._top_k * 3]
        items = [
            {"id": n["id"], "fact": n["fact"], "type": n.get("type"), "confidence": n.get("confidence")}
            for n in nodes
        ]
        return json.dumps({"nodes": items, "count": len(items), "project": self._project})

    def _spawn_extraction(self, text: str) -> None:
        """Spawn extraction with a 30-second timeout."""
        def _run():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(self._do_extract(text))
                finally:
                    loop.close()
            except TimeoutError:
                msg = "extraction timed out after 30s — fact was not stored. Check LLM endpoint."
                log.warning("waystone: %s", msg)
                with self._error_lock:
                    self._last_error = msg
            except Exception as e:
                log.error("waystone: background extraction failed: %s", e)
                with self._error_lock:
                    self._last_error = str(e)[:100]  # Truncate long error messages

        future = self._extraction_executor.submit(_run)
        # Store the thread reference for join() calls; set timeout on the future
        self._extract_thread = threading.Thread(
            target=lambda: self._wait_extraction(future),
            daemon=True,
            name="waystone-extract"
        )
        self._extract_thread.start()

    def _wait_extraction(self, future):
        """Wait for extraction future with a 30-second timeout."""
        try:
            future.result(timeout=30)
        except TimeoutError:
            msg = "extraction timed out after 30s — fact was not stored. Check LLM endpoint."
            log.warning("waystone: %s", msg)
            with self._error_lock:
                self._last_error = msg
        except Exception as e:
            log.error("waystone: extraction failed: %s", e)
            with self._error_lock:
                self._last_error = str(e)[:100]

    async def _do_extract(self, text: str) -> None:
        """Run LLM extraction and merge results into the graph."""
        from waystone.extractor import extract
        from waystone.store import GraphStore

        # Re-open store in this thread (SQLite is not thread-safe across threads)
        db_path = self._resolve_db_path()
        store = GraphStore(db_path)
        try:
            result = await extract(text, self._waystone_config, store=store)
            nodes = result.get("nodes", [])
            edges = result.get("edges", [])
            if nodes:
                store.merge_extraction(nodes, edges)
                log.info("waystone: extracted %d nodes from turn buffer", len(nodes))
                # Clear persisted buffer on successful extraction
                self._clear_persisted_buffer()
        except Exception as e:
            log.warning("waystone: extraction error: %s", e)
            with self._error_lock:
                self._last_error = str(e)[:100]
        finally:
            store.close()


# ---------------------------------------------------------------------------
# Plugin registration entrypoint
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """Called by Hermes plugin loader to register this provider."""
    ctx.register_memory_provider(WaystoneMemoryProvider())
