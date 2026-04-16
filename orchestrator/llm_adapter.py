"""LiteLLM adapter — model-agnostic LLM calls with tool use and auto-recovery."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, AsyncIterator

import litellm
from litellm.exceptions import (
    AuthenticationError,
    InternalServerError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)

from .types import Message, ToolCall

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

_TIKTOKEN_ENCODING = None


def _get_encoding():
    global _TIKTOKEN_ENCODING
    if _TIKTOKEN_ENCODING is None:
        try:
            import tiktoken
            _TIKTOKEN_ENCODING = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _TIKTOKEN_ENCODING = False  # sentinel: unavailable
    return _TIKTOKEN_ENCODING


def estimate_tokens(text: str) -> int:
    """Return token count for *text* using tiktoken cl100k_base; falls back to len//4."""
    enc = _get_encoding()
    if enc is False:
        return max(1, len(text) // 4)
    try:
        return max(1, len(enc.encode(text)))
    except Exception:
        return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

_TOOL_SCHEMAS: dict[str, dict] = {
    "bash": {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a shell command and return its stdout/stderr.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run."},
                    "timeout": {"type": "integer", "description": "Max seconds to wait (default 30)."},
                },
                "required": ["command"],
            },
        },
    },
    "read_file": {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file on disk.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or relative path to the file."},
                },
                "required": ["path"],
            },
        },
    },
    "write_file": {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file on disk, creating or overwriting it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to write to."},
                    "content": {"type": "string", "description": "Content to write."},
                },
                "required": ["path", "content"],
            },
        },
    },
    "glob": {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files matching a glob pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern, e.g. 'src/**/*.py'."},
                    "root": {"type": "string", "description": "Directory to search from (default: cwd)."},
                },
                "required": ["pattern"],
            },
        },
    },
    "grep": {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search file contents for a regex pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern to search for."},
                    "path": {"type": "string", "description": "File or directory to search."},
                    "glob": {"type": "string", "description": "File glob filter, e.g. '*.py'."},
                    "context_lines": {"type": "integer", "description": "Lines of context around each match."},
                },
                "required": ["pattern"],
            },
        },
    },
    "ctx_delete_node": {
        "type": "function",
        "function": {
            "name": "ctx_delete_node",
            "description": (
                "Delete a node and all its edges from the project knowledge graph. "
                "Use when a fact is confirmed stale, incorrect, or superseded and "
                "should not appear in future context retrievals."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "node_id": {"type": "string", "description": "ID of the node to delete (e.g. 'n_1a2b3c4d')."},
                },
                "required": ["node_id"],
            },
        },
    },
    "ctx_update_node": {
        "type": "function",
        "function": {
            "name": "ctx_update_node",
            "description": (
                "Update the fact text of an existing node in the project knowledge graph. "
                "Use to correct outdated or inaccurate facts rather than deleting and re-adding."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "node_id": {"type": "string", "description": "ID of the node to update."},
                    "new_fact": {"type": "string", "description": "Replacement fact text."},
                    "new_confidence": {
                        "type": "number",
                        "description": "Optional updated confidence score (0.0–1.0).",
                    },
                },
                "required": ["node_id", "new_fact"],
            },
        },
    },
    "ctx_graph_stats": {
        "type": "function",
        "function": {
            "name": "ctx_graph_stats",
            "description": (
                "Return a summary of the current project knowledge graph: total node and edge counts, "
                "breakdown by node type, number of superseded nodes, number of distinct source files, "
                "and the 5 most recently added nodes. Use when asked about graph status, database size, "
                "or what's in the knowledge base."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    "ctx_list_sources": {
        "type": "function",
        "function": {
            "name": "ctx_list_sources",
            "description": (
                "List all source files and transcripts that have been extracted into the "
                "project knowledge graph, along with the number of nodes from each source. "
                "Use when asked which files, documents, or sessions have been extracted."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    "ctx_synthesize": {
        "type": "function",
        "function": {
            "name": "ctx_synthesize",
            "description": (
                "Create a new synthesis node that summarizes or consolidates multiple existing nodes. "
                "Links the synthesis node to source nodes via 'relates_to' edges."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary_fact": {"type": "string", "description": "The synthesized fact text."},
                    "node_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "IDs of the source nodes being summarized.",
                    },
                    "node_type": {
                        "type": "string",
                        "description": "Node type for the synthesis node (default: 'decision').",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tags to attach to the synthesis node.",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Confidence score for the synthesis node (default: 0.9).",
                    },
                },
                "required": ["summary_fact", "node_ids"],
            },
        },
    },
}


def build_tool_schemas(enabled_tools: list[str]) -> list[dict]:
    """Return OpenAI-format tool schema list for *enabled_tools*."""
    schemas = []
    for name in enabled_tools:
        if name in _TOOL_SCHEMAS:
            schemas.append(_TOOL_SCHEMAS[name])
        else:
            log.warning("Unknown tool %r — skipping schema", name)
    return schemas


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_api_key(cfg: dict) -> str | None:
    """Resolve API key from config → env vars in priority order."""
    # 1. Explicit env var name in config (e.g. api_key_env: GEMINI_API_KEY)
    env_name = cfg.get("api_key_env")
    if env_name:
        val = os.environ.get(env_name)
        if val:
            return val
    # 2. Literal key hardcoded in config
    literal = cfg.get("api_key")
    if literal:
        return literal
    # 3. Well-known env var fallbacks
    for env in ("GEMINI_API_KEY", "CTX_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        val = os.environ.get(env)
        if val:
            return val
    return None


def _extract_retry_after(exc: Exception) -> float | None:
    """Parse Retry-After seconds from a RateLimitError, if present."""
    # LiteLLM embeds the raw response headers in some exception types
    for attr in ("response", "llm_provider_exception", "_response"):
        resp = getattr(exc, attr, None)
        if resp is None:
            continue
        headers = getattr(resp, "headers", None)
        if headers is None:
            continue
        ra = headers.get("retry-after") or headers.get("Retry-After")
        if ra is not None:
            try:
                return float(ra)
            except ValueError:
                pass
    return None


def _parse_tool_calls(response_message) -> list[ToolCall] | None:
    """Extract ToolCall list from a LiteLLM response message, or None."""
    raw_calls = getattr(response_message, "tool_calls", None)
    if not raw_calls:
        return None
    results: list[ToolCall] = []
    for tc in raw_calls:
        try:
            func = tc.function
            args = json.loads(func.arguments) if isinstance(func.arguments, str) else func.arguments
            results.append(ToolCall(id=tc.id, name=func.name, args=args or {}))
        except Exception as e:
            log.warning("Failed to parse tool call %r: %s", tc, e)
    return results or None


# ---------------------------------------------------------------------------
# Internal: shared kwargs builder
# ---------------------------------------------------------------------------


def _build_kwargs(
    messages: list[Message],
    system: str,
    cfg: dict,
    tools: list[str] | None = None,
) -> dict[str, Any]:
    """Build the LiteLLM acompletion kwargs dict (without stream flag)."""
    # Suppress LiteLLM's own verbose output
    log_level = cfg.get("log_level", "WARNING")
    litellm.set_verbose = False
    logging.getLogger("LiteLLM").setLevel(getattr(logging, log_level, logging.WARNING))

    api_key = _resolve_api_key(cfg)

    api_messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    api_messages.extend(m.to_api_dict() for m in messages)

    kwargs: dict[str, Any] = {
        "model": cfg.get("model", "anthropic/claude-sonnet-4-6"),
        "messages": api_messages,
        "temperature": cfg.get("temperature", 0.7),
        "max_tokens": cfg.get("max_tokens", 4096),
    }
    if api_key:
        kwargs["api_key"] = api_key
    base_url = cfg.get("base_url")
    if base_url:
        kwargs["api_base"] = base_url

    tool_schemas = build_tool_schemas(tools) if tools else []
    if tool_schemas:
        kwargs["tools"] = tool_schemas
        kwargs["tool_choice"] = "auto"

    return kwargs


# ---------------------------------------------------------------------------
# Primary entry point
# ---------------------------------------------------------------------------

async def call_llm(
    messages: list[Message],
    system: str,
    cfg: dict,
    tools: list[str] | None = None,
) -> tuple[str | None, list[ToolCall] | None, str]:
    """Call the LLM and return (text_reply, tool_calls, finish_reason).

    Parameters
    ----------
    messages:
        Conversation history (excluding system prompt).
    system:
        System prompt string.
    cfg:
        ``orchestrator.llm`` config dict (model, temperature, max_tokens, …).
    tools:
        List of tool names to enable (schemas built automatically).

    Returns
    -------
    (text, tool_calls, finish_reason)
        *text* is the assistant text or None when tool_calls is set.
        *finish_reason* is one of "stop", "tool_calls", "length", "error".
    """
    retry_cfg = cfg.get("_retry", {})
    transient_codes: list[int] = retry_cfg.get("transient_codes", [500, 502, 503, 504])
    max_retries: int = retry_cfg.get("max_retries", 4)
    backoff_seconds: list[float] = retry_cfg.get("backoff_seconds", [1.0, 2.0, 4.0, 8.0])
    friendly_threshold: float = retry_cfg.get("friendly_delay_threshold", 5.0)

    kwargs = _build_kwargs(messages, system, cfg, tools)

    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = await litellm.acompletion(**kwargs)
            if not response.choices:
                raise RuntimeError("LLM returned an empty choices list")
            msg = response.choices[0].message
            finish_reason: str = response.choices[0].finish_reason or "stop"

            tool_calls = _parse_tool_calls(msg)
            text: str | None = getattr(msg, "content", None) or None

            if tool_calls:
                finish_reason = "tool_calls"

            log.debug(
                "LLM call complete: finish_reason=%s tool_calls=%s tokens_used=%s",
                finish_reason,
                len(tool_calls) if tool_calls else 0,
                getattr(response, "usage", {}).get("total_tokens", "?"),
            )
            return text, tool_calls, finish_reason

        except AuthenticationError as e:
            # Never retry auth failures — do not log exception body (may contain headers)
            log.error("Authentication failed (status=%s)", getattr(e, "status_code", "unknown"))
            raise

        except RateLimitError as e:
            delay = _extract_retry_after(e)
            if delay is None:
                delay = backoff_seconds[min(attempt, len(backoff_seconds) - 1)]
            last_exc = e
            if attempt >= max_retries:
                break
            if delay >= friendly_threshold:
                log.info("Rate limited — retrying in %.0fs…", delay)
            log.debug("RateLimitError on attempt %d, sleeping %.1fs", attempt, delay)
            await asyncio.sleep(delay)

        except (InternalServerError, ServiceUnavailableError) as e:
            # Retry only if the status code is in transient_codes
            status = getattr(e, "status_code", None)
            if status is not None and status not in transient_codes:
                log.error("Non-transient server error %s (status=%s)", type(e).__name__, status)
                raise
            last_exc = e
            if attempt >= max_retries:
                break
            delay = backoff_seconds[min(attempt, len(backoff_seconds) - 1)]
            if delay >= friendly_threshold:
                log.info("Server error %s — retrying in %.0fs…", status, delay)
            log.debug("Transient error on attempt %d, sleeping %.1fs: %s", attempt, delay, type(e).__name__)
            await asyncio.sleep(delay)

        except Timeout as e:
            last_exc = e
            if attempt >= max_retries:
                break
            delay = backoff_seconds[min(attempt, len(backoff_seconds) - 1)]
            log.debug("Timeout on attempt %d, sleeping %.1fs", attempt, delay)
            await asyncio.sleep(delay)

    # Exhausted retries
    if last_exc is None:
        # Shouldn't happen, but guard against it
        raise RuntimeError("LLM call failed with no recorded exception")
    log.error("LLM call failed after %d attempts: %s", max_retries + 1, type(last_exc).__name__)
    raise last_exc


# ---------------------------------------------------------------------------
# Streaming entry point
# ---------------------------------------------------------------------------


async def stream_llm(
    messages: list[Message],
    system: str,
    cfg: dict,
) -> AsyncIterator[str]:
    """Stream the LLM reply as text delta chunks.

    No tool use — intended for the final synthesizing reply after tool rounds
    are complete, or for pure-chat turns where no tools are configured.

    Yields
    ------
    str
        Each content delta as it arrives from the model.  Yields nothing if
        the model returns only whitespace or an empty content delta.

    Notes
    -----
    Retry is intentionally omitted: resuming a partial stream is unsound,
    and the caller (``_llm_loop_stream``) falls back gracefully on exception.
    """
    kwargs = _build_kwargs(messages, system, cfg)
    kwargs["stream"] = True

    log.debug("stream_llm: starting stream (%s)", cfg.get("model", "?"))
    response = await litellm.acompletion(**kwargs)

    async for chunk in response:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        content: str | None = getattr(delta, "content", None)
        if content:
            yield content
