"""Build a REDACTED diagnostic bundle for bug reports.

`waystone diagnostics` collects version + config + logs + graph stats into one
shareable file so a stuck user doesn't have to hunt for log paths or scrub their own
keys. The hard requirement here is **redaction**: secrets (API keys, license tokens,
Stripe keys, DSN passwords, PEM keys) must never survive into the bundle, and the
graph's *content* is never included — only counts.
"""

from __future__ import annotations

import os
import platform
import re
from datetime import datetime, timezone
from pathlib import Path

_REDACTED = "***REDACTED***"

# Mapping keys whose VALUE is a secret wherever it appears (config, env dumps).
_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|secret|token|password|passwd|pwd|license|priv[_-]?key|dsn|"
    r"authorization|bearer|webhook|credential)",
    re.I,
)

# DSN password is redacted in place (scheme + user kept for diagnosis).
_DSN_PW_RE = re.compile(r"(postgres(?:ql)?://[^:@/\s]+:)[^@/\s]+(@)")

# Value patterns that are secrets wherever they appear in free text.
_SECRET_VALUE_RES = [
    re.compile(r"\b(?:sk|rk)_[A-Za-z0-9_]{6,}"),              # Stripe secret/restricted key (sk_live_…)
    re.compile(r"\bwhsec_[A-Za-z0-9_]{6,}"),                  # Stripe webhook secret
    re.compile(r"\bwaystone_[A-Za-z0-9_\-]{8,}"),              # Waystone API / member keys
    re.compile(r"\bBearer\s+\S+", re.I),                       # bearer tokens
    re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),  # JWT (license/Clerk)
    re.compile(r"\bAIza[A-Za-z0-9_\-]{20,}"),                  # Google API key
    re.compile(r"-----BEGIN [^-]+-----.*?-----END [^-]+-----", re.S),       # PEM private key
]


def redact_text(s: str) -> str:
    """Scrub secret value-patterns from a free-text blob (log lines, env, etc.)."""
    if not s:
        return s
    s = _DSN_PW_RE.sub(r"\1" + _REDACTED + r"\2", s)
    for rx in _SECRET_VALUE_RES:
        s = rx.sub(_REDACTED, s)
    return s


def redact_mapping(obj):
    """Deep-copy a config-like structure: any value under a secret-named key becomes
    ``***``, and every string leaf is run through ``redact_text``."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and _SECRET_KEY_RE.search(k):
                out[k] = _REDACTED
            else:
                out[k] = redact_mapping(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact_mapping(x) for x in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj


def _tail(path: Path, lines: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"(could not read: {e})"
    rows = text.splitlines()
    return "\n".join(rows[-lines:]) if rows else "(empty)"


def _project_stats(config: dict) -> str:
    base = Path(config.get("projects_dir", "~/.waystone/projects")).expanduser()
    if not base.exists():
        return "(no projects dir)"
    out = []
    for p in sorted(base.iterdir()):
        if not p.is_dir():
            continue
        db = p / "context.db"
        if not db.exists():
            out.append(f"  {p.name}: (no graph)")
            continue
        try:
            from .store import GraphStore
            s = GraphStore(db)
            try:
                n = s.get_stats().get("node_count", "?")
            finally:
                s.close()
            out.append(f"  {p.name}: {n} nodes")
        except Exception as e:
            out.append(f"  {p.name}: (stats unavailable: {e})")
    return "\n".join(out) or "(no projects)"


def build_report(config_path: str | None = None, log_lines: int = 200,
                 now: datetime | None = None) -> str:
    """Assemble the redacted diagnostic bundle as a single text blob."""
    import yaml  # local: only needed here

    stamp = (now or datetime.now(timezone.utc)).isoformat()
    parts: list[str] = []

    def section(title: str, body: str) -> None:
        parts.append(f"===== {title} =====\n{body.rstrip()}\n")

    # --- versions / platform ---
    try:
        from . import __version__ as ver
    except Exception:
        ver = "unknown"
    section("Waystone diagnostics", f"generated: {stamp}\nwaystone: {ver}\n"
            f"python: {platform.python_version()}\nplatform: {platform.platform()}")

    # --- selfcheck ---
    try:
        from ._selfcheck import run_quick
        _ok, checks, _v = run_quick(config_path)
        lines = [f"  {'OK ' if c['ok'] else 'FAIL'} {c['name']}"
                 + (f" ({c['detail']})" if c.get("detail") else "") for c in checks]
        section("Self-check", "\n".join(lines) or "(none)")
    except Exception as e:
        section("Self-check", f"(failed to run: {e})")

    # --- config (redacted) ---
    try:
        from .config import load_config
        cfg = load_config(config_path)
        section("Config (secrets redacted)", yaml.safe_dump(redact_mapping(cfg), sort_keys=True))
    except Exception as e:
        cfg = {}
        section("Config (secrets redacted)", f"(failed to load: {e})")

    # --- environment variable NAMES present (never values) ---
    prefixes = ("WAYSTONE", "LLM", "SENTRY", "STRIPE", "CB_", "GEMINI", "OPENAI",
                "ANTHROPIC", "DATABASE_URL", "CLERK", "RESEND")
    names = sorted(k for k in os.environ
                   if any(k == p or k.startswith(p) for p in prefixes))
    section("Relevant env vars set (NAMES only)", "\n".join(f"  {n}" for n in names) or "(none)")

    # --- graph stats (counts only, never content) ---
    section("Projects (counts only)", _project_stats(cfg))

    # --- logs (redacted) ---
    log_dir = Path.home() / ".waystone" / "logs"
    for fname in ("hooks.log", "mcp_server.log"):
        f = log_dir / fname
        body = redact_text(_tail(f, log_lines)) if f.exists() else "(not present)"
        section(f"Log: {fname} (last {log_lines} lines, redacted)", body)

    return "\n".join(parts)
