"""MCP server for Context Broker.

Exposes context retrieval and extraction as MCP tools so Claude Code
(and any MCP-compatible client) can call them directly without hook setup.

Usage:
    ctx mcp-serve                   # run on stdio (for Claude Code)
    ctx mcp-serve --transport sse   # run as HTTP SSE server
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import FastMCP

from .config import get_db_path, load_config
from .extractor import extract, score_extraction_quality, verify_extraction
from .retriever import retrieve_with_stats
from .store import GraphStore

mcp = FastMCP(name="context-broker")

_MAX_EXTRACT_CHARS = 200_000  # ~50k tokens — hard cap on MCP extract input

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_project_marker(start: Path | None = None) -> str | None:
    """Walk up from start (or CWD) looking for a .context-broker file."""
    path = (start or Path.cwd()).resolve()
    for candidate in [path, *path.parents]:
        marker = candidate / ".context-broker"
        if marker.exists():
            return marker.read_text().strip()
    return None


def _resolve_project(project: str | None, cwd: str | None = None) -> str:
    """Return project name, auto-detecting from .context-broker if not given.

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
        "No project specified and no .context-broker marker found. "
        "Pass project= explicitly, or pass cwd= (your working directory) "
        "so the server can locate the correct .context-broker marker. "
        "Run 'ctx hook-init <project>' to create one."
    )


def _load_config() -> dict:
    return load_config(None)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def context_broker_query(
    task: Annotated[str, "Task description or question to retrieve context for"],
    project: Annotated[str | None, "Project name (auto-detected from .context-broker if omitted)"] = None,
    cwd: Annotated[str | None, "Your working directory (pass this when auto-detecting project, so the server resolves the correct .context-broker marker)"] = None,
    hops: Annotated[int, "Graph traversal depth (default 3)"] = 3,
    top_k: Annotated[int, "Max nodes to return (default 25)"] = 25,
) -> str:
    """Retrieve relevant context from the project knowledge graph for a given task.

    Returns a markdown block of the most relevant decisions, constraints,
    and implementation details extracted from past conversations.
    """
    project_name = _resolve_project(project, cwd)  # raises ValueError → MCP error response

    config = _load_config()
    db_path = get_db_path(config, project_name)

    if not db_path.exists():
        raise FileNotFoundError(
            f"No graph found for project '{project_name}'. "
            f"Extract a transcript first: ctx extract {project_name} <transcript_file>"
        )

    store = GraphStore(db_path)
    stats = store.get_stats()
    if stats["node_count"] == 0:
        store.close()
        raise RuntimeError(f"Graph for '{project_name}' is empty. Extract a transcript first.")

    defaults = config.get("defaults", {})
    strategies = dict(config.get("strategies", {}))

    result = retrieve_with_stats(
        store, task,
        hops=hops,
        top_k=top_k or defaults.get("top_k", 25),
        strategies=strategies,
    )
    store.close()
    return result.markdown


@mcp.tool()
def context_broker_extract(
    text: Annotated[str, "Text to extract facts from (transcript, spec, notes, etc.)"],
    project: Annotated[str | None, "Project name (auto-detected from .context-broker if omitted)"] = None,
    cwd: Annotated[str | None, "Your working directory (pass this when auto-detecting project)"] = None,
    source_name: Annotated[str, "Label for this text (shown in node source info)"] = "mcp_extract",
    verify: Annotated[bool, "Run a second verification pass to catch missed facts"] = False,
) -> str:
    """Extract facts from text and merge them into the project knowledge graph.

    The text can be a conversation transcript, a project spec, meeting notes,
    or any document containing decisions, constraints, or implementation details.

    Returns a summary of what was extracted.
    """
    if len(text) > _MAX_EXTRACT_CHARS:
        raise ValueError(
            f"Input is {len(text):,} chars — exceeds the {_MAX_EXTRACT_CHARS:,}-char limit. "
            "Split the text into smaller pieces and call this tool multiple times."
        )

    project_name = _resolve_project(project, cwd)

    config = _load_config()
    db_path = get_db_path(config, project_name)

    if not db_path.parent.exists():
        raise FileNotFoundError(
            f"Project '{project_name}' not found. "
            f"Initialize it first: ctx init {project_name}"
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
            except Exception:
                pass  # Verification failure is non-fatal; continue with pass 1

        now = datetime.now(timezone.utc).isoformat()
        for node in nodes:
            node["source_transcript"] = source_name
            node.setdefault("created_at", now)

        store = GraphStore(db_path)
        store.merge_extraction(nodes, edges)
        store.close()

        quality = score_extraction_quality(nodes, edges, text)
        return (
            f"Extracted {len(nodes)} nodes, {len(edges)} edges into '{project_name}'.\n"
            f"Density: {quality['nodes_per_1k_chars']}/1kc  "
            f"Avg tags: {quality['avg_tags_per_node']}  "
            f"Edge/node: {quality['edge_to_node_ratio']}"
        )

    return asyncio.run(_run())


@mcp.tool()
def context_broker_stats(
    project: Annotated[str | None, "Project name (auto-detected if omitted)"] = None,
    cwd: Annotated[str | None, "Your working directory (pass this when auto-detecting project)"] = None,
) -> str:
    """Return node and edge counts for the project knowledge graph.

    Use this to check whether a project has been populated before querying.
    """
    project_name = _resolve_project(project, cwd)

    config = _load_config()
    db_path = get_db_path(config, project_name)

    if not db_path.exists():
        raise FileNotFoundError(f"No graph found for project '{project_name}'.")

    store = GraphStore(db_path)
    stats = store.get_stats()
    store.close()

    lines = [
        f"Project: {project_name}",
        f"Nodes: {stats['node_count']}  Edges: {stats['edge_count']}",
    ]
    if stats.get("type_counts"):
        lines.append("By type: " + ", ".join(
            f"{t}={c}" for t, c in sorted(stats["type_counts"].items())
        ))
    return "\n".join(lines)


@mcp.tool()
def context_broker_list_projects() -> str:
    """List all available Context Broker projects on this machine."""
    config = _load_config()
    projects_dir = Path(config.get("projects_dir", Path.home() / ".context-broker" / "projects"))

    if not projects_dir.exists():
        return "No projects found. Run 'ctx init <project>' to create one."

    projects = [
        d.name for d in sorted(projects_dir.iterdir())
        if d.is_dir() and (d / "context.db").exists()
    ]

    if not projects:
        return "No projects found. Run 'ctx init <project>' to create one."

    lines = [f"Available projects ({len(projects)}):"]
    for name in projects:
        db_path = get_db_path(config, name)
        store = GraphStore(db_path)
        stats = store.get_stats()
        store.close()
        lines.append(f"  {name}  ({stats['node_count']} nodes, {stats['edge_count']} edges)")
    return "\n".join(lines)


def run_server(transport: str = "stdio") -> None:
    """Start the MCP server."""
    mcp.run(transport=transport)
