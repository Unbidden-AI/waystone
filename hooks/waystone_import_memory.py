#!/usr/bin/env python3
"""Claude Code SessionEnd hook for Waystone (repo-clone fallback shim).

The normal install path uses the pip entry point ``waystone-hook-import-memory``.
This thin wrapper exists for repo clones that haven't pip-installed: it puts the
repo root on sys.path and delegates to the package implementation, so the hook
logic lives in exactly one place (``waystone/_hooks/import_memory.py``).
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from waystone._hooks.import_memory import main  # noqa: E402

if __name__ == "__main__":
    main()
