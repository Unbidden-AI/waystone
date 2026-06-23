# Windows verification checklist

CI (`test-windows` job) covers automated regressions: encoding, file I/O, path
handling, sqlite-vec load, and the test suite on `windows-latest`. It does **not**
cover what only a real first-run on a real machine reveals — install UX, the
cold-start hang, and Claude Code hook spawn semantics. Run this once on a clean
Windows box (PowerShell) before calling Windows "verified."

> Requires Python 3.13 (3.14 breaks sqlite-vec). A `GEMINI_API_KEY` is only
> needed for the extraction steps (6–7); the rest are offline.

## 1. Clean install from PyPI (not editable)

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install waystone
waystone --version          # prints a version, no crash
```

- [ ] Installs without a build error (sqlite-vec wheel resolves on Windows)
- [ ] `--version` prints; no `charmap`/cp1252 traceback on the ✓ glyphs

## 2. First-run cold-start UX (the sqlite-vec / Defender stall)

```powershell
waystone doctor
```

- [ ] First `import sqlite_vec` may take **minutes** while Defender scans the
      native binary. Note whether the user sees a *message* or a silent hang —
      a multi-minute hang with no output reads as "broken." (Mitigated on
      count-only paths via `vec_enabled=False`; this is the one path that loads it.)
- [ ] `doctor` reports config / LLM / marker / sqlite-vec status without crashing

## 3. Init + manual graph ops (offline)

```powershell
waystone init winproj
waystone show winproj
waystone query winproj "anything"
```

- [ ] Project dir created under `%USERPROFILE%\.waystone\projects\` (path handling)
- [ ] `show` / `query` run clean on an empty graph (no phantom errors)

## 4. Claude Code hook wiring (the spawn-semantics risk)

Install the hooks, then run a real Claude Code session in a marked dir.

```powershell
waystone pip-configure-hooks      # or the documented hook install path
cd <a project with a .waystone marker>
```

- [ ] UserPromptSubmit injects context (check `waystone last-context`)
- [ ] Background extraction worker actually **spawns and survives** on Windows
      process semantics — `python -m waystone._hooks.worker` must not crash on
      spawn (this is the exact class of silent failure that bit us before).
      Confirm with `waystone doctor` (reads `~/.waystone/logs/` for worker errors).

## 5. Statusline (legacy console glyphs)

- [ ] Statusline renders (ctx %, cost, node counts) without a cp1252 crash

## 6–7. Real extraction + query (needs GEMINI_API_KEY)

```powershell
$env:GEMINI_API_KEY = "..."
waystone verify                   # does one real ~$0.001 extraction in a temp HOME
```

- [ ] `verify` completes a real extraction loop end-to-end
- [ ] Nodes land in the DB and a query retrieves them

## Report

Note any step that hangs, crashes, or behaves differently from macOS/Linux.
The cold-start UX (step 2) and the hook spawn (step 4) are the two highest-risk
items — everything else is largely covered by the `test-windows` CI job.
