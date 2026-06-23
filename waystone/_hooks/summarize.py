#!/usr/bin/env python3
"""Background session-summary worker for Waystone.

Spawned by the Stop hook as a detached subprocess when the summary cadence is
reached (every N turns). Generates an incremental rolling summary (prior summary
+ new turns -> updated summary) and stores it as a ``session_summary`` node that
SUPERSEDES the prior one for the session.

Supersession keeps history: the prior summary is marked inactive (is_active=0,
valid_to set) but is NEVER deleted, so the full timeline is preserved and can be
replayed as the project's story (see ``waystone story``).

Fully background, fail-open: wraps everything, never raises. Per-session turn
state lives in ~/.waystone/summary_state/<session_id>.json.

Usage (internal -- called by the Stop hook):
  waystone-hook-summarize --project <name> --db-path <path> \
      --session-id <id> --transcript-path <path>
"""

import argparse
import asyncio
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .._logging import hook_entry, open_worker_log
from . import detached_popen_kwargs

try:
    from waystone._io import force_utf8
    force_utf8()
except Exception:
    pass

STATE_DIR = Path.home() / ".waystone"
PAUSE_FILE = STATE_DIR / "paused"

# Default recent-turns window fed to the summarizer each pass (overridable via
# config `session_summary.context_turns`). The prior summary carries older context
# forward, so this stays bounded regardless of session length.
SUMMARY_CONTEXT_TURNS = 30


@hook_entry
def main():
    """Entry point for the detached summary worker."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--session-id", default="")
    parser.add_argument("--transcript-path", default="")
    args = parser.parse_args()

    try:
        from waystone.config import load_config
        from waystone.extractor import generate_session_summary
        from waystone.store import GraphStore

        config = load_config()
        db_path = Path(args.db_path)
        session_id = args.session_id or "unknown"

        if PAUSE_FILE.exists():
            return

        if not args.transcript_path:
            return
        transcript_path = Path(args.transcript_path).expanduser()
        if not transcript_path.exists():
            return

        # Parse turns from the saved transcript markdown.
        turns_text = transcript_path.read_text(encoding="utf-8", errors="replace")
        turns = _parse_markdown_turns(turns_text)
        if not turns:
            return

        n_context = (config.get("session_summary", {}) or {}).get(
            "context_turns", SUMMARY_CONTEXT_TURNS)
        context_turns = _extract_last_n_turns(turns, n_context)
        new_turns_text = _turns_to_text(context_turns)
        if not new_turns_text.strip():
            return

        # Carry the prior summary forward (incremental rolling summary).
        store = GraphStore(db_path)
        prior_summary_node = _find_prior_summary(store, session_id)
        prior_summary = prior_summary_node.get("fact", "") if prior_summary_node else ""
        store.close()

        generated_summary = asyncio.run(
            generate_session_summary(new_turns_text, prior_summary, config)
        )
        if not generated_summary:
            return

        # Store the new summary, superseding the prior one (history preserved).
        store = GraphStore(db_path)
        supersedes = [prior_summary_node["id"]] if prior_summary_node else []
        now = datetime.now(timezone.utc).isoformat()
        store.add_node({
            "id": f"n_{uuid.uuid4().hex[:8]}",
            "fact": generated_summary,
            "type": "session_summary",
            "source_transcript": f"live_session:{session_id}",
            "confidence": 1.0,
            "tags": ["rolling", "session"],
            "created_at": now,
            "occurred_at": now,
            "supersedes": supersedes,
        })
        store.close()

        # Reset the counter so the next summary waits another cadence window.
        _reset_summary_counter(_summary_state_path(session_id))

    except Exception:
        pass  # Fail silently; never block the session.


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def _summary_state_path(session_id: str) -> Path:
    """Path to the per-session summary state file."""
    return STATE_DIR / "summary_state" / f"{session_id}.json"


def _load_summary_state(state_path: Path) -> dict:
    """Load summary state from disk; default if missing/unreadable."""
    if state_path.exists():
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"turns_since_last_summary": 0}


def _save_summary_state(state_path: Path, state: dict) -> None:
    """Persist summary state, best-effort."""
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass


def _increment_summary_counter(state_path: Path) -> None:
    """Increment the per-session turn counter."""
    state = _load_summary_state(state_path)
    state["turns_since_last_summary"] = state.get("turns_since_last_summary", 0) + 1
    _save_summary_state(state_path, state)


def _reset_summary_counter(state_path: Path) -> None:
    """Reset the turn counter to 0 (after a summary is spawned/generated)."""
    state = _load_summary_state(state_path)
    state["turns_since_last_summary"] = 0
    _save_summary_state(state_path, state)


# ---------------------------------------------------------------------------
# Turn parsing
# ---------------------------------------------------------------------------

def _parse_markdown_turns(markdown_text: str) -> list[tuple[str, str]]:
    """Parse a saved transcript into (role, content) tuples.

    Matches the Stop hook's ``_turns_to_markdown`` format:
      **User**: <content>
      **Assistant**: <content>
    """
    turns = []
    for raw in markdown_text.split("\n"):
        line = raw.strip()
        if line.startswith("**User**:"):
            turns.append(("user", line[len("**User**:"):].strip()))
        elif line.startswith("**Assistant**:"):
            turns.append(("assistant", line[len("**Assistant**:"):].strip()))
    return turns


def _extract_last_n_turns(turns: list[tuple[str, str]], n: int) -> list[tuple[str, str]]:
    """Return the last N turns (or all of them if fewer)."""
    if n <= 0 or not turns:
        return []
    return turns[-n:] if len(turns) > n else turns


def _turns_to_text(turns: list[tuple[str, str]]) -> str:
    """Render turns as plain ``Role: content`` lines."""
    parts = []
    for role, content in turns:
        prefix = "User" if role == "user" else "Assistant"
        parts.append(f"{prefix}: {content}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Database queries
# ---------------------------------------------------------------------------

def _find_prior_summary(store, session_id: str) -> dict | None:
    """Return the most recent ACTIVE session_summary for this session, or None."""
    try:
        nodes = store.get_nodes_by_type("session_summary")
        matching = [
            n for n in nodes
            if n.get("is_active") == 1
            and n.get("source_transcript") == f"live_session:{session_id}"
        ]
        if matching:
            return sorted(matching, key=lambda n: n.get("created_at", ""), reverse=True)[0]
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Public trigger helpers (called by the Stop hook)
# ---------------------------------------------------------------------------

def should_trigger_summary(
    state_path: Path,
    config: dict,
    pause_file: Path = PAUSE_FILE,
) -> bool:
    """True only if enabled, not paused, and the counter has reached cadence."""
    session_cfg = config.get("session_summary", {})
    if not session_cfg.get("enabled", True):
        return False
    if pause_file.exists():
        return False
    state = _load_summary_state(state_path)
    cadence = session_cfg.get("cadence_turns", 10)
    return state.get("turns_since_last_summary", 0) >= cadence


def spawn_background_summary(project: str, db_path: str, session_id: str,
                            transcript_path: str) -> None:
    """Spawn a detached summary worker (non-blocking, fail-open)."""
    try:
        import shutil
        cli = shutil.which("waystone-hook-summarize")
        if cli:
            args = []
        else:
            cli = sys.executable
            args = ["-m", "waystone._hooks.summarize"]

        # Capture stderr to a log (not DEVNULL) so a summarize crash — the path
        # behind a frozen session narrative — is visible to `waystone doctor`.
        err = open_worker_log("summarize")
        subprocess.Popen(
            [cli] + args + [
                "--project", project,
                "--db-path", db_path,
                "--session-id", session_id,
                "--transcript-path", transcript_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=err,
            **detached_popen_kwargs(),
        )
        if hasattr(err, "close"):
            err.close()  # child inherited the fd; drop the parent's copy
    except Exception:
        pass


if __name__ == "__main__":
    main()
