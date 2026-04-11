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
    for line in path.read_text().splitlines():
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


def from_plain_text(text: str) -> list[Utterance]:
    """Parse a simple Human:/Assistant: markdown transcript into Utterances.

    Lines starting with "Human:" or "**Human:**" mark user turns.
    Lines starting with "Assistant:" or "**Assistant:**" mark assistant turns.
    """
    utterances = []
    current_role = None
    current_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("**Human:**") or line.startswith("Human:"):
            if current_role:
                utterances.append(Utterance(role=current_role, content="\n".join(current_lines).strip()))
            current_role = "user"
            current_lines = [line.split(":", 1)[-1].strip()]
        elif line.startswith("**Assistant:**") or line.startswith("Assistant:"):
            if current_role:
                utterances.append(Utterance(role=current_role, content="\n".join(current_lines).strip()))
            current_role = "assistant"
            current_lines = [line.split(":", 1)[-1].strip()]
        else:
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
