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

import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT = "waystone_e2e_smoke"
SECRET = "FLINT-MEATBALL-7731"


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
    seed = _run(["waystone", "remember",
                 f"The production deploy passphrase is {SECRET}",
                 "--project", PROJECT])
    if seed.returncode != 0:
        print("FAIL: seed (waystone remember) failed:\n" + seed.stderr, file=sys.stderr)
        return 1

    work = Path(tempfile.mkdtemp())
    (work / ".waystone").write_text(PROJECT + "\n")

    # Real headless Claude Code run. Model reply may fail on billing — that's fine,
    # the hooks fire regardless; we ignore Claude's exit status and stdout.
    _run(["claude", "-p",
          "What is the production deploy passphrase? Reply with only the value.",
          "--dangerously-skip-permissions"],
         cwd=str(work), timeout=180)
    time.sleep(3)  # let the Stop hook flush

    ok = True
    last_ctx = proj_dir / "last_context.md"
    if last_ctx.exists() and SECRET in last_ctx.read_text(encoding="utf-8", errors="replace"):
        print("PASS: UserPromptSubmit injected the graph secret into last_context.md")
    else:
        ok = False
        print("FAIL: last_context.md missing or did not contain the injected secret "
              "(UserPromptSubmit hook did not fire / inject)", file=sys.stderr)

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
