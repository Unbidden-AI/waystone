#!/usr/bin/env python3
"""Claude Code SessionEnd hook for Waystone.

Auto-pipes Claude Code "Auto Memory" files — the per-project markdown facts
Claude writes under ``~/.claude/projects/<slug>/memory/`` — into the durable
Waystone graph when a session ends, so the machine-local scratchpad consolidates
into the brain.

Design:
  - ``waystone auto-import PROJECT DIRECTORY`` is idempotent (keeps its own
    (path, mtime) manifest and skips unchanged files), so NO external watermark
    is needed — re-running on every session end only ever imports new/changed
    files.
  - Scoped to the curated Auto Memory dir ONLY. Full transcripts are ingested by
    the live extraction worker; importing them here would double-extract.
  - Runs the import as a DETACHED subprocess so session teardown is never
    blocked and the per-call embedding-model load stays off the critical path.
  - Fails open: missing binary / missing memory dir / unresolved project / pause
    file all exit 0 silently. A hook must never break a session.

Skipped if ~/.waystone/paused exists, or if ``import_memory.enabled`` is false
in config.
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

STATE_DIR = Path.home() / ".waystone"
PAUSE_FILE = STATE_DIR / "paused"


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


def _memory_dir(cwd: str) -> Path:
    """Map a working directory to its Claude Code Auto Memory dir.

    Claude Code encodes the project path by replacing every non-alphanumeric
    character with '-', e.g. /Users/jane/Apps/Foo -> -Users-jane-Apps-Foo.
    """
    slug = re.sub(r"[^a-zA-Z0-9]", "-", str(Path(cwd).resolve()))
    return Path.home() / ".claude" / "projects" / slug / "memory"


def _spawn_import(project: str, mem_dir: Path) -> None:
    """Fire-and-forget: detached `waystone auto-import <project> <mem_dir>`."""
    exe = shutil.which("waystone")
    if exe:
        cmd = [exe, "auto-import", project, str(mem_dir)]
    else:
        # Editable/dev installs may lack a regenerated console script; invoke the
        # Click group directly with the same interpreter (PATH-independent).
        cmd = [
            sys.executable, "-c", "from waystone.cli import cli; cli()",
            "auto-import", project, str(mem_dir),
        ]

    log_dir = STATE_DIR / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log = open(log_dir / "automem-import.log", "ab")  # noqa: SIM115 (child owns it)
    except Exception:
        log = subprocess.DEVNULL

    popen_kwargs: dict = {"stdout": log, "stderr": subprocess.STDOUT,
                          "stdin": subprocess.DEVNULL}
    if sys.platform == "win32":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP — outlive the parent.
        popen_kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        popen_kwargs["start_new_session"] = True

    try:
        subprocess.Popen(cmd, **popen_kwargs)
    except Exception:
        pass


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

    cwd = hook_input.get("cwd") or "."

    try:
        from waystone.config import load_config

        config = load_config()
        im_cfg = config.get("import_memory", {}) or {}
        if not im_cfg.get("enabled", True):
            sys.exit(0)

        mem_dir = _memory_dir(cwd)
        if not mem_dir.is_dir():
            sys.exit(0)  # no Auto Memory written for this project — nothing to do

        project = _detect_project(cwd)
        _spawn_import(project, mem_dir)
    except Exception:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
