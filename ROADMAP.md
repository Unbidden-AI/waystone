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
- The current setup (manual `ctx extract`, CLI hooks) has too much friction for mainstream adoption
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
3. Add `ctx mcp-serve` CLI command (launches the MCP server on stdio or SSE)
4. Add `mcp_server` entry point to `pyproject.toml`
5. Write `claude_mcp_config.json` snippet users can paste into their Claude Code settings
6. Update `GETTING_STARTED.md` with MCP setup instructions (replaces hook-based setup)

### User experience (after Phase 1)

```json
// ~/.claude/claude_desktop_config.json
{
  "mcpServers": {
    "context-broker": {
      "command": "ctx",
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

### Implementation: `ctx onboard` command

```bash
ctx onboard [project]
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

Provide a `ctx import-claude-sessions` subcommand that reads `.jsonl` files and converts them to the markdown transcript format Context Broker expects.

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

Run `ctx extract` on each selected transcript with `--verify`. Show a progress bar. Use `--chunk-size 30000` automatically for transcripts over 40k chars.

**Step 4 — Show value immediately**

After import, run `ctx query <project> "what are the key architectural decisions?"` and print the result. The user sees their own knowledge reflected back at them in under 2 minutes.

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

## Phase 4: Distribution & Growth (ongoing)

### Claude Code Marketplace / MCP Registry

Submit to Anthropic's MCP server registry once it exists. This is the single highest-leverage distribution channel — zero CAC for the target audience.

### Cursor / Windsurf plugins

Both support MCP. The same `ctx mcp-serve` command works. Publish to each tool's plugin marketplace with a one-paragraph description.

### Team network effect

The most compelling long-term use case: a software team where multiple developers contribute to a shared knowledge graph. Every design session, every architecture decision, every "why did we do it this way" is captured. New team members onboard by running `ctx query <project> "give me an overview of the architecture"`.

Lean into this in marketing: **"Shared memory for your entire engineering team."**

---

## Competitive Moat

| Moat | Strength | Notes |
|------|----------|-------|
| Structured graph (not embeddings) | High | BFS traversal + typed edges + supersedes logic is qualitatively different from vector similarity |
| Team network effects | High | Shared graph grows more valuable with more contributors |
| Native MCP integration | Medium | First-mover advantage; other tools will follow |
| Switching costs (accumulated graph) | Medium | A 6-month-old graph with 5,000 nodes is hard to abandon |
| Integration breadth | Low | Cursor, Windsurf, Zed integrations require maintenance; anyone can copy |

The moat is **thin in year 1, strong in year 2+** as accumulated graphs and team adoption create lock-in.

---

## Open Questions

- **Extraction cost economics**: At $0/mo free tier, we absorb Gemini extraction costs. Need to model this against free-tier conversion rates.
- **Privacy / data residency**: Enterprise customers will ask about on-prem. Phase 2 should be designed for self-hosting from day one.
- **Graph ownership**: If a user cancels, they should be able to export their full graph (SQLite dump). Make this a clear commitment in ToS.
- **Benchmark the MCP path**: Does Claude Code use `context_broker_query` on every prompt, or only when it decides context is needed? This matters a lot for recall vs. latency tradeoffs.
