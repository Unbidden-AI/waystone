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

### Hermes Agent (native memory provider)

Beyond MCP, Waystone ships a first-class [Hermes Agent](https://github.com/NousResearch/hermes-agent) **memory provider** (`hermes_plugin/`). It plugs the knowledge graph into Hermes as a Tier-3 memory backend:

- **`prefetch()`** injects relevant graph context before each LLM call (in-process — the embedding model loads once and stays warm, so per-turn retrieval stays sub-second).
- **`waystone_query` / `waystone_recall`** tools let the agent search the graph on demand.
- **`on_session_end`** extraction grows the graph in the background. Fully local — no data leaves the machine.

Install:

```bash
cp -r hermes_plugin/ /path/to/hermes-agent/plugins/memory/waystone/
pip install waystone
hermes memory setup        # pick "waystone", set the project
```

## Key CLI commands

```bash
waystone init <project>              # create a project
waystone extract <project> <file>    # extract facts from a transcript
waystone query <project> "<query>"   # retrieve relevant context
waystone onboard <project>           # import existing session history
waystone show <project>              # view project stats
waystone story <project>             # replay the project's session-summary timeline
waystone catchup-summarize <project> # back-fill the story from saved transcripts
waystone doctor                      # health check (config, LLM, MCP, sqlite-vec)
```

## How it works

**At session end** — `waystone extract` reads the conversation transcript and pulls structured facts: decisions, constraints, implementations, lessons learned, open questions. These are stored as nodes in a local SQLite knowledge graph (`~/.waystone/`). Superseded facts are retired automatically — if a decision changes, the graph reflects the current state.

**At session start** — `waystone_query` (or a hook) runs BFS traversal from the most relevant entry points and surfaces the top 10–25 facts. Only what's relevant to the current context, not everything ever stored.

**Session summaries (the project's story)** — alongside atomic facts, Waystone keeps a rolling, high-altitude narrative of each work session (goal · arc · current state · next) that point-facts miss. A passive background worker updates it every few turns; each summary supersedes the prior one, but the full timeline is kept. Every new prompt is led with a "Where we are" block so a fresh session opens already oriented, and `waystone story <project>` replays the whole history chapter by chapter. Use `waystone catchup-summarize <project>` once to back-fill the story from a project's existing transcripts. The rolling call is bounded (~3k tokens in / ≤512 out regardless of session length), so it stays cheap.

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
- [Hermes Agent integration](https://unbidden.ai/docs/integrations/hermes/)
- [Advanced Configuration](./ADVANCED.md) — API embeddings (no PyTorch), strategy tuning, attachment extraction

## License

MIT — see [LICENSE](./LICENSE)
