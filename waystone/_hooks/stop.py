#!/usr/bin/env python3
"""Claude Code Stop hook for Waystone — transcript recording + maintenance.

After each conversation stop:
  1. Saves a clean markdown transcript to ~/.waystone/transcripts/<project>/.
  2. Spawns background extraction of only the NEW turns since last extraction,
     with 2 prior turns prepended as read-only co-reference context.
  3. If enough new nodes have been added since the last reconcile, spawns background
     `waystone reconcile` to find supersedes relationships.

Per-session extraction state is tracked in
  ~/.waystone/transcripts/<project>/<session_short_id>.state
so that each Stop-hook fire only extracts the delta (not the full growing transcript).

Thresholds (tunable via ~/.waystone/config.yaml under 'maintenance:'):
  reconcile_threshold: 75   # new nodes since last reconcile before triggering
  reconcile_min_total: 100  # minimum total nodes before reconcile makes sense

The saved transcripts can also be re-extracted manually with:
  waystone extract <project> ~/.waystone/transcripts/<project>/<file>.md

Install:
  python hooks/install.py
"""

import html
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path




TRANSCRIPTS_DIR = Path.home() / ".waystone" / "transcripts"

# Number of already-extracted turns to prepend as read-only co-reference context
PRIOR_CONTEXT_TURNS = 2

# Hard cap on new turns sent per extraction call. Prevents runaway cost if the
# .state file is lost/corrupted (last_extracted_idx resets to 0). When there
# are more new turns than this, only the most-recent MAX_DELTA_TURNS are sent
# and state is advanced to match, so older turns are skipped rather than
# re-extracted. Default 50 ≈ ~25 back-and-forth exchanges.
MAX_DELTA_TURNS = 50

# Defaults — overridable via config.yaml under 'maintenance:'
DEFAULT_RECONCILE_THRESHOLD = 75   # new nodes since last reconcile
DEFAULT_RECONCILE_MIN_TOTAL = 100  # minimum graph size before reconcile


def main():
    try:
        from waystone._io import force_utf8
        force_utf8()
    except Exception:
        pass
    try:
        hook_input_raw = sys.stdin.read()
        hook_input = json.loads(hook_input_raw)
    except Exception:
        sys.exit(0)

    # Re-spawn ourselves as a detached background process so the UI is never
    # blocked by transcript saving, DB stats, or extraction spawning.
    # The child sets ENGRAM_STOP_BG=1 so it skips this block and does the work.
    import os
    if not os.environ.get("ENGRAM_STOP_BG"):
        env = {**os.environ, "ENGRAM_STOP_BG": "1"}
        try:
            proc = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve())],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=env,
            )
            proc.stdin.write(hook_input_raw.encode())
            proc.stdin.close()
        except Exception:
            pass
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
        from waystone.config import get_db_path, load_config
        from waystone.store import GraphStore

        config = load_config()
        project = _detect_project(cwd)

        out_dir = TRANSCRIPTS_DIR / project
        out_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        short_id = session_id[:8] if len(session_id) >= 8 else session_id
        out_path = out_dir / f"{timestamp}_{short_id}.md"

        # Parse all turns from the JSONL
        turns = _jsonl_to_turns(jsonl_path)
        if not turns:
            sys.exit(0)

        # Save full transcript (for history and the latest symlink)
        md = _turns_to_markdown(turns)
        out_path.write_text(md, encoding="utf-8")

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

        # --- Incremental extraction: only new turns since last extraction ---
        state_path = out_dir / f"{short_id}.state"
        state = _load_state(state_path)
        last_idx = state.get("last_extracted_idx", 0)

        if last_idx >= len(turns):
            sys.exit(0)  # No new turns to extract

        # Apply hard cap: if state was lost (last_idx=0) and the conversation
        # is huge, only extract the most-recent MAX_DELTA_TURNS turns. Older
        # turns are intentionally skipped — stale info is less valuable than
        # preventing a runaway LLM bill.
        new_turns = turns[last_idx:]
        if len(new_turns) > MAX_DELTA_TURNS:
            skipped = len(new_turns) - MAX_DELTA_TURNS
            last_idx = last_idx + skipped
            new_turns = new_turns[skipped:]

        # Build delta snippet: 2 prior turns (for co-reference context) + new turns
        prior_turns = turns[max(0, last_idx - PRIOR_CONTEXT_TURNS):last_idx]
        snippet = _build_delta_snippet(prior_turns, new_turns)

        # Write delta to a temp file for extraction
        delta_path = out_dir / f"{timestamp}_{short_id}_delta.md"
        delta_path.write_text(snippet, encoding="utf-8")

        # Update state before spawning (prevent double-extraction if hook fires twice)
        state["last_extracted_idx"] = len(turns)
        _save_state(state_path, state)

        # Spawn extraction of the delta only
        _spawn_background_extraction(project, str(delta_path))

        # --- Threshold-triggered reconcile ---
        store = GraphStore(db_path)
        stats = store.get_stats()
        store.close()
        _maybe_spawn_reconcile(project, stats["node_count"], db_path.parent, config)

    except Exception:
        pass  # Never block the session

    sys.exit(0)


# ---------------------------------------------------------------------------
# Channel message sanitization
# ---------------------------------------------------------------------------

_CHANNEL_TAG_RE = re.compile(r'<channel\b[^>]*>(.*?)</channel>', re.DOTALL)


def _strip_channel_wrapper(text: str) -> str:
    """Strip <channel source="..."> wrappers injected by Discord/Telegram plugins.

    Extracts the inner message text and HTML-unescapes it. Handles multiple
    channel blocks in a single turn (rare but possible).
    """
    def _replace(m: re.Match) -> str:
        return html.unescape(m.group(1).strip())

    result = _CHANNEL_TAG_RE.sub(_replace, text)
    # Unescape any stray HTML entities outside channel tags (e.g. 2&gt;/dev/null)
    return html.unescape(result)


# ---------------------------------------------------------------------------
# Turn parsing
# ---------------------------------------------------------------------------

def _jsonl_to_turns(jsonl_path: Path) -> list[tuple[str, str]]:
    """Parse Claude Code JSONL transcript into a list of (role, content) tuples."""
    turns = []
    try:
        raw_lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return turns

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
                turns.append(("user", _strip_channel_wrapper(content.strip())))

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
                turns.append(("assistant", " ".join(text_parts)))

    return turns


def _turns_to_markdown(turns: list[tuple[str, str]]) -> str:
    """Convert turn list to plain markdown."""
    lines = []
    for role, content in turns:
        prefix = "**User**" if role == "user" else "**Assistant**"
        lines.append(f"{prefix}: {content}\n")
    return "\n".join(lines)


def _build_delta_snippet(
    prior_turns: list[tuple[str, str]],
    new_turns: list[tuple[str, str]],
) -> str:
    """Build extraction snippet: prior context header + new turns to extract."""
    parts = []
    if prior_turns:
        parts.append(
            "[Prior context — already extracted. Use only to resolve co-references "
            "in the current segment. Do NOT re-extract facts from this block.]\n"
        )
        parts.append(_turns_to_markdown(prior_turns))
        parts.append("\n\n[Current segment — extract from this:]\n")
    parts.append(_turns_to_markdown(new_turns))
    return "".join(parts)


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def _load_state(state_path: Path) -> dict:
    if state_path.exists():
        try:
            return json.loads(state_path.read_text())
        except Exception:
            pass
    return {"last_extracted_idx": 0}


def _save_state(state_path: Path, state: dict) -> None:
    try:
        state_path.write_text(json.dumps(state))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Background processes
# ---------------------------------------------------------------------------

def _engram_cmd() -> list[str]:
    """Return the best available command prefix to invoke the waystone CLI."""
    import shutil
    cli = shutil.which("waystone")
    if cli:
        return [cli]
    return [sys.executable, "-m", "waystone.cli"]


def _spawn_background_extraction(project: str, transcript_path: str) -> None:
    """Spawn `waystone extract <project> <transcript> --verify` as a detached process."""
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
    """Spawn `waystone reconcile` if enough new nodes have accumulated since last reconcile."""
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


# ---------------------------------------------------------------------------
# Project detection
# ---------------------------------------------------------------------------

def _detect_project(cwd: str) -> str:
    """Find the Waystone project name for this working directory."""
    cwd_path = Path(cwd).resolve()
    home = Path.home()

    for directory in [cwd_path, *cwd_path.parents]:
        marker = directory / ".waystone"
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
