# Context Broker Orchestrator — Implementation Plan

## Vision

Perpetually distill the context window to just what's necessary for each prompt. Eliminate AI "forgetfulness," memory loss from compaction, and redundant token spend. Provide a context management layer that makes small context windows viable and large ones efficient.

### The Core Problem

Current LLM tools (Claude Code, ChatGPT, etc.) manage conversation context poorly:

1. **Context windows fill up** — entire conversation history gets sent every turn, burning tokens on redundant, already-processed content
2. **Compaction is lossy** — when the window fills, orchestrators compress history, silently discarding knowledge the model may need later
3. **No persistent memory** — each session starts from scratch; cross-session knowledge requires manual re-explanation
4. **Tool outputs are ephemeral** — bash results, file reads, and diffs consume massive token budgets but are rarely referenced after 2-3 turns

### The Solution

An orchestrator that **owns the API call loop** and actively manages what the model sees:

- **Graph-based long-term memory** — durable facts extracted from conversations, stored as a queryable DAG
- **Sliding history window** — only recent turns stay in context; older turns are compacted to the graph
- **Per-turn retrieval** — each prompt gets only the graph nodes relevant to the current task
- **Proactive compaction** — extract facts *before* hitting token limits, not after
- **Dynamic system prompts** — composed from the graph each turn, evolving as knowledge grows

### Why This Must Be a Custom Orchestrator

Claude Code's hook system (`additionalContext`) is **additive** — it appends context to the existing conversation, it doesn't replace it. Mid-session, you're sending *more* tokens, not fewer. The hooks are valuable at:
- **Session start** — graph context replaces manual project summary pasting
- **Post-compaction** — re-hydrates knowledge that compaction discarded
- **Cross-session continuity** — persistent graph carries knowledge between conversations

But to fully control the context window — choosing what history stays, what gets compacted, what system prompt contains — you must own the API call loop. That's what this orchestrator does.

### How This Differs From Other Orchestrators

Every other orchestrator (LangChain, AutoGen, CrewAI) treats context as "stuff the whole history in and hope for the best." This orchestrator **actively manages** what the model sees — extracting durable knowledge to a graph, pruning stale history, and composing each prompt from only what's relevant.

**vs. LM Studio**: LM Studio is a model *runner* — it downloads and runs LLM weights locally, exposing an OpenAI-compatible API. It forgets everything between calls. The orchestrator is the conversation *manager* that controls what goes into each API call. They're complementary — the orchestrator can call LM Studio as its backend.

**vs. Perplexity.ai**: Perplexity is a product built on RAG (retrieval-augmented generation) — it retrieves from the *web* for each query. Our orchestrator retrieves from your *project graph*. Perplexity has no persistent memory about you or your work. Same retrieval pattern, different knowledge source.

---

## Existing Context Broker Architecture (What We're Building On)

### Modules Available for Reuse

| Module | Key APIs | Role in Orchestrator |
|--------|----------|---------------------|
| `store.py` | `GraphStore(db_path)`, `merge_extraction()`, `get_nodes_by_tags()`, `add_node()`, `add_edge()`, `get_stats()` | All graph operations — no changes needed |
| `extractor.py` | `extract_turn()`, `verify_extraction()`, `extract_targeted()`, `ExtractionBuffer` | Compaction extraction + background verification |
| `retriever.py` | `retrieve_with_stats(store, task, hops, top_k, strategies)` → `RetrievalResult` | Per-turn context retrieval |
| `config.py` | `load_config()`, `get_db_path(config, project)` | Config loading (extended with orchestrator section) |
| `prompts.py` | `EXTRACTION_PROMPT`, `INCREMENTAL_EXTRACTION_PROMPT`, `VERIFICATION_PROMPT`, `TARGETED_PASS_PROMPTS`, `RECONCILE_PROMPT` | All extraction prompts |

### Strategy Pipeline (retriever.py)

Applied in order during retrieval:
1. **superseded_pruning** — drops nodes that have been superseded by newer facts
2. **relevance_scoring** — ranks entry nodes by keyword-tag overlap before BFS traversal
3. **confidence_threshold** — filters nodes below a configurable confidence floor
4. **recency_decay** — exponential decay: `score = confidence * 2^(-age_days / half_life_days)`
5. **token_budget** — greedy packing by estimated tokens (~4 chars/token)

### Data Model

- **Nodes**: id, fact, type, confidence, tags (JSON), supersedes (JSON), source_transcript, created_at, fact_hash
- **Edges**: from_id, to_id, relation
- **Node types**: decision, constraint, implementation, question, resolved, lesson_learned, preference
- **Edge relations**: depends_on, flows_to, relates_to, supersedes

### Existing Features That Carry Forward

- **Fact-hash deduplication** — normalized text hash prevents duplicate nodes
- **Incremental extraction** — `extract_turn()` accepts existing nodes to avoid re-extraction
- **Two-tier extraction** — Tier 1 heuristic (~0.56ms) fills the gap before Tier 2 LLM extraction (30-120s)
- **Tier 1-guided verification** — heuristic sentences become priority targets for verification pass
- **Session state with expiration** — timestamp-based expiry keyed to `last_extract_at` per project
- **BFS batch queries** — O(hops) database queries via join-based collection
- **SQLite WAL mode** — concurrent reads + writes without blocking (critical for background extraction)

---

## Phase 1: Lightweight Wrapper (1-2 weeks)

### Goals

- Model-agnostic via LiteLLM (Anthropic, OpenAI, Gemini, Groq, local models)
- Tool use support (API returns intent, wrapper executes locally)
- Proactive compaction-to-graph as conversation progresses
- Sliding history window with token budgeting
- System prompt composed from graph retrieval per turn
- Reuse all existing Context Broker modules

### File Structure

```
ContextBroker/
├── orchestrator/                    # NEW
│   ├── __init__.py
│   ├── conversation.py              # Main conversation loop + REPL
│   ├── context_manager.py           # Sliding window + proactive compaction
│   ├── tool_executor.py             # Local tool execution
│   ├── system_prompt_builder.py     # Graph → system prompt composition
│   ├── llm_adapter.py              # LiteLLM integration layer
│   ├── types.py                     # Shared dataclasses
│   └── cli.py                       # Entry point
├── context_broker/                  # UNCHANGED
│   ├── store.py
│   ├── extractor.py
│   ├── retriever.py
│   ├── prompts.py
│   ├── config.py
│   ├── cli.py
│   └── mcp_server.py
├── hooks/                           # UNCHANGED
└── config.yaml                      # Extended with orchestrator section
```

### Module Details

#### `orchestrator/types.py` — Shared Data Classes

```python
@dataclass
class Message:
    role: str                    # "user", "assistant", "system", "tool"
    content: str
    timestamp: float             # time.time()
    token_estimate: int          # len(content) // 4
    tool_call_id: str | None     # for tool result messages

@dataclass
class ToolCall:
    id: str                      # provider-assigned ID
    name: str                    # "bash", "read_file", etc.
    args: dict                   # {"command": "ls src/"}

@dataclass
class ToolResult:
    tool_call_id: str
    name: str
    output: str
    error: str | None

class CompactionTrigger(Enum):
    HISTORY_DEPTH = "history_depth"   # too many turns
    TOKEN_BUDGET = "token_budget"     # approaching token limit
    IDLE_TIME = "idle_time"           # user hasn't typed in a while
```

#### `orchestrator/llm_adapter.py` — LiteLLM Integration

Thin wrapper around LiteLLM for model-agnostic tool use.

```python
async def call_llm(
    messages: list[dict],
    system: str,
    config: dict,
    tools: list[dict] | None = None,
) -> tuple[str | None, list[ToolCall] | None, str]:
    """
    Call LLM via LiteLLM.
    Returns: (content, tool_calls, stop_reason)

    LiteLLM handles provider-specific schema translation:
    - Anthropic: tool_use blocks
    - OpenAI: function_calling
    - Gemini: function_declarations
    """
    ...

def estimate_tokens(text: str) -> int:
    """~4 chars per token, or tiktoken for accuracy."""
    return len(text) // 4

def build_tool_schemas(enabled_tools: list[str]) -> list[dict]:
    """Generate OpenAI-format tool schemas for enabled tools."""
    ...
```

**Config integration:**
- Reads from `orchestrator.llm` section
- Falls back to existing `llm` section if not present
- LiteLLM model string format: `anthropic/claude-sonnet-4-6`, `openai/gpt-4o`, `groq/llama-3.3-70b`, `gemini/gemini-2.5-flash`

#### `orchestrator/tool_executor.py` — Local Execution (~150 lines)

The API **cannot** run anything on your machine. It returns structured JSON saying "I want to call this tool with these arguments." The wrapper receives that JSON, executes locally, then sends the result back.

```
Wrapper                         API
  |-- "list files in src/" ------>|
  |                               |  (model thinks: I need bash)
  |<-- {tool_use: "bash",         |
  |     input: "ls src/"} --------|
  |                               |
  | [WRAPPER RUNS: subprocess("ls src/")]
  |                               |
  |-- {tool_result: "foo.py       |
  |    bar.py"} ----------------->|
  |                               |  (model processes result)
  |<-- "The src/ directory        |
  |     contains foo.py..." ------|
```

**Tool implementations:**
- `bash(command, cwd, timeout)` → `subprocess.run()`
- `read_file(path)` → `Path.read_text()`
- `write_file(path, content)` → `Path.write_text()`
- `glob_files(pattern, root)` → `pathlib.Path.glob()`
- `grep(pattern, paths)` → regex search over file contents

**Safety:**
- Configurable sandbox root (restrict file access)
- Bash timeout (default 30s)
- Large outputs (>10k chars) truncated with indicator
- No execution outside allowed directories

#### `orchestrator/context_manager.py` — The Core

This is where the magic happens. Manages the sliding window, triggers compaction, and retrieves graph context.

```python
class ContextManager:
    def __init__(self, store: GraphStore, config: dict, project: str):
        self.store = store
        self.messages: list[Message] = []
        self.extraction_buffer = ExtractionBuffer(...)  # from extractor.py
        self.token_budget = config["orchestrator"]["context"]["token_budget"]
        self.window_size = config["orchestrator"]["context"]["window_size"]
```

**Key methods:**

**`add_message(msg)`** — Append to history, track token count, check if compaction needed.

**`should_compact() → CompactionTrigger | None`** — Returns trigger type when:
- `len(messages) > window_size` → `HISTORY_DEPTH`
- `total_tokens > token_budget * 0.8` → `TOKEN_BUDGET`
- `now - last_compaction > idle_seconds` → `IDLE_TIME`

**`compact_to_graph()`** — The proactive compaction flow:
1. Gather oldest N messages (N = `compaction_batch`, default 10)
2. Combine into text block
3. Call `extract_turn(text, existing_nodes, config)` (reuses existing extractor)
4. `store.merge_extraction(nodes, edges)` into graph
5. Remove extracted messages from history
6. Optionally spawn background `verify_extraction()` for better recall
7. Return: `(nodes_extracted, messages_removed, tokens_freed)`

**`retrieve_context(task)`** — Per-turn graph retrieval:
- Calls existing `retrieve_with_stats(store, task, hops, top_k, strategies)`
- Returns formatted markdown context
- Cache result (don't re-query for identical task)

**`get_messages_for_api()`** — Build the message list for the API call:
- Start from most recent messages
- Pack greedily until token budget reached
- Always preserve the most recent user message
- Return as OpenAI-format dicts

**Token budgeting algorithm:**
1. Estimate tokens for each message: `len(content) // 4 + overhead`
2. Sum: system prompt + graph context + recent messages
3. If total > `token_budget`:
   - Trigger compaction (extract + merge oldest turns)
   - Remove extracted messages
   - Re-estimate
   - If still over: drop oldest non-extracted messages
4. Reserve ~1000 tokens for response

**Compaction trigger thresholds:**
- `HISTORY_DEPTH`: `len(messages) > window_size` (default 20)
- `TOKEN_BUDGET`: `estimated_tokens > token_budget * 0.8` (default 8000 * 0.8 = 6400)
- `IDLE_TIME`: `now - last_compaction > idle_seconds` (default 600)

#### `orchestrator/system_prompt_builder.py` — Dynamic System Prompts

System prompt is rebuilt every turn — as the graph grows, the prompt evolves.

```python
def build_system_prompt(
    context_markdown: str,
    config: dict,
) -> str:
    """
    Compose: static instructions + retrieved graph context.

    If graph context exceeds token_limit, reduce top_k
    and re-retrieve.
    """
    ...
```

**Structure of generated system prompt:**
```
[Static instructions from config]

## Project Knowledge
[Graph context grouped by node type: decision > constraint > implementation > resolved > preference > question]

## Active Context
[Any session state or recent high-priority facts]
```

#### `orchestrator/conversation.py` — Main Loop

```python
class ConversationOrchestrator:
    def __init__(self, project: str, config: dict):
        self.store = GraphStore(get_db_path(config, project))
        self.context_mgr = ContextManager(self.store, config, project)
        self.tool_executor = ToolExecutor(config)
        self.prompt_builder = SystemPromptBuilder(config)
```

**`process_turn(user_input)` flow:**

```
User input
    ↓
Step 1: Check compaction trigger
    → If triggered: compact_to_graph() [extract + merge + prune history]
    ↓
Step 2: Retrieve graph context
    → retrieve_with_stats(store, user_input, hops, top_k, strategies)
    ↓
Step 3: Build system prompt
    → static instructions + graph context markdown
    ↓
Step 4: Call LLM via LiteLLM
    → messages = system + trimmed history + user input
    → tools = enabled tool schemas
    ↓
Step 5: Tool call loop (max 5 iterations)
    → If response contains tool_calls:
        → Execute each tool locally
        → Append tool results to messages
        → Call LLM again with results
    → If no tool_calls: break
    ↓
Step 6: Add assistant response to history
    → context_mgr.add_message(Message(role="assistant", ...))
    ↓
Step 7: Spawn background extraction (optional)
    → If accumulated turns exceed buffer threshold,
      extract in background for better recall
    ↓
Return final text to user
```

**REPL commands:**
- `/context` — show current graph context for last query
- `/stats` — conversation stats (messages, tokens, graph size)
- `/compact` — force compaction now
- `/reset` — clear history, keep graph
- `/quit` — exit

### Configuration

Added to `config.yaml` under new `orchestrator` section:

```yaml
orchestrator:
  llm:
    model: "anthropic/claude-sonnet-4-6"    # LiteLLM format
    temperature: 0.7
    max_tokens: 4096

  context:
    window_size: 20              # max turns before compaction
    token_budget: 8000           # max tokens per API call
    compaction_batch: 10         # compact this many messages at once
    idle_seconds_before_compact: 600
    extraction_buffer:
      min_turns: 3
      min_words: 200
      max_turns: 10
      short_turn_words: 20
    retrieve:
      hops: 2
      top_k: 20
      strategies:
        superseded_pruning: true
        confidence_threshold: 0.6
        recency_decay: false
        token_budget: 0

  system_prompt:
    static: |
      You are a helpful coding assistant. Use the project knowledge
      below to maintain consistency with prior decisions and context.
    include_edges: true          # show decision → rationale chains
    group_by_type: true          # group nodes by type
    context_token_limit: 2000    # max tokens for graph context portion

  tools:
    enabled:
      - bash
      - read_file
      - write_file
      - glob
      - grep
    sandbox_root: "."            # restrict file access
    max_output_chars: 10000      # truncate large outputs
    bash_timeout: 30
```

### What Gets Reused vs. What's New

**Reused (no changes needed):**
- `context_broker.store.GraphStore` — all methods
- `context_broker.retriever.retrieve_with_stats()` — full strategy pipeline
- `context_broker.extractor.extract_turn()` — incremental extraction with context
- `context_broker.extractor.verify_extraction()` — background verification
- `context_broker.extractor.ExtractionBuffer` — turn batching
- `context_broker.config.load_config()` — YAML deep-merge with defaults
- All extraction prompts in `prompts.py`

**New code (~500-700 lines total):**
- `orchestrator/types.py` (~40 lines)
- `orchestrator/llm_adapter.py` (~80 lines)
- `orchestrator/tool_executor.py` (~150 lines)
- `orchestrator/context_manager.py` (~200 lines)
- `orchestrator/system_prompt_builder.py` (~50 lines)
- `orchestrator/conversation.py` (~150 lines)
- `orchestrator/cli.py` (~30 lines)

### Edge Cases

| Edge Case | Solution |
|-----------|----------|
| **Empty graph (first conversation)** | System prompt has no context section. Graph populates as compaction runs. First few turns operate like a normal chatbot. |
| **Long tool output (>10k chars)** | Truncate at `max_output_chars`. Optionally LLM-summarize: "Summarize this output in 500 chars." |
| **Concurrent extraction** | SQLite WAL mode (already enabled in store.py) handles concurrent reads + one writer. Background extraction worker won't block the conversation. |
| **Provider-specific tool schemas** | LiteLLM handles translation between Anthropic tool_use, OpenAI function_calling, Gemini function_declarations. `llm_adapter.py` normalizes to internal ToolCall. |
| **System prompt grows as graph grows** | Token budget applies to full call (system + context + history). If graph context exceeds `context_token_limit`, reduce `top_k` for retrieval dynamically. |
| **Extraction of compacted messages** | `extract_turn()` receives existing nodes from graph, won't re-extract known facts. Dedup by fact-hash as final safety net. |
| **User wants to reference old context** | Graph retains all extracted knowledge. Even after messages are pruned from history, facts are retrievable via graph query. |

### Implementation Sequence

**Week 1:**
- `types.py` — data classes
- `llm_adapter.py` — LiteLLM integration, tool schema generation
- `tool_executor.py` — bash, read, write, glob, grep implementations
- Start `context_manager.py` — sliding window, token estimation

**Week 2:**
- Complete `context_manager.py` — compaction flow, retrieval integration
- `system_prompt_builder.py` — dynamic prompt composition
- `conversation.py` — main loop with tool call handling
- `cli.py` — REPL entry point
- Testing and edge cases

---

## Phase 2: Full Orchestrator (2-4 weeks after Phase 1)

### Feature Roadmap

| Feature | Effort | Depends On | Description |
|---------|--------|-----------|-------------|
| **Multi-agent coordination** | 1 week | Phase 1 | Spawn sub-agents for complex tasks (research, code review, planning). Agents share the same graph store. Parent coordinates and merges results. Context routing passes relevant subgraph to each sub-agent. |
| **Advanced tool suite** | 1 week | Phase 1 tools | python_exec, git, http_request. Tool chaining (output of one → input of next). Retry with backoff. Output caching (don't re-run same tool in same turn). |
| **Tool output processing** | 3-5 days | Phase 1 tools | Smart truncation (keep important parts of long outputs). LLM summarization for long-term storage. Diff visualization for file edits. Streaming output display. |
| **Enhanced compaction** | 1 week | Phase 1 context_manager | Selective compaction (task-aware — only compact nodes relevant to current work). Importance weighting (prioritize nodes by frequency of retrieval). Graph versioning (snapshot/restore at key points). |
| **Planning / decomposition** | 3-5 days | Multi-agent | Auto-break complex tasks into subtasks. ReAct-style reasoning traces. Max subtask depth. |
| **TUI / IDE integration** | 1-2 weeks | Phase 1 | Rich terminal UI with context window visualization. VS Code extension via HTTP server. Graph DAG explorer. Per-file context view. |
| **Adaptive strategies** | 1 week | Enhanced compaction | Learn which retrieval strategies work best for this project over time. Adaptive temperature/top_p based on task type. |

### Phase 2 File Structure

```
orchestrator/
├── (Phase 1 files)
├── agent.py                      # Base Agent class
├── agent_coordinator.py          # Multi-agent spawn/manage
├── agent_registry.py             # Task → agent routing
├── tool_suite.py                 # Expanded tool implementations
├── output_processor.py           # Smart truncation & summarization
├── planner.py                    # Task decomposition
├── context_filter.py             # Task-aware node selection
├── graph_versioning.py           # Snapshot/restore
├── ide_server.py                 # HTTP server for IDE clients
├── ide_protocol.py               # IDE message schema
└── ui/
    ├── __init__.py
    ├── interactive.py            # Rich TUI
    └── colors.py
```

### Phase 2 Configuration Additions

```yaml
orchestrator:
  # ... Phase 1 config inherited ...

  agents:
    enabled: true
    registry:
      research:
        llm_model: "anthropic/claude-sonnet-4-6"
        tools: [bash, read_file, http_request]
        system_prompt: "You are a research assistant..."
      code_review:
        llm_model: "anthropic/claude-sonnet-4-6"
        tools: [read_file, grep, git]
        system_prompt: "You are a code reviewer..."

  tools:
    # Phase 1 + expanded
    enabled:
      - bash
      - read_file
      - write_file
      - glob
      - grep
      - python_exec
      - git
      - http_request
    async_output: true
    cache_output: true
    max_retries: 3

  context:
    # Phase 1 + enhancements
    selective_compaction: true
    importance_weighting: true
    version_snapshots: true

  ide:
    enabled: true
    server_port: 8765
    server_host: "127.0.0.1"

  planning:
    enabled: true
    auto_decompose: true
    max_subtasks: 10
    subtask_timeout: 300
```

### Phase 2 Implementation Sequence

**Weeks 1-2:** Multi-agent framework + advanced tools
**Weeks 2-3:** Tool output processing + enhanced compaction
**Weeks 3-4:** Planning/decomposition + TUI
**Week 4+:** Adaptive strategies, IDE integration, polish

### Competitive Positioning (End of Phase 2)

| Feature | Phase 1 | Phase 2 | vs. AutoGen | vs. LangChain | vs. Claude Code |
|---------|---------|---------|-------------|---------------|-----------------|
| Tool use | Basic (5 tools) | Advanced (8+ tools) | Match | Match | Better (context-aware) |
| Context management | Sliding window | Multi-level compaction | Better | Better | Better (graph-based) |
| Multi-agent | N/A | Native | Match | Match | Better (shared graph) |
| IDE integration | N/A | VS Code + TUI | Better | Better | Comparable |
| Graph persistence | Yes | Enhanced (versioned) | Unique | Unique | Unique |
| Token optimization | Proactive | Adaptive | Better | Better | Better |
| Model agnostic | Yes (LiteLLM) | Yes | Match | Match | Better (vs Code's Anthropic lock-in) |

### Success Metrics

- Handle 50+ turns without context loss (facts survive compaction)
- 30-40% token reduction vs. naive full-history approach
- Graph retrieval latency <100ms per turn
- Compaction extraction <5s (async, non-blocking)
- Sub-agent spawn time <2s (Phase 2)
- Support 3+ model providers with identical behavior

---

## API & Authentication Notes

- **Anthropic API**: Separate from claude.ai subscription. Pay-per-token at console.anthropic.com. Claude Sonnet 4.6: $3/MTok input, $15/MTok output. ~$0.012 per coding turn at 4k context. ~$18/month at heavy usage (50 turns/day).
- **LiteLLM**: Abstracts provider differences. Single codebase works with Anthropic, OpenAI, Gemini, Groq, and local models (LM Studio, Ollama).
- **Local models**: LM Studio / Ollama expose OpenAI-compatible endpoints. Tool use support depends on model (Llama 3.x, Qwen 3.x support it; smaller models unreliable).
- **Claude Code CLI**: Authenticates via claude.ai subscription through an opaque internal mechanism — not usable for custom API calls.

---

## Relationship to Existing Context Broker Features

### Features That Carry Forward Unchanged
- Graph storage (SQLite with WAL)
- Extraction pipeline (main + verification + targeted passes)
- Retrieval with full strategy pipeline
- Fact-hash deduplication
- Incremental extraction with context awareness
- ExtractionBuffer for turn batching
- All 13 extraction prompt rules

### Features That Become More Powerful
- **Session state** → replaced by sliding window (more precise control)
- **Heuristic Tier 1** → still useful for immediate context before compaction completes
- **Background extraction** → becomes the compaction mechanism itself
- **Per-turn retrieval** → now controls the *entire* system prompt, not just an addendum

### Planned Features That Integrate Naturally
- **PostToolUse hook** → becomes `tool_executor.py` with built-in output management
- **`--numerics` targeted pass** → available during compaction for high-recall extraction
- **Reconciliation** → runs periodically to detect supersedes relationships in growing graph

---

## Quick Reference: What to Build First

```
Day 1:  types.py + llm_adapter.py (LiteLLM working with tool schemas)
Day 2:  tool_executor.py (bash, read, write running locally)
Day 3:  context_manager.py — sliding window + token budgeting
Day 4:  context_manager.py — compaction flow (extract_turn → merge → prune)
Day 5:  system_prompt_builder.py + retrieval integration
Day 6:  conversation.py — main loop with tool call handling
Day 7:  cli.py + REPL + testing
Day 8-10: Edge cases, polish, configuration validation
Day 11:  True LiteLLM streaming (stream_llm + _llm_loop_stream)
```

---

## Milestone 7 — True LiteLLM Streaming

### Motivation

Tool call rounds cannot benefit from streaming (arguments arrive as partial JSON that must be reassembled before execution). But the **final synthesizing reply** — the text the user actually reads — can stream token-by-token for a responsive feel. This milestone adds "quickest path" streaming that maximises time-to-first-token (TTFT) without complicating tool-call handling.

### Design: "Quickest Path"

```
No tools configured → stream_llm() directly          ← always TTFT
Tools + no tool calls on round 1 → yield text inline ← no double call
Tools + tool rounds → call_llm() × N, then stream_llm() ← TTFT on final reply
Max rounds hit → stream_llm() for final answer
```

### Changes

| File | Change |
|------|--------|
| `orchestrator/llm_adapter.py` | Extract `_build_kwargs()` helper; add `stream_llm()` async generator |
| `orchestrator/conversation.py` | Refactor `chat_stream()` to use `_llm_loop_stream()`; add `_llm_loop_stream()` |

### `_build_kwargs()` (llm_adapter.py)

Extracted shared kwargs construction from `call_llm()` so `stream_llm()` can reuse it without duplication. Takes `messages`, `system`, `cfg`, optional `tools` list; returns `dict` (no `stream` key).

### `stream_llm()` (llm_adapter.py)

```python
async def stream_llm(messages, system, cfg) -> AsyncIterator[str]:
    kwargs = _build_kwargs(messages, system, cfg)
    kwargs["stream"] = True
    response = await litellm.acompletion(**kwargs)
    async for chunk in response:
        delta = chunk.choices[0].delta
        content = getattr(delta, "content", None)
        if content:
            yield content
```

No retry — resuming a partial stream is unsound. Caller handles fallback.

### `_llm_loop_stream()` (conversation.py)

Replaces the single `call_llm()` in `chat_stream()`. Tool rounds use non-streaming `call_llm()`; only the final answer streams.

```python
# No tools: stream directly
if not tools:
    async for chunk in stream_llm(history, system, cfg): yield chunk; return

# Tool rounds
for round_num in range(_MAX_TOOL_ROUNDS):
    text, tool_calls, finish_reason = await call_llm(...)
    if finish_reason != "tool_calls":
        if text: yield text   # no second call needed
        return
    # execute tools, persist to context_mgr, extend history...

# Final synthesizing answer (or max-rounds fallback)
async for chunk in stream_llm(history, system, cfg): yield chunk
```
