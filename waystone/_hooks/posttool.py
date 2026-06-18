#!/usr/bin/env python3
"""Claude Code PostToolUse hook for Waystone.

Captures state-changing tool calls during long autonomous runs (plan/auto mode)
where neither UserPromptSubmit nor Stop fires until the very end. Each relevant
tool call is summarized into a per-project buffer; when the buffer crosses a
threshold the buffer is flushed to a background extraction worker, so the graph
fills in *while* the agent works instead of only at the end.

Fast and non-blocking: filters to state-changing tools, buffers to a small JSON
file, and only spawns a detached subprocess on flush. Always exits 0 so it can
never block tool execution.

Extraction is skipped if ~/.waystone/paused exists, or if posttool.enabled is
false in config.
"""

import json
import subprocess
import sys
from pathlib import Path

from .._logging import hook_entry, open_worker_log

STATE_DIR = Path.home() / ".waystone"
PAUSE_FILE = STATE_DIR / "paused"

# Defaults — overridable via the `posttool` config section.
_DEFAULT_TOOLS = ["Write", "Edit", "MultiEdit", "NotebookEdit", "Bash"]
_DEFAULT_MIN_EVENTS = 8
_DEFAULT_MAX_CHARS = 4000
_SNIPPET = 200  # chars of content captured per event


def _detect_project(cwd: str) -> str:
    """Resolve the project name from the nearest .waystone marker, else cwd name."""
    cwd_path = Path(cwd).resolve()
    home = Path.home()
    for directory in [cwd_path, *cwd_path.parents]:
        marker = directory / ".waystone"
        if marker.exists():
            try:
                name = marker.read_text(encoding="utf-8").strip()
                if name:
                    return name
            except Exception:
                pass
        if directory == home:
            break
    return cwd_path.name


def _summarize(tool_name: str, tool_input: dict) -> str:
    """Build a compact, extraction-friendly summary of one tool call."""
    ti = tool_input if isinstance(tool_input, dict) else {}
    if tool_name == "Bash":
        cmd = str(ti.get("command", "")).strip()
        desc = str(ti.get("description", "")).strip()
        line = f"Ran command: {cmd[:_SNIPPET]}"
        return f"{line} ({desc})" if desc else line
    if tool_name in ("Write",):
        path = ti.get("file_path", "")
        content = str(ti.get("content", "")).strip()
        return f"Wrote file {path}: {content[:_SNIPPET]}"
    if tool_name in ("Edit",):
        path = ti.get("file_path", "")
        new = str(ti.get("new_string", "")).strip()
        return f"Edited file {path}: {new[:_SNIPPET]}"
    if tool_name == "MultiEdit":
        path = ti.get("file_path", "")
        n = len(ti.get("edits", []) or [])
        return f"Edited file {path} ({n} edits)"
    if tool_name == "NotebookEdit":
        return f"Edited notebook {ti.get('notebook_path', ti.get('file_path', ''))}"
    return f"Used tool {tool_name}"


@hook_entry
def main() -> None:
    try:
        from waystone._io import force_utf8
        force_utf8()
    except Exception:
        pass
    try:
        hook_input = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    if PAUSE_FILE.exists():
        sys.exit(0)

    tool_name = hook_input.get("tool_name", "")
    cwd = hook_input.get("cwd", ".")
    session_id = hook_input.get("session_id", "")
    if not tool_name:
        sys.exit(0)

    try:
        from waystone.config import get_db_path, load_config

        config = load_config()
        pt_cfg = config.get("posttool", {}) or {}
        if not pt_cfg.get("enabled", True):
            sys.exit(0)

        tools = pt_cfg.get("tools", _DEFAULT_TOOLS)
        if tool_name not in tools:
            sys.exit(0)

        min_events = int(pt_cfg.get("min_events", _DEFAULT_MIN_EVENTS))
        max_chars = int(pt_cfg.get("max_chars", _DEFAULT_MAX_CHARS))

        project = _detect_project(cwd)
        db_path = get_db_path(config, project)
        buf_path = db_path.parent / "tool_buffer.json"

        summary = _summarize(tool_name, hook_input.get("tool_input", {}))
        if not summary.strip():
            sys.exit(0)

        # Load → append → maybe flush. Hooks for a session run serially, so a
        # plain read-modify-write is safe here.
        db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            buffer = json.loads(buf_path.read_text(encoding="utf-8")) if buf_path.exists() else []
            if not isinstance(buffer, list):
                buffer = []
        except Exception:
            buffer = []
        buffer.append(summary)

        total_chars = sum(len(s) for s in buffer)
        if len(buffer) >= min_events or total_chars >= max_chars:
            text = "\n".join(f"- {s}" for s in buffer)
            _spawn_extraction(text, project, db_path, session_id=session_id)
            buffer = []  # clear after flush

        try:
            buf_path.write_text(json.dumps(buffer), encoding="utf-8")
        except Exception:
            pass

    except Exception:
        pass
    sys.exit(0)


def _spawn_extraction(text: str, project: str, db_path: Path, session_id: str = "") -> None:
    """Fire-and-forget: spawn the extraction worker as a detached subprocess."""
    try:
        # Invoke as a module (-m), NOT a bare script path — worker.py's
        # top-level `from .._logging import hook_entry` crashes when run as
        # `python /path/worker.py` (no package context).
        cmd = [
            sys.executable, "-m", "waystone._hooks.worker",
            "--project", project,
            "--db-path", str(db_path),
            "--source", "tooluse",
        ]
        if session_id:
            cmd += ["--session-id", session_id]
        # Capture worker stderr to a log (not DEVNULL) so an import-time/pre-main
        # crash is visible to `waystone doctor` instead of failing silently.
        err = open_worker_log("worker")
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=err,
            start_new_session=True,
        )
        proc.stdin.write(text.encode("utf-8"))
        proc.stdin.close()
        if hasattr(err, "close"):
            err.close()  # child inherited the fd; drop the parent's copy
    except Exception:
        pass


if __name__ == "__main__":
    main()
