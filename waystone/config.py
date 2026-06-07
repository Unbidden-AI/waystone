"""Configuration loading for Waystone."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

# Load project-local .env (e.g. GEMINI_API_KEY) if python-dotenv is available.
# The .env file is gitignored — safe for API keys that should persist across
# shell sessions without polluting the global environment.
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env", override=False)
except ImportError:
    pass

DEFAULTS = {
    "llm": {
        "base_url": "http://localhost:1234/v1",
        "model": "qwen3.5-35b-a3b",
        "temperature": 0.1,
        "max_tokens": 4096,
        # Extraction LLM calls on a full chunk routinely take 30-120s+ (large
        # structured-JSON output). 30s was far too low and timed out on any
        # chunky transcript; 120s gives real extractions room to finish.
        "timeout": 120.0,
        "use_native_sdk": False,
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
    "embeddings": {
        # Semantic-search embedding backend. Default "local" = bge-small via
        # sentence-transformers (the waystone[semantic] extra, pulls PyTorch).
        # Set backend: "api" to embed through litellm using your LLM API key
        # instead — no PyTorch. Switching backends requires `waystone reembed`
        # (vector spaces differ). See docs/advanced.
        "backend": "local",                      # "local" | "api"
        "model": "gemini/text-embedding-004",    # litellm model id (api backend only)
        "dim": 768,                              # api model's vector dim — MUST match it
        "api_key_env": "",                       # env var for the key (else falls back to llm key)
    },
    "pilot": {
        "system_prompt": {
            "static": """\
You are the Waystone Pilot — an assistant for the Waystone project (a DAG-based \
context intelligence layer for LLM workflows that extracts facts from transcripts into \
a knowledge graph and retrieves relevant subgraphs per turn). Do not describe your \
underlying model or training. The **Project Knowledge** section below is retrieved live \
from the graph; use it to answer questions about this project accurately.""",
        },
    },
    "projects_dir": "~/.waystone/projects",
    "incremental": {
        "context_k": 30,       # max context nodes to include per turn
        "context_hops": 2,     # BFS hops when gathering context nodes
        "min_turns": 3,        # min buffered turns before flush is considered
        "min_words": 200,      # min total words across buffered turns to trigger flush
        "max_turns": 10,       # flush unconditionally after this many turns
        "short_turn_words": 20, # turns shorter than this don't count toward min_words
        "prior_turns_window": 0, # raw turns to append to retrieval context (0 = disabled)
    },
    "statusline": {
        # Claude Code status-line display. The Waystone segment shows from the
        # start of a session (project + "ready") and surfaces extraction errors.
        "enabled": True,
        "alert_on_error": True,   # show a ⚠ alert when extraction errors occur
    },
    "posttool": {
        # PostToolUse capture: during long autonomous runs (plan/auto mode) the
        # UserPromptSubmit and Stop hooks don't fire, so the graph stays empty
        # until the run ends. When enabled, state-changing tool calls are
        # buffered and flushed to background extraction mid-run.
        "enabled": True,
        "min_events": 8,       # flush after this many captured tool calls
        "max_chars": 4000,     # …or when buffered summaries exceed this many chars
        "tools": ["Write", "Edit", "MultiEdit", "NotebookEdit", "Bash"],
    },
    "sentence_index": {
        # Per-sentence raw transcript indexing for semantic fallback retrieval.
        # When enabled, every sentence from ingested transcripts is stored in a
        # separate raw_sentences table with a vector embedding.  At query time,
        # if the primary BFS retrieval returns fewer than sentence_fallback_threshold
        # entry nodes, semantic search over raw sentences is used as a fallback —
        # catching queries like "What degree did I graduate with?" that have no
        # matching graph tags but do semantically match raw utterances.
        "enabled": False,
        "min_length": 0,          # min chars per sentence (0 = no filter)
        "earlier_neighbors": 2,   # sentences of prior context to include around each hit
        "later_neighbors": 2,     # sentences of following context to include
        "fallback_threshold": 3,  # use fallback when BFS entry_nodes < this count
        "top_k": 10,              # max raw-sentence hits to retrieve
    },
}

CONFIG_SEARCH_PATHS = [
    Path("config.yaml"),
    Path.home() / ".waystone" / "config.yaml",
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
    """Return API key from WAYSTONE_API_KEY env var or config file.

    Env var takes precedence over config file so container/CI secrets
    are never silently overridden by a stale config.yaml value.
    """
    return os.environ.get("WAYSTONE_API_KEY") or config.get("api_key") or None


def resolve_llm_api_key(llm_cfg: dict) -> tuple[str | None, str]:
    """Resolve the extraction LLM's API key from an ``llm`` config block.

    Single source of truth for key resolution — used by the extractor (both the
    OpenAI-compatible and native-Gemini paths), ``waystone verify``, ``doctor``,
    and ``configure`` so they can never disagree about which key extraction will
    actually use.

    Resolution order: the configured ``api_key_env`` (that env var) → the inline
    ``api_key`` → generic env vars (CTX_API_KEY, WAYSTONE_API_KEY, OPENAI_API_KEY).

    Returns ``(key, source)`` where ``source`` is ``"env:<NAME>"``, ``"inline"``,
    or ``"none"`` (key is None only when source is "none" — e.g. a local model
    that needs no key).
    """
    llm_cfg = llm_cfg or {}
    env_name = llm_cfg.get("api_key_env")
    if env_name:
        val = os.environ.get(env_name)
        if val:
            return val, f"env:{env_name}"
    inline = llm_cfg.get("api_key")
    if inline:
        return inline, "inline"
    for generic in ("CTX_API_KEY", "WAYSTONE_API_KEY", "OPENAI_API_KEY"):
        val = os.environ.get(generic)
        if val:
            return val, f"env:{generic}"
    return None, "none"


def make_remote_client(config: dict):
    """Build a RemoteContextBroker from config."""
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
