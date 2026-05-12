"""Configuration loading for the Engram OpenClaw skill.

Config is resolved in priority order (highest first):
  1. Environment variables (ENGRAM_PROJECT, ENGRAM_TOP_K, …)
  2. ~/.openclaw/plugins/engram/config.yaml
  3. ~/.engram/openclaw.yaml
  4. OpenClaw plugin-specific defaults (OPENCLAW_DEFAULTS)
  5. Engram core defaults

The resolved config is then merged with Engram's core config so LLM
settings, strategy toggles, and DB paths are inherited automatically.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigError

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

OPENCLAW_DEFAULTS: dict[str, Any] = {
    # Required: set via ENGRAM_PROJECT or config file
    "project": "",
    # Retrieval
    "top_k": 15,
    "hops": 3,
    # Context injection — prepended to system prompt on session start
    "context_prefix": True,
    "context_top_k": 15,
    # Extraction behaviour
    "auto_extract": True,
    # True = one LLM call per session end (cost-safe default)
    # False = extract after every N turns (higher recall, higher cost)
    "extract_on_session_end_only": True,
    # MEMORY.md sync
    "memory_md_path": "~/.openclaw/MEMORY.md",
    "memory_md_max_bytes": 4096,       # Hard cap; oldest facts archived when hit
    "memory_md_section": "## Engram Context",  # Section injected into MEMORY.md
    # Dreaming
    "dream_interval_turns": 10,
    # Developer options
    "dry_run": False,  # Show what *would* be extracted, don't spend tokens
}

# Config file search order
_CONFIG_PATHS = [
    Path("~/.openclaw/plugins/engram/config.yaml"),
    Path("~/.engram/openclaw.yaml"),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_openclaw_config() -> dict:
    """Load and return the resolved OpenClaw plugin config.

    Merges OPENCLAW_DEFAULTS → file config → env vars → Engram core config.
    The Engram core config (LLM settings, strategy toggles, projects_dir) is
    stored under the ``_engram`` key and used by memory_sync and skill.py.

    Returns a flat dict with OpenClaw-specific keys plus ``_engram`` (dict).
    """
    from engram.config import load_config, _merge  # local import to avoid circular

    cfg: dict = dict(OPENCLAW_DEFAULTS)

    # 1. File config
    for p in _CONFIG_PATHS:
        resolved = p.expanduser()
        if resolved.exists():
            try:
                file_cfg = yaml.safe_load(resolved.read_text()) or {}
                cfg = _merge(cfg, file_cfg)
            except Exception:
                pass  # Corrupt config file — fall through to defaults
            break

    # 2. Environment variable overrides
    if v := os.environ.get("ENGRAM_PROJECT"):
        cfg["project"] = v
    if v := os.environ.get("ENGRAM_TOP_K"):
        try:
            cfg["top_k"] = int(v)
            cfg["context_top_k"] = int(v)
        except ValueError:
            pass
    if v := os.environ.get("ENGRAM_HOPS"):
        try:
            cfg["hops"] = int(v)
        except ValueError:
            pass
    if os.environ.get("ENGRAM_EXTRACT") == "0":
        cfg["auto_extract"] = False
    if os.environ.get("ENGRAM_DRY_RUN") == "1":
        cfg["dry_run"] = True

    # 3. Load Engram core config and store under _engram
    try:
        engram_cfg = load_config(os.environ.get("ENGRAM_CONFIG") or None)
        cfg["_engram"] = engram_cfg
    except Exception:
        cfg["_engram"] = {}

    return cfg


def get_project(cfg: dict) -> str:
    """Return the project name, raising ConfigError if not set."""
    project = cfg.get("project", "").strip()
    if not project:
        raise ConfigError(
            "Engram project not configured. Set ENGRAM_PROJECT=<name> or add "
            "'project: <name>' to ~/.openclaw/plugins/engram/config.yaml"
        )
    return project


def get_memory_md_path(cfg: dict) -> Path:
    """Return resolved absolute path to MEMORY.md."""
    return Path(cfg.get("memory_md_path", "~/.openclaw/MEMORY.md")).expanduser()


def get_db_path(cfg: dict) -> Path:
    """Return resolved path to the project's SQLite DB."""
    from engram.config import get_db_path as _core_get_db_path
    engram_cfg = cfg.get("_engram", {})
    project = get_project(cfg)
    return _core_get_db_path(engram_cfg, project)
