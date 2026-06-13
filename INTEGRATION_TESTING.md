# Integration Testing — real-product verification on self-hosted runners

> **Status (2026-06-13): both Claude Code and Hermes e2e are AUTHORED + validated.**
> Committed runner scripts:
> - `ci/e2e_claude_hooks.py` — a real `claude -p` run; asserts the hooks injected a
>   graph-only secret into `last_context.md` + saved a transcript. **Credit-robust:**
>   the UserPromptSubmit hook fires *before* the model call, so injection is verifiable
>   even when Claude's reply fails on billing (see the credit note below).
> - `ci/e2e_hermes_provider.py` — binds the provider to **real hermes-agent**'s
>   `MemoryProvider` ABC, instantiates it, and asserts prefetch injects (validated
>   against `hermes-agent==0.16.0`; PyPI package name is `hermes-agent`).
>
> **⚠ Anthropic credit:** on the dev box, headless `claude -p` returned *"Credit balance
> is too low"* — it auths via the `ANTHROPIC_API_KEY` env (pay-go), which is empty.
> The injection check passes anyway (hook runs pre-model). To also get Claude's *reply*
> green (and any token-spending e2e), top up that key on the runner.

Waystone's CI has two tiers, split by whether a test can run on a blank machine:

| Tier | Runs where | Needs | What it proves |
|------|-----------|-------|----------------|
| **Hermetic** (existing) | GitHub's disposable Ubuntu runners | nothing external | the package installs, imports, config loads, hooks *execute*, MCP tools register (`waystone selfcheck --deep`) |
| **Integration** (this doc) | a **self-hosted runner** you control | the real product (Claude Code / OpenClaw / Hermes) **+ its auth** | the product actually *invokes* our hooks / loads our MCP server end-to-end |

The hermetic tier already runs post-publish (see `.github/workflows/publish.yml` → `smoke` job). It can't test "does Claude Code actually fire our hooks," because GitHub's runners have no Claude install and no login. That's what the integration tier is for.

> **The make-or-break question for each product: can it authenticate _headlessly_** (no human at a keyboard)? Claude Code can (API key / token in env). **Verify this first for OpenClaw and Hermes** — if a product requires interactive login, the runner VM must be set up by hand and kept warm; it can't be re-provisioned automatically.

---

## 1. Stand up a self-hosted runner (Claude Code example)

A self-hosted runner is just a machine you own that registers with the GitHub repo and waits for jobs labelled for it. Steps for a `claude-runner`:

1. **Provision a VM** (e.g. Ubuntu 22.04+, small is fine). Use a dedicated non-root user.
2. **Install runtimes:**
   ```bash
   sudo apt-get update && sudo apt-get install -y python3.13 python3-pip nodejs npm
   ```
3. **Install Claude Code** (the product under test):
   ```bash
   npm install -g @anthropic-ai/claude-code      # confirm current install command in Claude Code docs
   claude --version
   ```
4. **Headless auth for Claude Code** — set an API key in the runner's environment (no interactive login):
   ```bash
   export ANTHROPIC_API_KEY=sk-ant-...           # store as a runner secret / systemd EnvironmentFile, NOT in the repo
   ```
   (Alternatively `claude setup-token` once if you prefer a long-lived token. API key in env is simplest for automation.)
5. **Install + configure Waystone** on the runner so its hooks are wired:
   ```bash
   pip install waystone
   waystone configure --non-interactive        # or hand-write ~/.waystone/config.yaml with the extraction key
   ```
6. **Register the GitHub Actions runner** (repo → Settings → Actions → Runners → New self-hosted runner) and **give it a label** `claude-runner`. Run it as a service:
   ```bash
   ./config.sh --url https://github.com/Unbidden-AI/waystone --token <reg-token> --labels claude-runner
   sudo ./svc.sh install && sudo ./svc.sh start
   ```
7. **Harden:** dedicated user, least-privilege, scoped/rotatable API keys, and treat the box as holding live credentials.

Repeat per product later: `openclaw-runner`, `hermes-runner` (each only after confirming headless auth).

---

## 2. The `integration` job (template — add to `publish.yml` once a runner exists)

Do **not** add this until a matching self-hosted runner is registered, or the job will queue forever waiting for one.

```yaml
  integration-claude:
    name: E2E — Claude Code invokes our hooks
    needs: publish                      # run against the just-published version
    runs-on: [self-hosted, claude-runner]
    steps:
      - uses: actions/checkout@v4

      - name: Install the just-published version
        run: |
          VERSION="${GITHUB_REF_NAME#v}"
          pip install --upgrade "waystone==$VERSION"
          waystone configure --non-interactive   # re-wire hooks for this version

      - name: Run the Claude e2e hook test
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          WAYSTONE_E2E: "1"
        run: python ci/e2e_claude_hooks.py
```

Gate it to **release or nightly**, not every commit — it makes real, paid API calls. (The hermetic `smoke` job stays per-publish.)

---

## 3. The e2e check itself (`ci/e2e_claude_hooks.py` — AUTHORED + validated 2026-06-13)

`ci/e2e_claude_hooks.py` implements **Strategy A** below, improved to be **billing-robust**:
it seeds a graph-only secret, runs a real `claude -p`, and asserts the secret landed in
`<projects_dir>/<proj>/last_context.md`. Because UserPromptSubmit fires *before* the model
call, this passes even when Claude's reply errors on credit — so it proves
retrieval+injection without spending a token. (A separate run with a funded key confirms
the model then *reads* the injected secret.) Run it: `python ci/e2e_claude_hooks.py`.

Goal: prove Claude Code actually *fires our hooks* during a real run — the one thing the hermetic synthetic-payload smoke can't. Two viable strategies:

**A. Side-effect assertion (robust, no log parsing).** Run a single headless prompt in a throwaway project with extraction **paused** (so Stop fires but spends no LLM tokens), then assert our hooks left their fingerprints:
- `UserPromptSubmit` → wrote `~/.waystone/projects/<proj>/last_context.md`
- `Stop` → saved a transcript under `~/.waystone/projects/<proj>/transcripts/`

```python
# sketch — validate against real output on the runner before trusting it
import os, subprocess, tempfile, time
from pathlib import Path

home = Path.home()
(home / ".waystone").mkdir(exist_ok=True)
(home / ".waystone" / "paused").touch()        # suppress extraction (no LLM cost from our Stop hook)
try:
    proj = tempfile.mkdtemp()
    (Path(proj) / ".waystone").write_text("e2e_smoke\n")
    subprocess.run(["claude", "-p", "Say hello."], cwd=proj, check=True, timeout=120)
    time.sleep(2)
    tdir = home / ".waystone" / "projects" / "e2e_smoke" / "transcripts"
    assert tdir.exists() and any(tdir.iterdir()), "Stop hook did not save a transcript — hooks not firing!"
    print("E2E OK — Claude Code fired the Waystone hooks.")
finally:
    (home / ".waystone" / "paused").unlink(missing_ok=True)
```

**B. Observe hook events directly.** `claude -p "..." --include-hook-events --output-format stream-json` emits the hook events; parse the stream and assert `UserPromptSubmit`/`Stop` appear. (Confirm the exact event JSON schema on first run — `--include-hook-events` output shape should be captured into a fixture before relying on it.)

Either way: **validate the script with one real run on the runner first** — `claude -p` is non-hermetic and the exact output/auth behaviour must be confirmed live, not assumed. The same script doubles as a **local dev tool** (run it on any machine with `claude` + a key + `WAYSTONE_E2E=1`).

> Place the committed e2e script under `ci/` (NOT `scripts/`, which is gitignored — a self-hosted runner checks out the repo and needs the file present).

---

## 4. Per-product status

All three support headless/server auth, so a per-product runner is feasible. Auth methods below are research-derived (Justin, 2026-06-07) — **confirm against each product's current docs when standing up its runner** (VERIFY-FIRST).

| Product | Headless auth | Runner | e2e |
|---------|---------------|--------|-----|
| Claude Code | ✅ API key / token in env (`ANTHROPIC_API_KEY`) | `claude-runner` | ✅ `ci/e2e_claude_hooks.py` (validated; reply needs a funded key) |
| Hermes Agent | ✅ API keys + `.env` env vars; runs as a background service (`hermes gateway install` → systemd); also Codex OAuth | `hermes-runner` | ✅ `ci/e2e_hermes_provider.py` (validated vs `hermes-agent==0.16.0`) |
| OpenClaw | ✅ device-pairing + cryptographic **Ed25519 token** handshake with the gateway; tokens rotatable/revocable via CLI | `openclaw-runner` (TBD) | unit-covered (`tests/test_openclaw.py`, 42 tests); real-runner TBD |

**Hermes runner job** (gate to release/nightly; `hermes-runner` label):

```yaml
  integration-hermes:
    name: E2E — Waystone provider loads in real Hermes
    needs: publish
    runs-on: [self-hosted, hermes-runner]
    steps:
      - uses: actions/checkout@v4                 # repo root has hermes_plugin/
      - run: pip install hermes-agent "waystone==${GITHUB_REF_NAME#v}"
      - run: python ci/e2e_hermes_provider.py
```

**To activate Waystone in a real Hermes install** (not auto-listed in `hermes memory setup`):
drop the plugin at `~/.hermes/plugins/memory/waystone/` and set `memory.provider: waystone`
in the Hermes config. Hermes discovers memory plugins via `plugins.memory` (built-ins:
honcho/openviking/mem0/hindsight/holographic/retaindb/byterover).

**Setup nuance per product:**
- **Hermes** is the simplest — same shape as Claude (API key / `.env`), plus a `systemd` background-service install for 24/7. Wire the extraction LLM key + the Hermes auth env vars on the runner.
- **OpenClaw** needs a **one-time device pairing** to mint an Ed25519 token for the runner node (every headless node signs a handshake with the gateway). Store that token as a runner secret; rotate/revoke via CLI as part of credential hygiene.

---

## Recommended path

1. **Now:** keep the hermetic post-publish smoke (`selfcheck --deep`) — it already catches install/packaging/hook-execution/MCP bugs on every release.
2. **Claude e2e — script done (`ci/e2e_claude_hooks.py`).** Stand up a `claude-runner`, **top up the `ANTHROPIC_API_KEY`** so the model reply (not just injection) is exercised, then add the `integration-claude` job gated to release/nightly.
3. **Hermes e2e — script done (`ci/e2e_hermes_provider.py`).** Stand up a `hermes-runner` (`pip install hermes-agent`), add the `integration-hermes` job. The same check also runs in the unit suite as `tests/test_hermes_plugin.py::test_real_hermes_base_class_compatibility` (auto-skips when hermes-agent is absent), so any box with Hermes installed exercises it for free.
4. **OpenClaw:** unit-covered today (42 tests); add an `openclaw-runner` + real e2e when its integration matures (needs the Ed25519 device-pairing token as a runner secret).
