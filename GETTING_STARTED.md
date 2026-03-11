# Getting Started with Context Broker

Context Broker extracts facts from your Claude Code conversations into a knowledge graph, then injects relevant context into every future prompt — so Claude always knows your project's decisions, constraints, and history.

**Two setup paths — pick one:**

| | MCP Server (recommended) | Hooks (manual) |
|---|---|---|
| **Setup** | One JSON snippet in Claude Code config | Run `hooks/install.py` |
| **Extraction** | `ctx onboard` or call `context_broker_extract` from Claude | `ctx extract` after each session |
| **Context injection** | Claude calls `context_broker_query` automatically | Hook injects on every `UserPromptSubmit` |
| **Best for** | New users, quick start | Power users, background auto-extraction |

---

## Prerequisites

- Python 3.11+
- Claude Code CLI installed
- An LLM API key for extraction (Gemini recommended — see Step 2)

---

## Step 1: Install the package

From the Context Broker repo directory:

```bash
cd /Users/justinwalton/Apps/ContextBroker
pip install -e ".[dev]"
```

Verify it worked:

```bash
ctx --help
```

---

## Step 2: Configure your LLM API key

Context Broker needs an LLM to extract facts from transcripts. Retrieval (the hot path on every prompt) is fully local SQLite — no LLM calls at query time.

A config file has been created at `~/.context-broker/config.yaml`. Open it and replace `YOUR_GEMINI_API_KEY` with your key:

```bash
open ~/.context-broker/config.yaml
```

The file is pre-configured for Gemini (recommended). To use OpenAI instead, uncomment the OpenAI section and comment out the Gemini section.

> **Note:** If you prefer not to put your key in the file, set `OPENAI_API_KEY` in your shell environment instead and remove the `api_key` line. The Gemini OpenAI-compatible endpoint also reads `OPENAI_API_KEY`.

---

## Step 3A: Set up the MCP server (recommended)

Add Context Broker as an MCP server so Claude Code can call it directly as a tool.

**Option 1 — Claude Code CLI:**
```bash
claude mcp add context-broker ctx -- mcp-serve
```

**Option 2 — Manual config:**

Edit `~/.claude/claude_desktop_config.json` (create it if it doesn't exist) and add:

```json
{
  "mcpServers": {
    "context-broker": {
      "command": "ctx",
      "args": ["mcp-serve"]
    }
  }
}
```

A ready-to-paste snippet is at `claude_mcp_config.json` in this repo.

**Restart Claude Code.** You should see `context-broker` appear in the MCP server list.

> **Skip ahead:** Once the MCP server is running, jump to [Step 3A Quick Start](#step-3a-quick-start-ctx-onboard) to import your existing sessions with one command.

---

## Step 3A Quick Start: `ctx onboard`

If you've already used Claude Code, import your recent sessions in one step:

```bash
ctx onboard myproject
```

You'll see a menu of your recent Claude Code sessions:

```
Found 12 recent Claude Code session(s):

  [ 1] 2026-03-10 14:22   42KB  -Users-you-Apps-MyApp/abc123.jsonl
  [ 2] 2026-03-09 11:08   18KB  -Users-you-Apps-MyApp/def456.jsonl
  ...

Import which sessions? (e.g. 1,3-5 or 'all' or Enter to import all): all
```

After import, it runs a sample query so you immediately see your own knowledge reflected back. Then every new Claude Code conversation will have that context available.

---

## Step 3B: Install the Claude Code hooks (alternative)

Use this path if you prefer automatic background extraction after every session.

```bash
python /Users/justinwalton/Apps/ContextBroker/hooks/install.py
```

This adds three things to `~/.claude/settings.json`:

| Hook | What it does |
|------|-------------|
| `UserPromptSubmit` | Queries the graph and injects relevant context into every prompt |
| `Stop` | Records each session as a transcript to `~/.context-broker/transcripts/<project>/` |
| Status line | Shows retrieval metrics (nodes retrieved, tokens injected, latency) |

**Restart Claude Code** after running the installer.

---

## Step 4: Mark your project directory

In the root of the project you want to track:

```bash
ctx hook-init myproject
```

Or manually:

```bash
echo 'myproject' > /path/to/your/project/.context-broker
```

Replace `myproject` with any short name (e.g. `ContextBroker`, `MyApp`). This name is how your graph is stored and identified.

> The hook walks up the directory tree looking for this file, so it works from any subdirectory of your project.

---

## Step 5: Have a Claude Code session

Just work normally in your project. The `Stop` hook automatically saves each session as a markdown transcript to:

```
~/.context-broker/transcripts/<project>/YYYYMMDD_HHMMSS_<id>.md
~/.context-broker/transcripts/<project>/latest.md  ← always points to most recent
```

No action needed — it happens automatically at the end of every session.

---

## Step 6: Extract your first transcript

After a session (or using any existing transcript):

```bash
ctx extract myproject ~/.context-broker/transcripts/myproject/latest.md
```

You'll see output like:
```
Extracted 47 nodes, 23 edges from latest.md  [density=3.2/1kc  avg_tags=7.1  edge/node=0.49]
```

The graph is now stored at `~/.context-broker/projects/myproject/context.db`.

> **Have an existing transcript?** You can also extract from any exported Claude conversation (File → Export in Claude.ai, or a manually written markdown file). The format should use `**Name**: message` speaker labels, but the extractor handles most common formats.

---

## Step 7: Verify retrieval is working

```bash
ctx query myproject "how does the authentication work" --stats
```

Then check what the hook would inject for that query:

```bash
ctx last-context
```

---

## Step 8: Start a new Claude Code session

With the graph built, open Claude Code in your project directory. Every prompt you submit will automatically have relevant context injected from the graph.

The status line shows live metrics:
```
Claude Sonnet 4.6 │ ctx [████░░░░] 12% │ $0.0041 │ CB(myproject): 8/47 nodes ~240tok [18ms]
```

- `8/47 nodes` — 8 relevant nodes retrieved out of 47 total
- `~240tok` — estimated tokens injected
- `[18ms]` — retrieval latency (local SQLite, always fast)

---

## Ongoing workflow

```
Session ends → transcript auto-saved → run ctx extract → next session has context
```

After a few sessions, accumulate transcripts and re-extract to grow the graph:

```bash
ctx extract myproject ~/.context-broker/transcripts/myproject/20260309_*.md
```

Or extract each new session as it happens:

```bash
ctx extract myproject ~/.context-broker/transcripts/myproject/latest.md
```

---

## Useful commands

```bash
# One-click import from recent Claude Code sessions
ctx onboard myproject

# Import specific .jsonl session files
ctx import-claude-sessions myproject ~/.claude/projects/abc123/session.jsonl

# List discoverable sessions without importing
ctx import-claude-sessions myproject --list-only

# Start the MCP server (for Claude Code integration)
ctx mcp-serve                  # stdio (default, for Claude Code)
ctx mcp-serve --transport sse  # HTTP SSE (for other clients)

# See what's in your graph
ctx show myproject

# Query manually (useful for testing)
ctx query myproject "describe the data pipeline" --stats

# See exactly what was injected into the last prompt
ctx last-context

# Export the full graph as markdown
ctx export myproject

# Initialize a fresh empty graph
ctx init myproject
```

---

## Uninstalling / Rolling Back

If Context Broker doesn't work as expected and you want to remove it completely:

### Step 1: Restore your Claude Code settings

The installer made a timestamped backup before modifying `~/.claude/settings.json`:

```bash
ls ~/.claude/settings.json.bak.*
```

Pick the most recent backup and restore it:

```bash
cp ~/.claude/settings.json.bak.YYYYMMDD_HHMMSS ~/.claude/settings.json
```

If you prefer to edit manually instead, open `~/.claude/settings.json` and remove:
- The `UserPromptSubmit` entry containing `context_broker_submit`
- The `Stop` entry containing `context_broker_stop`
- The `statusLine` entry (or restore it to your previous value)

**Restart Claude Code** after editing settings.

### Step 2: Remove the project marker file

In any project directory where you ran `ctx hook-init` (or manually created `.context-broker`):

```bash
rm /path/to/your/project/.context-broker
```

### Step 3: Remove Context Broker data (optional)

This deletes all graphs, transcripts, and state:

```bash
rm -rf ~/.context-broker/
```

To remove only a specific project's graph:

```bash
rm -rf ~/.context-broker/projects/myproject/
rm -rf ~/.context-broker/transcripts/myproject/
```

### Step 4: Uninstall the package

```bash
pip uninstall context-broker
```

---

## Troubleshooting

**"No graph found" in the status line**
→ Run `ctx onboard myproject` or `ctx extract myproject <transcript>` to build the graph first.

**MCP server not showing up in Claude Code**
→ Confirm `ctx mcp-serve` runs without error: `ctx mcp-serve --help`
→ Check that `~/.claude/claude_desktop_config.json` has the correct JSON syntax.
→ Restart Claude Code after editing the config.

**`ctx onboard` finds no sessions**
→ Sessions appear in `~/.claude/projects/` after using Claude Code at least once.
→ Check: `ls ~/.claude/projects/`

**Hook not firing / no status line**
→ Make sure you restarted Claude Code after running `hooks/install.py`.
→ Check `~/.claude/settings.json` for the hook entries.

**"No relevant context found" on every query**
→ Try `ctx show myproject` to confirm nodes exist, then try a broader query term.
→ Check that the `.context-broker` file in your project directory contains the correct project name.

**Extraction fails with auth error**
→ Verify your `api_key` in `~/.context-broker/config.yaml` or confirm `OPENAI_API_KEY` is set in your shell.

**Large transcript extraction fails or times out**
→ Add `--timeout 600` to extend the LLM timeout.
→ Add `--chunk-size 30000` to split the file into smaller pieces.

**Want to see the raw transcript the hook saved?**
```bash
cat ~/.context-broker/transcripts/myproject/latest.md | head -50
```
