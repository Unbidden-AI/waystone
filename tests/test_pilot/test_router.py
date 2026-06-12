"""Tests for pilot/router.py — model routing logic."""

from __future__ import annotations

from pilot.router import _apply_rule, _match, route

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

BASE_CFG = {
    "model": "gemini/gemini-2.5-flash-lite",
    "max_tokens": 4096,
    "temperature": 0.7,
    "_retry": {"max_retries": 4},
}

ROUTING_CFG_DISABLED = {
    "enabled": False,
    "rules": [
        {
            "name": "fast",
            "patterns": ["^list "],
            "model": "gemini/fast-model",
        }
    ],
}

ROUTING_CFG_ENABLED = {
    "enabled": True,
    "rules": [
        {
            "name": "fast",
            "patterns": ["^list ", "^show "],
            "model": "gemini/gemini-2.5-flash-lite",
            "max_tokens": 2048,
            "temperature": 0.0,
        },
        {
            "name": "deep",
            "patterns": ["design|architect"],
            "model": "gemini/gemini-2.5-flash",
            "max_tokens": 8192,
        },
    ],
}


# ---------------------------------------------------------------------------
# _match
# ---------------------------------------------------------------------------


def test_match_simple_prefix():
    assert _match("^list ", "list all nodes") is True


def test_match_no_match():
    assert _match("^list ", "show all nodes") is False


def test_match_case_insensitive():
    assert _match("design", "Design a new API") is True


def test_match_alternation():
    assert _match("design|architect", "please architect the system") is True
    assert _match("design|architect", "unrelated question") is False


def test_match_invalid_regex_returns_false():
    # Should not raise; invalid regex is skipped
    assert _match("[invalid", "some text") is False


def test_match_caches_compiled_pattern():
    # Call twice with the same pattern — should hit cache on second call
    pattern = "^cache_test "
    _match(pattern, "cache_test me")
    _match(pattern, "cache_test me again")
    from pilot.router import _PATTERN_CACHE
    assert pattern in _PATTERN_CACHE


# ---------------------------------------------------------------------------
# _apply_rule
# ---------------------------------------------------------------------------


def test_apply_rule_overrides_model():
    rule = {"model": "gemini/other", "name": "test"}
    result = _apply_rule(BASE_CFG, rule)
    assert result["model"] == "gemini/other"


def test_apply_rule_overrides_max_tokens():
    rule = {"max_tokens": 1024}
    result = _apply_rule(BASE_CFG, rule)
    assert result["max_tokens"] == 1024


def test_apply_rule_overrides_temperature():
    rule = {"temperature": 0.0}
    result = _apply_rule(BASE_CFG, rule)
    assert result["temperature"] == 0.0


def test_apply_rule_preserves_retry():
    rule = {"model": "gemini/other"}
    result = _apply_rule(BASE_CFG, rule)
    assert result["_retry"] == BASE_CFG["_retry"]


def test_apply_rule_does_not_mutate_base():
    rule = {"model": "gemini/other", "max_tokens": 1024}
    _apply_rule(BASE_CFG, rule)
    # Base must be unchanged
    assert BASE_CFG["model"] == "gemini/gemini-2.5-flash-lite"
    assert BASE_CFG["max_tokens"] == 4096


def test_apply_rule_partial_override():
    rule = {"max_tokens": 512}  # only max_tokens
    result = _apply_rule(BASE_CFG, rule)
    assert result["model"] == BASE_CFG["model"]
    assert result["temperature"] == BASE_CFG["temperature"]
    assert result["max_tokens"] == 512


def test_apply_rule_ignores_non_overridable_keys():
    rule = {"model": "gemini/other", "name": "fast", "patterns": ["^list "]}
    result = _apply_rule(BASE_CFG, rule)
    assert "name" not in result
    assert "patterns" not in result


# ---------------------------------------------------------------------------
# route — disabled routing
# ---------------------------------------------------------------------------


def test_route_disabled_returns_base():
    result = route("list all nodes", ROUTING_CFG_DISABLED, BASE_CFG)
    assert result is BASE_CFG  # same object, no copy


def test_route_disabled_no_mutation():
    route("list all nodes", ROUTING_CFG_DISABLED, BASE_CFG)
    assert BASE_CFG["model"] == "gemini/gemini-2.5-flash-lite"


# ---------------------------------------------------------------------------
# route — enabled, matching
# ---------------------------------------------------------------------------


def test_route_first_rule_wins():
    # "list " matches fast rule, not deep
    result = route("list all nodes", ROUTING_CFG_ENABLED, BASE_CFG)
    assert result["model"] == "gemini/gemini-2.5-flash-lite"
    assert result["max_tokens"] == 2048


def test_route_second_rule_fires():
    result = route("design a new auth system", ROUTING_CFG_ENABLED, BASE_CFG)
    assert result["model"] == "gemini/gemini-2.5-flash"
    assert result["max_tokens"] == 8192


def test_route_no_match_returns_base():
    result = route("something completely unrelated", ROUTING_CFG_ENABLED, BASE_CFG)
    assert result is BASE_CFG


def test_route_match_is_case_insensitive():
    result = route("DESIGN the system", ROUTING_CFG_ENABLED, BASE_CFG)
    assert result["model"] == "gemini/gemini-2.5-flash"


def test_route_retry_preserved_on_match():
    result = route("list things", ROUTING_CFG_ENABLED, BASE_CFG)
    assert result["_retry"] == BASE_CFG["_retry"]


# ---------------------------------------------------------------------------
# route — edge cases
# ---------------------------------------------------------------------------


def test_route_empty_rules_returns_base():
    cfg = {"enabled": True, "rules": []}
    result = route("list all nodes", cfg, BASE_CFG)
    assert result is BASE_CFG


def test_route_rule_without_patterns_skipped():
    cfg = {
        "enabled": True,
        "rules": [
            {"name": "no-patterns", "model": "should-not-match"},
            {"name": "fallback", "patterns": ["list"], "model": "gemini/fallback"},
        ],
    }
    result = route("list nodes", cfg, BASE_CFG)
    assert result["model"] == "gemini/fallback"


def test_route_missing_enabled_key_returns_base():
    cfg = {"rules": [{"patterns": ["^list "], "model": "gemini/other"}]}
    # enabled defaults to False
    result = route("list things", cfg, BASE_CFG)
    assert result is BASE_CFG


def test_route_empty_routing_cfg_returns_base():
    result = route("anything", {}, BASE_CFG)
    assert result is BASE_CFG
