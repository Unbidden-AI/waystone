# Changelog

All notable changes to Waystone are documented here.

## [Unreleased]

---

## [0.4.43] – 2026-06-15

### Fixed

- **Stripe webhook idempotency — duplicate/retried events no longer double-issue.** Stripe delivers webhooks at-least-once and retries on any non-2xx, so the same `checkout.session.completed` / `invoice.paid` could mint a second key/license and send a second email. The handler now **claims each event id** (new `processed_events` table + `claim_event()`) and acks-and-skips a duplicate. (A delivery that fails before the claim commits can still be retried, so genuine failures aren't lost.)

### Tests

- Webhook idempotency (`claim_event` dedup; a replayed event mints+emails exactly once) and Stripe **signature-verification edge cases** — malformed headers (empty, no `=`, missing `t`/`v1`), a forged signature, key-rotation (multiple `v1`, one valid), and "no secret configured rejects everything".

---

## [0.4.42] – 2026-06-15

### Added

- **Production observability (P1) — you can now diagnose a deployed Team Server.** A central logging layer (`waystone/_logging.py`) wires the sinks the modules never configured: the **API server logs one structured access line per request** to stderr (method, path, status, latency, and a one-way-hashed key id — never the key itself), plus pool/webhook events; `docker compose logs -f server` is now useful. Verbosity via `WAYSTONE_LOG_LEVEL`.
- **Hooks now record failures instead of vanishing.** A `@hook_entry` decorator on every Claude Code hook routes logs to a rotating `~/.waystone/logs/hooks.log` (hook **stdout stays the protocol channel** — logs never leak into it) and guarantees fail-open: any unhandled error is logged and the hook exits 0, so it can't crash or block the editor session. Previously a hook failure in a deployed server was completely invisible.
- **Postgres connection pool diagnostics** — logs pool creation and, critically, a `WARNING` on a checkout timeout, so connection-starvation surfaces as a clear signal instead of an opaque 500.

---

## [0.4.41] – 2026-06-15

### Fixed

- **Windows cp1252 crashes — pinned `encoding="utf-8"` on 79 file-I/O sites across 14 modules** (CLI, setup/configure, hooks, config, store, mcp_server, openclaw). Unpinned `read_text()`/`write_text()`/`open()` used the platform default, so on Windows any non-ASCII content (in a transcript, a `.jsonl` session, a user-edited `~/.claude/settings.json`, etc.) raised `UnicodeDecodeError`/`UnicodeEncodeError` — and in the hooks that could break a live Claude Code session. Reads of external/untrusted data also get `errors="replace"`. (Same class as the 0.4.5 hotfix; this is the comprehensive sweep, surfaced by a multi-agent review.)
- **Extraction crashed (`KeyError: 'relation'`) on edges from a model that omits the field** — `assign_ids`/`assign_ids_incremental` now default a missing edge relation to `relates_to` instead of raising. (The historical Mistral-style non-compliant-output failure.)
- **Admin-DB connection leak in `_check_auth`** — if `validate_key()` raised, the connection was never closed; now wrapped in `try/finally`. Slow connection exhaustion on a busy Team Server under DB errors.
- **Silent error-swallowing made purchases undiagnosable** — the Stripe line-item lookup and the admin-DB schema migrations now `log.warning(...)` instead of `except: pass`, so an operator can see why a checkout resolved to a default tier/seat count or why a migration didn't apply. The expected "duplicate column" migration case stays quiet.

---

## [0.4.40] – 2026-06-15

### Fixed

- **`waystone configure` wired NO Claude Code hooks for pip-installed users** — the core "auto-inject context before every prompt" feature silently never activated after a normal `pip install waystone`. `configure` only called `install_hooks()` when a repo-relative `hooks/` directory existed, but that directory doesn't ship in the wheel. `install_hooks()` already handles the pip case correctly (it wires `~/.claude/settings.json` to the installed `waystone-hook-*` console-script entry points and only needs `hooks/` as a repo-clone fallback) — so it's now always called, with `hook_dir` passed only when present. Pip users running `configure` now get all five hooks (UserPromptSubmit, Stop, PostToolUse, SessionStart, SessionEnd) and the CLAUDE.md section. The publish smoke test missed this because it runs the hooks directly rather than through `configure`; a new end-to-end install harness (`ci/acceptance_install.sh`) caught it.

### Added

- **`ci/acceptance_install.sh`** — end-USER install acceptance: installs the real published artifact from PyPI into a clean virtualenv and verifies the actual commands work (console scripts, `--version`, `selfcheck --deep`, `configure` wiring Claude Code) and, behind `--full`, a real solo extraction round-trip plus a pip-installed client talking to a Team Server. Complements `acceptance_teamserver.sh` (which tests the server in Docker).

---

## [0.4.39] – 2026-06-14

### Fixed

- **Self-hosted Team Server members didn't actually share a graph (the headline feature was broken).** In per-seat mode the Postgres tenant was scoped per API key (`<key-prefix>:<project>`), so two members writing the same project got *different* tenants and never saw each other's work — directly contradicting "one shared knowledge graph for your whole team." That per-key isolation is correct for the multi-tenant **hosted** SaaS (different customers must not mix) but wrong for a self-hosted single-org server. Per-key scoping is now gated on `_isolate_by_key()`, which is true only when `STRIPE_WEBHOOK_SECRET` is set (the hosted service); a self-hosted Team Server shares the project graph across all members. Same gate applied to the project-dir/listing/limit paths, so a self-hosted server also no longer wrongly imposes hosted per-tier project limits. (No data migration needed — the Team Server shipped today, so no real per-key tenants exist yet.)

### Added

- **`ci/acceptance_teamserver.sh`** — a black-box acceptance battery that stands up a throwaway server on the published image and verifies boot/safety, auth, licensing & seats, persistence (structural, no LLM cost), and — behind `--full` — multiplayer shared-graph, tenant isolation, and remote-client CLI wiring (real extraction). This is the harness that caught the sharing bug above.

---

## [0.4.38] – 2026-06-14

### Fixed

- **Fresh-database Postgres Team Server failed on first graph use (`PoolTimeout`).** The connection pool's `configure` callback registers the pgvector type via `register_vector()`, which looks up the `vector` type in the database — but on a brand-new database the `vector` extension isn't created until schema init, which itself needs a pooled connection. That chicken-and-egg made every pooled connection fail to configure, and the pool timed out after 30s on the buyer's first `extract`/`query` (health checks and `team` commands hit SQLite, so `docker compose up` looked healthy and the failure only surfaced on first real graph work). Now the `vector` extension is bootstrapped once — on a throwaway autocommit connection before the pool opens, and before `register_vector` on the non-pooled path — so a fresh self-hosted server works on first use. Caught by CI's fresh-DB run; verified against a clean pgvector database (29 Postgres tests pass in <1s, previously 30s timeouts each).

---

## [0.4.37] – 2026-06-14

### Added

- **Frictionless buyer onboarding for the self-hosted Team Server.** A license now auto-enables per-seat mode: pasting `WAYSTONE_LICENSE` is the only thing a buyer sets — no need to also flip `CB_USE_ADMIN_DB` or invent a throwaway shared key (`_use_admin_db()` falls back to license presence; an explicit `CB_USE_ADMIN_DB` still wins, so the hosted billing server is unaffected). The container **refuses to boot wide-open** — if neither a license nor a `WAYSTONE_API_KEY` is set, the entrypoint exits with the exact one-line fix instead of silently serving unauthenticated. `.env.example` rewritten buyer-first (and dropped stale LemonSqueezy config).
- **Pre-built Team Server image (no clone, no build).** `publish-image.yml` builds and pushes a multi-arch `ghcr.io/<org>/waystone-server` image on `v*` tags; the `deploy/` bundle (`docker-compose.yml` + `env.example`) lets a buyer `docker compose up` against the published image with no source checkout. `docs/team-server.md` leads with this path; building from source remains supported via the repo's root compose.

### Added (continued)

- **Connection pooling for the Postgres Team backend.** The Team Server opened a new database connection per request; under a busy team that adds latency and risks exhausting Postgres `max_connections`. `PostgresGraphStore` now borrows from a process-wide `psycopg_pool` pool per DSN (warm, reused connections; `register_vector` applied per physical connection via the pool's `configure`). Thread-safe first-touch (lock-guarded pool + one-time schema DDL — the server runs sync endpoints in a threadpool); connections are rolled back + returned on `close()`, never leaked on a failed init. Opt out with `WAYSTONE_PG_POOL=0`; size with `WAYSTONE_PG_POOL_MAX` (default 10). Adds `psycopg-pool` to the `team` extra.
- **Sell self-hosted Team Server licenses via Stripe.** A purchase of the Team-license price (`STRIPE_TEAM_LICENSE_PRICE_ID`) makes the `/webhooks/stripe` handler mint a signed Ed25519 license token and email it — no API key; the customer runs their own server and the token is verified offline. Seats resolve from the checkout `seats` metadata → purchased **quantity** (so one per-seat price with adjustable quantity just works) → default 5. **Subscription renewals** (`invoice.paid`, `billing_reason=subscription_cycle`) re-mint a fresh token; the first invoice doesn't double-issue. The billing server holds the private signing key via `WAYSTONE_LICENSE_PRIVKEY` (or `_FILE`); if it's unset the webhook acks + logs loudly rather than 500. Backward-compatible: inert unless both env vars are set, and normal hosted-tier purchases are untouched. (`issue_license_from_env`, `is_team_license_price`, `send_license_email`.)

### Fixed

- **README Claude Code hook setup was wrong** — it showed `waystone hook query my-project` / `waystone hook extract my-project`, but there is no `hook` subcommand (those fail). Corrected to run `waystone configure` (the auto-installer) or wire the real `waystone-hook-submit` / `waystone-hook-stop` console scripts. Verified the entrypoint end-to-end (stdin JSON → injected context).

### Tests

- **Hermes Agent memory provider** (`hermes_plugin/`) now has coverage (was zero): the provider contract Hermes depends on — name, initialize, is_available, prefetch, the query/recall tools, tool schemas, and the `register()` convention — exercised against a real local graph.

---

## [0.4.36] – 2026-06-13

### Fixed

- **`/v1/health` (and OpenAPI) reported a stale `version: 0.2.0`** while the package was 0.4.x — `waystone/__init__.__version__` was a hardcoded string. It now derives from the installed package metadata (single source of truth = `pyproject`), so the API server and CLI can never disagree about the version. Found during end-to-end validation of the published 0.4.35.

---

## [0.4.35] – 2026-06-12

### Added

- **Self-hosted Team Server — one shared knowledge graph for a whole team.** A team flips `backend: remote` and points their client at a server backed by a multi-writer **PostgreSQL + pgvector** graph; each member's session injects the team's context every prompt and writes new facts back to the same graph, on their own infrastructure.
  - **`backend: remote` switch** — `query` / `extract` / `show` / `export` / `init` and the Claude Code hooks route to the server over HTTP (the existing `RemoteContextBroker`). The UserPromptSubmit hook injects shared context (fail-open, ~8s cap so a slow server never blocks a prompt) and routes extraction to a detached `--remote` worker. `backend: local` forces a machine back to its private SQLite graph. The local-only path is byte-for-byte unchanged.
  - **`docker compose up`** — Postgres (pgvector) + the API, ready in ~30s. The backend is env-configured (`WAYSTONE_STORE_BACKEND` / `DATABASE_URL`); schema auto-creates on first request. See `docs/team-server.md`.
  - **Offline per-seat licensing** — Ed25519-signed license tokens (seats + expiry) verified **locally**, no phone-home. `waystone team` CLI (`license` / `issue` / `members` / `revoke`) manages member keys up to the licensed seat count (3 trial seats unlicensed). Fail-closed: a tampered/expired license never grants seats.
  - **`PostgresGraphStore`** — a multi-writer, tenant-scoped, drop-in for the SQLite `GraphStore` (psycopg3 + pgvector, jsonb tags, generated-tsvector FTS, bi-temporal). Schema built once per process.

---

## [0.4.34] – 2026-06-11

### Added

- **`waystone invalidate <project>` — proactive staleness detection (deterministic, no LLM).** Unlike supersession (which only retires a fact when a new contradicting one arrives), this hunts the existing graph for facts likely gone stale: nodes past their per-type half-life (`transition` 14d, `decision` 180d, `constraint` 365d, …, all × a 1.5 conservatism factor), and aged never-retrieved low-confidence nodes. Preview by default; `--execute` soft-retires them (`is_active=0`, `valid_to` set — history KEPT, never deleted, still reachable via `--at-time`). Pinned and high-confidence nodes are exempt; recall-preserving by design. `GraphStore.detect_stale_candidates()` powers it. (An optional LLM "is this still true?" pass and a benchmark are planned follow-ups.)

---

## [0.4.33] – 2026-06-11

### Added

- **`session_summary.context_turns` config** (default 30) — the number of recent turns the live rolling summarizer feeds the LLM each fire. Previously hardcoded. Lowering it (e.g. to ~`cadence_turns`) cuts per-call input tokens for runs on expensive models, at some cost to cross-window coherence; the prior summary still carries older context forward either way. Per-call usage stays bounded (~3k input + ≤512 output on default settings, O(1) regardless of session length).

---

## [0.4.32] – 2026-06-11

### Added

- **`waystone catchup-summarize <project>`** — back-fill a project's story from its already-saved session transcripts. Walks the distinct cumulative transcripts under `~/.waystone/transcripts/<project>/` in chronological order and builds one rolling `session_summary` chain — a chapter per session, each superseding the prior — so `waystone story <project>` replays the whole history. Each session is digested in `--window`-turn windows (default 40) so the full session is captured, not just its tail. `--sessions N` (most-recent N), `--replace/--append`, `--transcripts-dir`, `--dry-run`. Run once to catch a project up; the live Stop-hook summarizer handles new sessions. (Generalized from the one-off pass that reconstructed the NanoSwarm project history into 4 readable chapters across a month of work.)

---

## [0.4.31] – 2026-06-10

### Fixed

- **The per-turn extractor no longer mints thin `session_summary` nodes.** `session_summary` was in the extractor's type palette (`domain_profiles`), so the LLM kept typing one-line process-narration ("X was committed", "Y was offered to Justin") as session summaries — polluting the `waystone story` timeline with fragments alongside the real rolling summaries. Removed it from the palette: it's now a system-generated type, created only by the live rolling-summary worker and host-recap (away_summary) ingestion via `store.add_node` (which has no profile gate). The store/retriever still treat it as first-class. Extraction now also drops any stray `session_summary` the model emits.

---

## [0.4.30] – 2026-06-10

### Fixed

- **Live session-summary cadence now actually tracks turns.** The Stop-hook summary trigger was placed *after* the incremental-extraction early-exit (`if last_idx >= len(turns): sys.exit(0)`), so the per-turn counter only advanced when there was a new extraction delta — making rolling summaries fire far more rarely than the configured `cadence_turns` (and not at all once extraction caught up). Moved the trigger ahead of that early-exit so it runs on every Stop invocation, as intended. Found while verifying the feature on a live session whose counter had been stuck for hours.

---

## [0.4.29] – 2026-06-10

### Added

- **`waystone doctor` now diagnoses sqlite-vec capability.** It probes whether the running Python can actually load the sqlite-vec extension (`enable_load_extension` + a real `vec_version()` load) and, when it can't, names the cause and the fix. Root cause clarified: the **python.org macOS Framework build** ships sqlite3 *without* loadable-extension support (Apple's system SQLite strips the symbols) — this affects *every* version, not just 3.14; Homebrew/pyenv/uv builds work. Because retrieval degrades gracefully to keyword (tag + BM25/FTS5) and `semantic` is off by default in config, the check is informational (`–`) when semantic is disabled and only a failure (`✗`) when semantic is actually enabled. Fix hint: use a Homebrew/pyenv/uv Python (e.g. `brew install python@3.14`) and reinstall there — no downgrade needed.

---

## [0.4.28] – 2026-06-10

### Added

- **Per-prompt session-narrative injection (P4a).** Every prompt's injected context now *leads* with a **"Where we are (session narrative)"** block — the latest active `session_summary`. Rolling summaries carry generic tags so keyword retrieval never surfaced them; this fetches the freshest one directly (preferring the current session's live summary for continuity, else the most recent across the project). It injects even when keyword retrieval returns nothing, so a fresh session opens already oriented to the project's arc. Toggle with `session_summary.inject` (default true). This is the lever that turns the rolling summary from a passive timeline into context the model actually reads each turn.

---

## [0.4.27] – 2026-06-10

### Fixed

- **Session-summary windows no longer silently blank out.** `generate_session_summary` now retries (bounded, with backoff) on the two empty-window failure modes seen running the live summarizer over a real 531-turn session: transient API errors (5xx/429/timeout) and a **null `content`** field (some models return `content: null` on length-truncation or a content filter — no exception, just blank). `max_tokens` raised 256 → 512 for headroom. `session_summary.retries` (default 2 → 3 attempts total) controls the budget. Covered by retry/exhaustion tests.

---

## [0.4.26] – 2026-06-10

### Added

- **Live passive session summarization (P3).** The Stop hook now counts turns per session and, every `session_summary.cadence_turns` (default **5** — sharp, to catch detail on decisions as they happen), spawns a detached background worker (`waystone-hook-summarize`) that folds the prior summary + recent turns into an updated narrative and stores it as a `session_summary` node **superseding** the prior one. Fully passive: no user-facing output, fails silently, respects the `~/.waystone/paused` flag. Builds on `summarize-session` (0.4.25) but runs automatically, model-agnostic, during any session.
- **`waystone story <project>`** — replays the project's story: walks the **whole** `session_summary` timeline *including superseded (inactive) summaries*, oldest → newest, marking the current one. Supersession keeps history; this presents it. `--session <id>` to scope to one live session, `--limit N` for the most recent points.
- `session_summary` config block (`enabled`, `cadence_turns`) and the `waystone-hook-summarize` console script.

### Fixed

- Session-summary worker now supplies a node `id` and timestamps to `add_node` — without them the insert raised a (silently swallowed) `KeyError`, so live summaries were never stored. Covered by an end-to-end `main()` regression test.

---

## [0.4.25] – 2026-06-09

### Added

- **`waystone summarize-session` — periodic session summarization (model-agnostic).** Walks a transcript every N turns, incrementally updating a rolling narrative (goal · arc · current state · next), and stores each as a `session_summary` node that **supersedes** the prior — so retrieval surfaces the latest while the full **timeline** is kept (bi-temporal). `--dry-run` to preview, `--every`/`--max-windows` to control cadence/cost. This captures the session-level altitude atomic fact extraction misses, for any host (not just Claude Code). The live during-session trigger (Stop hook) builds on this next.
- `generate_session_summary()` in the extractor — an incremental rolling-summary LLM call (prior summary + only the new turns), cheap and bounded.

---

## [0.4.24] – 2026-06-09

### Added

- **Captures Claude Code's native session recaps.** Claude Code persists rolling `away_summary` entries (goal/arc/state/next) in the session `.jsonl`; Waystone previously dropped them (it only read user/assistant turns). Now `onboard`/`import-claude-sessions` store the session's last (cumulative) recap as a high-altitude **`session_summary` node** — the session-level narrative that atomic fact extraction misses, captured for free (no LLM). `session_summary` is surfaced first in retrieval ordering. The onboard menu also prefers the native recap as each session's label (and only spends an LLM summary on sessions that lack one).

---

## [0.4.23] – 2026-06-06

### Changed

- **`onboard` menu now shows an LLM one-line summary of each session** instead of just its first prompt. The first prompt is the session's *opener*, not its topic — a big session can start with a throwaway "is it configured?" and then do hours of real work, making it look skippable. The picker now summarizes each session's head/middle/tail in parallel (cheap, ~a penny total), so you can tell what a session was actually about. Falls back to the first-prompt snippet (then filename) if no LLM is reachable or a summary times out.

---

## [0.4.22] – 2026-06-06

### Changed

- **`onboard` now auto-cleans meta-noise before the sample query.** After importing, onboard removes transient self-referential nodes ("database is empty", "0 nodes", "no facts captured") and reports the count, so the closing sample query showcases *real* extracted facts to a new user instead of junk. Uses the same narrow, validated filter as `prune --meta-noise`. (The extraction-prompt approach was benchmark-tested and dropped — it regressed recall 2–11 pts; meta-noise is a post-processing problem, not a prompt problem.)

---

## [0.4.21] – 2026-06-06

### Fixed

- **`UnicodeEncodeError: '\udc8f' surrogates not allowed` crash.** LLM extraction output occasionally contains a lone surrogate code point; SQLite can't encode it, so `add_node`'s INSERT raised `UnicodeEncodeError` and crashed the entire extraction / `onboard` run mid-import. `add_node` now strips lone surrogates (→ U+FFFD) from the fact, source, and tags before storing — protecting every path that writes a node. Additionally, `onboard`'s per-session merge is isolated in a try/except so a single bad session is reported and skipped instead of killing the whole import.

---

## [0.4.20] – 2026-06-06

### Added

- **`waystone prune --meta-noise`** — new filter on the `prune` command that removes transient self-referential "meta-noise" facts an extractor sometimes captures from debugging transcripts (e.g. "the database is empty", "0 nodes and 0 edges", "no decisions have been captured"). Preview by default; `--execute` to delete. Narrow by design — real facts, even negatively-phrased decisions like "decided NOT to use Redis", are left alone. (This is the post-hoc cleanup complement to the onboard preview and the extraction-time noise rule.)

### Changed

- `prune` now **unions** its filters (instead of requiring all) and deletes via the per-node path (cleans edges/tags), and previews up to 100 nodes.

---

## [0.4.19] – 2026-06-06

### Added

- **`onboard` session menu now shows a content preview** — each session is listed with its first user prompt (a cheap, no-LLM snippet read from the `.jsonl`), so you can tell at a glance which sessions are worth importing vs. which to skip, instead of guessing from filename + size.

---

## [0.4.18] – 2026-06-06

### Fixed

- **Default LLM timeout raised 30s → 120s.** 30s was far too short for extracting a full chunk (large structured-JSON output routinely takes 30–120s+), so any chunky transcript timed out repeatedly — in `onboard` *and* normal Stop-hook extraction. 120s gives real extractions room to finish.

### Added

- **Adaptive chunking** — when an extraction chunk times out (or the response is truncated), it's automatically re-split into smaller pieces and retried (down to a floor), so a large/slow session yields partial facts instead of failing outright. Non-timeout errors (e.g. auth) still propagate immediately without pointless re-splitting.
- **`onboard` end-of-run tally** — reports imported/skipped/partial sessions by name (read errors, empty conversions, and sessions that lost chunks to failures) so nothing hides in a long import.

---

## [0.4.17] – 2026-06-06

### Added

- **SessionStart hook** — runs a fast offline selfcheck when a Claude Code session starts and warns only if the install/config is broken (silent on success, never blocks the session). Wired automatically by `waystone configure`; entry point `waystone-hook-sessionstart`. So a broken install is surfaced at session start instead of via silent failures later.
- **`waystone configure` now self-checks at the end** — prints the offline health check (import, config, key resolution, deps) so you immediately see whether the setup is runnable, and points you to `waystone verify` for the deeper extraction test.

### Changed

- Quick-check logic is now shared (`waystone/_selfcheck.py`) across `waystone selfcheck`, the SessionStart hook, and `configure`. `selfcheck --deep` and the post-publish CI smoke now also exercise the SessionStart hook.

---

## [0.4.16] – 2026-06-06

### Added

- **`waystone selfcheck --deep`** — integration smoke test that exercises the integrations the way Claude Code would, without driving a real session: runs every hook (submit/stop/posttool/import_memory/statusline) against a synthetic stdin payload in an isolated temp HOME (extraction paused so no LLM call), and checks the MCP server's tools are registered. Catches the import/packaging/encoding bugs that only surface through the hook and MCP entry points. (Entry-point-on-PATH is reported but non-fatal, since console scripts can be stale on editable dev installs.)
- **CI post-publish smoke now runs `--deep`** plus a hard assertion that every console script (`waystone`, `waystone-mcp`, all `waystone-hook-*`, `waystone-statusline`) is installed on a fresh PyPI install — so a missing entry point or a crashing hook fails the release automatically.

---

## [0.4.15] – 2026-06-06

### Fixed

- **`waystone --version` now works.** The CLI group had no version flag, so `waystone --version` errored — caught immediately by the new post-publish smoke test on its first run. Added `--version` (reads installed package metadata).

---

## [0.4.14] – 2026-06-06

### Added

- **`waystone selfcheck`** — fast, offline health check (no network/LLM call): confirms the package imports + reports its version, config loads, the API key resolves, and optional deps (sqlite-vec) are present. Exit 0/1, `--json`. Cheap enough to run on every install / session start / in CI. (`waystone verify` remains the deeper real-extraction check.)
- **Post-publish CI smoke test** — the publish workflow now installs the just-published version fresh from PyPI (retrying for propagation) and runs `waystone --version` + `waystone selfcheck`, so every release is automatically verified to actually install and run. Would have caught the 0.4.x packaging regressions (statusline, hook entry points) before users hit them.

---

## [0.4.13] – 2026-06-06

### Added

- **`waystone verify`** — confirms the configured LLM actually works for extraction by running a real tiny extraction round-trip (native or OpenAI-compatible path) and asserting facts come back. Reports backend, model, resolved key source, latency, and a failure category (`auth`/`model`/`quota`/`network`/`config`); `--json` for scripts. Exit 0/1. Catches auth, wrong model names, missing structured-output support, and endpoint issues that a connectivity ping misses.
- **`scripts/verify_providers.py`** — a real-key provider matrix that runs configure → verify → extract across Gemini/OpenAI/Anthropic (any provider whose key is in the env), each in an isolated temp HOME; `--fresh-install` also pip-installs the repo into a throwaway venv first. Mocked, always-on counterpart: `tests/test_cli.py::TestVerify`.

### Changed

- **Unified LLM API-key resolution** into one shared `config.resolve_llm_api_key()` used by the extractor (both paths), `verify`, `doctor`, and `configure`'s connection test — so they can no longer disagree about which key extraction will use (the root of the "doctor passes but extraction fails" mismatch). `doctor` now also reports the resolved key source.

---

## [0.4.12] – 2026-06-06

### Added

- **`waystone reset <project>`** — delete a project's graph and re-initialize it empty, to re-extract from scratch. Keeps saved transcripts/exports by default (pass `--purge` to delete those too); never touches your raw Claude Code session files under `~/.claude`. Prompts for confirmation unless `--yes`. Re-populate afterward with `waystone onboard <project>`.

---

## [0.4.11] – 2026-06-06

### Fixed

- **Windows: `onboard`/`import-claude-sessions` crashed reading some sessions** (`'charmap' codec can't decode byte 0x90/0x8f`). `from_claude_jsonl` read session files with the platform-default encoding (cp1252 on Windows) instead of UTF-8, so any byte outside cp1252 aborted that session. Now reads UTF-8 with `errors="replace"` — which also prevents the surrogate-on-encode crash (`'utf-8' codec can't encode '\udc8f'`) seen on the same files.

---

## [0.4.10] – 2026-06-06

### Fixed

- **`onboard` / `import-claude-sessions` imported 0 nodes from every session** ("SKIP (empty after conversion)"). The `.jsonl` converter read top-level `role`/`content`, but Claude Code session files nest the message under a `{"type": ..., "message": {"role", "content"}}` envelope — so every session converted to empty text and nothing was extracted. The converter now delegates to the canonical `from_claude_jsonl` parser (which handles the nested schema, including text/thinking/tool_use blocks). Added a regression test using the real nested format.

---

## [0.4.9] – 2026-06-06

### Fixed

- **Extraction ignored the inline `api_key` when `api_key_env` was set.** If config had both `api_key_env` and an inline `api_key`, the extractor *required* the env var and aborted (`API key env var 'X' is not set`) instead of falling back to the inline key — so extraction failed and the graph stayed empty even though a valid key was sitting in `config.yaml`. (`doctor` accepted either, so it passed while extraction failed — a confusing mismatch.) Key resolution is now: configured env var → inline `api_key` → generic env vars, in both the OpenAI-compatible and native-Gemini paths.
- **`auto-import`, `watch`, and `doctor`'s onboard-fix silently did nothing.** They spawn the CLI via `python -m waystone.cli …`, but `cli.py` had no `__main__` guard, so the module imported and exited 0 without running. `auto-import` reported "N/N imported" while extracting zero nodes — which also meant the **SessionEnd Auto Memory hook (0.4.6) extracted nothing**. Added the guard; all four call sites now work.

### Added

- **`waystone configure` offers to backfill history.** After creating a project, configure prompts to import your recent Claude Code sessions (`waystone onboard`) so the graph is useful immediately. Defaults to yes; runs the extractor only on confirmation; skipped in `--non-interactive`.

---

## [0.4.8] – 2026-06-06

### Fixed

- **Status line never rendered on pip installs.** `waystone configure` wired the status line to a `waystone-statusline` command, but that console script was never registered and the status-line code lived only in the repo's `hooks/` dir — so pip-installed users (e.g. on Windows) got a status line pointing at a non-existent binary, which silently rendered nothing. The status line is now a packaged module (`waystone._hooks.statusline`) with a real `waystone-statusline` entry point; the repo `hooks/statusline.py` is now a thin shim over it. **Existing installs self-heal on upgrade** — the wired command was correct all along, it just had no backing executable; `pip install waystone --upgrade` makes it work (no re-configure needed).

---

## [0.4.7] – 2026-06-06

### Fixed

- **`waystone configure` crashed with `NameError: name 'config' is not defined`** when marking a project (the new graph-init step in 0.4.6 referenced an undefined `config`). It now loads the config via `load_config()` before initializing the graph. Verified end-to-end through the real wizard. If you hit this on 0.4.6, upgrade and re-run `waystone configure` (or `waystone init <project>`) — the marker was written but the graph wasn't created.

---

## [0.4.6] – 2026-06-06

### Fixed

- **Windows: multi-minute hang / "internal error" on first MCP call.** On a fresh install the first load of the native `sqlite-vec` extension (Windows Defender scanning/gating the unsigned binary) could block for minutes — surfacing as `waystone_stats` appearing to hang, then failing with "Tool result missing due to internal error." Count-only tools (`waystone_stats`, `waystone_list_projects`) now skip the vector extension entirely (they only count), and the extension is pre-warmed during `waystone configure` and in a background thread at MCP-server startup, so the first real query never pays the cold-load cost. See ADVANCED.md → "Windows: slow first query."
- **`waystone configure` left projects un-initialized.** Configure wrote the `.waystone` marker but never created the graph, leaving the project showing as "No projects found" / "not initialized." Configure now initializes the empty graph (project dir + `context.db`) immediately after marking, so the project is usable right away.

### Added

- **SessionEnd Auto Memory import hook.** Claude Code "Auto Memory" files (`~/.claude/projects/<slug>/memory/`) are now imported into the Waystone graph when a session ends, via a `SessionEnd` hook (`waystone-hook-import-memory`) wired automatically by `waystone configure`. Idempotent (manifest-based, skips unchanged files), detached so teardown isn't blocked, and scoped to the curated memory files only (transcripts are handled by the existing extraction worker).

---

## [0.4.5] – 2026-06-06

### Fixed

- **Windows `UnicodeEncodeError` crash.** Hooks/CLI/status-line forced their Unicode glyphs (✓, ⚠) through the Windows cp1252 console and crashed — silently killing the Stop hook before it could extract, so the graph stayed empty. Output is now forced to UTF-8 at every entry point and content file writes pin `encoding="utf-8"`. No `PYTHONUTF8=1` workaround needed.

### Added

- **PostToolUse capture hook.** Captures state-changing tool calls (Write/Edit/MultiEdit/NotebookEdit/Bash) during long autonomous (plan/auto) runs and flushes them to background extraction mid-run, so the graph fills in while the agent works instead of only at the end. Tunable via the `posttool` config section.
- **`waystone remember` + `/btw`.** Instant, no-LLM single-node capture; `waystone configure` installs a `/btw` Claude Code slash command that wraps it.
- **Status line from session start + error alerts.** Shows `WS(<project>): ready` from the first render and surfaces extraction errors with a ⚠ alert (configurable via `statusline.alert_on_error`).

---

## [0.4.4] – 2026-06-05

### Added

- **Opt-in API embedding backend.** Set `embeddings.backend: api` in config to embed via `litellm` using your LLM API key — no PyTorch / `sentence-transformers` required. Configurable `model`/`dim`; default remains the local bge-small backend (unchanged). New `waystone reembed <project>` rebuilds the vector table when switching backends.
- **Automatic chat-attachment extraction.** Long Discord/Telegram messages that arrive as `message.txt` attachments are now scanned from the plugin inbox and extracted into the graph automatically (per-project ledger prevents re-extraction).
- **Advanced Configuration guide** (`ADVANCED.md`) covering API embeddings, retrieval strategy tuning, and attachment extraction.

---

## [0.4.3] – 2026-06-05

### Changed

- **`sqlite-vec` is now a core dependency** (moved out of the optional `semantic` extra). It's a lightweight native extension with no transitive deps, so semantic storage works on a default `pip install waystone` and `waystone doctor` no longer warns about it. The heavier embedding stack (`sentence-transformers` → PyTorch) stays opt-in via `waystone[semantic]`.

### Added

- **Hermes Agent memory provider** (`hermes_plugin/`) reconciled with the verified `MemoryProvider` base class: `prefetch` / `queue_prefetch` / `sync_turn` / `on_session_end` signatures now match the base exactly, and `plugin.yaml` declares the `on_session_end` hook. Documented in the README.

---

## [0.2.0] – 2026-05-19

### Added

**Product rename: Engram → Waystone**
- CLI entry point renamed to `waystone`; package renamed to `waystone`; config dir renamed to `~/.waystone/`
- MCP tool names updated: `context_broker_*` → `waystone_*`
- Config marker file renamed from `.context-broker` to `.waystone`

**Stripe billing**
- Replaced LemonSqueezy with Stripe for payment processing
- Webhook handler verifies `Stripe-Signature` and processes `checkout.session.completed` events
- API key generation on successful checkout with tier determination from Stripe line items
- Email delivery via Resend with dead-letter queue for transient failures
- `waystone/billing.py` — API key management (generate, hash, validate, revoke), tier definitions (free/pro/team), rate limiter

**Hosted API server** (`waystone/api_server.py`)
- FastAPI server deployable to Fly.io or Railway
- `/v1/health` — liveness probe
- `/v1/account` — Clerk JWT-authenticated account info
- `/v1/projects/{project}/query` — remote context retrieval
- `/v1/projects/{project}/extract` — remote extraction
- `/webhooks/stripe` — Stripe payment webhook
- `/account/key` — API key provisioning endpoint
- `fly.toml` — Fly.io deployment config (`waystone-api`, 512 MB, health check)

**Retrieval improvements**
- RRF (Reciprocal Rank Fusion) re-ranking across BFS entry points
- Semantic dedup CLI (`waystone dedup`) — collapse near-duplicate nodes above cosine threshold
- `process` node type — captures ongoing processes, background jobs, and scheduled tasks
- Person-hub fanout in retriever — exhaustive retrieval for person-centric queries
- `waystone reflect` — in-session hook watermark; dedup cap on reflected nodes

**Pilot orchestrator** (`pilot/`)
- Model-agnostic conversation manager with proactive context compaction
- Layer-0 system prompt builder, tool executor, router, scheduler
- `litellm>=1.40` and `tiktoken>=0.7` dependencies
- `pilot:` configuration section in `config.yaml`

**Benchmarks**
- LOCOMO benchmark harness (`benchmarks/locomo/`) — multi-conversation memory benchmark (Snap Research); best result 88.1% LLM accuracy on dev split (n=762, GPT-4o-mini judge)
- LongMemEval benchmark harness (`benchmarks/longmemeval/`) — 500-question S-split; best result 61.6% overall; 87.5% on single-session-assistant category
- OpenAI Batch API integration in scoring for 50% cost reduction
- `BENCHMARK_RESULTS.md` — public benchmark documentation with competitor comparison

**Website integration**
- `unbidden-site/netlify/functions/create-checkout.js` — returns Stripe Payment Link by plan
- `unbidden-site/netlify/functions/get-api-key.js` — proxies API key fetch with Clerk Bearer token

### Changed
- `config.yaml` section renamed: `orchestrator:` → `pilot:`
- `fly.toml` and `railway.toml` updated to Stripe env vars (removed LemonSqueezy vars)
- Benchmark utilities updated: `compaction_eval.py`, `compare_baseline.py`

### Fixed
- Keyword extractor now emits both hyphenated compound tokens and their parts (`hot-path` → `hot-path`, `hot`, `path`), fixing tag misses on hyphenated facts
- Superseding nodes now include prior-state tags so queries for old terms surface the transition node

---

## [0.1.0] – 2026-03-10

### Added

**Core**
- DAG-based graph store (`GraphStore`) backed by SQLite with WAL mode for concurrent access
- LLM-based extraction via any OpenAI-compatible endpoint (`waystone extract`)
- BFS graph traversal with configurable depth (`waystone query --hops`)
- Strategy pipeline: `superseded_pruning`, `confidence_threshold`, `recency_decay`, `token_budget`, `relevance_scoring`
- Incremental per-turn extraction (`waystone extract-replay`)
- Graph reconciliation to find missed supersedes edges (`waystone reconcile`)
- Structured logging in all library modules (`logging.getLogger(__name__)`)

**CLI commands**
- `waystone init <project>` — create a new project
- `waystone extract <project> <file>` — extract from transcript (50 MB guard, `--verify` flag)
- `waystone extract-replay <project> <file>` — turn-by-turn incremental extraction
- `waystone query <project> "<task>"` — retrieve relevant context as markdown
- `waystone show <project>` — list all nodes
- `waystone export <project>` — export graph to markdown
- `waystone reconcile <project>` — find and add missed supersedes edges (`--dry-run`)
- `waystone onboard` — interactive import of recent Claude Code sessions
- `waystone import-claude-sessions` — batch import Claude Code `.jsonl` sessions
- `waystone doctor` — preflight check: config, API key, LLM reachability, DB state, hooks
- `waystone mcp-serve` — start the MCP server on stdio

**MCP server** (`context_broker/mcp_server.py`)
- `context_broker_query` — retrieve context for a task
- `context_broker_extract` — extract and store facts from text (200 k char limit)
- `context_broker_stats` — node/edge counts for a project
- `context_broker_list_projects` — list all projects on the machine
- All tools auto-detect project from `.context-broker` marker file

**Reliability**
- Exponential backoff with `Retry-After` support on HTTP 429/5xx (up to 4 retries)
- API key resolution: `api_key_env` in config → `CTX_API_KEY` env var → `OPENAI_API_KEY`
- Input size guard: 50 MB on CLI, 200 k chars on MCP tool

**Developer tooling**
- GitHub Actions CI: pytest matrix (Python 3.11 / 3.12 / 3.13) + ruff lint + smoke test
- GitHub Actions publish: PyPI trusted publishing on `v*` tags (no token required)
- Ruff configured with `E`, `F`, `W`, `I` rules, line-length 120

**Benchmarks** (`benchmarks/`)
- Three synthetic transcripts (api_design, auth_system, data_pipeline)
- 23 ground-truth eval questions with precision/recall scoring
- Per-model config files; results show Gemini 2.5 Flash achieves 94% recall with `--verify`

**Hooks** (`hooks/`)
- `context_broker_submit.py` — PreToolUse hook: injects retrieved context into task description
- `context_broker_stop.py` — Stop hook: extracts from completed session transcript
- `statusline.py` — StatusLine hook: shows graph size in Claude Code status bar

**Documentation**
- `GETTING_STARTED.md` — quick-start for both MCP server and hook workflows
- `PROJECT.md` — architecture and design decisions
- `FINDINGS.md` — benchmark findings and model notes
- `ROADMAP.md` — planned features
- `claude_mcp_config.json` — ready-to-paste MCP config snippet

[0.1.0]: https://github.com/justinwalton/context-broker/releases/tag/v0.1.0
