"""Context manager — sliding history window, token budget, and proactive compaction."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from context_broker.extractor import extract
from context_broker.retriever import retrieve_with_stats
from context_broker.store import GraphStore

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
            Full config dict passed to ``context_broker.extractor.extract()``.
            Should include the ``llm`` section from the top-level config.yaml.
        """
        self._cfg = cfg
        self._store = store
        self._project_name = project_name
        self._extractor_cfg = extractor_config

        self._history: list[Message] = []
        self._total_tokens: int = 0
        self._last_activity: float = time.monotonic()

        # Per-config limits
        self._window_size: int = cfg.get("window_size", 20)
        self._token_budget: int = cfg.get("token_budget", 8000)
        self._compaction_batch: int = cfg.get("compaction_batch", 10)
        self._idle_seconds: float = float(cfg.get("idle_seconds_before_compact", 600))
        self._token_trigger_ratio: float = cfg.get("token_trigger_ratio", 0.8)

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

        # History depth
        if len(self._history) >= self._window_size:
            log.debug(
                "HISTORY_DEPTH trigger: %d messages >= %d window_size",
                len(self._history),
                self._window_size,
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

        Extracts the oldest ``compaction_batch`` messages (or all if fewer remain).
        System messages are skipped (not compacted) but counted toward the batch
        so that they don't permanently clog the front of history.

        Returns
        -------
        CompactionResult
            Summary of what was extracted and removed.
        """
        batch_size = min(self._compaction_batch, len(self._history))
        batch = self._history[:batch_size]

        # Build transcript text from the batch (skip system messages for extraction)
        transcript_parts: list[str] = []
        for msg in batch:
            if msg.role == "system":
                continue
            role_label = msg.role.capitalize()
            transcript_parts.append(f"{role_label}: {msg.content}")

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
                # Extraction failure must not block the conversation
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
        """Check triggers and compact if warranted. Returns result or None."""
        trigger = self.should_compact()
        if trigger is None:
            return None
        return await self.compact(trigger)

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
