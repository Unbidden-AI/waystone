"""Shared installation helpers for hooks/install.py and `waystone configure`."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

import yaml

SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
CLAUDE_MD_PATH = Path.home() / ".claude" / "CLAUDE.md"
WAYSTONE_CONFIG_PATH = Path.home() / ".waystone" / "config.yaml"

CLAUDE_MD_MARKER = "## Waystone (Context Intelligence)"
CLAUDE_MD_SECTION = """
## Waystone (Context Intelligence)

Waystone is installed. It maintains a knowledge graph of decisions, constraints,
and facts extracted from past Claude Code sessions.

Context is automatically injected before each prompt when a `.waystone` project
marker file is present in (or above) the working directory.

Key commands:
- `waystone query <project> "<question>" [--stats]` — query the graph manually
- `waystone last-context` — see exactly what was injected into the last prompt
- `waystone extract <project> <file>` — extract a transcript or document into the graph
- `waystone onboard <project>` — bulk-import recent Claude Code sessions
- `waystone show <project>` — inspect the graph (node count, types, recent entries)

When Waystone context appears above a user message, treat it as authoritative
project history — prefer it over generic assumptions about the codebase.
"""

# Known LLM providers and their defaults
PROVIDERS: dict[str, dict] = {
    "gemini": {
        "label": "Gemini (recommended) — fast, affordable, best recall",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "default_model": "gemini-2.5-flash-lite",
        "models": ["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.5-pro"],
        "api_key_env": "GEMINI_API_KEY",
        "key_url": "https://aistudio.google.com/app/apikey",
    },
    "openai": {
        "label": "OpenAI — GPT-4o-mini or GPT-4o",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "models": ["gpt-4o-mini", "gpt-4o"],
        "api_key_env": "OPENAI_API_KEY",
        "key_url": "https://platform.openai.com/api-keys",
    },
    "anthropic": {
        "label": "Anthropic — Claude Haiku or Sonnet",
        "base_url": "https://api.anthropic.com/v1",
        "default_model": "claude-haiku-4-5-20251001",
        "models": ["claude-haiku-4-5-20251001", "claude-sonnet-4-6"],
        "api_key_env": "ANTHROPIC_API_KEY",
        "key_url": "https://console.anthropic.com/settings/keys",
    },
    "local": {
        "label": "Local (LM Studio / Ollama) — no API key needed",
        "base_url": "http://localhost:1234/v1",
        "default_model": "",
        "models": [],
        "api_key_env": None,
        "key_url": None,
    },
    "custom": {
        "label": "Custom — enter your own base URL and model",
        "base_url": "",
        "default_model": "",
        "models": [],
        "api_key_env": "OPENAI_API_KEY",
        "key_url": None,
    },
}


# ---------------------------------------------------------------------------
# LLM config
# ---------------------------------------------------------------------------

def write_llm_config(
    base_url: str,
    model: str,
    api_key_env: str | None,
    api_key: str | None,
    max_tokens: int = 65000,
    timeout: float = 120.0,
) -> Path:
    """Write (or update) the llm section of ~/.waystone/config.yaml.

    Only writes the llm block — everything else falls back to DEFAULTS at
    load time, so the file stays minimal and readable.
    Returns the path written.
    """
    WAYSTONE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    existing: dict = {}
    if WAYSTONE_CONFIG_PATH.exists():
        with open(WAYSTONE_CONFIG_PATH) as f:
            existing = yaml.safe_load(f) or {}

    llm: dict = {
        "base_url": base_url,
        "model": model,
        "temperature": 0.0,
        "max_tokens": max_tokens,
        "timeout": timeout,
    }
    if api_key_env:
        llm["api_key_env"] = api_key_env
    if api_key:
        llm["api_key"] = api_key

    existing["llm"] = llm
    with open(WAYSTONE_CONFIG_PATH, "w") as f:
        yaml.dump(existing, f, default_flow_style=False, sort_keys=False)

    return WAYSTONE_CONFIG_PATH


# ---------------------------------------------------------------------------
# Claude Code settings.json
# ---------------------------------------------------------------------------

def install_hooks(hook_dir: Path) -> tuple[list[str], list[str]]:
    """Add Waystone hooks and status line to ~/.claude/settings.json.

    Returns (added, skipped) lists of label strings.
    """
    submit_cmd = f"python {hook_dir / 'waystone_submit.py'}"
    stop_cmd = f"python {hook_dir / 'waystone_stop.py'}"
    statusline_cmd = f"python {hook_dir / 'statusline.py'}"

    settings: dict = {}
    if SETTINGS_PATH.exists():
        try:
            settings = json.loads(SETTINGS_PATH.read_text()) or {}
        except json.JSONDecodeError:
            pass

        backup = SETTINGS_PATH.with_suffix(
            f".json.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        shutil.copy(SETTINGS_PATH, backup)

    added: list[str] = []
    skipped: list[str] = []
    hooks = settings.setdefault("hooks", {})

    # UserPromptSubmit
    submit_entries = hooks.setdefault("UserPromptSubmit", [])
    existing_submit = [
        h.get("command", "")
        for e in submit_entries
        for h in e.get("hooks", [])
    ]
    if any("waystone_submit" in c or "engram_submit" in c for c in existing_submit):
        skipped.append("UserPromptSubmit hook")
    else:
        submit_entries.append({"hooks": [{"type": "command", "command": submit_cmd}]})
        added.append("UserPromptSubmit hook")

    # Stop
    stop_entries = hooks.setdefault("Stop", [])
    existing_stop = [
        h.get("command", "")
        for e in stop_entries
        for h in e.get("hooks", [])
    ]
    if any("waystone_stop" in c or "engram_stop" in c for c in existing_stop):
        skipped.append("Stop hook")
    else:
        stop_entries.append({"hooks": [{"type": "command", "command": stop_cmd}]})
        added.append("Stop hook")

    # Status line
    existing_sl = settings.get("statusLine")
    existing_sl_cmd = (
        existing_sl.get("command", "") if isinstance(existing_sl, dict) else str(existing_sl or "")
    )
    if "statusline" in existing_sl_cmd.lower() and "waystone" in existing_sl_cmd.lower():
        skipped.append("status line")
    elif existing_sl and "waystone" not in existing_sl_cmd.lower():
        # Something else owns the status line — don't clobber
        skipped.append(f"status line (already set: {existing_sl_cmd!r})")
    else:
        settings["statusLine"] = {"type": "command", "command": statusline_cmd}
        added.append("status line")

    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2))
    return added, skipped


# ---------------------------------------------------------------------------
# CLAUDE.md
# ---------------------------------------------------------------------------

def install_claude_md() -> bool:
    """Append Waystone usage section to ~/.claude/CLAUDE.md.

    Returns True if the file was modified.
    """
    existing = CLAUDE_MD_PATH.read_text() if CLAUDE_MD_PATH.exists() else ""
    if CLAUDE_MD_MARKER in existing:
        return False

    CLAUDE_MD_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CLAUDE_MD_PATH.open("a") as f:
        f.write(CLAUDE_MD_SECTION)
    return True


# ---------------------------------------------------------------------------
# MCP server registration
# ---------------------------------------------------------------------------

def register_mcp_server() -> tuple[bool, str]:
    """Register Waystone as a Claude Code MCP server via `claude mcp add`.

    Returns (success, message).
    """
    import shutil as _shutil
    import subprocess as _subprocess

    if not _shutil.which("claude"):
        snippet = json.dumps(
            {"mcpServers": {"waystone": {"command": "waystone", "args": ["mcp-serve"]}}},
            indent=2,
        )
        return False, (
            "claude CLI not found in PATH.\n"
            "Add this to ~/.claude/claude_desktop_config.json manually:\n\n"
            + snippet
        )

    result = _subprocess.run(
        ["claude", "mcp", "add", "waystone", "waystone", "mcp-serve"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return True, "MCP server registered via `claude mcp add waystone`"
    return False, f"claude mcp add failed: {result.stderr.strip() or result.stdout.strip()}"
