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

def install_hooks(hook_dir: Path | None = None) -> tuple[list[str], list[str]]:
    """Add Waystone hooks and status line to ~/.claude/settings.json.

    Uses pip-installed entry points (waystone-hook-submit / waystone-hook-stop)
    when available; falls back to repo-relative script paths when hook_dir is given
    and the entry points are not installed.

    Returns (added, skipped) lists of label strings.
    """
    import shutil as _shutil

    # Prefer entry-point commands (pip install) over raw script paths (repo clone)
    if _shutil.which("waystone-hook-submit"):
        submit_cmd = "waystone-hook-submit"
        stop_cmd = "waystone-hook-stop"
        statusline_cmd = "waystone-statusline" if _shutil.which("waystone-statusline") else (
            f"python {hook_dir / 'statusline.py'}" if hook_dir else "waystone-statusline"
        )
    elif hook_dir:
        submit_cmd = f"python {hook_dir / 'waystone_submit.py'}"
        stop_cmd = f"python {hook_dir / 'waystone_stop.py'}"
        statusline_cmd = f"python {hook_dir / 'statusline.py'}"
    else:
        raise RuntimeError(
            "waystone-hook-submit entry point not found and no hook_dir provided. "
            "Run 'pip install waystone' first."
        )

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
    if any("waystone_submit" in c or "engram_submit" in c or "waystone-hook-submit" in c for c in existing_submit):
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
    if any("waystone_stop" in c or "engram_stop" in c or "waystone-hook-stop" in c for c in existing_stop):
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
# LLM connection test
# ---------------------------------------------------------------------------

# Human-readable explanations for common HTTP status codes from LLM APIs
_HTTP_MESSAGES: dict[int, str] = {
    400: "bad request — the request was malformed (check model name or base URL)",
    401: "invalid API key — the key is missing or not recognised by the provider",
    403: "API key rejected — key may be expired, from the wrong project, or lack quota",
    404: "endpoint not found — check that base_url is correct for this provider",
    408: "request timed out — the provider took too long to respond",
    422: "unprocessable request — check model name and request parameters",
    429: "rate limited — you've hit the provider's request quota; wait and retry",
    500: "provider internal error — this is on the provider's side; try again later",
    502: "bad gateway — provider infrastructure issue; try again later",
    503: "service unavailable — provider is down or overloaded; try again later",
    504: "gateway timeout — provider is slow; try again later",
}


def test_llm_connection(
    base_url: str,
    api_key: str | None = None,
    api_key_env: str | None = None,
    timeout: float = 8.0,
) -> tuple[bool, str]:
    """Make a test request to the LLM endpoint and return (ok, human_message).

    Tries GET /models first (standard OpenAI-compatible endpoint).
    Falls back to a minimal chat completion if /models returns 404.
    Resolves the API key from: explicit api_key arg → api_key_env env var → OPENAI_API_KEY.
    """
    import os as _os

    import httpx as _httpx

    # Resolve key
    resolved_key = (
        api_key
        or (api_key_env and _os.environ.get(api_key_env))
        or _os.environ.get("OPENAI_API_KEY")
        or ""
    )

    headers = {"Authorization": f"Bearer {resolved_key}"} if resolved_key else {}
    models_url = base_url.rstrip("/") + "/models"

    try:
        r = _httpx.get(models_url, headers=headers, timeout=timeout)
    except _httpx.ConnectError:
        return False, f"cannot reach {base_url} — check your internet connection and base URL"
    except _httpx.TimeoutException:
        return False, f"connection to {base_url} timed out after {timeout:.0f}s"
    except Exception as exc:
        return False, f"unexpected error connecting to {base_url}: {exc}"

    code = r.status_code
    if code == 200:
        return True, f"connected successfully ({base_url})"

    if code == 404:
        # Some providers (e.g. Anthropic) don't expose /models — do a minimal chat call
        chat_url = base_url.rstrip("/") + "/chat/completions"
        try:
            rc = _httpx.post(
                chat_url,
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}],
                },
                timeout=timeout,
            )
            if rc.status_code in (200, 400):  # 400 = reached but bad params = auth works
                return True, f"connected successfully ({base_url})"
            code = rc.status_code
        except Exception:
            pass  # fall through to generic /models 404 message

    msg = _HTTP_MESSAGES.get(code, f"HTTP {code} — unexpected response")
    return False, msg


# ---------------------------------------------------------------------------
# MCP server registration
# ---------------------------------------------------------------------------

def _write_mcp_config_directly() -> tuple[bool, str]:
    """Write Waystone MCP entry directly to ~/.claude/claude_desktop_config.json.

    Used as a fallback when the `claude` CLI is not in PATH.
    """
    config_path = Path.home() / ".claude" / "claude_desktop_config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict = {}
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text())
        except Exception:
            pass

    servers = existing.setdefault("mcpServers", {})
    if "waystone" in servers:
        return True, f"MCP server already registered in {config_path}"

    servers["waystone"] = {"command": "waystone", "args": ["mcp-serve"]}
    config_path.write_text(json.dumps(existing, indent=2))
    return True, f"MCP server config written to {config_path}"


def register_mcp_server() -> tuple[bool, str]:
    """Register Waystone as a Claude Code MCP server.

    Tries `claude mcp add` first; falls back to writing claude_desktop_config.json
    directly when the claude CLI is not in PATH (e.g. first-time Windows installs).
    Returns (success, message).
    """
    import shutil as _shutil
    import subprocess as _subprocess

    if _shutil.which("claude"):
        result = _subprocess.run(
            ["claude", "mcp", "add", "waystone", "waystone", "mcp-serve"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True, "MCP server registered via `claude mcp add waystone`"
        # CLI present but command failed — try direct write as fallback
        cli_err = result.stderr.strip() or result.stdout.strip()
    else:
        cli_err = ""

    ok, msg = _write_mcp_config_directly()
    if ok:
        return True, msg
    hint = f" (cli error: {cli_err})" if cli_err else ""
    return False, f"Could not register MCP server{hint}. Add manually to ~/.claude/claude_desktop_config.json"


# ---------------------------------------------------------------------------
# Google Antigravity integration
# ---------------------------------------------------------------------------

ANTIGRAVITY_SETTINGS_PATH = Path.home() / ".gemini" / "antigravity-cli" / "settings.json"
ANTIGRAVITY_MCP_PATH = Path.home() / ".gemini" / "antigravity-cli" / "mcp_config.json"


def install_antigravity_hooks(hook_dir: Path | None = None) -> tuple[list[str], list[str]]:
    """Add Waystone hooks to ~/.gemini/antigravity-cli/settings.json.

    Antigravity uses the same UserPromptSubmit/Stop hook events and JSON
    stdin/stdout format as Claude Code, so the same entry-point commands work.
    Returns (added, skipped) lists.
    """
    import shutil as _shutil

    submit_cmd = "waystone-hook-submit" if _shutil.which("waystone-hook-submit") else (
        f"python {hook_dir / 'waystone_submit.py'}" if hook_dir else "waystone-hook-submit"
    )
    stop_cmd = "waystone-hook-stop" if _shutil.which("waystone-hook-stop") else (
        f"python {hook_dir / 'waystone_stop.py'}" if hook_dir else "waystone-hook-stop"
    )

    settings: dict = {}
    if ANTIGRAVITY_SETTINGS_PATH.exists():
        try:
            settings = json.loads(ANTIGRAVITY_SETTINGS_PATH.read_text()) or {}
        except json.JSONDecodeError:
            pass

    added: list[str] = []
    skipped: list[str] = []
    hooks = settings.setdefault("hooks", {})

    # UserPromptSubmit
    submit_entries = hooks.setdefault("UserPromptSubmit", [])
    existing_submit = [h.get("command", "") for e in submit_entries for h in e.get("hooks", [])]
    if any("waystone" in c for c in existing_submit):
        skipped.append("Antigravity UserPromptSubmit hook")
    else:
        submit_entries.append({"hooks": [{"type": "command", "command": submit_cmd}]})
        added.append("Antigravity UserPromptSubmit hook")

    # Stop
    stop_entries = hooks.setdefault("Stop", [])
    existing_stop = [h.get("command", "") for e in stop_entries for h in e.get("hooks", [])]
    if any("waystone" in c for c in existing_stop):
        skipped.append("Antigravity Stop hook")
    else:
        stop_entries.append({"hooks": [{"type": "command", "command": stop_cmd}]})
        added.append("Antigravity Stop hook")

    if added:
        ANTIGRAVITY_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        ANTIGRAVITY_SETTINGS_PATH.write_text(json.dumps(settings, indent=2))

    return added, skipped


def register_antigravity_mcp() -> tuple[bool, str]:
    """Write Waystone MCP server config to Antigravity's mcp_config.json."""
    ANTIGRAVITY_MCP_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if ANTIGRAVITY_MCP_PATH.exists():
        try:
            existing = json.loads(ANTIGRAVITY_MCP_PATH.read_text())
        except Exception:
            pass
    servers = existing.setdefault("mcpServers", {})
    if "waystone" in servers:
        return True, f"MCP server already in {ANTIGRAVITY_MCP_PATH}"
    servers["waystone"] = {"command": "waystone", "args": ["mcp-serve"]}
    ANTIGRAVITY_MCP_PATH.write_text(json.dumps(existing, indent=2))
    return True, f"MCP server written to {ANTIGRAVITY_MCP_PATH}"


# ---------------------------------------------------------------------------
# OpenAI Codex CLI integration
# ---------------------------------------------------------------------------

CODEX_HOOKS_PATH = Path.home() / ".codex" / "hooks.json"   # hooks (JSON)
CODEX_CONFIG_PATH = Path.home() / ".codex" / "config.toml"  # MCP + settings (TOML)


def _install_hooks_json(
    hooks_path: Path,
    submit_cmd: str,
    stop_cmd: str,
    label_prefix: str,
) -> tuple[list[str], list[str]]:
    """Generic JSON hooks.json installer shared by Codex and OpenHands."""
    settings: dict = {}
    if hooks_path.exists():
        try:
            settings = json.loads(hooks_path.read_text()) or {}
        except json.JSONDecodeError:
            pass

    added: list[str] = []
    skipped: list[str] = []
    hooks = settings.setdefault("hooks", {})

    for event, cmd, label in [
        ("UserPromptSubmit", submit_cmd, f"{label_prefix} UserPromptSubmit hook"),
        ("Stop", stop_cmd, f"{label_prefix} Stop hook"),
    ]:
        entries = hooks.setdefault(event, [])
        existing_cmds = [e.get("command", "") for e in entries if isinstance(e, dict)]
        if any("waystone" in c for c in existing_cmds):
            skipped.append(label)
        else:
            entries.append({"type": "command", "command": cmd})
            added.append(label)

    if added:
        hooks_path.parent.mkdir(parents=True, exist_ok=True)
        hooks_path.write_text(json.dumps(settings, indent=2))

    return added, skipped


def install_codex_hooks(hook_dir: Path | None = None) -> tuple[list[str], list[str]]:
    """Add Waystone hooks to ~/.codex/hooks.json."""
    import shutil as _shutil
    submit_cmd = "waystone-hook-submit" if _shutil.which("waystone-hook-submit") else (
        f"python {hook_dir / 'waystone_submit.py'}" if hook_dir else "waystone-hook-submit"
    )
    stop_cmd = "waystone-hook-stop" if _shutil.which("waystone-hook-stop") else (
        f"python {hook_dir / 'waystone_stop.py'}" if hook_dir else "waystone-hook-stop"
    )
    return _install_hooks_json(CODEX_HOOKS_PATH, submit_cmd, stop_cmd, "Codex")


def register_codex_mcp() -> tuple[bool, str]:
    """Add Waystone as an MCP server in ~/.codex/config.toml."""
    try:
        import tomlkit
    except ImportError:
        existing = CODEX_CONFIG_PATH.read_text() if CODEX_CONFIG_PATH.exists() else ""
        if "waystone" in existing and "mcp_servers" in existing:
            return True, f"MCP server already in {CODEX_CONFIG_PATH}"
        snippet = '\n[mcp_servers.waystone]\ncommand = "waystone"\nargs = ["mcp-serve"]\n'
        CODEX_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CODEX_CONFIG_PATH.write_text(existing + snippet)
        return True, f"MCP server appended to {CODEX_CONFIG_PATH}"

    doc = tomlkit.parse(CODEX_CONFIG_PATH.read_text()) if CODEX_CONFIG_PATH.exists() else tomlkit.document()
    mcp = doc.get("mcp_servers", tomlkit.table())
    if "waystone" in mcp:
        return True, f"MCP server already in {CODEX_CONFIG_PATH}"
    entry = tomlkit.table()
    entry.add("command", "waystone")
    entry.add("args", ["mcp-serve"])
    mcp["waystone"] = entry
    doc["mcp_servers"] = mcp
    CODEX_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CODEX_CONFIG_PATH.write_text(tomlkit.dumps(doc))
    return True, f"MCP server written to {CODEX_CONFIG_PATH}"


# ---------------------------------------------------------------------------
# OpenHands integration  (https://docs.openhands.dev)
# ---------------------------------------------------------------------------

OPENHANDS_HOOKS_PATH = Path.home() / ".openhands" / "hooks.json"


def install_openhands_hooks(hook_dir: Path | None = None) -> tuple[list[str], list[str]]:
    """Add Waystone hooks to ~/.openhands/hooks.json.

    OpenHands uses the same UserPromptSubmit/Stop events and additionalContext
    protocol as Claude Code (added in OpenHands 1.6.0, March 2026).
    """
    import shutil as _shutil
    submit_cmd = "waystone-hook-submit" if _shutil.which("waystone-hook-submit") else (
        f"python {hook_dir / 'waystone_submit.py'}" if hook_dir else "waystone-hook-submit"
    )
    stop_cmd = "waystone-hook-stop" if _shutil.which("waystone-hook-stop") else (
        f"python {hook_dir / 'waystone_stop.py'}" if hook_dir else "waystone-hook-stop"
    )
    return _install_hooks_json(OPENHANDS_HOOKS_PATH, submit_cmd, stop_cmd, "OpenHands")


# ---------------------------------------------------------------------------
# Integration mode persistence
# ---------------------------------------------------------------------------

_INTEGRATION_MODES = {
    "1": "mcp",
    "2": "hooks",
    "3": "both",
    "4": "skip",
}


def save_integration_mode(choice: str, extra_tools: list[str] | None = None) -> None:
    """Persist the chosen integration mode and extra tools to ~/.waystone/config.yaml."""
    mode = _INTEGRATION_MODES.get(choice, "skip")
    WAYSTONE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if WAYSTONE_CONFIG_PATH.exists():
        with open(WAYSTONE_CONFIG_PATH) as f:
            existing = yaml.safe_load(f) or {}
    existing["integration_mode"] = mode
    if extra_tools is not None:
        existing["integration_tools"] = extra_tools
    with open(WAYSTONE_CONFIG_PATH, "w") as f:
        yaml.dump(existing, f, default_flow_style=False, sort_keys=False)
