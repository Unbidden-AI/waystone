"""Cross-platform IO hardening.

On Windows the console/stdio default to a legacy code page (cp1252) that cannot
encode the Unicode glyphs Waystone prints (✓, ⟁, …). Printing one raises
UnicodeEncodeError, which silently kills hooks mid-run (e.g. the Stop hook
crashes before it can extract, leaving the graph empty). Forcing the std streams
to UTF-8 at entry makes output safe regardless of the console code page.
"""

from __future__ import annotations

import sys


def force_utf8() -> None:
    """Reconfigure stdout/stderr to UTF-8 (errors replaced). Safe no-op where
    the stream doesn't support reconfigure (e.g. test capture, pipes)."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
