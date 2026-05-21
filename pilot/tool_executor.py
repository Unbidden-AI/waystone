"""Tool executor — runs LLM-requested tools locally with sandbox enforcement."""

from __future__ import annotations

import asyncio
import glob as _glob
import logging
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .types import ToolCall, ToolResult

if TYPE_CHECKING:
    from waystone.store import GraphStore

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sandbox helper
# ---------------------------------------------------------------------------


def _resolve_and_check(path: str, sandbox_root: Path) -> Path:
    """Resolve *path* and raise if it escapes *sandbox_root*.

    Raises
    ------
    PermissionError
        If the resolved path is outside the sandbox.
    """
    # resolve() follows all symlinks — the resulting path is always the final
    # on-disk target, so relative_to() correctly catches any symlink-based
    # traversal attempts out of the sandbox.
    resolved = Path(path).expanduser().resolve()
    try:
        resolved.relative_to(sandbox_root)
    except ValueError:
        raise PermissionError("Access denied: path is outside allowed directory")
    return resolved


# ---------------------------------------------------------------------------
# Individual tool implementations
# ---------------------------------------------------------------------------


async def _run_bash(args: dict[str, Any], cfg: dict) -> str:
    """Execute a shell command inside the sandbox root.

    Security note: this tool intentionally runs arbitrary shell commands — it
    is a developer tool, not a user-facing one.  The sandbox constrains the
    working directory (cwd=sandbox_root) but does not restrict shell
    metacharacters or command content.  Only enable this tool for trusted
    LLM-controlled agents operating on known codebases.
    """
    command = args.get("command")
    if not command or not isinstance(command, str):
        raise ValueError("bash requires a 'command' string argument")

    timeout: int = int(args.get("timeout", cfg.get("bash_timeout", 30)))
    sandbox_root = Path(cfg.get("sandbox_root", ".")).resolve()
    max_output = int(cfg.get("max_output_chars", 10_000))

    log.debug("bash: running command (timeout=%ds): %s", timeout, command[:120])

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(sandbox_root),
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return f"[bash timed out after {timeout}s]"
    except Exception as e:
        log.debug("bash: subprocess launch failed: %s", e)
        return f"[bash error: {type(e).__name__}]"

    output = stdout.decode("utf-8", errors="replace")
    if len(output) > max_output:
        output = output[:max_output] + f"\n[output truncated at {max_output} chars]"
    return output


async def _run_read_file(args: dict[str, Any], cfg: dict) -> str:
    """Read a file within the sandbox."""
    path_str = args.get("path")
    if not path_str or not isinstance(path_str, str):
        raise ValueError("read_file requires a 'path' string argument")

    sandbox_root = Path(cfg.get("sandbox_root", ".")).resolve()
    max_output = int(cfg.get("max_output_chars", 10_000))

    resolved = _resolve_and_check(path_str, sandbox_root)
    if not resolved.exists():
        return "[read_file error: file not found]"
    if not resolved.is_file():
        return "[read_file error: path is not a file]"

    try:
        content = resolved.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"[read_file error: {type(e).__name__}]"

    if len(content) > max_output:
        content = content[:max_output] + f"\n[content truncated at {max_output} chars]"
    return content


async def _run_write_file(args: dict[str, Any], cfg: dict) -> str:
    """Write content to a file within the sandbox."""
    path_str = args.get("path")
    content = args.get("content")

    if not path_str or not isinstance(path_str, str):
        raise ValueError("write_file requires a 'path' string argument")
    if content is None or not isinstance(content, str):
        raise ValueError("write_file requires a 'content' string argument")

    sandbox_root = Path(cfg.get("sandbox_root", ".")).resolve()
    resolved = _resolve_and_check(path_str, sandbox_root)

    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
    log.debug("write_file: wrote %d chars to %s", len(content), resolved)
    return f"[write_file: wrote {len(content)} chars]"


async def _run_glob(args: dict[str, Any], cfg: dict) -> str:
    """Find files matching a glob pattern within the sandbox."""
    pattern = args.get("pattern")
    if not pattern or not isinstance(pattern, str):
        raise ValueError("glob requires a 'pattern' string argument")

    sandbox_root = Path(cfg.get("sandbox_root", ".")).resolve()
    root_str = args.get("root")
    if root_str:
        search_root = _resolve_and_check(root_str, sandbox_root)
    else:
        search_root = sandbox_root

    # Reject patterns with path traversal sequences
    if ".." in pattern or pattern.startswith("/") or "\x00" in pattern:
        raise ValueError("glob: pattern must not contain '..', absolute paths, or null bytes")

    # Ensure the glob is rooted inside the sandbox
    full_pattern = str(search_root / pattern)
    matches = _glob.glob(full_pattern, recursive=True)

    # Filter to sandbox and make relative for readability
    results: list[str] = []
    for m in sorted(matches):
        mp = Path(m).resolve()
        try:
            rel = mp.relative_to(sandbox_root)
            results.append(str(rel))
        except ValueError:
            pass  # outside sandbox — skip silently

    if not results:
        return "[glob: no matches]"
    return "\n".join(results)


async def _run_grep(args: dict[str, Any], cfg: dict) -> str:
    """Search file contents for a regex pattern within the sandbox."""
    pattern = args.get("pattern")
    if not pattern or not isinstance(pattern, str):
        raise ValueError("grep requires a 'pattern' string argument")

    # Validate regex before running
    try:
        re.compile(pattern)
    except re.error as e:
        raise ValueError(f"grep: invalid regex {pattern!r}: {e}") from e

    sandbox_root = Path(cfg.get("sandbox_root", ".")).resolve()
    max_output = int(cfg.get("max_output_chars", 10_000))

    path_str = args.get("path", str(sandbox_root))
    search_path = _resolve_and_check(path_str, sandbox_root)

    raw_filter = args.get("glob", "*")
    # Whitelist: only alphanumeric, dots, underscores, hyphens, braces, commas, wildcards
    if not re.fullmatch(r"[\w\-.*?,{}]+", raw_filter):
        raise ValueError(f"grep: invalid glob filter {raw_filter!r} — only alphanumeric, dots, wildcards allowed")
    glob_filter = raw_filter
    context_lines = int(args.get("context_lines", 0))

    rg_args = ["rg", "--no-heading", "-n", pattern, str(search_path)]
    if glob_filter and glob_filter != "*":
        rg_args.extend(["--glob", glob_filter])
    if context_lines > 0:
        rg_args.extend(["-C", str(context_lines)])

    # Fallback to grep if rg not available
    safe_include = f"*.{glob_filter.lstrip('*').lstrip('.')}" if glob_filter != "*" else None
    grep_args = ["grep", "-r", "-n", pattern, str(search_path)]
    if safe_include:
        grep_args.extend(["--include", safe_include])
    if context_lines > 0:
        grep_args.extend([f"-C{context_lines}"])

    for i, cmd in enumerate((rg_args, grep_args)):
        is_primary = i == 0  # rg is primary; grep is fallback
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(sandbox_root),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            if proc.returncode == 1 and not stdout:
                return "[grep: no matches]"
            if proc.returncode not in (0, 1):
                # Tool not found or other error — try fallback
                if is_primary:
                    continue
                return f"[grep error: {stderr.decode('utf-8', errors='replace').strip()}]"
            output = stdout.decode("utf-8", errors="replace")
            if len(output) > max_output:
                output = output[:max_output] + f"\n[output truncated at {max_output} chars]"
            return output or "[grep: no matches]"
        except (FileNotFoundError, asyncio.TimeoutError):
            if is_primary:
                continue  # try grep fallback
            return "[grep error: neither rg nor grep available, or timed out]"

    return "[grep error: no search tool available]"


# ---------------------------------------------------------------------------
# Graph-store write tools (require a live GraphStore reference)
# ---------------------------------------------------------------------------


async def _run_ctx_delete_node(args: dict[str, Any], cfg: dict, store: "GraphStore") -> str:
    """Delete a node and all its edges from the project knowledge graph."""
    node_id = args.get("node_id")
    if not node_id or not isinstance(node_id, str):
        raise ValueError("ctx_delete_node requires a 'node_id' string argument")

    node = store.get_node(node_id)
    if node is None:
        return f"[ctx_delete_node: node {node_id!r} not found]"

    fact_preview = node["fact"][:80] + ("…" if len(node["fact"]) > 80 else "")
    deleted = store.delete_node(node_id)
    if deleted:
        log.info("ctx_delete_node: deleted %s — %s", node_id, fact_preview)
        return f"Deleted node {node_id}: {fact_preview!r}"
    return f"[ctx_delete_node: failed to delete {node_id!r}]"


async def _run_ctx_update_node(args: dict[str, Any], cfg: dict, store: "GraphStore") -> str:
    """Update a node's fact text in the knowledge graph."""
    node_id = args.get("node_id")
    new_fact = args.get("new_fact")
    if not node_id or not isinstance(node_id, str):
        raise ValueError("ctx_update_node requires a 'node_id' string argument")
    if not new_fact or not isinstance(new_fact, str):
        raise ValueError("ctx_update_node requires a 'new_fact' string argument")

    new_confidence = args.get("new_confidence")
    if new_confidence is not None:
        new_confidence = float(new_confidence)

    updated = store.update_node_fact(node_id, new_fact, new_confidence)
    if updated:
        log.info("ctx_update_node: updated %s", node_id)
        return f"Updated node {node_id}: {new_fact[:80]!r}"
    return f"[ctx_update_node: node {node_id!r} not found]"


async def _run_ctx_graph_stats(args: dict[str, Any], cfg: dict, store: "GraphStore") -> str:
    """Return a summary of the current graph database state."""
    stats = store.get_stats()

    superseded_count = store.conn.execute(
        "SELECT COUNT(DISTINCT to_id) FROM edges WHERE relation = 'supersedes'"
    ).fetchone()[0]

    recent_rows = store.conn.execute(
        "SELECT fact, type, created_at FROM nodes ORDER BY created_at DESC LIMIT 5"
    ).fetchall()

    source_count = store.conn.execute(
        "SELECT COUNT(DISTINCT source_transcript) FROM nodes "
        "WHERE source_transcript IS NOT NULL AND source_transcript != '__synthesis__'"
    ).fetchone()[0]

    lines = [
        f"Nodes: {stats['node_count']}  |  Edges: {stats['edge_count']}  |  Sources: {source_count}",
        f"Superseded (prunable): {superseded_count}",
        "",
        "Node types:",
    ]
    for ntype, cnt in sorted(stats["type_counts"].items(), key=lambda x: -x[1]):
        lines.append(f"  {ntype}: {cnt}")

    if recent_rows:
        lines.append("")
        lines.append("Most recently added nodes:")
        for row in recent_rows:
            fact_preview = row["fact"][:80] + ("…" if len(row["fact"]) > 80 else "")
            lines.append(f"  [{row['type']}] {fact_preview}  ({row['created_at'][:19]})")

    return "\n".join(lines)


async def _run_ctx_list_sources(args: dict[str, Any], cfg: dict, store: "GraphStore") -> str:
    """List distinct source transcripts/files that have been extracted into the graph."""
    rows = store.conn.execute(
        "SELECT DISTINCT source_transcript, COUNT(*) as cnt "
        "FROM nodes WHERE source_transcript IS NOT NULL AND source_transcript != '__synthesis__' "
        "GROUP BY source_transcript ORDER BY source_transcript"
    ).fetchall()
    if not rows:
        return "No sources found in the graph."
    lines = [f"{row[0]}  ({row[1]} nodes)" for row in rows]
    return "\n".join(lines)


async def _run_ctx_synthesize(args: dict[str, Any], cfg: dict, store: "GraphStore") -> str:
    """Create a synthesis node summarizing multiple existing nodes."""
    summary_fact = args.get("summary_fact")
    node_ids = args.get("node_ids")
    if not summary_fact or not isinstance(summary_fact, str):
        raise ValueError("ctx_synthesize requires a 'summary_fact' string argument")
    if not node_ids or not isinstance(node_ids, list):
        raise ValueError("ctx_synthesize requires a 'node_ids' list argument")

    node_type = args.get("node_type", "decision")
    tags = args.get("tags", [])
    confidence = float(args.get("confidence", 0.9))

    new_id = f"n_{uuid.uuid4().hex[:8]}"
    node = {
        "id": new_id,
        "fact": summary_fact,
        "type": node_type,
        "confidence": confidence,
        "tags": tags,
        "source_transcript": "__synthesis__",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "supersedes": [],
    }
    canonical_id = store.add_node(node)
    for src_id in node_ids:
        if store.get_node(src_id) is not None:
            store.add_edge(canonical_id, src_id, "relates_to")

    log.info("ctx_synthesize: created %s from %d source nodes", canonical_id, len(node_ids))
    return f"Created synthesis node {canonical_id} (relates_to {len(node_ids)} nodes): {summary_fact[:80]!r}"


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_HANDLERS = {
    "bash": _run_bash,
    "read_file": _run_read_file,
    "write_file": _run_write_file,
    "glob": _run_glob,
    "grep": _run_grep,
}

_STORE_HANDLERS = {
    "ctx_delete_node": _run_ctx_delete_node,
    "ctx_update_node": _run_ctx_update_node,
    "ctx_synthesize": _run_ctx_synthesize,
    "ctx_list_sources": _run_ctx_list_sources,
    "ctx_graph_stats": _run_ctx_graph_stats,
}


async def execute_tool(tool_call: ToolCall, cfg: dict, store: "GraphStore | None" = None) -> ToolResult:
    """Execute a single ToolCall and return a ToolResult.

    Parameters
    ----------
    tool_call:
        The parsed tool invocation from the LLM.
    cfg:
        ``pilot.tools`` config dict (sandbox_root, max_output_chars, …).

    Returns
    -------
    ToolResult
        Always returns a result; errors are captured in ``result.error``.
    """
    # Check store-aware handlers first
    if tool_call.name in _STORE_HANDLERS:
        enabled: list[str] = cfg.get("enabled", [])
        if tool_call.name not in enabled:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                output="",
                error=f"Tool {tool_call.name!r} is not enabled in this session",
            )
        if store is None:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                output="",
                error=f"Tool {tool_call.name!r} requires a graph store but none was provided",
            )
        try:
            output = await _STORE_HANDLERS[tool_call.name](tool_call.args, cfg, store)
            return ToolResult(tool_call_id=tool_call.id, name=tool_call.name, output=output)
        except ValueError as e:
            return ToolResult(tool_call_id=tool_call.id, name=tool_call.name, output="", error=str(e))
        except Exception as e:
            log.error("Unexpected error in store tool %s: %s", tool_call.name, e)
            return ToolResult(
                tool_call_id=tool_call.id, name=tool_call.name, output="",
                error=f"Internal error: {type(e).__name__}: {e}",
            )

    handler = _HANDLERS.get(tool_call.name)
    if handler is None:
        return ToolResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            output="",
            error=f"Unknown tool: {tool_call.name!r}",
        )

    enabled = cfg.get("enabled", list(_HANDLERS.keys()))
    if tool_call.name not in enabled:
        return ToolResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            output="",
            error=f"Tool {tool_call.name!r} is not enabled in this session",
        )

    try:
        output = await handler(tool_call.args, cfg)
        return ToolResult(tool_call_id=tool_call.id, name=tool_call.name, output=output)
    except PermissionError as e:
        log.warning("Sandbox violation in %s: %s", tool_call.name, e)
        return ToolResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            output="",
            error=f"Permission denied: {e}",
        )
    except ValueError as e:
        return ToolResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            output="",
            error=str(e),
        )
    except Exception as e:
        log.error("Unexpected error in tool %s: %s", tool_call.name, e)
        return ToolResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            output="",
            error=f"Internal error: {type(e).__name__}: {e}",
        )


async def execute_tools(
    tool_calls: list[ToolCall], cfg: dict, store: "GraphStore | None" = None
) -> list[ToolResult]:
    """Execute multiple ToolCalls concurrently and return results in order."""
    return list(await asyncio.gather(*[execute_tool(tc, cfg, store) for tc in tool_calls]))
