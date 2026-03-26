#!/usr/bin/env python3
"""Claude Code Stop hook for Context Broker — transcript recording + maintenance.

After each conversation stop:
  1. Saves a clean markdown transcript to ~/.context-broker/transcripts/<project>/.
  2. Spawns background extraction of the saved transcript (full-quality, with --verify).
  3. If enough new nodes have been added since the last reconcile, spawns background
     `engram reconcile` to find supersedes relationships.

Thresholds (tunable via ~/.context-broker/config.yaml under 'maintenance:'):
  reconcile_threshold: 75   # new nodes since last reconcile before triggering
  reconcile_min_total: 100  # minimum total nodes before reconcile makes sense

The saved transcripts can also be re-extracted manually with:
  engram extract <project> ~/.context-broker/transcripts/<project>/<file>.md

Install:
  python hooks/install.py
"""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

TRANSCRIPTS_DIR = Path.home() / ".context-broker" / "transcripts"

# Defaults — overridable via config.yaml under 'maintenance:'
DEFAULT_RECONCILE_THRESHOLD = 75   # new nodes since last reconcile
DEFAULT_RECONCILE_MIN_TOTAL = 100  # minimum graph size before reconcile


def main():
    try:
        hook_input = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    transcript_path = hook_input.get("transcript_path", "")
    cwd = hook_input.get("cwd", ".")
    session_id = hook_input.get("session_id", "unknown")

    if not transcript_path:
        sys.exit(0)

    jsonl_path = Path(transcript_path).expanduser()
    if not jsonl_path.exists():
        sys.exit(0)

    try:
        from engram.config import get_db_path, load_config
        from engram.store import GraphStore

        config = load_config()
        project = _detect_project(cwd)

        out_dir = TRANSCRIPTS_DIR / project
        out_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        short_id = session_id[:8] if len(session_id) >= 8 else session_id
        out_path = out_dir / f"{timestamp}_{short_id}.md"

        md = _jsonl_to_markdown(jsonl_path)
        if not md.strip():
            sys.exit(0)

        out_path.write_text(md)

        # Update the symlink to always point to the latest transcript
        latest_link = out_dir / "latest.md"
        if latest_link.exists() or latest_link.is_symlink():
            latest_link.unlink()
        latest_link.symlink_to(out_path.name)

        # Clear session state — LLM extraction will have processed it by next session
        db_path = get_db_path(config, project)
        session_state_path = db_path.parent / "session_state.md"
        if session_state_path.exists():
            session_state_path.unlink()

        # --- Background extraction of this transcript ---
        _spawn_background_extraction(project, str(out_path))

        # --- Threshold-triggered reconcile ---
        store = GraphStore(db_path)
        stats = store.get_stats()
        store.close()
        _maybe_spawn_reconcile(project, stats["node_count"], db_path.parent, config)

    except Exception:
        pass  # Never block the session

    sys.exit(0)


def _engram_cmd() -> list[str]:
    """Return the best available command prefix to invoke the engram CLI."""
    import shutil
    cli = shutil.which("engram")
    if cli:
        return [cli]
    return [sys.executable, "-m", "engram.cli"]


def _spawn_background_extraction(project: str, transcript_path: str) -> None:
    """Spawn `engram extract <project> <transcript> --verify` as a detached process."""
    try:
        subprocess.Popen(
            _engram_cmd() + ["extract", project, transcript_path, "--verify"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


def _maybe_spawn_reconcile(project: str, current_nodes: int, project_dir: Path, config: dict) -> None:
    """Spawn `engram reconcile` if enough new nodes have accumulated since last reconcile."""
    maint_cfg = config.get("maintenance", {})
    threshold = int(maint_cfg.get("reconcile_threshold", DEFAULT_RECONCILE_THRESHOLD))
    min_total = int(maint_cfg.get("reconcile_min_total", DEFAULT_RECONCILE_MIN_TOTAL))

    if current_nodes < min_total:
        return

    state_path = project_dir / "maintenance.json"
    state: dict = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
        except Exception:
            pass

    last_reconcile_nodes = int(state.get("last_reconcile_nodes", 0))
    delta = current_nodes - last_reconcile_nodes

    if delta < threshold:
        return

    # Update state before spawning (don't double-trigger if hook runs twice)
    state["last_reconcile_nodes"] = current_nodes
    state["last_reconcile_at"] = datetime.now().isoformat()
    try:
        state_path.write_text(json.dumps(state))
    except Exception:
        pass

    try:
        subprocess.Popen(
            _engram_cmd() + ["reconcile", project],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


def _jsonl_to_markdown(jsonl_path: Path) -> str:
    """Convert a Claude Code JSONL transcript to plain markdown."""
    lines = []
    try:
        raw_lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return ""

    for raw in raw_lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except Exception:
            continue

        entry_type = entry.get("type")
        message = entry.get("message", {})
        role = message.get("role", "")

        # User turn
        if entry_type == "user" and role == "user":
            content = message.get("content", "")
            if isinstance(content, str) and content.strip():
                lines.append(f"**User**: {content.strip()}\n")

        # Assistant turn
        elif role == "assistant":
            content = message.get("content", [])
            text_parts = []
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "").strip()
                        if text:
                            text_parts.append(text)
            elif isinstance(content, str) and content.strip():
                text_parts.append(content.strip())

            if text_parts:
                lines.append(f"**Assistant**: {' '.join(text_parts)}\n")

    return "\n".join(lines)


def _detect_project(cwd: str) -> str:
    """Find the Context Broker project name for this working directory."""
    cwd_path = Path(cwd).resolve()
    home = Path.home()

    for directory in [cwd_path, *cwd_path.parents]:
        marker = directory / ".context-broker"
        if marker.exists():
            try:
                name = marker.read_text().strip()
                if name:
                    return name
            except Exception:
                pass
        if directory == home:
            break

    return cwd_path.name


if __name__ == "__main__":
    main()
