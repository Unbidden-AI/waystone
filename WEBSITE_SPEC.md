# Website Spec — unbidden.ai

**Company:** Unbidden AI
**Product:** Engram
**Domain:** unbidden.ai
**Stage:** Pre-launch

---

## Site Architecture

```
unbidden.ai/                        → Company home
unbidden.ai/engram                  → Product page
unbidden.ai/pricing                 → Pricing tiers
unbidden.ai/docs/                   → Docs landing
unbidden.ai/docs/quickstart         → 5-minute getting started
unbidden.ai/docs/mcp                → MCP server reference
unbidden.ai/docs/mcp/tools          → MCP tool reference (engram_query, etc.)
unbidden.ai/docs/mcp/clients        → Per-client config (Claude Code, Cursor, etc.)
unbidden.ai/docs/api                → REST API reference
unbidden.ai/docs/cli                → CLI (engram) reference
unbidden.ai/docs/config             → config.yaml reference
unbidden.ai/docs/integrations/      → Per-integration guides
unbidden.ai/blog/                   → Technical posts
unbidden.ai/changelog               → Product updates
```

---

## Page Specs

### `/` — Company Home

**Above the fold:**
- Tagline: *"AI that remembers. Context that compounds."*
- Subhead: *"Unbidden builds memory infrastructure for AI development workflows. Your AI gets smarter the longer it works with you."*
- Single CTA: `Try Engram →`
- No pricing on home.

**Below the fold:**
- Problem one-liner: *"Every AI session starts from zero. Unbidden fixes that."*
- Product card: Engram
- Footer: GitHub, docs, pricing, blog, contact

**Tone:** Technical, confident, not startup-bro. Written by a developer who is tired of bad tooling.

---

### `/engram` — Product Page

**1. Hero**
> *"Your AI forgets everything between sessions. Engram doesn't."*

Subhead: *"Persistent memory for AI-assisted development. Works with any OpenAI-compatible model via MCP or REST API."*
CTAs: `Get Started (free)` | `View Docs`

**2. Problem section — three cards:**
- *Local model users:* "Your 4K context fills up at turn 15. Engram extends it to unlimited."
- *API teams:* "You're paying for every token in history. Engram cuts that by 60–80%."
- *Long-running projects:* "Week 6 AI contradicts week 1 decisions. Engram prevents that."

**3. How it works — three steps:**
1. Connect Engram to your AI editor via MCP or REST API
2. Engram automatically extracts and stores what matters from every session
3. Next session, Engram surfaces only what's relevant — not everything, not nothing

**4. Social proof (fill post-launch):**
- "95% recall across 23 benchmark questions"
- Benchmark comparison table

**5. Integration list:**
Claude Code · Cursor · Windsurf · Continue.dev · OpenClaw · any MCP-compatible client · REST API

**6. Pricing teaser → `/pricing`**

**7. FAQ** (see `/docs` FAQ section)

---

### `/pricing`

| | Free | Pro | Team |
|---|---|---|---|
| Price | $0 | $20/mo | $80/mo |
| Projects | 1 | Unlimited | Unlimited |
| Memory capacity | 500 facts | Unlimited | Unlimited |
| API calls | 10/min | 100/min | 500/min |
| Support | Community | Email | Priority |
| Users | 1 | 1 | Up to 10 |

- Free tier: API key only, no credit card
- Annual: Pro $200/yr, Team $800/yr (2 months free)
- CTA: *"Start free — no credit card required"*

---

### Email Capture (Global — every page)

Slim footer bar on all pages: *"Get the benchmark report + release notes → [email] [Subscribe]"*

- **Lead magnet:** Cost calculator breakdown PDF or benchmark methodology doc
- **Tool:** Buttondown or Resend (not Mailchimp)
- **Drip sequence:**
  1. Immediate: lead magnet + 3-bullet Engram summary
  2. Day 4: cost math blog post (value, no ask)
  3. Day 10: soft CTA to try free tier

---

## Docs Section

### `/docs` — Landing Page

Grid of doc sections with brief descriptions. Highlights:
- Quickstart (5 min)
- MCP Server
- REST API
- CLI Reference
- Integration Guides

---

### `/docs/quickstart`

**Goal:** Working Engram integration in under 5 minutes.

```bash
# 1. Install
pip install engram   # or: npm install -g engram

# 2. Initialize a project
engram init my-project

# 3. Extract a transcript
engram extract my-project transcript.txt

# 4. Query
engram query my-project "what auth approach did we decide on?"

# 5. Start MCP server (for editor integration)
engram mcp-serve
```

Then: link to the relevant client integration page.

---

### `/docs/mcp` — MCP Server Overview

Engram exposes a Model Context Protocol server that registers tools your AI editor calls automatically.

**Transport modes:**
| Mode | Command | Use case |
|------|---------|----------|
| stdio | `engram mcp-serve` | Claude Code, Cursor, Windsurf (default) |
| SSE | `engram serve --host 0.0.0.0 --port 8000` | Remote/networked clients |

**Authentication:** API key via `ENGRAM_API_KEY` env var (hosted mode) or none required (local mode).

**Compatibility:**

| Client | Status |
|--------|--------|
| Claude Code | ✅ Tested |
| Cursor | ✅ Tested |
| Windsurf | ✅ Tested |
| Continue.dev | ✅ Tested |
| OpenClaw | ✅ Tested |
| Any MCP client | ✅ via stdio or SSE |

---

### `/docs/mcp/tools` — MCP Tool Reference

The MCP server registers four tools. All tools auto-detect the active project from the nearest `.context-broker` marker file if `project` is omitted.

---

#### `engram_query`

Retrieve relevant context from the project memory for a given task.

```
engram_query(task, project?, cwd?, hops?, top_k?)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `task` | string | required | Task description or question to retrieve context for |
| `project` | string | auto | Project name — auto-detected from `.context-broker` if omitted |
| `cwd` | string | auto | Working directory for project auto-detection |
| `hops` | int | 3 | Retrieval depth |
| `top_k` | int | 25 | Max nodes to return |

**Returns:** Markdown block of the most relevant decisions, constraints, and implementation details from past sessions.

**Example response:**
```markdown
## Nodes (retrieved: 8 of 247)

- **Auth approach** [decision, confidence: 0.9]: We chose JWT over sessions because the
  API is stateless and we're deploying across multiple regions. (source: session-2025-03-01)

- **Database** [decision, confidence: 0.95]: PostgreSQL via Supabase. Rejected SQLite
  because of concurrent write requirements. (source: session-2025-02-28)
```

**When to call it:** At the start of every session, or when starting a new task within a session. Most clients do this automatically via hooks.

---

#### `engram_extract`

Extract facts from text and merge them into the project memory.

```
engram_extract(text, project?, cwd?, source_name?, verify?)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `text` | string | required | Text to extract from (transcript, spec, notes, etc.) |
| `project` | string | auto | Project name |
| `cwd` | string | auto | Working directory for auto-detection |
| `source_name` | string | `"mcp_extract"` | Label shown in node provenance |
| `verify` | bool | false | Run a second verification pass to catch missed facts |

**Input limit:** ~100,000 characters. For longer inputs, split and call multiple times.

**Returns:** Summary of what was extracted.

```
Extracted 12 nodes, 4 edges into 'my-project'.
Density: 2.3/1kc  Avg tags: 2.1
```

**When to call it:** At session end, or after any significant block of work. Most clients call this automatically via hooks.

---

#### `engram_stats`

Get memory statistics for the project.

```
engram_stats(project?, cwd?)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `project` | string | auto | Project name |
| `cwd` | string | auto | Working directory for auto-detection |

**Returns:** Node count, edge count, project age, last extraction timestamp, memory size.

---

#### `engram_list_projects`

List all projects in the current Engram store.

```
engram_list_projects()
```

**Returns:** Project names with node counts and edge counts.

---

### `/docs/mcp/clients` — Client Configuration

#### Claude Code

Add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "engram": {
      "command": "engram",
      "args": ["mcp-serve"],
      "env": {
        "ENGRAM_PROJECT": "my-project"
      }
    }
  }
}
```

**Recommended agent instructions** (add to your CLAUDE.md or system prompt):
```
## Memory (Engram)
At the start of each session, call engram_query with a description of the current task.
After completing significant work or before ending a session, call engram_extract with
a summary of what was built, decided, or changed.
Do not write to MEMORY.md — use Engram tools instead.
```

**Hooks (zero-friction auto-query):**

Add to `~/.claude/settings.json` hooks section to automatically query at session start:
```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [{
          "type": "command",
          "command": "engram last-context --raw"
        }]
      }
    ]
  }
}
```

---

#### Cursor

Add to `.cursor/mcp.json` in your project root (or global `~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "engram": {
      "command": "engram",
      "args": ["mcp-serve"],
      "env": {
        "ENGRAM_PROJECT": "my-project"
      }
    }
  }
}
```

---

#### Windsurf

Add to `~/.codeium/windsurf/mcp_config.json`:

```json
{
  "mcpServers": {
    "engram": {
      "command": "engram",
      "args": ["mcp-serve"]
    }
  }
}
```

---

#### OpenClaw

Add to `openclaw.json`:

```json
{
  "mcpServers": {
    "engram": {
      "command": "engram",
      "args": ["mcp-serve"]
    }
  }
}
```

**Replace** `write to MEMORY.md` instructions with `call engram_extract`.
**Add** `call engram_query at session start` to your agent instructions.

To import existing `MEMORY.md`:
```bash
engram extract my-project MEMORY.md
```

---

### `/docs/api` — REST API Reference

Base URL: `https://api.unbidden.ai` (hosted) or `http://localhost:8000` (self-hosted via `engram serve`)

Authentication: `Authorization: Bearer <api-key>` header on all requests except `/v1/health`.

---

#### `GET /v1/health`

Health check. No auth required.

**Response:**
```json
{ "status": "ok" }
```

---

#### `GET /v1/projects`

List all projects for the authenticated API key.

**Response:**
```json
[
  { "name": "my-project", "node_count": 247, "edge_count": 89 },
  { "name": "other-project", "node_count": 54, "edge_count": 12 }
]
```

---

#### `POST /v1/projects/{project}`

Initialize a new project.

**Response (201):**
```json
{ "project": "my-project", "created": true }
```

---

#### `GET /v1/projects/{project}/stats`

Get memory statistics for a project.

**Response:**
```json
{
  "project": "my-project",
  "node_count": 247,
  "edge_count": 89,
  "last_extracted_at": "2026-03-22T18:00:00Z"
}
```

---

#### `POST /v1/projects/{project}/query`

Retrieve relevant context for a task.

**Request:**
```json
{
  "task": "what auth approach did we decide on?",
  "hops": 3,
  "top_k": 25
}
```

**Response:**
```json
{
  "markdown": "## Nodes (retrieved: 8 of 247)\n\n- **Auth approach** ...",
  "node_count": 8,
  "total_nodes": 247
}
```

---

#### `POST /v1/projects/{project}/extract`

Extract facts from text and store them.

**Request:**
```json
{
  "text": "Today we decided to use PostgreSQL over SQLite because...",
  "source_name": "session-2026-03-22",
  "verify": false
}
```

**Response:**
```json
{
  "nodes_extracted": 12,
  "edges_extracted": 4,
  "nodes_per_1k_chars": 2.3,
  "avg_tags_per_node": 2.1
}
```

---

#### `GET /v1/projects/{project}/export`

Export the full project memory as JSON.

**Response:** Full memory export as JSON.

---

### `/docs/cli` — CLI Reference (`engram`)

Install: `pip install engram`
All commands: `engram --help`

---

#### Core workflow

| Command | Description |
|---------|-------------|
| `engram init <project>` | Initialize a new project |
| `engram extract <project> <file>` | Extract facts from a transcript or document |
| `engram query <project> "<task>"` | Retrieve relevant context |
| `engram show <project>` | Browse stored nodes |
| `engram export <project>` | Export memory to JSON/markdown |

---

#### `engram extract` — options

```bash
engram extract my-project transcript.txt [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--verify` | Second LLM pass to catch missed facts |
| `--lessons` | Targeted pass for failed approaches and rejected alternatives |
| `--decisions` | Targeted pass for decision nodes and rationale |
| `--questions` | Targeted pass for open questions and unresolved items |
| `--constraints` | Targeted pass for hard constraints and requirements |
| `--synthesize` | Run synthesis pass after extraction (creates cross-cutting summary nodes) |
| `--chunk-size N` | Characters per chunk (for large transcripts) |
| `--timeout N` | LLM timeout in seconds |

---

#### `engram query` — options

```bash
engram query my-project "what auth approach?" [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--hops N` | Retrieval depth (default: 3) |
| `--top-k N` | Max nodes to return |
| `--confidence N` | Min confidence threshold (e.g. 0.6) |
| `--token-budget N` | Max tokens in output |
| `--enable <strategy>` | Enable a retrieval strategy |
| `--disable <strategy>` | Disable a retrieval strategy |
| `--stats` | Show retrieval stats (for benchmarking) |

---

#### Maintenance commands

| Command | Description |
|---------|-------------|
| `engram synthesize <project>` | Create cross-cutting summary nodes from stored memory |
| `engram reconcile <project>` | Merge near-duplicate nodes |
| `engram prune <project>` | Remove low-confidence or stale nodes (dry-run by default) |
| `engram feedback <project>` | Rate nodes to improve retrieval quality |

---

#### Import & onboarding

| Command | Description |
|---------|-------------|
| `engram onboard <project>` | Import recent Claude Code sessions automatically |
| `engram import-claude-sessions <project>` | Import specific session files |
| `engram extract-replay <project> <file>` | Replay a transcript turn-by-turn (for benchmarking) |

---

#### Server commands

| Command | Description |
|---------|-------------|
| `engram mcp-serve` | Start MCP server (stdio transport, default) |
| `engram serve` | Start REST API server |
| `engram hook-init <project>` | Install Claude Code hooks in the current project |
| `engram last-context` | Print last retrieved context (used by hooks) |
| `engram doctor` | Diagnose installation and config issues |
| `engram pause` / `engram resume` | Pause/resume background extraction |

---

### `/docs/config` — Configuration Reference

Engram looks for `config.yaml` in `~/.config/engram/config.yaml` (or path passed via `--config`).

```yaml
# LLM provider for extraction
llm:
  provider: google          # google | openai | anthropic | ollama
  model: gemini-2.0-flash   # model name
  api_key: ""               # or set GOOGLE_API_KEY env var

# Storage
storage:
  path: ~/.engram           # where the memory store is located

# Retrieval defaults
defaults:
  hops: 3
  top_k: 25
  confidence: 0.5
  token_budget: 2000

# Retrieval strategies (all enabled by default)
strategies:
  relevance_scoring: true
  superseded_pruning: true
  recency_decay: true

# Remote API mode (optional — omit for local-only)
remote:
  url: https://api.unbidden.ai
  api_key: ""               # or set ENGRAM_API_KEY env var
```

---

---

### `/docs/faq` — Frequently Asked Questions

Also rendered as a collapsible section on the `/engram` product page.

---

**Q: How is this different from RAG?**
RAG retrieves from a document corpus — you put documents in, it fetches chunks when asked. Engram builds memory from *conversations* — it watches what you're working on, extracts decisions and facts as you go, and surfaces them in future sessions automatically. It's session memory, not document search.

---

**Q: How is this different from the built-in memory in Claude Code or ChatGPT?**
Built-in memory tools summarize old context or drop it when the context window fills. This means architectural decisions from early in a project eventually disappear. Engram extracts structured facts — it doesn't summarize or discard. A decision from week 1 retrieves just as accurately in week 12 as it did on day 2.

---

**Q: Does it work with local models (Ollama, LM Studio)?**
Yes. Engram works with any OpenAI-compatible endpoint. Local models can be used for both extraction and your downstream AI assistant. Gemini Flash is the recommended extraction model for accuracy; local extraction is supported and documented.

---

**Q: Does my conversation data leave my machine?**
**Local mode:** Nothing leaves your machine. The MCP server runs locally, extraction calls your configured endpoint, and the memory store is a local SQLite file.

**Hosted API mode:** Session text is sent to the Engram API for extraction and stored in your project's database on Unbidden's servers. See the privacy policy for full details.

---

**Q: What's the difference between local mode and hosted mode?**
Local mode runs entirely on your machine — no account, no API key, no network calls (except to your chosen LLM). Hosted mode stores your project memory on Unbidden's servers, enabling sync across machines, the team tier, and the web dashboard. You can switch between modes; your data is exportable at any time.

---

**Q: What AI editors does it support?**
Claude Code, Cursor, Windsurf, Continue.dev, OpenClaw, and any editor with MCP support. The REST API works with anything that can make HTTP calls.

---

**Q: What languages and frameworks does it work with?**
All of them. Engram stores decisions and context from your *conversations* — it doesn't read your code directly. If you're discussing Python, Rust, or SQL schema design, the facts extracted are language-agnostic.

---

**Q: How does the free tier work?**
Free tier gives you 1 project and 500 stored facts — enough for a real project. No credit card required. Rate-limited to 10 API calls/min. Upgrade when you need more capacity.

---

**Q: Can multiple developers share a project?**
Yes, on the Team tier. Up to 10 users can query and contribute to the same project memory. All team members see the same accumulated memory, and extractions from any team member's sessions are merged into the shared project.

---

**Q: What happens to old facts when I change direction?**
When a new decision supersedes an old one, Engram marks the old fact as retired. It stops appearing in retrieval. You see the current state of the project — not a history of every decision including ones you've reversed.

---

**Q: How accurate is the memory extraction?**
95% recall on our 23-question benchmark across three synthetic projects (API design, auth system, data pipeline). Full benchmark methodology and results are in the repo under `benchmarks/`. Accuracy is higher with `--verify` flag enabled.

---

**Q: Can I import my existing MEMORY.md or conversation history?**
Yes. `engram extract` can process any text file — including `MEMORY.md`, exported chat logs, spec docs, or meeting notes. Run it once to seed your project with existing context before switching to automated extraction.

For Claude Code users: `engram onboard` or `engram import-claude-sessions` will automatically find and import your recent Claude Code session transcripts.

---

**Q: What does it cost to run in local mode?**
The extraction model (Gemini Flash) costs ~$0.15/1M tokens. A typical 50-turn session generates ~5,000 tokens of transcript. Extraction cost per session: ~$0.001. For a 5-person team doing 440 sessions/month: under $0.50/month in LLM extraction costs.

---

**Q: Is there a self-hosted option for the API server?**
Yes. `engram serve` starts the full REST API server locally or on your own infrastructure. The server code is open source. Self-hosting gives you full data control and no rate limits.

---

**Q: How do I cancel or export my data?**
You can cancel at any time from the billing page — no lock-in. Your memory is always exportable via `engram export` or `GET /v1/projects/{project}/export`. Data is returned as portable JSON.

---

**Q: What extraction model does it use, and can I change it?**
Default: Gemini Flash (`gemini-2.0-flash`) — fast, cheap, and accurate for fact extraction. You can switch to any supported provider (OpenAI, Anthropic, Ollama) in `config.yaml` under the `llm` key. Local-only setups can use Ollama with a supported model.

---

**Q: Will it slow down my AI editor?**
No. The MCP server runs as a background process. `engram_query` typically returns in under 500ms for projects with thousands of nodes. Extraction (`engram_extract`) runs asynchronously at session end — it doesn't block your workflow.

---

## Design Direction

- Dark background, monospace accent font
- No carousels, no animations, no gradients
- Code snippet in the hero (a single `engram mcp-serve` or MCP config block)
- One accent color (not blue)
- Mobile-responsive, desktop-first
- Docs: rendered from in-repo markdown — no docs platform until needed

---

## Open Items

- [ ] Finalize install method (pip? npm? binary?)
- [ ] Confirm `ENGRAM_API_KEY` is the correct env var name for hosted mode
- [ ] Write Quickstart page from `GETTING_STARTED.md`
- [ ] Record 30-second demo GIF for product page hero
- [ ] Decide: hosted API domain — `api.unbidden.ai` or `unbidden.ai/api`?
