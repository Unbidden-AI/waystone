"""MCP server for Waystone.

Exposes context retrieval and extraction as MCP tools so Claude Code
(and any MCP-compatible client) can call them directly without hook setup.

Usage:
    waystone mcp-serve                   # run on stdio (for Claude Code)
    waystone mcp-serve --transport sse   # run as HTTP SSE server
"""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import FastMCP

from .config import get_db_path, is_remote, load_config, make_remote_client
from .extractor import extract, score_extraction_quality, verify_extraction
from .monitoring import init_sentry
from .retriever import retrieve_with_stats
from .store import GraphStore

log = logging.getLogger(__name__)

mcp = FastMCP(name="waystone")

_MAX_EXTRACT_CHARS = 200_000  # ~50k tokens — hard cap on MCP extract input

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _setup_logging() -> None:
    """Set up structured logging to file with rotation."""
    log_dir = Path.home() / ".waystone" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "mcp_server.log"
    handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,  # 5MB
        backupCount=3,
    )
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.DEBUG)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_project_marker(start: Path | None = None) -> str | None:
    """Walk up from start (or CWD) looking for a .waystone file."""
    path = (start or Path.cwd()).resolve()
    for candidate in [path, *path.parents]:
        marker = candidate / ".waystone"
        # Must be a FILE: ~/.waystone is the config DIRECTORY, not a project
        # marker. Reading a dir as text raises PermissionError on Windows /
        # IsADirectoryError on POSIX — so guard the read too (fail-soft).
        if marker.is_file():
            try:
                return marker.read_text(encoding="utf-8").strip()
            except OSError:
                continue
    return None


def _resolve_project(project: str | None, cwd: str | None = None) -> str:
    """Return project name, auto-detecting from .waystone if not given.

    cwd should be the caller's working directory (not the MCP server's CWD).
    When running multiple Claude Code instances, pass cwd= to avoid all
    instances resolving to the same project via the server's own CWD.
    """
    if project:
        return project
    start = Path(cwd).resolve() if cwd else None
    detected = _find_project_marker(start)
    if detected:
        return detected
    raise ValueError(
        "No project specified and no .waystone marker found. "
        "Pass project= explicitly, or pass cwd= (your working directory) "
        "so the server can locate the correct .waystone marker. "
        "Run 'waystone init <project>' to create one."
    )


def _load_config() -> dict:
    return load_config(None)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def waystone_query(
    task: Annotated[str, "Task description or question to retrieve context for"],
    project: Annotated[str | None, "Project name (auto-detected from .waystone if omitted)"] = None,
    cwd: Annotated[str | None, "Your working directory (pass this when auto-detecting project, so the server resolves the correct .waystone marker)"] = None,
    hops: Annotated[int, "Graph traversal depth (default 3)"] = 3,
    top_k: Annotated[int, "Max nodes to return (default 25)"] = 25,
) -> str:
    """Retrieve relevant context from the project knowledge graph for a given task.

    Returns a markdown block of the most relevant decisions, constraints,
    and implementation details extracted from past conversations.
    """
    try:
        project_name = _resolve_project(project, cwd)  # raises ValueError → MCP error response

        config = _load_config()

        if is_remote(config):
            client = make_remote_client(config)
            result = asyncio.run(client.query(project_name, task, hops=hops, top_k=top_k))
            log.debug(f"waystone_query: project={project_name}, task_length={len(task)}, remote=true")
            return result["markdown"] or f"(graph is empty for '{project_name}')"

        db_path = get_db_path(config, project_name)

        if not db_path.exists():
            raise FileNotFoundError(
                f"No graph found for project '{project_name}'. "
                f"Extract a transcript first: waystone extract {project_name} <transcript_file>"
            )

        store = GraphStore(db_path)
        try:
            stats = store.get_stats()
            if stats["node_count"] == 0:
                raise RuntimeError(f"Graph for '{project_name}' is empty. Extract a transcript first.")

            defaults = config.get("defaults", {})
            strategies = dict(config.get("strategies", {}))

            # Fall back to config defaults if user didn't pass explicit values
            effective_hops = hops if hops != 3 else defaults.get("hops", hops)
            effective_top_k = top_k if top_k != 25 else defaults.get("top_k", top_k)

            result = retrieve_with_stats(
                store, task,
                hops=effective_hops,
                top_k=effective_top_k,
                strategies=strategies,
            )
            log.debug(f"waystone_query: project={project_name}, nodes_returned={result.nodes_after_strategies}, tokens={result.tokens_estimated}")
        finally:
            store.close()
        return result.markdown
    except (ValueError, FileNotFoundError, RuntimeError):
        raise  # already user-friendly
    except Exception as e:
        log.error(f"waystone_query failed: {e}", exc_info=True)
        try:
            import sentry_sdk
            if sentry_sdk.is_initialized():
                sentry_sdk.capture_exception(e)
        except ImportError:
            pass
        raise ValueError(f"AI API call failed — check your API key and quota: {e}") from e


@mcp.tool()
def waystone_extract(
    text: Annotated[str, "Text to extract facts from (transcript, spec, notes, etc.)"],
    project: Annotated[str | None, "Project name (auto-detected from .waystone if omitted)"] = None,
    cwd: Annotated[str | None, "Your working directory (pass this when auto-detecting project)"] = None,
    source_name: Annotated[str, "Label for this text (shown in node source info)"] = "mcp_extract",
    verify: Annotated[bool, "Run a second verification pass to catch missed facts"] = False,
) -> str:
    """Extract facts from text and merge them into the project knowledge graph.

    The text can be a conversation transcript, a project spec, meeting notes,
    or any document containing decisions, constraints, or implementation details.

    Returns a summary of what was extracted.
    """
    try:
        if len(text) > _MAX_EXTRACT_CHARS:
            raise ValueError(
                f"Input is {len(text):,} chars — exceeds the {_MAX_EXTRACT_CHARS:,}-char limit. "
                "Split the text into smaller pieces and call this tool multiple times."
            )

        project_name = _resolve_project(project, cwd)

        config = _load_config()

        if is_remote(config):
            client = make_remote_client(config)
            result = asyncio.run(
                client.extract(project_name, text, source_name=source_name, verify=verify)
            )
            log.debug(f"waystone_extract: project={project_name}, nodes={result['nodes_extracted']}, edges={result['edges_extracted']}, remote=true")
            return (
                f"Extracted {result['nodes_extracted']} nodes, {result['edges_extracted']} edges "
                f"into '{project_name}'.\n"
                f"Density: {result['nodes_per_1k_chars']}/1kc  "
                f"Avg tags: {result['avg_tags_per_node']}"
            )

        db_path = get_db_path(config, project_name)

        if not db_path.parent.exists():
            raise FileNotFoundError(
                f"Project '{project_name}' not found. "
                f"Initialize it first: waystone init {project_name}"
            )

        async def _run() -> str:
            result = await extract(text, config)  # raises on LLM error → propagates as MCP error

            nodes = result["nodes"]
            edges = result["edges"]

            if verify:
                try:
                    v_result = await verify_extraction(text, nodes, config)
                    nodes = nodes + v_result["nodes"]
                    edges = edges + v_result["edges"]
                except Exception as _ve:
                    log.warning("Verification pass failed (non-fatal): %s", _ve, exc_info=True)

            now = datetime.now(timezone.utc).isoformat()
            for node in nodes:
                node["source_transcript"] = source_name
                node.setdefault("created_at", now)

            store = GraphStore(db_path)
            try:
                store.merge_extraction(nodes, edges)
            finally:
                store.close()

            quality = score_extraction_quality(nodes, edges, text)
            return (
                f"Extracted {len(nodes)} nodes, {len(edges)} edges into '{project_name}'.\n"
                f"Density: {quality['nodes_per_1k_chars']}/1kc  "
                f"Avg tags: {quality['avg_tags_per_node']}  "
                f"Edge/node: {quality['edge_to_node_ratio']}"
            )

        return asyncio.run(_run())
    except (ValueError, FileNotFoundError, RuntimeError):
        raise  # already user-friendly
    except Exception as e:
        log.error(f"waystone_extract failed: {e}", exc_info=True)
        try:
            import sentry_sdk
            if sentry_sdk.is_initialized():
                sentry_sdk.capture_exception(e)
        except ImportError:
            pass
        raise ValueError(f"AI API call failed — check your API key and quota: {e}") from e


@mcp.tool()
def waystone_stats(
    project: Annotated[str | None, "Project name (auto-detected if omitted)"] = None,
    cwd: Annotated[str | None, "Your working directory (pass this when auto-detecting project)"] = None,
) -> str:
    """Return node and edge counts for the project knowledge graph.

    Use this to check whether a project has been populated before querying.
    """
    try:
        project_name = _resolve_project(project, cwd)

        config = _load_config()

        if is_remote(config):
            client = make_remote_client(config)
            stats = asyncio.run(client.get_stats(project_name))
            log.debug(f"waystone_stats: project={project_name}, nodes={stats['node_count']}, edges={stats['edge_count']}, remote=true")
            lines = [
                f"Project: {project_name}",
                f"Nodes: {stats['node_count']}  Edges: {stats['edge_count']}",
            ]
            if stats.get("type_counts"):
                lines.append("By type: " + ", ".join(
                    f"{t}={c}" for t, c in sorted(stats["type_counts"].items())
                ))
            return "\n".join(lines)

        db_path = get_db_path(config, project_name)

        if not db_path.exists():
            raise FileNotFoundError(f"No graph found for project '{project_name}'.")

        # Counts only — no semantic search, so skip the sqlite-vec extension.
        # On a cold Windows box the first `import sqlite_vec` can stall for
        # minutes (Defender scanning the native binary); stats must never pay it.
        store = GraphStore(db_path, vec_enabled=False)
        stats = store.get_stats()
        store.close()

        log.debug(f"waystone_stats: project={project_name}, nodes={stats['node_count']}, edges={stats['edge_count']}")
        lines = [
            f"Project: {project_name}",
            f"Nodes: {stats['node_count']}  Edges: {stats['edge_count']}",
        ]
        if stats.get("type_counts"):
            lines.append("By type: " + ", ".join(
                f"{t}={c}" for t, c in sorted(stats["type_counts"].items())
            ))
        return "\n".join(lines)
    except (ValueError, FileNotFoundError):
        raise  # already user-friendly
    except Exception as e:
        log.error(f"waystone_stats failed: {e}", exc_info=True)
        try:
            import sentry_sdk
            if sentry_sdk.is_initialized():
                sentry_sdk.capture_exception(e)
        except ImportError:
            pass
        raise ValueError(f"Stats retrieval failed: {e}") from e


@mcp.tool()
def waystone_list_projects() -> str:
    """List all available Waystone projects on this machine."""
    try:
        config = _load_config()

        if is_remote(config):
            client = make_remote_client(config)
            projects = asyncio.run(client.list_projects())
            log.debug(f"waystone_list_projects: project_count={len(projects)}, remote=true")
            if not projects:
                return "No projects found on remote server."
            lines = [f"Available projects ({len(projects)}):"]
            for p in projects:
                lines.append(f"  {p['name']}  ({p['node_count']} nodes, {p['edge_count']} edges)")
            return "\n".join(lines)

        projects_dir = Path(config.get("projects_dir", Path.home() / ".waystone" / "projects"))

        if not projects_dir.exists():
            return "No projects found. Run 'waystone init <project>' to create one."

        projects = [
            d.name for d in sorted(projects_dir.iterdir())
            if d.is_dir() and (d / "context.db").exists()
        ]

        if not projects:
            return "No projects found. Run 'waystone init <project>' to create one."

        log.debug(f"waystone_list_projects: project_count={len(projects)}")
        lines = [f"Available projects ({len(projects)}):"]
        for name in projects:
            db_path = get_db_path(config, name)
            store = GraphStore(db_path, vec_enabled=False)  # counts only — skip sqlite-vec
            stats = store.get_stats()
            store.close()
            lines.append(f"  {name}  ({stats['node_count']} nodes, {stats['edge_count']} edges)")
        return "\n".join(lines)
    except Exception as e:
        log.error(f"waystone_list_projects failed: {e}", exc_info=True)
        try:
            import sentry_sdk
            if sentry_sdk.is_initialized():
                sentry_sdk.capture_exception(e)
        except ImportError:
            pass
        raise ValueError(f"Project listing failed: {e}") from e


def run_server(transport: str = "stdio") -> None:
    """Start the MCP server."""
    _setup_logging()
    init_sentry()
    # Warm the sqlite-vec native extension off the critical path: the first cold
    # load can stall (OS scanning the binary on a fresh install, esp. Windows).
    # Doing it in a background daemon thread means the first semantic query
    # doesn't pay it. Counts-only tools (stats/list) already skip sqlite-vec.
    import threading

    from .store import prewarm_sqlite_vec
    threading.Thread(target=prewarm_sqlite_vec, name="prewarm-sqlite-vec", daemon=True).start()
    mcp.run(transport=transport)
