# Changelog

All notable changes to Context Broker are documented here.

## [Unreleased]

### Added
- `pilot/` package skeleton — model-agnostic conversation manager with proactive compaction
- `tests/test_pilot/` — test directory for orchestrator modules
- `litellm>=1.40` and `tiktoken>=0.7` dependencies
- `pilot:` configuration section in `config.yaml` with full schema
- `PILOT_PLAN.md` — architecture and module design for the orchestrator
- `DEVELOPMENT_PLAN.md` — agent workflow, milestones, and process conventions

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
