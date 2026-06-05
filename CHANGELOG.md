# Changelog

All notable changes to Waystone are documented here.

## [Unreleased]

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
