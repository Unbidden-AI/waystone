"""Conversation — the main turn-by-turn loop tying all orchestrator modules together."""

from __future__ import annotations

import logging
from typing import AsyncIterator

from context_broker.store import GraphStore

from .context_manager import ContextManager
from .llm_adapter import build_tool_schemas, call_llm
from .system_prompt_builder import SystemPromptBuilder
from .tool_executor import execute_tools
from .types import CompactionResult, Message, ToolCall

log = logging.getLogger(__name__)

# Maximum tool-call rounds per user turn before we break the loop
_MAX_TOOL_ROUNDS = 10


class Conversation:
    """Manages one ongoing conversation session.

    Per-turn flow
    -------------
    1. Retrieve graph context for the current user message.
    2. Build system prompt (static + context).
    3. Append user message to history.
    4. Check compaction triggers; compact if warranted.
    5. Call LLM with current history.
    6. Execute any tool calls, feed results back, repeat until ``stop``.
    7. Append assistant reply to history.
    8. Return final text reply.
    """

    def __init__(
        self,
        cfg: dict,
        store: GraphStore,
        project_name: str,
    ):
        """
        Parameters
        ----------
        cfg:
            Full loaded config dict (output of ``load_config()``).
        store:
            Open GraphStore for the project.
        project_name:
            Used for logging.
        """
        orch_cfg = cfg.get("orchestrator", {})
        ctx_cfg = orch_cfg.get("context", {})
        sp_cfg = orch_cfg.get("system_prompt", {})
        tools_cfg = orch_cfg.get("tools", {})
        retry_cfg = orch_cfg.get("retry", {})

        self._llm_cfg: dict = {
            **orch_cfg.get("llm", {}),
            "_retry": retry_cfg,
        }
        self._tools_cfg: dict = tools_cfg
        self._enabled_tools: list[str] = tools_cfg.get("enabled", [])
        self._project_name = project_name

        # Sub-components
        self._context_mgr = ContextManager(
            cfg=ctx_cfg,
            store=store,
            project_name=project_name,
            extractor_config=cfg,
        )
        self._prompt_builder = SystemPromptBuilder(sp_cfg)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def chat(self, user_message: str) -> str:
        """Process one user turn and return the assistant's final text reply.

        Parameters
        ----------
        user_message:
            Raw text from the user.

        Returns
        -------
        str
            The assistant's reply (after any tool call rounds are complete).
        """
        # 1. Retrieve graph context keyed to this message
        context_md = self._context_mgr.retrieve_context(user_message)

        # 2. Build system prompt
        system = self._prompt_builder.build(
            context_markdown=context_md,
            task_description=user_message,
        )

        # 3. Add user message to history
        user_msg = Message(role="user", content=user_message)
        self._context_mgr.add_message(user_msg)

        # 4. Proactive compaction before calling the LLM
        compaction = await self._context_mgr.compact_if_needed()
        if compaction:
            log.info(
                "Compacted: removed=%d freed=%d tokens extracted=%d nodes [%s]",
                compaction.messages_removed,
                compaction.tokens_freed,
                compaction.nodes_extracted,
                compaction.trigger.value,
            )

        # 5–6. LLM call loop (handles tool use rounds)
        reply = await self._llm_loop(system)

        # 7. Append assistant reply to history
        self._context_mgr.add_message(Message(role="assistant", content=reply))
        self._context_mgr.touch()

        return reply

    async def chat_stream(self, user_message: str) -> AsyncIterator[str]:
        """Yield reply tokens incrementally (tool rounds are buffered internally).

        Yields the final reply in chunks split on word boundaries.
        This is a lightweight streaming shim — true streaming would require
        LiteLLM streaming support wired through the LLM adapter.
        """
        reply = await self.chat(user_message)
        # Yield in ~80-char chunks to simulate streaming
        chunk_size = 80
        for i in range(0, len(reply), chunk_size):
            yield reply[i : i + chunk_size]

    def reset(self) -> None:
        """Clear history (start a new logical session, keep the graph store)."""
        self._context_mgr._history.clear()
        self._context_mgr._total_tokens = 0
        log.info("Conversation reset for project %r", self._project_name)

    def stats(self) -> dict:
        """Return current context manager stats."""
        return self._context_mgr.stats()

    # ------------------------------------------------------------------
    # Internal: LLM call loop
    # ------------------------------------------------------------------

    async def _llm_loop(self, system: str) -> str:
        """Run the LLM → tool-call → result loop until a text reply is produced.

        Returns the final text reply string.
        """
        history = self._context_mgr.get_history()
        tools = self._enabled_tools if self._enabled_tools else None

        for round_num in range(_MAX_TOOL_ROUNDS):
            text, tool_calls, finish_reason = await call_llm(
                messages=history,
                system=system,
                cfg=self._llm_cfg,
                tools=tools,
            )

            if finish_reason != "tool_calls" or not tool_calls:
                # Done — return whatever text we got
                return text or ""

            # Execute tools and append results to local history for next round
            log.debug("Tool round %d: %d calls", round_num + 1, len(tool_calls))
            results = await execute_tools(tool_calls, self._tools_cfg)

            # Record the tool-call turn in both the working history (for the next
            # LLM round) and the context manager (so future turns see the tool trail).
            tool_summary = _format_tool_summary(tool_calls, results)
            tool_msgs = [
                Message(role="assistant", content=tool_summary),
                *[r.to_message() for r in results],
            ]
            for msg in tool_msgs:
                self._context_mgr.add_message(msg)
            history = list(history) + tool_msgs

        # Fell off the loop — ask once more for a plain reply
        log.warning("Tool loop hit %d rounds; requesting final answer", _MAX_TOOL_ROUNDS)
        text, _, _ = await call_llm(
            messages=history + [Message(role="user", content="Please summarize your findings.")],
            system=system,
            cfg=self._llm_cfg,
            tools=None,  # no tools on final round
        )
        return text or ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_tool_summary(tool_calls: list[ToolCall], results) -> str:
    """Build a short assistant content string describing tool invocations."""
    lines = []
    for tc, res in zip(tool_calls, results):
        status = "ok" if res.error is None else f"error: {res.error}"
        lines.append(f"[tool:{tc.name}({_truncate_args(tc.args)}) → {status}]")
    return "\n".join(lines)


def _truncate_args(args: dict, max_len: int = 60) -> str:
    """Render tool args as a short string for logging."""
    parts = []
    for k, v in args.items():
        sv = str(v)
        if len(sv) > max_len:
            sv = sv[:max_len] + "…"
        parts.append(f"{k}={sv!r}")
    return ", ".join(parts)
