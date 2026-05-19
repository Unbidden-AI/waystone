# Waystone

Persistent cross-session memory for LLM agents. A knowledge graph that stores decisions, constraints, and context across coding sessions — so your agent starts informed, not blank.

## Install

```bash
pip install waystone
```

Requires Python 3.11+. An LLM API key is needed for extraction (Gemini Flash recommended — fast and cheap).

## Quick start

### Option 1: MCP server (recommended)

Add to your editor's MCP config:

```json
{
  "mcpServers": {
    "waystone": {
      "command": "waystone",
      "args": ["mcp-serve"],
      "env": { "WAYSTONE_PROJECT": "my-project" }
    }
  }
}
```

Restart your editor. `waystone_query`, `waystone_extract`, and `waystone_stats` appear as tools. Your agent pulls context when it needs it.

### Option 2: Claude Code hooks (zero manual calls)

Add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [{ "hooks": [{ "type": "command", "command": "waystone hook query my-project" }] }],
    "Stop": [{ "hooks": [{ "type": "command", "command": "waystone hook extract my-project" }] }]
  }
}
```

Context is injected automatically before every prompt. Facts are extracted automatically when Claude finishes.

## Supported clients

Claude Code · Cursor · Windsurf · Continue.dev · Cline · Zed · OpenClaw · Hermes

Full per-client setup: [unbidden.ai/docs/mcp-server/](https://unbidden.ai/docs/mcp-server/)

## Key CLI commands

```bash
waystone init <project>              # create a project
waystone extract <project> <file>    # extract facts from a transcript
waystone query <project> "<query>"   # retrieve relevant context
waystone onboard <project>           # import existing session history
waystone show <project>              # view project stats
```

## How it works

**At session end** — `waystone extract` reads the conversation transcript and pulls structured facts: decisions, constraints, implementations, lessons learned, open questions. These are stored as nodes in a local SQLite knowledge graph (`~/.waystone/`). Superseded facts are retired automatically — if a decision changes, the graph reflects the current state.

**At session start** — `waystone_query` (or a hook) runs BFS traversal from the most relevant entry points and surfaces the top 10–25 facts. Only what's relevant to the current context, not everything ever stored.

## Benchmarks

Tested on 23 questions across 3 domains (API design, auth systems, data pipelines):

| | Recall |
|---|---|
| Baseline | 82% |
| With retrieval improvements | **89%** |

Token usage vs. naive MEMORY.md on a mature project: typically 60–80% fewer context tokens per session (exact savings depend on project age and query specificity).

Full results: [BENCHMARK_RESULTS.md](./BENCHMARK_RESULTS.md)

## Hosted API

The default store is local SQLite — no cloud dependency, no infra to manage. For cross-machine sync and team access, a hosted API is available:

- **Pro** ($20/mo) — unlimited projects, hosted API, 1 user
- **Team** ($80/mo) — unlimited projects, hosted API, up to 10 users

[unbidden.ai/pricing/](https://unbidden.ai/pricing/)

## Docs

- [Quickstart](https://unbidden.ai/docs/)
- [MCP Server setup](https://unbidden.ai/docs/mcp-server/)
- [CLI Reference](https://unbidden.ai/docs/cli/)
- [REST API](https://unbidden.ai/docs/rest-api/)

## License

MIT — see [LICENSE](./LICENSE)
