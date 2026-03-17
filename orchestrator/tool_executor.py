"""Tool executor — runs LLM-requested tools locally with sandbox enforcement."""

from __future__ import annotations

import asyncio
import glob as _glob
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from .types import ToolCall, ToolResult

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
# Dispatcher
# ---------------------------------------------------------------------------

_HANDLERS = {
    "bash": _run_bash,
    "read_file": _run_read_file,
    "write_file": _run_write_file,
    "glob": _run_glob,
    "grep": _run_grep,
}


async def execute_tool(tool_call: ToolCall, cfg: dict) -> ToolResult:
    """Execute a single ToolCall and return a ToolResult.

    Parameters
    ----------
    tool_call:
        The parsed tool invocation from the LLM.
    cfg:
        ``orchestrator.tools`` config dict (sandbox_root, max_output_chars, …).

    Returns
    -------
    ToolResult
        Always returns a result; errors are captured in ``result.error``.
    """
    handler = _HANDLERS.get(tool_call.name)
    if handler is None:
        return ToolResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            output="",
            error=f"Unknown tool: {tool_call.name!r}",
        )

    enabled: list[str] = cfg.get("enabled", list(_HANDLERS.keys()))
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


async def execute_tools(tool_calls: list[ToolCall], cfg: dict) -> list[ToolResult]:
    """Execute multiple ToolCalls concurrently and return results in order."""
    return list(await asyncio.gather(*[execute_tool(tc, cfg) for tc in tool_calls]))
