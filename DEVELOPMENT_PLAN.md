# Orchestrator Development Plan

This document covers the *how* of building the orchestrator — agent workflows, milestone sequencing, and process conventions. See `ORCHESTRATOR_PLAN.md` for the *what* — architecture, module designs, and code.

---

## Agent Roles & Model Assignment

| Agent | Model | When to Use |
|-------|-------|-------------|
| **Orchestrator (Claude)** | Opus | Planning, complex implementation, architectural decisions |
| **Code Review** | Sonnet | After each milestone — fresh-eyes review, feedback implementation |
| **QA** | Sonnet | Unit tests, error handling verification, test output fed back to orchestrator |
| **Security** | Sonnet | After any module touching file I/O, subprocess, or external calls |
| **GitHub** | Haiku | Commit/PR at each milestone — purely mechanical |
| **Research** | Sonnet | Best practices questions, library evaluation, open design questions |

**Model selection rationale:**
- Security review is pattern-matching and reasoning, not planning — Sonnet is sufficient, Opus is wasteful
- GitHub commits are mechanical — Haiku is appropriate
- QA requires reasoning about edge cases but not architecture — Sonnet
- Opus reserved for decisions that shape the whole system

---

## Process Conventions

### At Each Milestone
1. **Build** the milestone deliverable (Opus)
2. **Security review** on any module with file I/O, subprocess, or external calls (Sonnet)
3. **Code review** with fresh eyes (Sonnet) — implement agreed changes and document them
4. **QA** — write and run tests; error output fed back to orchestrator for correction (Sonnet)
5. **GitHub** — commit at each minor milestone, PR at each major milestone (Haiku)

### Error Tracking
QA agent outputs test results directly to the orchestrator. All failing tests must be corrected before proceeding to the next milestone. No milestone is "done" with failing tests.

### Documentation
- The GitHub agent updates `CHANGELOG.md` at each PR
- Code review feedback and decisions are documented inline in this file under each milestone
- Security findings are documented in the milestone notes below

---

## Development Milestones

### Milestone 0: Foundation Setup
**Before writing any code**

**Deliverables:**
- Add `litellm` to `pyproject.toml` dependencies
- Add `tiktoken` to `pyproject.toml` (accurate token estimation)
- Add `orchestrator` package to `pyproject.toml` entry points (`engram chat`)
- Extend `config.yaml` with `orchestrator:` section (schema in `ORCHESTRATOR_PLAN.md`)
- Create `orchestrator/` directory with `__init__.py`
- Create `tests/test_orchestrator/` directory with `__init__.py`
- Create `CHANGELOG.md`

**Agents:** GitHub (Haiku) — commit skeleton

**Notes:** _(populated during development)_

---

### Milestone 1: `types.py` + `llm_adapter.py`
**The communication layer — everything else depends on this**

**Deliverables:**
- `orchestrator/types.py`
  - `Message` dataclass (role, content, timestamp, token_estimate, tool_call_id)
  - `ToolCall` dataclass (id, name, args)
  - `ToolResult` dataclass (tool_call_id, name, output, error)
  - `CompactionTrigger` enum (HISTORY_DEPTH, TOKEN_BUDGET, IDLE_TIME)
  - `ConversationState` dataclass
  - `CompactionResult` dataclass (nodes_extracted, messages_removed, tokens_freed)

- `orchestrator/llm_adapter.py`
  - `async call_llm(messages, system, config, tools=None) -> tuple[str | None, list[ToolCall] | None, str]`
  - `estimate_tokens(text) -> int` (~4 chars/token, tiktoken for accuracy)
  - `build_tool_schemas(enabled_tools) -> list[dict]`
  - LiteLLM integration with provider-specific normalization
  - Graceful handling: AuthenticationError, RateLimitError, Timeout, backoff

**Auto-recovery from API errors:**

The orchestrator owns the API call loop, which means all LLM failures route through `llm_adapter.py` and are fully recoverable. Two scenarios:

- **Transient 500/502/503/504**: Retry with exponential backoff (1s, 2s, 4s, 8s). User never sees it. LiteLLM raises typed exceptions (`InternalServerError`) that we catch by status code.
- **RateLimitError (429)**: Backoff respects the `Retry-After` header if present; otherwise exponential. Surfaces a friendly "rate limited, retrying..." message if backoff exceeds 5s.
- **Timeout**: Configurable per-request timeout. On timeout, retry up to N times before surfacing error.
- **Unrecoverable errors** (AuthenticationError, invalid model): Fail fast with a clear message rather than retrying.
- **Mid-tool-loop failure**: If a 500 hits during the tool call loop, the last successful tool result is preserved in message history. On retry, the loop resumes from that checkpoint rather than restarting from scratch.

This is the same pattern already in `context_broker/extractor.py`'s `_call_llm()` — the orchestrator extends and formalizes it.

Note: Anthropic-side 500s hitting *Claude Code itself* (as seen in this session) are outside our control. Once you're using the orchestrator directly, you own the API loop and all failures route through your retry logic instead.

**Agent sequence:**
1. **Security** (Sonnet) — API key handling in `llm_adapter.py`; ensure keys never logged or leaked; review all error paths
2. **Code Review** (Sonnet) — review both files; implement agreed changes
3. **QA** (Sonnet) — unit tests:
   - Token estimation within 10% of actual for varied input sizes
   - Tool schemas valid for Anthropic, OpenAI, and Gemini providers
   - `call_llm` retries on 500/502/503/504 with correct backoff intervals
   - Rate limit backoff respects `Retry-After` header
   - Mid-tool-loop 500 preserves prior tool results and resumes correctly
   - AuthenticationError fails fast (no retry)
   - Timeout retries up to configured max, then surfaces clean error
4. **GitHub** (Haiku) — commit: `feat: add types and LiteLLM adapter`

**Code Review Feedback:** _(populated during development)_
**Security Findings:** _(populated during development)_
**QA Results:** _(populated during development)_

---

### Milestone 2: `tool_executor.py`
**Local execution — highest security surface area in Phase 1**

**Deliverables:**
- `orchestrator/tool_executor.py`
  - `execute_tool(name, args) -> str` — dispatcher
  - `bash(command, cwd, timeout=30) -> str` — subprocess execution
  - `read_file(path) -> str`
  - `write_file(path, content) -> str`
  - `glob_files(pattern, root) -> str`
  - `grep(pattern, paths, case_sensitive=False) -> str`
  - Sandbox enforcement (configurable `sandbox_root`)
  - Output truncation at `max_output_chars` with indicator
  - `ToolExecutor` class wrapping config

**Agent sequence:**
1. **Security** (Sonnet) — **most important security review in Phase 1**:
   - Command injection risks in `bash()` — is `shell=True` safe? Alternatives?
   - Path traversal in file tools (`../../etc/passwd` patterns)
   - Sandbox escape vectors
   - Symlink attacks in file operations
   - Environment variable leakage to subprocesses
2. **Research** (Sonnet) — subprocess sandboxing best practices; `subprocess` vs `asyncio.create_subprocess_shell`; any lightweight sandboxing libraries worth using
3. **Code Review** (Sonnet) — implement security + research findings
4. **QA** (Sonnet) — tests:
   - Bash timeout kills the process (not just waits)
   - Path traversal attempts (`../../etc/passwd`) are blocked
   - Output >10k chars is truncated with indicator
   - Command injection attempts in tool args are neutralized
   - All 5 tools return useful error strings rather than raising exceptions
   - Empty/null inputs handled gracefully
5. **GitHub** (Haiku) — commit: `feat: add local tool executor`

**Code Review Feedback:** _(populated during development)_
**Security Findings:** _(populated during development)_
**QA Results:** _(populated during development)_

---

### Milestone 3: `context_manager.py`
**The core — sliding window + proactive compaction. Most complex module.**

**Deliverables:**
- `orchestrator/context_manager.py`
  - `ContextManager(store, config, project)` class
  - `add_message(msg) -> None` — append + check compaction
  - `should_compact() -> CompactionTrigger | None`
  - `compact_to_graph() -> CompactionResult` — extract + merge + prune history
  - `retrieve_context(task, hops, top_k) -> str` — calls existing `retrieve_with_stats()`
  - `get_messages_for_api() -> list[dict]` — trimmed history within token budget
  - `apply_token_budget(messages, budget) -> list[Message]`
  - Integration with `extract_turn()`, `ExtractionBuffer`, `GraphStore`
  - Background verification pass spawning (optional, post-compaction)

**Compaction trigger thresholds (configurable):**
- `HISTORY_DEPTH`: `len(messages) > window_size` (default 20)
- `TOKEN_BUDGET`: `estimated_tokens > token_budget * 0.8` (default 8000 * 0.8 = 6400)
- `IDLE_TIME`: `now - last_compaction > idle_seconds` (default 600)

**Compaction flow:**
1. Gather oldest N messages (`compaction_batch`, default 10)
2. `extract_turn(text, existing_nodes, config)` — reuses existing extractor
3. `store.merge_extraction(nodes, edges)` — graph dedup handles overlap
4. Remove extracted messages from history
5. Optionally spawn background `verify_extraction()` for better recall
6. Return `CompactionResult`

**Agent sequence:**
1. **Research** (Sonnet) — how AutoGen/LangChain handle compaction; optimal window sizes; published work on context compression for code assistants
2. **Code Review** (Sonnet) — architecture review mid-build (rare case — the compaction flow design deserves review before it's locked in)
3. **QA** (Sonnet) — **most complex QA milestone**:
   - Empty graph (first conversation) — no errors, graceful no-op context
   - Compaction triggered at correct thresholds for all 3 trigger types
   - Facts from turn 3 retrievable at turn 20 (survived compaction)
   - Token budget never exceeded in `get_messages_for_api()` output
   - Concurrent extraction doesn't corrupt graph (SQLite WAL)
   - Compaction with empty buffer (no-op case)
   - Double-compaction (compaction triggered twice in a row)
4. **Security** (Sonnet) — review background verification subprocess spawning
5. **GitHub** (Haiku) — commit: `feat: add context manager with proactive compaction`

**Code Review Feedback:** _(populated during development)_
**Security Findings:** _(populated during development)_
**QA Results:** _(populated during development)_

---

### Milestone 4: `system_prompt_builder.py`
**Dynamic per-turn system prompt composition**

**Deliverables:**
- `orchestrator/system_prompt_builder.py`
  - `build_system_prompt(context_markdown, config) -> str`
  - Node grouping by type (decision > constraint > implementation > resolved > preference > question)
  - Token budget enforcement for context section
  - Graceful empty graph handling (no context section)
  - Dynamic `top_k` reduction if context exceeds `context_token_limit`

**Agent sequence:**
1. **Code Review** (Sonnet) — small module; combined review is efficient
2. **QA** (Haiku) — simple module; tests:
   - Token limit enforcement (context section never exceeds `context_token_limit`)
   - Empty context produces no graph section in output
   - Node type grouping order matches spec
   - Static instructions always present regardless of context
3. **GitHub** (Haiku) — commit: `feat: add dynamic system prompt builder`

**Code Review Feedback:** _(populated during development)_
**QA Results:** _(populated during development)_

---

### Milestone 5: `conversation.py` + `cli.py`
**Main loop — first working end-to-end prototype**

**Deliverables:**
- `orchestrator/conversation.py`
  - `ConversationOrchestrator(project, config)` class
  - `async process_turn(user_input) -> str` — full loop with tool call handling
  - Tool call retry loop (max 5 iterations)
  - Rate limit backoff (surfaces friendly message, retries)
  - `/context`, `/stats`, `/compact`, `/reset`, `/quit` command handlers
  - `run_interactive()` — REPL entry point

- `orchestrator/cli.py`
  - `engram chat <project> [--config CONFIG] [--model MODEL] [--dry-run]`
  - `--dry-run`: prints composed system prompt + message list without calling LLM

**process_turn flow:**
```
User input
    ↓
Check compaction trigger → if yes: compact_to_graph()
    ↓
retrieve_context(user_input)
    ↓
build_system_prompt(context_markdown)
    ↓
call_llm(messages, system, tools)
    ↓
Tool call loop (max 5 iterations):
    → execute_tool(name, args)
    → append tool result
    → call_llm again
    ↓
add_message(assistant_response)
    ↓
Return text to user
```

**Agent sequence:**
1. **Code Review** (Sonnet) — full loop logic, tool call retry handling, error recovery paths
2. **Security** (Sonnet) — REPL input handling; shell injection surface in `/commands`
3. **QA** (Sonnet) — integration tests:
   - Full turn: user input → LLM → tool call → tool result → LLM → response
   - Tool call loop exits after max iterations (no infinite loop)
   - All REPL commands work correctly
   - `/compact` forces compaction and reports nodes extracted
   - `--dry-run` prints without calling LLM
   - Rate limit triggers retry with backoff
4. **GitHub** (Haiku) — commit: `feat: Phase 1 complete — working conversation loop`

**Code Review Feedback:** _(populated during development)_
**Security Findings:** _(populated during development)_
**QA Results:** _(populated during development)_

---

### Milestone 6: Phase 1 Integration Testing & Polish
**End-to-end validation before Phase 1 is "done"**

**Deliverables:**
- Full integration test suite in `tests/test_orchestrator/test_integration.py`
- Token efficiency benchmark (measure reduction vs. naive full-history)
- Full Phase 1 security audit across all modules together
- Holistic code review now that the full system works
- `ORCHESTRATOR_PLAN.md` updated with any design changes made during implementation
- `CHANGELOG.md` updated

**Agent sequence:**
1. **QA** (Sonnet) — full integration test suite:
   - 20-turn conversation with compaction triggered mid-session
   - Facts from turn 3 retrievable at turn 20 (survived compaction)
   - Token budget held across all turns
   - Multiple model providers (Anthropic + at least one other via LiteLLM)
   - Tool use round-trip (bash → result → LLM processes → response)
   - Session survives and recovers from LLM API errors
2. **Security** (Sonnet) — full Phase 1 security audit across all modules
3. **Code Review** (Sonnet) — holistic review across all modules
4. **Research** (Sonnet) — token efficiency benchmark; compare vs. naive approach; document findings
5. **GitHub** (Haiku) — PR: `Phase 1: Lightweight Orchestrator Wrapper`

**Integration Test Results:** _(populated during development)_
**Token Efficiency Results:** _(populated during development)_
**Security Audit Summary:** _(populated during development)_

---

## Phase 2 Milestones

After Phase 1 stabilizes. Each milestone follows the same agent sequence pattern.

| Milestone | Description | Key Agents |
|-----------|-------------|-----------|
| **2.1** | Multi-agent framework (`agent.py`, `agent_coordinator.py`, `agent_registry.py`) | Research (Sonnet) first — evaluate AutoGen patterns |
| **2.2** | Advanced tools (`python_exec`, `git`, `http_request`, tool chaining) | Security (Sonnet) — highest risk milestone in Phase 2 |
| **2.3** | Tool output processing (LLM summarization, streaming, diff visualization) | Code Review + QA |
| **2.4** | Enhanced compaction (selective/task-aware, importance weighting, graph versioning) | Research first — complex algorithms |
| **2.5** | Planning / decomposition (ReAct-style reasoning, task breakdown) | Research (Opus) + Code Review (Sonnet) |
| **2.6** | TUI / IDE integration (Rich terminal UI or VS Code extension) | Code Review + QA + Security |
| **2.7** | Adaptive strategies (learn which retrieval strategies work best per project) | Research + QA |
| **2.8** | Health dashboard (`engram inspect` CLI + optional local web panel): node density, BFS depth distribution, retrieval score histograms, type breakdown, token budget utilization — actionable signals for extraction/retrieval quality without ground truth | Code Review + QA |
| **2.9** | On-the-fly accuracy monitoring: self-supervised synthetic QA generation from extracted nodes, retrieval tested against generated questions, anomaly alerts surfaced via health metrics | Research (Sonnet) + QA |
| **2.10** | Dynamic `extraction_focus` generation: auto-generate domain `extraction_focus` from first session sample rather than hand-authoring; fires when domain detection confidence is low; generated focus injected on top of generic domain schema | Research + QA |

---

## Additional Engineering Recommendations

### Added to Scope

1. **`CHANGELOG.md`** — GitHub agent updates at each PR. Makes project history readable without `git log`.

2. **Full type hints throughout** — existing codebase is inconsistently typed. Orchestrator is fully typed from day one. Catches integration bugs before QA does.

3. **Structured logging** — Python `logging` module with configurable level (`orchestrator.log_level` in config). Essential for debugging background extraction. QA verifies log output at each milestone.

4. **`tests/test_orchestrator/` directory** — parallel to `tests/test_store.py`. QA agent owns this directory. Tests committed with each milestone, never after.

5. **`--dry-run` flag on `engram chat`** — prints composed system prompt + message list without calling LLM. Invaluable for debugging token budget and context composition without burning API credits.

6. **Graceful degradation** — if graph DB is unavailable or corrupted, conversation loop continues without context rather than crashing. QA tests this explicitly.

7. **Rate limiting awareness** — LiteLLM surfaces `RateLimitError`. Conversation loop backs off and retries with a friendly message rather than surfacing raw errors.

8. **`tiktoken` for token estimation** — more accurate than `len(text) // 4`, especially for code-heavy conversations.

### Scope Guard (what we're NOT building)
- Custom tokenizer (use tiktoken)
- Custom LLM client (use LiteLLM)
- Custom sandbox beyond subprocess + path validation (Phase 1)
- IDE extension (Phase 2.6 only)
- Web UI (out of scope)

---

## Success Metrics

### Phase 1
- Handle 50+ turns without context loss (facts survive compaction)
- 30-40% token reduction vs. naive full-history approach
- Graph retrieval latency <100ms per turn
- Compaction extraction <5s (async, non-blocking)
- Support 3+ model providers with identical behavior
- Zero failing tests at milestone completion

### Phase 2
- Sub-agent spawn time <2s
- Tool output processing <1s for outputs <50k chars
- Graph versioning overhead <50ms per snapshot
- TUI renders within 100ms of model response
