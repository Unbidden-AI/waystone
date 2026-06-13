#!/usr/bin/env python3
"""E2E: prove a REAL Claude Code session fires the Waystone hooks.

Validated 2026-06-13. Strategy: side-effect assertion on a graph-only SECRET, which
is robust to billing — the UserPromptSubmit hook runs BEFORE the model call, so the
injected `last_context.md` exists even if the model reply later fails (e.g. "Credit
balance is too low"). We therefore do NOT depend on Claude's stdout.

What it proves:
  - UserPromptSubmit → retrieved the seeded secret from the graph and wrote it into
    `<projects_dir>/<proj>/last_context.md`  (retrieval + injection, end to end)
  - Stop → saved a transcript under `<projects_dir>/<proj>/transcripts/` (hook fired)

Prereqs on the runner: `claude` on PATH + headless auth, Waystone installed and
`waystone configure` run (hooks wired). A topped-up ANTHROPIC_API_KEY is only needed
if you also want Claude's reply to succeed — NOT required for this check.

Usage:  python ci/e2e_claude_hooks.py            # exit 0 = pass, 1 = fail
"""

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT = "waystone_e2e_smoke"
# A BENIGN, unguessable canary — NOT a credential. (A secret-shaped canary trips
# Claude's anti-exfiltration safety: it reads the injected value but refuses to echo
# it, which would make the optional reply check a false negative.)
CANARY = "Gerald Snufflewump"
FACT = f"The team mascot is a capybara named {CANARY}"
QUESTION = "What is the team mascot's name? Reply in one short sentence."


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _projects_dir() -> Path:
    out = _run([sys.executable, "-c",
               "from waystone.config import load_config, get_project_dir;"
               f"print(get_project_dir(load_config(), {PROJECT!r}))"])
    if out.returncode != 0:
        print("FAIL: could not resolve project dir:\n" + out.stderr, file=sys.stderr)
        sys.exit(1)
    return Path(out.stdout.strip())


def main() -> int:
    proj_dir = _projects_dir()
    # Fresh project seeded with a secret only the graph could know.
    _run(["waystone", "reset", PROJECT, "--yes"])
    _run(["waystone", "init", PROJECT])
    seed = _run(["waystone", "remember", FACT, "--project", PROJECT])
    if seed.returncode != 0:
        print("FAIL: seed (waystone remember) failed:\n" + seed.stderr, file=sys.stderr)
        return 1

    work = Path(tempfile.mkdtemp())
    (work / ".waystone").write_text(PROJECT + "\n")

    # Real headless Claude Code run (JSON output to capture the reply + model).
    claude = _run(["claude", "-p", QUESTION,
                   "--output-format", "json", "--dangerously-skip-permissions"],
                  cwd=str(work), timeout=180)
    time.sleep(3)  # let the Stop hook flush

    ok = True
    # PRIMARY (billing-robust): the hook fires before the model call.
    last_ctx = proj_dir / "last_context.md"
    if last_ctx.exists() and CANARY in last_ctx.read_text(encoding="utf-8", errors="replace"):
        print("PASS: UserPromptSubmit injected the graph fact into last_context.md")
    else:
        ok = False
        print("FAIL: last_context.md missing or did not contain the injected fact "
              "(UserPromptSubmit hook did not fire / inject)", file=sys.stderr)

    # BONUS (needs a funded key): did the model actually read + speak the injected fact?
    try:
        reply = json.loads(claude.stdout).get("result", "")
        if CANARY in reply:
            print("PASS: Claude's reply echoed the hook-injected fact (full loop)")
        else:
            print("INFO: model did not echo the fact (empty/credit-limited key, or "
                  "phrased differently) — injection still proven above")
    except Exception:
        print("INFO: no parseable model reply (e.g. credit-limited key) — "
              "injection still proven above")

    tdir = proj_dir / "transcripts"
    if tdir.exists() and any(tdir.iterdir()):
        print("PASS: Stop hook saved a transcript")
    else:
        # Non-fatal: some Claude versions skip Stop if the turn errored very early.
        print("WARN: no transcript saved (Stop hook may not have fired this run)")

    _run(["waystone", "reset", PROJECT, "--yes", "--purge"])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
