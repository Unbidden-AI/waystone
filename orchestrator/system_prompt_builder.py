"""System prompt builder — assembles static instructions + per-turn graph context."""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path

from .llm_adapter import estimate_tokens

log = logging.getLogger(__name__)

_CONTEXT_HEADER = "## Project Knowledge\n\n"
_CONTEXT_FOOTER = "\n---\n"

# Sentinel shown when retrieval found nothing relevant
_NO_CONTEXT_MSG = "_No relevant project context found for this task._"


class SystemPromptBuilder:
    """Composes the system prompt sent to the LLM on each turn.

    The prompt has two parts:

    1. **Static** — fixed instructions from ``orchestrator.system_prompt.static``
       in config.yaml (role description, tone, rules).

    2. **Dynamic** — graph context retrieved by ``ContextManager.retrieve_context()``
       for the current task description.  The dynamic section is capped at
       ``context_token_limit`` tokens so it never crowds out history.

    Usage::

        builder = SystemPromptBuilder(cfg["orchestrator"]["system_prompt"])
        system_text = builder.build(context_markdown, task_description="…")
    """

    def __init__(self, cfg: dict, project_root: Path | None = None):
        """
        Parameters
        ----------
        cfg:
            The ``orchestrator.system_prompt`` section of config.yaml.
            Expected keys:
                static (str): fixed instructions appended after any static_files
                static_files (list[str]): file paths to prepend verbatim (e.g. CLAUDE.md)
                context_token_limit (int): max tokens for graph context (default 2000)
                include_context (bool): whether to append graph context (default True)
        project_root:
            Base directory for resolving relative paths in static_files.
            Defaults to the current working directory.
        """
        root = project_root or Path.cwd()

        parts: list[str] = []

        # Load static_files first (e.g. CLAUDE.md, guardrails.md)
        for path_str in cfg.get("static_files", []):
            p = Path(path_str)
            if not p.is_absolute():
                p = root / p
            p = p.expanduser()
            if p.exists():
                content = p.read_text(encoding="utf-8").strip()
                if content:
                    parts.append(content)
                log.debug("SystemPromptBuilder: loaded static_file %s (%d chars)", p, len(content))
            else:
                log.warning("SystemPromptBuilder: static_files entry not found: %s", p)

        # Append the inline static block from config
        raw_static = cfg.get("static", "")
        inline = textwrap.dedent(raw_static).strip()
        if inline:
            parts.append(inline)

        self._static: str = "\n\n---\n\n".join(parts)
        self._context_token_limit: int = int(cfg.get("context_token_limit", 2000))
        self._include_context: bool = bool(cfg.get("include_context", True))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self, context_markdown: str = "", task_description: str = "") -> str:
        """Assemble and return the full system prompt string.

        Parameters
        ----------
        context_markdown:
            Graph context string returned by ``ContextManager.retrieve_context()``.
            May be empty or None — treated as no context available.
        task_description:
            The current user task (used only for logging).

        Returns
        -------
        str
            The complete system prompt ready to pass to the LLM.
        """
        parts: list[str] = []

        if self._static:
            parts.append(self._static)

        if self._include_context:
            ctx = self._trim_context(context_markdown or "")
            context_section = _CONTEXT_HEADER + (ctx if ctx else _NO_CONTEXT_MSG) + _CONTEXT_FOOTER
            parts.append(context_section)
            log.debug(
                "SystemPromptBuilder: task=%r ctx_tokens=%d",
                (task_description or "")[:80],
                estimate_tokens(ctx),
            )

        prompt = "\n\n".join(parts)
        log.debug("SystemPromptBuilder: total prompt tokens ~%d", estimate_tokens(prompt))
        return prompt

    def static_tokens(self) -> int:
        """Return the token estimate for the static portion alone."""
        return estimate_tokens(self._static)

    def overhead_tokens(self) -> int:
        """Return tokens consumed by static + context framing (no graph content)."""
        framing = _CONTEXT_HEADER + _NO_CONTEXT_MSG + _CONTEXT_FOOTER
        return estimate_tokens(self._static + "\n\n" + framing)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _trim_context(self, context_markdown: str) -> str:
        """Truncate context to fit within ``_context_token_limit`` tokens.

        Trims at the last complete line that fits rather than cutting mid-sentence.
        """
        if not context_markdown.strip():
            return ""

        if self._context_token_limit <= 0:
            return context_markdown

        if estimate_tokens(context_markdown) <= self._context_token_limit:
            return context_markdown

        # Binary search for the longest prefix that fits
        lines = context_markdown.splitlines(keepends=True)
        lo, hi = 0, len(lines)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            candidate = "".join(lines[:mid])
            if estimate_tokens(candidate) <= self._context_token_limit:
                lo = mid
            else:
                hi = mid - 1

        trimmed = "".join(lines[:lo]).rstrip()
        if lo < len(lines):
            trimmed += "\n\n_[context trimmed to fit token budget]_"
            log.debug(
                "Context trimmed: %d → %d lines (%d tokens budget)",
                len(lines),
                lo,
                self._context_token_limit,
            )
        return trimmed
