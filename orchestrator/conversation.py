"""Conversation — the main turn-by-turn loop tying all orchestrator modules together."""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator

from waystone.store import GraphStore

from .context_manager import ContextManager
from .llm_adapter import build_tool_schemas, call_llm, stream_llm
from .system_prompt_builder import SystemPromptBuilder
from .tool_executor import execute_tools
from .types import CompactionResult, Message, ToolCall

log = logging.getLogger(__name__)

# Maximum tool-call rounds per user turn before we break the loop
_MAX_TOOL_ROUNDS = 4


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
        project_root=None,
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
        project_root:
            Base directory for resolving relative ``static_files`` paths.
            Defaults to the current working directory.
        """
        from pathlib import Path

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
        self._store = store
        self._context_mgr = ContextManager(
            cfg=ctx_cfg,
            store=store,
            project_name=project_name,
            extractor_config=cfg,
        )
        self._prompt_builder = SystemPromptBuilder(sp_cfg, project_root=Path(project_root) if project_root else None)

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
        recent_turns = self._context_mgr.get_recent_turns_markdown()

        # 2. Build system prompt
        system = self._prompt_builder.build(
            context_markdown=context_md,
            task_description=user_message,
            recent_turns=recent_turns,
        )

        # 3. Add user message to history
        user_msg = Message(role="user", content=user_message)
        self._context_mgr.add_message(user_msg)

        # 4. Proactive compaction — history is pruned synchronously; extraction runs in background
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
        """Yield reply tokens as they arrive from the model.

        Tool rounds (if any) are executed synchronously before streaming
        begins — only the final synthesizing reply is streamed, giving
        time-to-first-token on the response the user actually sees.
        """
        # 1. Retrieve graph context
        context_md = self._context_mgr.retrieve_context(user_message)
        recent_turns = self._context_mgr.get_recent_turns_markdown()

        # 2. Build system prompt
        system = self._prompt_builder.build(
            context_markdown=context_md,
            task_description=user_message,
            recent_turns=recent_turns,
        )

        # 3. Add user message to history
        user_msg = Message(role="user", content=user_message)
        self._context_mgr.add_message(user_msg)

        # 4. Proactive compaction — history pruned synchronously; extraction runs in background
        compaction = await self._context_mgr.compact_if_needed()
        if compaction:
            log.info(
                "Compacted: removed=%d freed=%d tokens extracted=%d nodes [%s]",
                compaction.messages_removed,
                compaction.tokens_freed,
                compaction.nodes_extracted,
                compaction.trigger.value,
            )

        # 5–6. Stream tool rounds then final reply
        reply_parts: list[str] = []
        async for chunk in self._llm_loop_stream(system):
            reply_parts.append(chunk)
            yield chunk

        # 7. Append assembled reply to history
        reply = "".join(reply_parts)
        self._context_mgr.add_message(Message(role="assistant", content=reply))
        self._context_mgr.touch()

    def reset(self) -> None:
        """Clear history (start a new logical session, keep the graph store)."""
        self._context_mgr.reset()
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
            results = await execute_tools(tool_calls, self._tools_cfg, self._store)

            # Record the tool-call turn in both the working history (for the next
            # LLM round) and the context manager (so future turns see the tool trail).
            # raw_tool_calls preserves the proper OpenAI-format array so providers
            # like Gemini can match tool_call_ids in subsequent messages.
            tool_summary = _format_tool_summary(tool_calls, results)
            raw_tc = _to_raw_tool_calls(tool_calls)
            tool_msgs = [
                Message(role="assistant", content=tool_summary, raw_tool_calls=raw_tc),
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

    async def _llm_loop_stream(self, system: str) -> AsyncIterator[str]:
        """Run tool rounds then stream the final reply.

        If no tools are configured, streams immediately for best TTFT.
        If tools are configured, tool rounds are executed non-streaming (so
        that tool-call JSON can be parsed cleanly), then the final
        synthesizing reply is streamed.  If the LLM returns a text reply
        on the first round without invoking any tools, that text is yielded
        directly — no redundant second call.
        """
        history = self._context_mgr.get_history()
        tools = self._enabled_tools if self._enabled_tools else None

        # No tools configured — stream directly for best TTFT
        if not tools:
            async for chunk in stream_llm(history, system, self._llm_cfg):
                yield chunk
            return

        # Tool rounds: non-streaming so tool-call JSON arrives complete
        for round_num in range(_MAX_TOOL_ROUNDS):
            text, tool_calls, finish_reason = await call_llm(
                messages=history,
                system=system,
                cfg=self._llm_cfg,
                tools=tools,
            )

            if finish_reason != "tool_calls" or not tool_calls:
                # LLM returned a text reply without tool calls — yield it
                # directly; no need for a second streaming call.
                if text:
                    yield text
                return

            log.debug("Stream tool round %d: %d calls", round_num + 1, len(tool_calls))
            results = await execute_tools(tool_calls, self._tools_cfg, self._store)

            tool_summary = _format_tool_summary(tool_calls, results)
            raw_tc = _to_raw_tool_calls(tool_calls)
            tool_msgs = [
                Message(role="assistant", content=tool_summary, raw_tool_calls=raw_tc),
                *[r.to_message() for r in results],
            ]
            for msg in tool_msgs:
                self._context_mgr.add_message(msg)
            history = list(history) + tool_msgs

        # All tool rounds exhausted — use call_llm (non-streaming) for the final answer.
        # stream_llm is unreliable here: Gemini returns UNEXPECTED_TOOL_CALL even with
        # tools=None after a long tool loop, yielding an empty stream.
        log.warning("Tool loop hit %d rounds; requesting final answer", _MAX_TOOL_ROUNDS)
        text, _, _ = await call_llm(
            messages=history + [Message(role="user", content="Please summarize your findings.")],
            system=system,
            cfg=self._llm_cfg,
            tools=None,
        )
        if text:
            yield text


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


def _to_raw_tool_calls(tool_calls: list[ToolCall]) -> list[dict]:
    """Convert ToolCall objects to the OpenAI-format tool_calls array.

    This is stored on assistant messages so that providers like Gemini can
    match tool_call_ids when the conversation history is replayed.
    """
    return [
        {
            "id": tc.id,
            "type": "function",
            "function": {
                "name": tc.name,
                "arguments": json.dumps(tc.args),
            },
        }
        for tc in tool_calls
    ]
