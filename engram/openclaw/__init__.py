"""Engram OpenClaw skill — persistent knowledge graph memory for OpenClaw.

Hooks (registered via SKILL.md):
    engram.openclaw:on_session_start
    engram.openclaw:on_turn_end
    engram.openclaw:on_session_end
    engram.openclaw:on_dream

Commands (triggered by @claw <command>):
    remember, recall, forget, summarize, sync_now, status, dream, export

Quick start:
    pip install engram[openclaw]
    export ENGRAM_PROJECT=myproject
    engram init myproject
"""

from .skill import (
    cmd_dream,
    cmd_export,
    cmd_forget,
    cmd_recall,
    cmd_remember,
    cmd_status,
    cmd_summarize,
    cmd_sync_now,
    on_dream,
    on_session_end,
    on_session_start,
    on_turn_end,
)

__all__ = [
    "on_session_start",
    "on_turn_end",
    "on_session_end",
    "on_dream",
    "cmd_remember",
    "cmd_recall",
    "cmd_forget",
    "cmd_summarize",
    "cmd_sync_now",
    "cmd_status",
    "cmd_dream",
    "cmd_export",
]
