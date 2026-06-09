"""Transcript abstraction for loading and slicing conversation history."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass
class Utterance:
    """A single turn in a conversation."""

    role: str  # "user" or "assistant"
    content: str
    ts: str | None = None


def from_claude_jsonl(path: Path) -> list[Utterance]:
    """Read a Claude Code .jsonl session file into Utterances.

    Handles two formats:
    - Flat: top-level role + content fields
    - Nested: message.role + message.content (Claude Code session format)
    """
    utterances = []
    # Always read as UTF-8 (Claude Code session files are UTF-8). Without this,
    # Windows defaults to cp1252 and crashes on bytes like 0x90/0x8f ("'charmap'
    # codec can't decode byte ..."). errors="replace" keeps a stray bad byte from
    # aborting a whole session and avoids introducing surrogate chars.
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        # Claude Code session format: message is nested under obj["message"]
        msg = obj.get("message")
        if isinstance(msg, dict):
            role = msg.get("role", "")
            content = msg.get("content", "")
            ts = obj.get("timestamp") or msg.get("ts")
        else:
            # Flat format
            role = obj.get("role", "")
            content = obj.get("content", "")
            ts = obj.get("ts") or obj.get("timestamp")

        # content can be a list of content blocks
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
            content = "\n".join(parts)

        if role in ("user", "assistant") and content and content.strip():
            utterances.append(Utterance(role=role, content=content, ts=ts))
    return utterances


_SPEAKER_RE = None


def _get_speaker_re():
    global _SPEAKER_RE
    if _SPEAKER_RE is None:
        import re
        # Matches optional ** markdown bold, a capitalized speaker name (1+ words),
        # optional **, then ": ". Captures the speaker name.
        _SPEAKER_RE = re.compile(r"^\*?\*?([A-Z][A-Za-z0-9 ]*)\*?\*?:\s*(.*)")
    return _SPEAKER_RE


def from_plain_text(text: str) -> list[Utterance]:
    """Parse a conversation transcript into Utterances.

    Supports two formats:
    - Human:/Assistant: (or **Human:**/**Assistant:**)
    - Any consistent speaker-labeled format (e.g. "Caroline: ...", "Melanie: ...")
      where each speaker name starts with a capital letter.

    For non-Human/Assistant formats, speakers alternate: the first speaker seen
    maps to "user", the second to "assistant", and so on (alternating).
    """
    import re
    speaker_re = _get_speaker_re()

    # First pass: detect all unique speakers in order of first appearance
    speakers_seen: list[str] = []
    for line in text.splitlines():
        m = speaker_re.match(line)
        if m:
            name = m.group(1).strip().rstrip("*")
            if name not in speakers_seen:
                speakers_seen.append(name)

    # Build role map: Human/Assistant get their literal roles; others alternate user/assistant
    role_map: dict[str, str] = {}
    alternating = ["user", "assistant"]
    alt_idx = 0
    for name in speakers_seen:
        lower = name.lower().replace("*", "")
        if lower == "human":
            role_map[name] = "user"
        elif lower == "assistant":
            role_map[name] = "assistant"
        else:
            role_map[name] = alternating[alt_idx % 2]
            alt_idx += 1

    utterances = []
    current_role = None
    current_speaker = None
    current_lines: list[str] = []

    for line in text.splitlines():
        m = speaker_re.match(line)
        if m:
            name = m.group(1).strip().rstrip("*")
            rest = m.group(2).strip()
            if name in role_map:
                if current_role and current_lines:
                    utterances.append(Utterance(role=current_role, content="\n".join(current_lines).strip()))
                current_speaker = name
                current_role = role_map[name]
                current_lines = [rest] if rest else []
                continue
        current_lines.append(line)

    if current_role and current_lines:
        utterances.append(Utterance(role=current_role, content="\n".join(current_lines).strip()))
    return utterances


def slice_since(utterances: list[Utterance], since_turn: int) -> list[Utterance]:
    """Return utterances from index since_turn onward."""
    return utterances[since_turn:]


def to_prompt_text(utterances: list[Utterance]) -> str:
    """Format utterances as a plain conversation string for LLM input.

    Each turn is labeled "Human:" or "Assistant:" for clarity.
    """
    lines = []
    for u in utterances:
        role_label = "Human" if u.role == "user" else "Assistant"
        lines.append(f"{role_label}: {u.content}")
        lines.append("")
    return "\n".join(lines)


def extract_away_summaries(path: Path) -> list[str]:
    """Return all `away_summary` recap texts from a Claude Code .jsonl session.

    Claude Code persists its native session recaps as
    ``{"type":"system","subtype":"away_summary","content":"Goal… Next…"}``.
    Returned in chronological order — the LAST is the most complete/cumulative.
    UTF-8 with errors="replace" (Windows-safe). Empty list if none.
    """
    summaries: list[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return summaries
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "system" and obj.get("subtype") == "away_summary":
            content = (obj.get("content") or "").strip()
            if content:
                summaries.append(content)
    return summaries


def extract_ai_title(path: Path) -> str:
    """Return the most recent `ai-title` (conversation title) from a session, or "".

    Claude Code emits ``{"type":"ai-title","aiTitle":"<short title>"}`` (key may
    also be ``ai_title``). UTF-8 with errors="replace".
    """
    last_title = ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return last_title
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "ai-title":
            title = obj.get("aiTitle") or obj.get("ai_title")
            if title:
                last_title = str(title).strip()
    return last_title
