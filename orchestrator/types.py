"""Shared data classes for the Context Broker orchestrator."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


@dataclass
class Message:
    """A single conversation turn."""

    role: str  # "user", "assistant", "system", "tool"
    content: str
    timestamp: float = field(default_factory=time.time)
    token_estimate: int = 0
    tool_call_id: str | None = None  # set on role="tool" result messages
    # Raw OpenAI-format tool_calls array — preserved on assistant messages that
    # initiated tool calls so the API history remains well-formed for providers
    # (e.g. Gemini) that require matching tool_call_ids between assistant and
    # tool-result messages.
    raw_tool_calls: list[dict] | None = None

    def __post_init__(self) -> None:
        if self.token_estimate == 0:
            self.token_estimate = max(1, len(self.content) // 4)

    def to_api_dict(self) -> dict:
        """Convert to OpenAI-format message dict for API calls."""
        d: dict = {"role": self.role, "content": self.content or ""}
        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id
        if self.raw_tool_calls is not None:
            d["tool_calls"] = self.raw_tool_calls
        return d


@dataclass
class ToolCall:
    """A tool invocation returned by the LLM — intent only, not yet executed."""

    id: str
    name: str
    args: dict


@dataclass
class ToolResult:
    """The result of executing a ToolCall locally."""

    tool_call_id: str
    name: str
    output: str
    error: str | None = None

    def to_message(self) -> Message:
        """Convert to a tool-role Message for inclusion in the conversation."""
        content = self.output if self.error is None else f"Error: {self.error}\n{self.output}"
        return Message(role="tool", content=content, tool_call_id=self.tool_call_id)


class CompactionTrigger(Enum):
    """Reason a compaction was triggered."""

    HISTORY_DEPTH = "history_depth"  # too many turns in history
    TOKEN_BUDGET = "token_budget"    # approaching token limit
    IDLE_TIME = "idle_time"          # user inactivity threshold crossed


@dataclass
class CompactionResult:
    """Summary of a completed compaction operation."""

    nodes_extracted: int
    messages_removed: int
    tokens_freed: int
    trigger: CompactionTrigger
