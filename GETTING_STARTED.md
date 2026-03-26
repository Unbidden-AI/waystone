# Getting Started with Context Broker

Context Broker extracts facts from your Claude Code conversations into a knowledge graph, then injects relevant context into every future prompt — so Claude always knows your project's decisions, constraints, and history.

**Two setup paths — pick one:**

| | MCP Server (recommended) | Hooks (manual) |
|---|---|---|
| **Setup** | One JSON snippet in Claude Code config | Run `hooks/install.py` |
| **Extraction** | `engram onboard` or call `context_broker_extract` from Claude | `engram extract` after each session |
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
engram --help
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
claude mcp add context-broker engram mcp-serve
```

**Option 2 — Manual config:**

Edit `~/.claude/claude_desktop_config.json` (create it if it doesn't exist) and add:

```json
{
  "mcpServers": {
    "context-broker": {
      "command": "engram",
      "args": ["mcp-serve"]
    }
  }
}
```

A ready-to-paste snippet is at `claude_mcp_config.json` in this repo.

**Restart Claude Code.** You should see `context-broker` appear in the MCP server list.

> **Skip ahead:** Once the MCP server is running, jump to [Step 3A Quick Start](#step-3a-quick-start-engram-onboard) to import your existing sessions with one command.

---

## Step 3A Quick Start: `engram onboard`

If you've already used Claude Code, import your recent sessions in one step:

```bash
engram onboard myproject
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
engram hook-init myproject
```

Or manually:

```bash
echo 'myproject' > /path/to/your/project/.context-broker
```

Replace `myproject` with any short name (e.g. `ContextBroker`, `MyApp`). This name is how your graph is stored and identified.

> The hook walks up the directory tree looking for this file, so it works from any subdirectory of your project.

---

## Step 4B: Seed the graph before your first session (new projects)

If the project has no prior sessions to import, the graph starts empty and the first few turns get no context injection. You can fix this in a few minutes.

**Write a short project brief.** Create a file — `project_brief.md` is a good name — with 1–3 paragraphs covering:

- What the project does (one sentence is enough)
- The core tech stack and key architectural choices
- Any hard constraints the model should always respect (e.g. "no vendor lock-in", "must run offline", "Python 3.11+")

```markdown
# MyApp

MyApp is a mobile-first expense tracker that syncs across devices via a self-hosted
PostgreSQL backend and a React Native frontend. All amounts are stored in cents to
avoid floating-point rounding.

Key constraints: offline-first (all local writes must succeed before sync),
no third-party auth providers (we roll our own JWT), iOS 16+ minimum.

Tech stack: React Native 0.73, Expo, PostgreSQL 15, FastAPI, SQLAlchemy 2.0.
```

**Extract it:**

```bash
engram extract myproject project_brief.md
```

You'll get 20–50 nodes covering the decisions and constraints you wrote down. Every session from that point forward will have those facts available.

> **Tip:** Design documents, ADRs, a README, or existing specifications work just as well — `engram extract` handles any markdown file, not just conversation transcripts.

---

## Step 4C: Set a project brief in the orchestrator static prompt (orchestrator mode only)

If you're using `engram orchestrate` instead of the hooks/MCP path, add a 1–2 sentence project brief to the `static` field in your config. This gives the model orientation before it sees any retrieved graph context — particularly important on the first turn of a session when the graph may return nothing relevant.

Open `~/.context-broker/config.yaml` and find the `orchestrator.system_prompt` section:

```yaml
orchestrator:
  system_prompt:
    static_files:
      - CLAUDE.md        # optional: carry project instructions into every session
    static: |
      You are a senior engineer on MyApp — a mobile-first expense tracker with an
      offline-first architecture, self-hosted PostgreSQL backend, and React Native
      frontend. All monetary values are stored in cents.

      ## Non-negotiables
      - Run tests before declaring anything done
      - Read files before modifying them
      - Store amounts as integers (cents), never floats
```

**What belongs here vs. the graph:**

| Belongs in `static` | Belongs in the graph |
|---------------------|---------------------|
| Project name + 1-sentence description | Architecture details, data flow |
| Universal behavioral rules ("always run tests") | Specific decisions made in past sessions |
| Hard constraints that apply to every turn | Tech choices explained in context |

The graph fills in the rich knowledge; `static` just gives the model a hook to hang that knowledge on.

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
engram extract myproject ~/.context-broker/transcripts/myproject/latest.md
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
engram query myproject "how does the authentication work" --stats
```

Then check what the hook would inject for that query:

```bash
engram last-context
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
Session ends → transcript auto-saved → run engram extract → next session has context
```

After a few sessions, accumulate transcripts and re-extract to grow the graph:

```bash
engram extract myproject ~/.context-broker/transcripts/myproject/20260309_*.md
```

Or extract each new session as it happens:

```bash
engram extract myproject ~/.context-broker/transcripts/myproject/latest.md
```

---

## Useful commands

```bash
# One-click import from recent Claude Code sessions
engram onboard myproject

# Import specific .jsonl session files
engram import-claude-sessions myproject ~/.claude/projects/abc123/session.jsonl

# List discoverable sessions without importing
engram import-claude-sessions myproject --list-only

# Start the MCP server (for Claude Code integration)
engram mcp-serve                  # stdio (default, for Claude Code)
engram mcp-serve --transport sse  # HTTP SSE (for other clients)

# See what's in your graph
engram show myproject

# Query manually (useful for testing)
engram query myproject "describe the data pipeline" --stats

# See exactly what was injected into the last prompt
engram last-context

# Export the full graph as markdown
engram export myproject

# Initialize a fresh empty graph
engram init myproject
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

In any project directory where you ran `engram hook-init` (or manually created `.context-broker`):

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
→ Run `engram onboard myproject` or `engram extract myproject <transcript>` to build the graph first.

**MCP server not showing up in Claude Code**
→ Confirm `engram mcp-serve` runs without error: `engram mcp-serve --help`
→ Check that `~/.claude/claude_desktop_config.json` has the correct JSON syntax.
→ Restart Claude Code after editing the config.

**`engram onboard` finds no sessions**
→ Sessions appear in `~/.claude/projects/` after using Claude Code at least once.
→ Check: `ls ~/.claude/projects/`

**Hook not firing / no status line**
→ Make sure you restarted Claude Code after running `hooks/install.py`.
→ Check `~/.claude/settings.json` for the hook entries.

**"No relevant context found" on every query**
→ Try `engram show myproject` to confirm nodes exist, then try a broader query term.
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
