"""Configuration loading for Engram."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

DEFAULTS = {
    "llm": {
        "base_url": "http://localhost:1234/v1",
        "model": "qwen3.5-35b-a3b",
        "temperature": 0.1,
        "max_tokens": 4096,
        "timeout": 30.0,
    },
    "defaults": {
        "hops": 3,
        "top_k": 10,
        "format": "markdown",
    },
    "strategies": {
        "superseded_pruning": True,
        "confidence_threshold": 0.0,  # 0.0 = disabled; e.g. 0.6 to filter tentative facts
        "recency_decay": False,
        "recency_half_life_days": 30,  # nodes lose half their score after this many days
        "token_budget": 0,  # 0 = unlimited; e.g. 500 to cap output
        "relevance_scoring": True,  # rank entry nodes by tag overlap count
    },
    "domain": {
        "name": "software_dev",
    },
    "orchestrator": {
        "system_prompt": {
            "static": """\
You are the Engram Orchestrator — an assistant for the Engram project (a DAG-based \
context intelligence layer for LLM workflows that extracts facts from transcripts into \
a knowledge graph and retrieves relevant subgraphs per turn). Do not describe your \
underlying model or training. The **Project Knowledge** section below is retrieved live \
from the graph; use it to answer questions about this project accurately.""",
        },
    },
    "projects_dir": "~/.engram/projects",
    "incremental": {
        "context_k": 30,       # max context nodes to include per turn
        "context_hops": 2,     # BFS hops when gathering context nodes
        "min_turns": 3,        # min buffered turns before flush is considered
        "min_words": 200,      # min total words across buffered turns to trigger flush
        "max_turns": 10,       # flush unconditionally after this many turns
        "short_turn_words": 20, # turns shorter than this don't count toward min_words
        "prior_turns_window": 0, # raw turns to append to retrieval context (0 = disabled)
    },
}

CONFIG_SEARCH_PATHS = [
    Path("config.yaml"),
    Path.home() / ".engram" / "config.yaml",
]


def load_config(path: str | Path | None = None) -> dict:
    """Load config from YAML file, falling back to defaults."""
    if path is not None:
        p = Path(path)
        if p.exists():
            with open(p) as f:
                user_cfg = yaml.safe_load(f) or {}
            return _merge(DEFAULTS, user_cfg)
        raise FileNotFoundError(f"Config file not found: {p}")

    result = dict(DEFAULTS)
    for candidate in CONFIG_SEARCH_PATHS:
        if candidate.exists():
            with open(candidate) as f:
                file_cfg = yaml.safe_load(f) or {}
            result = _merge(result, file_cfg)
    return result


def get_domain_profile(config: dict):
    """Return the DomainProfile for the configured domain.

    Reads config["domain"]["name"] (default "software_dev") and returns the
    corresponding built-in DomainProfile.
    """
    from .domain_profiles import get_profile
    name = config.get("domain", {}).get("name", "software_dev")
    return get_profile(name)


def get_project_dir(config: dict, project_name: str) -> Path:
    """Resolve the directory for a given project."""
    return Path(config["projects_dir"]).expanduser() / project_name


def get_db_path(config: dict, project_name: str) -> Path:
    """Resolve the SQLite database path for a given project."""
    return get_project_dir(config, project_name) / "context.db"


# ---------------------------------------------------------------------------
# Remote API helpers
# ---------------------------------------------------------------------------

def is_remote(config: dict) -> bool:
    """Return True when config points to a hosted API instead of local SQLite."""
    return bool(config.get("api_url"))


def get_api_url(config: dict) -> str:
    """Return the remote API base URL (raises if not configured)."""
    url = config.get("api_url")
    if not url:
        raise ValueError(
            "No api_url in config. Add 'api_url: https://...' to config.yaml "
            "to use the hosted API."
        )
    return url.rstrip("/")


def get_api_key(config: dict) -> str | None:
    """Return API key from config or CB_API_KEY env var."""
    return config.get("api_key") or os.environ.get("CB_API_KEY") or None


def make_remote_client(config: dict):
    """Build a RemoteEngram from config."""
    from .remote_client import RemoteContextBroker  # local import to avoid circular dep

    return RemoteContextBroker(
        api_url=get_api_url(config),
        api_key=get_api_key(config),
        timeout=config.get("llm", {}).get("timeout", 120.0),
    )


def _merge(defaults: dict, overrides: dict) -> dict:
    """Deep-merge overrides into defaults."""
    result = dict(defaults)
    for key, value in overrides.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result
