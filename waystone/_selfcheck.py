"""Shared health-check logic used by `waystone selfcheck`, the SessionStart hook,
and `waystone configure`.

Kept deliberately light (no heavy imports) so the SessionStart hook can run it on
every session start without adding latency: package import + version, config
loads, API key resolves, optional deps. No network, no LLM calls.
"""
from __future__ import annotations


def run_quick(config_path=None) -> tuple[bool, list[dict], str]:
    """Run the fast, offline checks. Returns (ok, checks, version).

    Each check is ``{"name", "ok", "detail", "fatal"}``. ``ok`` (overall) is True
    when every *fatal* check passes. Non-fatal checks (api key, sqlite-vec) are
    reported but don't fail the result — a fresh keyless/local setup still "runs".
    """
    from .config import load_config, resolve_llm_api_key

    checks: list[dict] = []

    def add(name, ok, detail="", fatal=True):
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "fatal": fatal})

    # Version (and implicitly: the package imports — we're running its code).
    try:
        from importlib.metadata import version as _v
        ver = _v("waystone")
    except Exception:  # noqa: BLE001
        ver = "unknown"
    add("import waystone", True, ver)

    # Config loads.
    try:
        config = load_config(config_path)
        add("config loads", True)
    except Exception as e:  # noqa: BLE001
        config = {}
        add("config loads", False, str(e)[:120])

    # API key resolves (non-fatal — keyless local models are valid).
    key, source = resolve_llm_api_key(config.get("llm", {}) or {})
    add("api key resolves", key is not None,
        source if key else "none configured (ok for keyless local models)", fatal=False)

    # sqlite-vec extension (non-fatal — keyword fallback exists).
    try:
        import sqlite_vec  # noqa: F401
        add("sqlite-vec available", True, fatal=False)
    except Exception as e:  # noqa: BLE001
        add("sqlite-vec available", False, f"{type(e).__name__}: keyword-only fallback", fatal=False)

    ok = all(c["ok"] for c in checks if c["fatal"])
    return ok, checks, ver
