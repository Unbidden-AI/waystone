#!/usr/bin/env python3
"""Install Waystone hooks, status line, and CLAUDE.md guidance into Claude Code.

Run once per machine:
  python hooks/install.py

What it does:
  1. Adds a UserPromptSubmit hook that queries your project graph and injects
     relevant context into each Claude prompt.
  2. Adds a Stop hook that records each session transcript to
     ~/.waystone/transcripts/<project>/.
  3. Configures the status line to show retrieval metrics.
  4. Appends a Waystone usage section to ~/.claude/CLAUDE.md.
  5. Backs up settings.json before modifying it.

After installing:
  1. Mark your project directory:
       echo 'myproject' > /path/to/your/project/.waystone
  2. Start a Claude Code session — transcripts are recorded automatically.
  3. Extract a recorded transcript:
       waystone extract myproject ~/.waystone/transcripts/myproject/latest.md
  4. View what was injected last:
       waystone last-context
"""

import sys
from pathlib import Path

# Allow running from the repo root without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from waystone.setup import (  # noqa: E402
    CLAUDE_MD_PATH,
    SETTINGS_PATH,
    install_claude_md,
    install_hooks,
)

HOOK_DIR = Path(__file__).resolve().parent


def main():
    print("Waystone Installer")
    print("=" * 40)

    added, skipped = install_hooks(hook_dir=HOOK_DIR)

    for label in added:
        print(f"\n  ✓  {label}: added")
    for label in skipped:
        print(f"\n  –  {label}: already installed (skipping)")

    if added:
        print(f"\nSettings written: {SETTINGS_PATH}")
    else:
        print("\nNo changes to settings.json needed.")

    if install_claude_md():
        print(f"CLAUDE.md: Waystone section appended → {CLAUDE_MD_PATH}")
    else:
        print("CLAUDE.md: Waystone section already present (skipping)")

    print("\nNext steps:")
    print("  1. Restart Claude Code to pick up the new hooks and CLAUDE.md.")
    print("  2. Mark your project directory:")
    print("       echo 'myproject' > /path/to/project/.waystone")
    print("  3. Start a session — transcripts are saved automatically on Stop.")
    print("  4. Extract a transcript:")
    print("       waystone extract myproject ~/.waystone/transcripts/myproject/latest.md")
    print("  5. Future sessions will auto-inject context. View what was injected:")
    print("       waystone last-context")


if __name__ == "__main__":
    main()
