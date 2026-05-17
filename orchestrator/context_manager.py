"""Context manager — sliding history window, token budget, and proactive compaction."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from waystone.extractor import extract
from waystone.retriever import retrieve_with_stats
from waystone.store import GraphStore

from .llm_adapter import estimate_tokens
from .types import CompactionResult, CompactionTrigger, Message

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


class ContextManager:
    """Maintains conversation history with token budget and proactive compaction.

    Responsibilities
    ----------------
    - Track all messages in the current session with per-message token estimates.
    - Detect when compaction should be triggered (depth, token budget, idle time).
    - Compact the oldest ``compaction_batch`` messages: extract facts → graph, prune history.
    - Retrieve relevant graph context per turn for inclusion in the system prompt.
    """

    def __init__(self, cfg: dict, store: GraphStore, project_name: str, extractor_config: dict):
        """
        Parameters
        ----------
        cfg:
            ``orchestrator.context`` config dict.
        store:
            Open GraphStore for the current project.
        project_name:
            Used only for logging.
        extractor_config:
            Full config dict passed to ``waystone.extractor.extract()``.
            Should include the ``llm`` section from the top-level config.yaml.
        """
        self._cfg = cfg
        self._store = store
        self._project_name = project_name
        self._extractor_cfg = extractor_config

        self._history: list[Message] = []
        self._total_tokens: int = 0
        self._last_activity: float = time.monotonic()
        self._compaction_task: asyncio.Task | None = None

        # Per-config limits
        self._window_size: int = cfg.get("window_size", 20)
        self._token_budget: int = cfg.get("token_budget", 8000)
        self._compaction_batch: int = cfg.get("compaction_batch", 10)
        self._idle_seconds: float = float(cfg.get("idle_seconds_before_compact", 600))
        self._token_trigger_ratio: float = cfg.get("token_trigger_ratio", 0.8)
        self._recent_turns_n: int = cfg.get("recent_turns_n", 2)

        # Retrieval sub-config
        retrieve_cfg = cfg.get("retrieve", {})
        self._retrieve_hops: int = retrieve_cfg.get("hops", 2)
        self._retrieve_top_k: int = retrieve_cfg.get("top_k", 20)
        self._retrieve_strategies: dict = retrieve_cfg.get(
            "strategies",
            {"superseded_pruning": True, "confidence_threshold": 0.6},
        )

    # ------------------------------------------------------------------
    # History management
    # ------------------------------------------------------------------

    def add_message(self, message: Message) -> None:
        """Append a message to the history and update token accounting."""
        self._history.append(message)
        self._total_tokens += message.token_estimate
        self._last_activity = time.monotonic()
        log.debug(
            "ContextManager: added %s message (%d tokens), total=%d tokens, depth=%d",
            message.role,
            message.token_estimate,
            self._total_tokens,
            len(self._history),
        )

    def get_history(self) -> list[Message]:
        """Return the current history window (read-only view)."""
        return list(self._history)

    def total_tokens(self) -> int:
        """Return the sum of token estimates for all messages in history."""
        return self._total_tokens

    def reset(self) -> None:
        """Clear history and reset all accounting state."""
        self._history.clear()
        self._total_tokens = 0
        self._last_activity = time.monotonic()

    def touch(self) -> None:
        """Update the last-activity timestamp (call on any user interaction)."""
        self._last_activity = time.monotonic()

    # ------------------------------------------------------------------
    # Compaction trigger detection
    # ------------------------------------------------------------------

    def should_compact(self) -> CompactionTrigger | None:
        """Return a CompactionTrigger if compaction is warranted, else None.

        Priority: TOKEN_BUDGET > HISTORY_DEPTH > IDLE_TIME.
        """
        if not self._history:
            return None

        # Token budget: compact when history exceeds threshold of budget
        if self._token_budget > 0:
            threshold = int(self._token_budget * self._token_trigger_ratio)
            if self._total_tokens >= threshold:
                log.debug(
                    "TOKEN_BUDGET trigger: %d tokens >= %d threshold",
                    self._total_tokens,
                    threshold,
                )
                return CompactionTrigger.TOKEN_BUDGET

        # History depth: fire as soon as we have a full batch ready.
        # With async extraction there's no reason to wait — the batch can be
        # extracted immediately without blocking the conversation.
        if len(self._history) >= self._compaction_batch:
            log.debug(
                "HISTORY_DEPTH trigger: %d messages >= %d compaction_batch",
                len(self._history),
                self._compaction_batch,
            )
            return CompactionTrigger.HISTORY_DEPTH

        # Idle time
        if self._idle_seconds > 0:
            idle = time.monotonic() - self._last_activity
            if idle >= self._idle_seconds:
                log.debug("IDLE_TIME trigger: %.0fs idle >= %.0fs threshold", idle, self._idle_seconds)
                return CompactionTrigger.IDLE_TIME

        return None

    # ------------------------------------------------------------------
    # Compaction
    # ------------------------------------------------------------------

    async def compact(self, trigger: CompactionTrigger) -> CompactionResult:
        """Extract facts from the oldest messages, merge into graph, prune history.

        Blocking coroutine — awaits extraction before returning.  Used directly
        in tests and for idle-triggered compaction (where latency is acceptable).

        For the non-blocking production path, use ``compact_if_needed()``.
        """
        batch_size = _safe_batch_size(self._history, min(self._compaction_batch, len(self._history)))
        batch = self._history[:batch_size]

        transcript_parts: list[str] = []
        for msg in batch:
            if msg.role == "system":
                continue
            transcript_parts.append(f"{msg.role.capitalize()}: {msg.content}")
        transcript_text = "\n\n".join(transcript_parts)
        tokens_freed = sum(m.token_estimate for m in batch)

        nodes_extracted = 0
        if transcript_text.strip():
            try:
                log.info(
                    "Compacting %d messages (%d tokens) → graph [trigger=%s]",
                    batch_size,
                    tokens_freed,
                    trigger.value,
                )
                extraction = await extract(transcript_text, self._extractor_cfg)
                nodes_extracted = len(extraction.get("nodes", []))
                self._store.merge_extraction(
                    extraction.get("nodes", []),
                    extraction.get("edges", []),
                )
                log.info(
                    "Compaction complete: %d nodes extracted, %d edges",
                    nodes_extracted,
                    len(extraction.get("edges", [])),
                )
            except Exception as e:
                log.error("Compaction extraction failed: %s — history pruned anyway", e)

        # Remove compacted messages from history
        self._history = self._history[batch_size:]
        self._total_tokens = sum(m.token_estimate for m in self._history)

        return CompactionResult(
            nodes_extracted=nodes_extracted,
            messages_removed=batch_size,
            tokens_freed=tokens_freed,
            trigger=trigger,
        )

    async def compact_if_needed(self) -> CompactionResult | None:
        """Check triggers and compact if warranted.

        History is pruned *synchronously* so the LLM call is never blocked.
        Extraction (the slow part) runs as a background ``asyncio.Task`` — the
        graph is updated a few seconds later without delaying the current turn.

        Returns a ``CompactionResult`` immediately (``nodes_extracted=0`` because
        extraction is still in flight).  Returns ``None`` if no compaction needed.
        """
        trigger = self.should_compact()
        if trigger is None:
            return None

        # If a previous compaction is still extracting, skip — don't pile up tasks.
        if self._compaction_task and not self._compaction_task.done():
            log.debug("Compaction already in progress, skipping this trigger")
            return None

        batch_size = _safe_batch_size(self._history, min(self._compaction_batch, len(self._history)))
        batch = self._history[:batch_size]

        transcript_parts: list[str] = []
        for msg in batch:
            if msg.role == "system":
                continue
            transcript_parts.append(f"{msg.role.capitalize()}: {msg.content}")
        transcript_text = "\n\n".join(transcript_parts)
        tokens_freed = sum(m.token_estimate for m in batch)

        # Prune history immediately — LLM call proceeds without waiting for extraction
        self._history = self._history[batch_size:]
        self._total_tokens = sum(m.token_estimate for m in self._history)

        log.info(
            "Compaction: pruned %d messages (%d tokens) [trigger=%s]; extraction in background",
            batch_size,
            tokens_freed,
            trigger.value,
        )

        if transcript_text.strip():
            self._compaction_task = asyncio.create_task(
                self._extract_and_merge(transcript_text, trigger)
            )

        return CompactionResult(
            nodes_extracted=0,  # unknown until background task completes
            messages_removed=batch_size,
            tokens_freed=tokens_freed,
            trigger=trigger,
        )

    async def _extract_and_merge(self, transcript_text: str, trigger: CompactionTrigger) -> None:
        """Background coroutine: extract facts and merge into the graph store."""
        try:
            extraction = await extract(transcript_text, self._extractor_cfg)
            nodes = extraction.get("nodes", [])
            edges = extraction.get("edges", [])
            self._store.merge_extraction(nodes, edges)
            log.info(
                "Background compaction complete: %d nodes, %d edges [trigger=%s]",
                len(nodes),
                len(edges),
                trigger.value,
            )
        except Exception as e:
            log.error("Background compaction extraction failed: %s", e)

    # ------------------------------------------------------------------
    # Per-turn graph retrieval
    # ------------------------------------------------------------------

    def retrieve_context(self, task_description: str) -> str:
        """Retrieve relevant graph context for the given task.

        Returns a markdown string suitable for injection into the system prompt.
        Returns empty string if no relevant nodes are found.
        """
        if not task_description.strip():
            return ""

        context_token_limit: int = self._cfg.get("context_token_limit", 2000)
        strategies = {
            **self._retrieve_strategies,
            "token_budget": context_token_limit,
        }

        try:
            result = retrieve_with_stats(
                self._store,
                task_description,
                hops=self._retrieve_hops,
                top_k=self._retrieve_top_k,
                strategies=strategies,
            )
            log.debug(
                "Context retrieval: %d→%d nodes, ~%d tokens",
                result.nodes_before_strategies,
                result.nodes_after_strategies,
                result.tokens_estimated,
            )
            return result.markdown
        except Exception as e:
            log.error("Context retrieval failed: %s", e)
            return ""

    def get_recent_turns_markdown(self, n: int | None = None) -> str:
        """Return the last *n* complete user+assistant turn pairs as markdown.

        Used as a "hot cache" in the system prompt so that facts from the most
        recent turns are always visible even if they haven't been extracted to
        the graph yet (e.g. immediately after async compaction).

        Parameters
        ----------
        n:
            Number of turn pairs to include.  Defaults to ``recent_turns_n``
            from config (default 2).

        Returns
        -------
        str
            Markdown section, or empty string if no complete turns exist.
        """
        count = n if n is not None else self._recent_turns_n
        if count <= 0 or not self._history:
            return ""

        # Walk backwards collecting (user, assistant) pairs
        pairs: list[tuple[Message, Message]] = []
        i = len(self._history) - 1
        while i >= 0 and len(pairs) < count:
            if self._history[i].role == "assistant":
                # Find the preceding user message
                j = i - 1
                while j >= 0 and self._history[j].role != "user":
                    j -= 1
                if j >= 0:
                    pairs.insert(0, (self._history[j], self._history[i]))
                    i = j - 1
                else:
                    i -= 1
            else:
                i -= 1

        if not pairs:
            return ""

        lines = ["## Recent Turns\n"]
        for user_msg, asst_msg in pairs:
            lines.append(f"**User:** {user_msg.content}\n")
            lines.append(f"**Assistant:** {asst_msg.content}\n")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        """Return a snapshot of current state for logging/debugging."""
        return {
            "project": self._project_name,
            "history_depth": len(self._history),
            "total_tokens": self._total_tokens,
            "token_budget": self._token_budget,
            "idle_seconds": time.monotonic() - self._last_activity,
            "window_size": self._window_size,
        }


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _safe_batch_size(history: list[Message], target: int) -> int:
    """Return the largest cut index <= target that doesn't orphan tool responses.

    Compaction slices ``history[:k]``. If ``k`` lands between an assistant
    message with tool_calls and its subsequent tool-response messages, the tool
    messages remain in history without their matching assistant message — causing
    Gemini (and other providers) to reject the conversation with
    "Missing corresponding tool call for tool response message".

    This function walks forward grouping tool-call exchanges into atomic units
    (assistant-with-tool-calls + all following tool messages) and returns the
    largest safe cut that fits within *target*.
    """
    if target <= 0:
        return 0
    if target >= len(history):
        return len(history)

    i = 0
    safe = 0
    while i < target:
        msg = history[i]
        if msg.role == "assistant" and getattr(msg, "raw_tool_calls", None):
            # Atomic group: this assistant message + all consecutive tool responses
            j = i + 1
            while j < len(history) and history[j].role == "tool":
                j += 1
            group_end = j
        else:
            group_end = i + 1

        if group_end <= target:
            safe = group_end
            i = group_end
        else:
            # Including this group would exceed target — stop here
            break

    return safe
