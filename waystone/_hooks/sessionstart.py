#!/usr/bin/env python3
"""Claude Code SessionStart hook for Waystone.

Runs a fast, offline selfcheck when a session starts and prints a one-line
warning ONLY if something core is broken (silent on success, so there's no
per-session noise). Always exits 0 — it must never block a session.

This catches a broken install/config at the moment it would start mattering,
rather than letting the user discover it via silent extraction failures later.
"""

import sys

from .._logging import hook_entry


@hook_entry
def main() -> None:
    try:
        from waystone._io import force_utf8
        force_utf8()
    except Exception:
        pass

    # Drain stdin (the SessionStart payload: cwd/source/session_id) — not needed.
    try:
        sys.stdin.read()
    except Exception:
        pass

    try:
        from waystone._selfcheck import run_quick
        ok, checks, _ver = run_quick()
        if not ok:
            broken = ", ".join(c["name"] for c in checks if c["fatal"] and not c["ok"])
            print(f"⚠ Waystone selfcheck failed ({broken}). Run 'waystone selfcheck' for details.")
    except Exception:
        # Never let the hook break a session.
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
