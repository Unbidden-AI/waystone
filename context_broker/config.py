"""Configuration loading for Context Broker."""

from pathlib import Path

import yaml

DEFAULTS = {
    "llm": {
        "base_url": "http://localhost:1234/v1",
        "model": "qwen3.5-35b-a3b",
        "temperature": 0.1,
        "max_tokens": 4096,
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
    "projects_dir": "./projects",
}

CONFIG_SEARCH_PATHS = [
    Path("config.yaml"),
    Path.home() / ".context-broker" / "config.yaml",
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

    for candidate in CONFIG_SEARCH_PATHS:
        if candidate.exists():
            with open(candidate) as f:
                user_cfg = yaml.safe_load(f) or {}
            return _merge(DEFAULTS, user_cfg)

    return dict(DEFAULTS)


def get_project_dir(config: dict, project_name: str) -> Path:
    """Resolve the directory for a given project."""
    return Path(config["projects_dir"]) / project_name


def get_db_path(config: dict, project_name: str) -> Path:
    """Resolve the SQLite database path for a given project."""
    return get_project_dir(config, project_name) / "context.db"


def _merge(defaults: dict, overrides: dict) -> dict:
    """Deep-merge overrides into defaults."""
    result = dict(defaults)
    for key, value in overrides.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result
