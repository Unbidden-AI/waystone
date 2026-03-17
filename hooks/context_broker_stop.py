#!/usr/bin/env python3
"""Claude Code Stop hook for Context Broker — transcript recording.

After each conversation stop, reads the session JSONL transcript and writes a
clean markdown copy to ~/.context-broker/transcripts/<project>/.

The saved transcripts can be used later with:
  ctx extract <project> ~/.context-broker/transcripts/<project>/<file>.md

Or for incremental per-turn extraction:
  ctx extract-replay <project> ~/.context-broker/transcripts/<project>/<file>.md

Install:
  python hooks/install.py

Manual settings.json entry:
  {
    "hooks": {
      "Stop": [
        {"hooks": [{"type": "command", "command": "python /path/to/hooks/context_broker_stop.py"}]}
      ]
    }
  }
"""

import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

TRANSCRIPTS_DIR = Path.home() / ".context-broker" / "transcripts"


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
        from context_broker.config import get_db_path, load_config

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
        session_state_path = get_db_path(config, project).parent / "session_state.md"
        if session_state_path.exists():
            session_state_path.unlink()

    except Exception:
        pass  # Never block the session

    sys.exit(0)


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
