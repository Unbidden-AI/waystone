# Context Broker — Commercial Roadmap

This document outlines the path from the current open-source CLI tool to a commercially viable product. It captures the strategic direction, prioritized milestones, and implementation plan for the two highest-leverage bets: a hosted API with MCP server integration, and a one-click onboarding flow.

---

## Strategic Priorities

### Why these two bets?

**Hosted API + Claude Code MCP Server** is the shortest path to revenue:
- MCP is the emerging standard for context injection across Claude, Cursor, Windsurf, and other AI coding tools
- Being a native MCP server puts Context Broker in the Anthropic ecosystem's distribution channel at zero CAC
- The API abstraction enables team sharing, cross-machine sync, and usage-based billing

**One-click onboarding** is the unlock for retention:
- The current setup (manual `waystone extract`, CLI hooks) has too much friction for mainstream adoption
- Users need to see value within 60 seconds or they churn
- "Import your last 5 Claude sessions" requires zero domain knowledge — it just works

---

## Phase 1: MCP Server (4–6 weeks)

### What
Expose Context Broker as a native MCP server so Claude Code (and any MCP-compatible client) can call it directly — no hook installation, no shell scripts.

### MCP Tools to expose

| Tool | Description |
|------|-------------|
| `context_broker_query` | Retrieve relevant context for a task description |
| `context_broker_extract` | Extract facts from a text block and merge into graph |
| `context_broker_list_projects` | List available projects |
| `context_broker_stats` | Return node/edge counts for a project |

### Architecture

```
Claude Code
    │
    ▼ MCP protocol (stdio or SSE)
context-broker MCP server (new: mcp_server.py)
    │
    ▼ existing Python API
GraphStore (SQLite) + retriever.py
```

The MCP server is a thin wrapper around the existing `retrieve()` and `extract()` functions. No new business logic needed.

### Implementation steps

1. Add `mcp` dependency (`pip install mcp`)
2. Create `context_broker/mcp_server.py` implementing the four tools above
3. Add `waystone mcp-serve` CLI command (launches the MCP server on stdio or SSE)
4. Add `mcp_server` entry point to `pyproject.toml`
5. Write `claude_mcp_config.json` snippet users can paste into their Claude Code settings
6. Update `GETTING_STARTED.md` with MCP setup instructions (replaces hook-based setup)

### User experience (after Phase 1)

```json
// ~/.claude/claude_desktop_config.json
{
  "mcpServers": {
    "context-broker": {
      "command": "waystone",
      "args": ["mcp-serve"]
    }
  }
}
```

No hook installation. Claude Code calls `context_broker_query` automatically on every prompt.

---

## Phase 1: MCP Server (4–6 weeks)

### What
Expose Context Broker as a native MCP server so Claude Code (and any MCP-compatible client) can call it directly — no hook installation, no shell scripts.

### MCP Tools to expose

| Tool | Description |
|------|-------------|
| `context_broker_query` | Retrieve relevant context for a task description |
| `context_broker_extract` | Extract facts from a text block and merge into graph |
| `context_broker_list_projects` | List available projects |
| `context_broker_stats` | Return node/edge counts for a project |

### Architecture

```
Claude Code
    │
    ▼ MCP protocol (stdio or SSE)
context-broker MCP server (new: mcp_server.py)
    │
    ▼ existing Python API
GraphStore (SQLite) + retriever.py
```

The MCP server is a thin wrapper around the existing `retrieve()` and `extract()` functions. No new business logic needed.

### Implementation steps

1. Add `mcp` dependency (`pip install mcp`)
2. Create `context_broker/mcp_server.py` implementing the four tools above
3. Add `waystone mcp-serve` CLI command (launches the MCP server on stdio or SSE)
4. Add `mcp_server` entry point to `pyproject.toml`
5. Write `claude_mcp_config.json` snippet users can paste into their Claude Code settings
6. Update `GETTING_STARTED.md` with MCP setup instructions (replaces hook-based setup)

### User experience (after Phase 1)

```json
// ~/.claude/claude_desktop_config.json
{
  "mcpServers": {
    "context-broker": {
      "command": "waystone",
      "args": ["mcp-serve"]
    }
  }
}
```

No hook installation. Claude Code calls `context_broker_query` automatically on every prompt.

---

## Phase 2: Hosted API (8–12 weeks)

### What
Replace local SQLite with a cloud backend. Graphs sync across machines. Teams share a project graph. Usage-based billing.

### Architecture

```
Client (CLI / MCP server / SDK)
    │
    ▼ HTTPS + API key
Context Broker Cloud API
    ├── POST /v1/projects/{project}/extract
    ├── POST /v1/projects/{project}/query
    ├── GET  /v1/projects/{project}/stats
    └── GET  /v1/projects/{project}/export
    │
    ▼
PostgreSQL (nodes/edges) + object store (transcript archives)
```

The local CLI becomes a thin client: it makes HTTP calls to the cloud API instead of reading/writing local SQLite. The extraction LLM call moves server-side (we absorb the Gemini cost, charge a margin).

### API Design

```
GET /v1/projects/
GET /v1/projects/{project}
POST /v1/projects/{project}/extract
POST /v1/projects/{project}/query
GET  /v1/projects/{project}/stats
GET  /v1/projects/{project}/export
```

**Auth:** API key (Bearer token). Rate-limited per tier.

**Pricing tiers:**

| Tier | Monthly | Per-project cost | Extraction limit | Storage | Max API calls/min |
|------|---------|------------------|------------------|---------|-------------------|
| Free | $0 | — | 1 project, 50 MB | 50 MB | 10 |
| Pro | $40 | included | 10 projects, 1 GB each | 10 GB | 100 |
| Team | $200 | — | Unlimited | 50 GB | 1000 |

**Billing model:**
- Monthly recurring per tier
- Usage overage: $0.10/1M API calls beyond tier limit
- Storage overage: $0.05/GB/month beyond tier limit

### Implementation steps

1. Spin up a Vercel + Supabase (PostgreSQL) project
2. Migrate `GraphStore` to use PostgreSQL backend (via `psycopg2` or `sqlalchemy`)
3. Implement auth middleware (API key validation)
4. Implement rate limiting (sliding-window per key)
5. Deploy the API to Vercel
6. Build a minimal web dashboard for key management and usage stats
7. Update CLI to auto-switch to API mode when `CONTEXT_BROKER_API_KEY` is set

---

## Phase 3: One-Click Onboarding (6–8 weeks)

### What
Reduce "first value" friction from 15 minutes of manual setup to 60 seconds of clicking a button.

### MVP flow

1. User opens landing page (vercel.app subdomain)
2. "Connect Claude Code" button → reads `~/.claude/settings.json`
3. Auto-inserts MCP server config
4. "Import your last 5 sessions" button → scans Claude Code conversation history
5. Extracts facts from each session in parallel (via hosted API)
6. "Done!" → dashboard with node count, retrieval stats
7. Optional: onboarding tour of the CLI

### Landing page

Minimal Vercel + Next.js site:
- Hero: "Persistent memory for Claude Code"
- CTA: "Connect in 60 seconds"
- Three-step onboarding flow
- Pricing & FAQ
- Dashboard login (API key)

### Dashboard (minimum viable)

- API key management (create/revoke)
- Project list (nodes/edges/last-synced)
- Extraction history (what was added, when)
- Usage stats (API calls, tokens spent, storage)

---

## Timeline & Staffing

| Phase | Duration | Effort | Solo? | Dependencies |
|-------|----------|--------|-------|--------------|
| Phase 1 (MCP) | 4–6 weeks | ~100 hours | Yes | None |
| Phase 2 (Hosted API) | 8–12 weeks | ~200 hours | Yes (harder) | Phase 1 complete |
| Phase 3 (Onboarding) | 6–8 weeks | ~150 hours | Yes | Phase 2 stable |

**Critical path:** Phase 1 → Phase 2 → Phase 3. Phases 1 and 2 must be done before any public launch.

### Pricing model

| Tier | Price | Limits |
|------|-------|--------|
| Free | $0 | 1 project, 500 nodes, community support |
| Pro | $20/mo | 10 projects, 10k nodes, 1 user |
| Team | $80/mo | Unlimited projects, 100k nodes, 5 seats, shared graphs |
| Enterprise | Custom | On-prem/VPC, SSO, audit log, SLA |

**Usage-based overage**: $0.005 per extraction LLM call above tier limit.

### Switchover strategy

- `config.yaml` gains a `api_url` and `api_key` field
- When `api_url` is set, all store/retrieve operations route to the cloud; otherwise use local SQLite
- This is backward-compatible — local mode remains fully functional forever

### Team sharing

When two users share a project name and API key, they write to and read from the same graph. Every extraction includes the author's user ID as `source_user`. The `superseded_pruning` and `reconcile` features work the same way, now across the team's combined session history.

---

## Phase 3: One-Click Onboarding (2–3 weeks, can parallel Phase 1)

### What
A single command that auto-discovers recent Claude Code sessions, imports them into Context Broker, and shows the user what was extracted — all in under 2 minutes.

### Implementation: `waystone onboard` command

```bash
waystone onboard [project]
```

**Step 1 — Discover sessions**

Claude Code stores session transcripts (via the Stop hook) at:
```
~/.context-broker/transcripts/<project>/
```

For new users who haven't set up the hook yet, also check Claude Code's own session storage:
```
~/.claude/projects/<project-hash>/*.jsonl
```

Provide a `waystone import-claude-sessions` subcommand that reads `.jsonl` files and converts them to the markdown transcript format Context Broker expects.

**Step 2 — Present a menu**

```
Found 7 recent sessions for project 'MailMind':

  [1] 2026-03-10  14,200 chars  "architecture planning session"
  [2] 2026-03-09   8,400 chars  "API design"
  [3] 2026-03-08  21,000 chars  "authentication system"
  [4] 2026-03-07   5,100 chars  "database schema"
  [5] 2026-03-06   3,200 chars  "initial planning"

Import all 5? [Y/n]  or select (e.g. 1,3,5):
```

**Step 3 — Batch extract**

Run `waystone extract` on each selected transcript with `--verify`. Show a progress bar. Use `--chunk-size 30000` automatically for transcripts over 40k chars.

**Step 4 — Show value immediately**

After import, run `waystone query <project> "what are the key architectural decisions?"` and print the result. The user sees their own knowledge reflected back at them in under 2 minutes.

### JSONL → markdown converter

Claude Code `.jsonl` session files have one JSON object per line with `role` and `content` fields. The converter:

```python
def jsonl_to_markdown(jsonl_path: Path) -> str:
    lines = []
    for line in jsonl_path.read_text().splitlines():
        msg = json.loads(line)
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "user":
            lines.append(f"**Human:** {content}\n")
        elif role == "assistant":
            lines.append(f"**Assistant:** {content}\n")
    return "\n".join(lines)
```

---

## Phase 4: Advanced Differentiation (parallel to Phase 2–3)

These features are not required for launch but represent the highest-leverage architectural moats. Schedule them after Phase 1 ships and benchmark numbers are solid.

### 4a: Local LLM Extraction (air-gapped / enterprise)

**Problem**: Enterprise security teams (fintech, defense, legal) cannot send design conversations to any cloud API — not Gemini, not OpenAI, not Anthropic.

**Solution**: Gate extraction behind a configurable `extraction_backend`:
- `cloud` (default): current Gemini/OpenAI/Claude routing
- `local`: route to a local vLLM or Ollama instance

Recommended local model: **Qwen 2.5-32B** (5/5 constraint-following score in benchmarks, 15–20 tok/s on a 64GB machine). Already validated for structured extraction in internal benchmarks.

```yaml
# config.yaml
extraction_backend: local
local_llm_url: http://localhost:11434/v1
local_llm_model: qwen2.5:32b
```

This makes Context Broker the only memory tool that works fully air-gapped. Mem0 and Zep have no equivalent.

### 4b: Bi-Temporal Fact Validity

**Problem**: `recency_decay` penalizes old facts but doesn't expire them. A superseded constraint stays retrievable at reduced confidence indefinitely, creating noise. There's no distinction between "when this decision was made" and "when it was ingested."

**Solution**: Add `valid_from` / `valid_until` timestamps to nodes (event time, not ingestion time). Facts expire rather than decay. Enables accurate historical queries: "what did we decide about auth before the PCI audit?"

Based on the Zep/Graphiti bi-temporal model (arXiv:2501.13956). Implementation: new columns on the `nodes` table; extraction prompt updated to capture event dates where present.

### 4c: Graph Synthesis (RAPTOR-style Summarization)

**Problem**: As a graph grows (5k+ nodes), no single query can surface a high-level view. "What have we decided about the payment system this quarter?" requires synthesizing dozens of disconnected nodes.

**Solution**: Periodic background job that clusters related nodes into higher-level summary nodes linked via `synthesizes_from` edges. Three levels:
- **L0**: raw extracted facts (existing)
- **L1**: weekly summaries per topic cluster
- **L2**: project-level architectural overview

These summary nodes are retrievable like any other, dramatically improving "give me an overview" queries.

### 4d: Zero-Friction Hook Capture

**Problem**: Users must explicitly run `waystone extract` on a transcript. This is too much friction — most sessions are never captured.

**Solution**: Claude Code hook that silently captures every session on stop. VS Code extension that captures on save/close. Zero-configuration capture: install once, memory accumulates automatically.

This is already partially specified in Phase 3 (`waystone onboard`). The delta here is **automatic ongoing capture** vs. one-time import. The hook runs extraction in the background after each session ends, so the graph grows without the user ever thinking about it.

### 4e: Graph Portability (data ownership commitment)

**Problem**: If a user cancels their subscription, or wants to migrate to a self-hosted instance, they should be able to take their graph with them.

**Commitment** (encode in ToS and implement in CLI):
- `waystone export <project>` dumps a full SQLite file — readable without Context Broker
- `waystone import <file>` ingests an exported graph into any instance (local or cloud)
- Exported graphs include all nodes, edges, confidence scores, and timestamps

This is a trust signal. Mem0's cloud is a black box. "You own your memory" is a real differentiator for privacy-conscious developers and enterprises.

### 4f: Process Knowledge — `reflect`, `survey`, and the `process` Node Type

**Problem**: `waystone extract` captures facts declared in a single conversation turn. It cannot capture implicit process knowledge — the pattern that emerges when you try A, try B, try C, and B wins. These convergent conclusions are never *declared*; they're *discovered* across a conversation arc. Without capturing them, teams repeat the same experiments every session.

This is especially acute in software development workflows: "we always use the failure analysis script before tweaking the retriever" or "LLM judge runs are only needed for production benchmarks, not retrieval tuning" are process facts that are obvious in hindsight but never written down.

**Design**

Three interlocking pieces:

**1. `process` node type** (software_dev domain only initially)

A new node type that captures emergent workflows, protocols, and team conventions. Unlike `decision` (a single choice at a point in time), a `process` node describes a *repeatable pattern* that applies across multiple sessions.

```
type: process
fact: "When tuning retriever parameters, use keyword scorer (failure_analysis.py) for rapid iteration; reserve LLM judge runs for validating final configs before committing results."
tags: ["retrieval", "benchmark", "process", "scorer", "locomo"]
confidence: 0.9
domain: software_dev
```

Process nodes are:
- Tagged with `domain: software_dev` and `type: process` for type-filtered retrieval
- Supersedeable — when a process changes, the new `process` node supersedes the old one via the standard `supersedes` edge
- Injected as pinned context in software_dev domain sessions (always surfaced regardless of query)

**2. `waystone reflect` command** — conversation-arc synthesis

```bash
waystone reflect <project> <transcript> [--since-turn N] [--domain software_dev]
```

Unlike `waystone extract` (turn-by-turn fact capture), `reflect` reads the *full arc* of a conversation (or a window of it) and asks the LLM: "What processes, protocols, or convergent conclusions emerged from this work session?" It outputs `process` nodes and high-level `decision` nodes that span multiple turns.

The extraction prompt for `reflect` focuses on:
- Patterns discovered through iteration ("we tried X, Y, Z — Z worked because...")
- Implicit protocols established mid-session ("going forward we will...")
- Negatives worth preserving ("we ruled out X because...", "do not combine Y and Z")

**3. `waystone survey` command** (rename from `waystone synthesize`)

The existing `waystone synthesize` command does graph-to-graph cross-cutting synthesis (reads existing nodes, produces comparative summary nodes). It is renamed to `waystone survey` to avoid collision with `reflect` and better describe its role: surveying the accumulated graph for patterns.

```bash
waystone survey <project> [--type process] [--domain software_dev] [--output summary_nodes]
```

**In-session hook trigger**

`reflect` should fire automatically during a session, not just at session end (which is often systematic rather than intentional). The hook tracks a `_reflect_watermark` — the last transcript offset where `reflect` ran. When `(current_offset - _reflect_watermark) >= N turns` (default: N=20), the hook fires `waystone reflect` in the background against the transcript window since the watermark.

This is agnostic of the orchestrator. The hook reads a neutral transcript format:

```jsonl
{"role": "user", "content": "...", "ts": "2026-04-11T10:00:00Z"}
{"role": "assistant", "content": "...", "ts": "2026-04-11T10:00:05Z"}
```

Claude JSONL sessions are converted to this format by the existing Stop hook before being passed to `reflect`. Any orchestrator that produces `{role, content}` pairs is compatible.

**Implementation steps**

1. Add `process` to the node type enum in `store.py` and `extractor.py`
2. Add `domain` column to `nodes` table (nullable; existing nodes are unaffected)
3. Write `REFLECT_PROMPT` in `prompts.py` focused on arc-level process discovery
4. Add `reflect_extraction()` in `extractor.py` (separate from `extract()` and `verify_extraction()`)
5. Add `waystone reflect` CLI command in `cli.py`
6. Rename `waystone synthesize` → `waystone survey` in `cli.py` (keep old name as deprecated alias)
7. Add `_reflect_watermark` tracking to `hooks/context_broker_stop.py` and `hooks/context_broker_submit.py`
8. Update `waystone_sentence_index` and `software_dev` domain profiles to pin `process` nodes
9. Update `retriever.py` type_order to place `process` nodes at the top of the output (alongside `decision`)

**Why this matters competitively**

No existing memory tool attempts to capture process knowledge. Mem0 stores facts. Zep stores facts. Both miss the convergent conclusions that emerge from multi-turn iteration. This is the class of knowledge that most determines whether a team gets faster or slower over time.

The combination of `process` nodes + `reflect` + in-session triggering makes Context Broker the first tool that captures *how a team works*, not just *what a team decided*.

---

## Phase 5: Distribution & Growth (ongoing)

### Hermes Agent Memory Provider Integration

**What**: Implement Waystone as a first-class memory provider plugin for [Hermes Agent](https://github.com/nousresearch/hermes-agent) (Nous Research, Feb 2026 — 64K+ GitHub stars, ~$1B valuation). Hermes is the fastest-growing open-source autonomous agent platform of 2026, operating across Discord, Telegram, Slack, Email, and 10+ other platforms.

**Why this matters**: Hermes has a first-class, documented `MemoryProvider` plugin interface. Existing providers (Mem0, Honcho, Hindsight, Supermemory) are all plugging into the same interface. Waystone's DAG structure + typed nodes + supersedes logic + SQLite (zero infra) is the most differentiated option in that ecosystem. This is a direct distribution channel to 64K+ developers already using Hermes.

**Architecture**: Hermes calls `prefetch(query)` before each turn and `sync_turn(user, assistant)` after each response. Waystone maps cleanly:

| Hermes lifecycle hook | Waystone operation |
|---|---|
| `initialize(session_id)` | Open GraphStore, configure project path |
| `prefetch(query)` | Run BFS retrieval (`retriever.py`), inject structured context into system prompt |
| `sync_turn(user, assistant)` | Queue `waystone extract` on new turn content (background, non-blocking) |
| `get_tool_schemas()` | Expose `waystone_query`, `waystone_search` as Hermes tools |
| `shutdown()` | Flush extraction queue, close store |

**Implementation** (`plugins/memory/waystone/` in the Hermes Agent repo, or as a standalone installable):

```
plugins/memory/waystone/
├── __init__.py       # WaystoneMemoryProvider(MemoryProvider) implementation
├── plugin.yaml       # name, description, config_schema
└── cli.py            # waystone-specific CLI extensions (optional)
```

Key implementation notes:
- `prefetch()` must return in ≤5 seconds (Hermes hard deadline) — BFS retrieval is sub-second, safe
- `sync_turn()` must be non-blocking — queue extraction to a background thread; don't await LLM call
- `is_available()` checks that the configured Waystone project DB exists; no network calls
- Config fields: `project` (required), `db_path` (optional override), `top_k`, `hops`

**MCP path (orthogonal, already works)**: Hermes is a full MCP client. The existing `waystone-mcp` server can be pointed at by any Hermes instance today with zero new code — just add it to Hermes's MCP server list. This is the zero-effort integration; the memory provider plugin is the deeper, lifecycle-aware integration.

**Competitive position among Hermes memory providers**:

| Provider | Storage | Infra required | Graph structure | Temporal validity |
|---|---|---|---|---|
| **Waystone** | SQLite | None (local file) | DAG + typed edges | ✓ supersedes, valid_to |
| Mem0 | Vector DB | Cloud API | Flat cards | ✗ |
| Hindsight | External server | Cloud API | Entity graph | Partial |
| Honcho | Cloud DB | Cloud API | Flat | ✗ |
| Supermemory | Cloud DB | Cloud API | Flat | ✗ |

Waystone is the only zero-infrastructure, fully local, graph-structured option.

**Implementation effort**: ~1 day. The `MemoryProvider` interface is fully documented and stable.

**Next steps**:
1. Implement `plugins/memory/waystone/__init__.py` against Hermes's abstract `MemoryProvider`
2. Test against Hermes's CLI gateway (Discord or Telegram session)
3. Submit as a PR to `nousresearch/hermes-agent` plugins directory
4. Publish as `waystone-hermes` on PyPI for standalone install (`pip install waystone-hermes`)
5. Cross-post to Hermes's Discord/community to claim the memory provider slot before competitors

### Claude Code Marketplace / MCP Registry

Submit to Anthropic's MCP server registry once it exists. This is the single highest-leverage distribution channel — zero CAC for the target audience.

### Cursor / Windsurf plugins

Both support MCP. The same `waystone mcp-serve` command works. Publish to each tool's plugin marketplace with a one-paragraph description.

### Team network effect

The most compelling long-term use case: a software team where multiple developers contribute to a shared knowledge graph. Every design session, every architecture decision, every "why did we do it this way" is captured. New team members onboard by running `waystone query <project> "give me an overview of the architecture"`.

Lean into this in marketing: **"Shared memory for your entire engineering team."**

---

## Competitive Moat

| Moat | Strength | Notes |
|------|----------|-------|
| Structured graph (not embeddings) | High | BFS traversal + typed edges + supersedes logic is qualitatively different from vector similarity |
| Team network effects | High | Shared graph grows more valuable with more contributors; cannot replicate with flat cards |
| `supersedes` edges | High | Structural temporal invalidation — Mem0 accumulates contradictions; Waystone resolves them at ingest |
| Local LLM / air-gapped | High | Enterprise/fintech/defense use case; Mem0 and Zep have no answer to this |
| Typed nodes | Medium | decisions, constraints, trade-offs enable type-filtered retrieval; Mem0 cards are untyped blobs |
| Zero per-query LLM cost | Medium | Retrieval is pure graph traversal; Mem0 runs LLM on every search call |
| Data portability | Medium | SQLite export = you own your graph; Mem0 cloud is a black box |
| Graph synthesis (Phase 4c) | Medium | L1/L2 summary nodes enable high-level queries; Mem0 flat cards never get synthesized |
| Process knowledge (`reflect`) | High | Captures emergent team protocols and convergent conclusions across conversation arcs; no equivalent in Mem0, Zep, or any other memory tool |
| Native MCP integration | Medium | First-mover advantage; other tools will follow |
| Switching costs (accumulated graph) | Medium | A 6-month-old graph with 5,000 nodes is hard to abandon |
| Integration breadth | Low | Cursor, Windsurf, Zed integrations require maintenance; anyone can copy |

The moat is **thin in year 1, strong in year 2+** as accumulated graphs and team adoption create lock-in.

---

## Open Questions

- **Extraction cost economics**: At $0/mo free tier, we absorb Gemini extraction costs. Need to model this against free-tier conversion rates.
- **Privacy / data residency**: Enterprise customers will ask about on-prem. Phase 2 should be designed for self-hosting from day one.
- **Graph ownership**: Formalized in Phase 4e — `waystone export` / `waystone import` with SQLite portability guarantee. Encode in ToS.
- **Benchmark the MCP path**: Does Claude Code use `context_broker_query` on every prompt, or only when it decides context is needed? This matters a lot for recall vs. latency tradeoffs.
