#!/usr/bin/env python3
"""Install Context Broker hooks and status line into ~/.claude/settings.json.

Run once per machine:
  python hooks/install.py

What it does:
  1. Adds a UserPromptSubmit hook that queries your project graph and injects
     relevant context into each Claude prompt.
  2. Adds a Stop hook that records each session transcript to
     ~/.context-broker/transcripts/<project>/.
  3. Configures the status line to show CB retrieval metrics.
  4. Backs up your existing settings.json before modifying it.

After installing:
  1. Mark your project directory:
       echo 'myproject' > /path/to/your/project/.context-broker
  2. Start a Claude Code session — transcripts are recorded automatically.
  3. Extract a recorded transcript:
       ctx extract myproject ~/.context-broker/transcripts/myproject/latest.md
  4. View what was injected last:
       ctx last-context
"""

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
HOOK_DIR = Path(__file__).resolve().parent

SUBMIT_HOOK_CMD = f"python {HOOK_DIR / 'context_broker_submit.py'}"
STOP_HOOK_CMD = f"python {HOOK_DIR / 'context_broker_stop.py'}"
STATUSLINE_CMD = f"python {HOOK_DIR / 'statusline.py'}"


def main():
    print("Context Broker Hook Installer")
    print("=" * 40)

    # Load existing settings
    settings: dict = {}
    if SETTINGS_PATH.exists():
        try:
            settings = json.loads(SETTINGS_PATH.read_text()) or {}
        except json.JSONDecodeError:
            print(f"Warning: could not parse {SETTINGS_PATH} — will create fresh copy")

        # Backup
        backup = SETTINGS_PATH.with_suffix(
            f".json.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        shutil.copy(SETTINGS_PATH, backup)
        print(f"Backed up settings to: {backup}")

    changed = False

    # --- UserPromptSubmit hook ---
    hooks = settings.setdefault("hooks", {})
    submit_entries = hooks.setdefault("UserPromptSubmit", [])

    existing_cmds = [
        h.get("command", "")
        for entry in submit_entries
        for h in entry.get("hooks", [])
    ]
    if any("context_broker_submit" in c for c in existing_cmds):
        print("\nUserPromptSubmit hook: already installed (skipping)")
    else:
        submit_entries.append({
            "hooks": [{"type": "command", "command": SUBMIT_HOOK_CMD}]
        })
        print(f"\nUserPromptSubmit hook: added")
        print(f"  {SUBMIT_HOOK_CMD}")
        changed = True

    # --- Stop hook (transcript recording) ---
    stop_entries = hooks.setdefault("Stop", [])

    existing_stop_cmds = [
        h.get("command", "")
        for entry in stop_entries
        for h in entry.get("hooks", [])
    ]
    if any("context_broker_stop" in c for c in existing_stop_cmds):
        print("\nStop hook: already installed (skipping)")
    else:
        stop_entries.append({
            "hooks": [{"type": "command", "command": STOP_HOOK_CMD}]
        })
        print(f"\nStop hook (transcript recording): added")
        print(f"  {STOP_HOOK_CMD}")
        changed = True

    # --- Status line ---
    if "statusLine" in settings:
        existing = settings["statusLine"]
        existing_cmd = existing.get("command", "") if isinstance(existing, dict) else ""
        if "statusline" in existing_cmd.lower() and "context_broker" in existing_cmd.lower():
            print("\nStatus line: already configured (skipping)")
        else:
            print(f"\nStatus line: already set to another command:")
            print(f"  {existing_cmd or existing}")
            print(f"  To use Context Broker status line instead, set:")
            print(f"    statusLine.command = \"{STATUSLINE_CMD}\"")
    else:
        settings["statusLine"] = {"type": "command", "command": STATUSLINE_CMD}
        print(f"\nStatus line: configured")
        print(f"  {STATUSLINE_CMD}")
        changed = True

    # Write
    if changed:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(settings, indent=2))
        print(f"\nSettings written: {SETTINGS_PATH}")
    else:
        print("\nNo changes needed.")

    print("\nNext steps:")
    print("  1. Mark your project directory:")
    print("       echo 'myproject' > /path/to/project/.context-broker")
    print("  2. Start a Claude Code session — transcripts are recorded automatically.")
    print("  3. Extract a recorded transcript:")
    print("       ctx extract myproject ~/.context-broker/transcripts/myproject/latest.md")
    print("  4. Future sessions will auto-inject context. View what was injected:")
    print("       ctx last-context")


if __name__ == "__main__":
    main()
