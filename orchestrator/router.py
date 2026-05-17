"""Model router — selects a model config based on the user message content.

Config shape (under ``orchestrator.routing`` in config.yaml):

    routing:
      enabled: true
      rules:
        - name: fast          # optional human-readable label
          patterns:           # case-insensitive regex patterns matched against the message
            - "^list "
            - "^show "
            - "what is "
          model: "gemini/gemini-2.5-flash-lite"
          max_tokens: 2048    # optional — falls back to base config value if omitted
          temperature: 0.0    # optional
        - name: deep
          patterns:
            - "design|architect|tradeoff|impact analysis|compare"
          model: "gemini/gemini-2.5-flash"
          max_tokens: 8192

Rules are evaluated in order.  The first matching rule wins.  Each rule may
override any subset of {model, max_tokens, temperature}.  Fields not present
in the rule fall through to the base LLM config unchanged.

If routing is disabled or no rule matches, the base config is returned
unmodified — zero cost, no allocation.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)


def route(message: str, routing_cfg: dict, base_llm_cfg: dict) -> dict:
    """Return the LLM config dict to use for this turn.

    Parameters
    ----------
    message:
        The raw user message for this turn.
    routing_cfg:
        The ``orchestrator.routing`` config block.  Must have ``enabled``
        (bool) and ``rules`` (list of rule dicts).
    base_llm_cfg:
        The default ``orchestrator.llm`` config (including ``_retry``).

    Returns
    -------
    dict
        A (possibly new) LLM config dict.  When no rule fires, the original
        ``base_llm_cfg`` object is returned — callers must not mutate it.
    """
    if not routing_cfg.get("enabled", False):
        return base_llm_cfg

    msg_lower = message.lower()
    for rule in routing_cfg.get("rules", []):
        patterns: list[str] = rule.get("patterns", [])
        if not patterns:
            continue
        if any(_match(p, msg_lower) for p in patterns):
            override = _apply_rule(base_llm_cfg, rule)
            log.info(
                "router: matched rule %r → model=%s",
                rule.get("name", patterns[0]),
                override.get("model", "<unchanged>"),
            )
            return override

    log.debug("router: no rule matched, using base model=%s", base_llm_cfg.get("model"))
    return base_llm_cfg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PATTERN_CACHE: dict[str, re.Pattern] = {}


def _match(pattern: str, text: str) -> bool:
    """Return True if *pattern* (case-insensitive regex) matches anywhere in *text*."""
    try:
        compiled = _PATTERN_CACHE.get(pattern)
        if compiled is None:
            compiled = re.compile(pattern, re.IGNORECASE)
            _PATTERN_CACHE[pattern] = compiled
        return compiled.search(text) is not None
    except re.error:
        log.warning("router: invalid regex pattern %r — skipping", pattern)
        return False


_OVERRIDABLE_KEYS = {"model", "max_tokens", "temperature"}


def _apply_rule(base: dict, rule: dict) -> dict:
    """Build an overridden config dict from *base* + *rule* overrides.

    Only keys in ``_OVERRIDABLE_KEYS`` are taken from the rule; everything
    else (including ``_retry``) is inherited from *base*.
    """
    override = dict(base)
    for key in _OVERRIDABLE_KEYS:
        if key in rule:
            override[key] = rule[key]
    return override
