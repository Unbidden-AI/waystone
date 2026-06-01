# Getting Started with Waystone

Waystone extracts facts from your Claude Code conversations into a knowledge graph, then injects relevant context into every future prompt — so Claude always knows your project's decisions, constraints, and history.

> **Waystone requires an external LLM API key for extraction.** It uses Gemini, OpenAI, Anthropic, or a local model (LM Studio / Ollama) to read your transcripts and pull out facts. Retrieval — the per-prompt context injection — is 100% local SQLite with no API calls. You're only paying for extraction.

---

## Prerequisites

- Python 3.11+
- Claude Code CLI (or another supported tool — see Step 2)
- **An API key** from a supported LLM provider:
  - [Gemini](https://aistudio.google.com/app/apikey) (recommended — fast, affordable, best recall)
  - [OpenAI](https://platform.openai.com/api-keys)
  - [Anthropic](https://console.anthropic.com/settings/keys)
  - Local model via [LM Studio](https://lmstudio.ai) or Ollama (no key needed)

---

## Step 1: Install and configure

```bash
pip install waystone
```

Then run the setup wizard **from your project directory** (not your home directory):

```bash
cd /path/to/your/project
waystone configure
```

The wizard walks you through three steps:
1. **LLM provider** — choose Gemini, OpenAI, Anthropic, Local, or Custom; enter your API key
2. **Claude Code integration** — MCP server (recommended) or hooks
3. **Project marker** — marks the current directory so Waystone knows which graph to use

Verify everything is working:

```bash
waystone doctor
```

> **Important:** Run `waystone configure` from inside your project directory, not your home directory. Waystone stores its data in `~/.waystone/` — running configure there causes a conflict with the project marker file. If you see a "Permission denied" or "already a directory" error, `cd` into your project first and re-run.

---

## Step 2: Configure your LLM API key

`waystone configure` handles this for you. If you need to update it later, edit `~/.waystone/config.yaml`:

```bash
# macOS / Linux
open ~/.waystone/config.yaml

# Windows
notepad %USERPROFILE%\.waystone\config.yaml
```

> **Tip:** If you prefer environment variables over a config file, set `GEMINI_API_KEY` (or `OPENAI_API_KEY`) in your shell and leave `api_key` out of the config.

---

## Step 3A: Set up the MCP server (recommended)

Add Waystone as an MCP server so Claude Code can call it directly as a tool.

**Option 1 — Claude Code CLI:**
```bash
claude mcp add waystone waystone mcp-serve
```

**Option 2 — Manual config:**

Edit `~/.claude/claude_desktop_config.json` (create it if it doesn't exist) and add:

```json
{
  "mcpServers": {
    "waystone": {
      "command": "waystone",
      "args": ["mcp-serve"]
    }
  }
}
```

A ready-to-paste snippet is at `claude_mcp_config.json` in this repo.

**Restart Claude Code.** You should see `waystone` appear in the MCP server list.

> **Skip ahead:** Once the MCP server is running, jump to [Step 3A Quick Start](#step-3a-quick-start-waystone-onboard) to import your existing sessions with one command.

---

## Step 3A Quick Start: `waystone onboard`

If you've already used Claude Code, import your recent sessions in one step:

```bash
waystone onboard myproject
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
python hooks/install.py
```

This makes four changes:

| What | Where |
|------|-------|
| `UserPromptSubmit` hook | `~/.claude/settings.json` — queries the graph and injects relevant context into every prompt |
| `Stop` hook | `~/.claude/settings.json` — records each session transcript to `~/.waystone/transcripts/<project>/` |
| Status line | `~/.claude/settings.json` — shows retrieval metrics (nodes retrieved, tokens injected, latency) |
| Waystone usage guide | `~/.claude/CLAUDE.md` — teaches Claude Code how to use Waystone and interpret injected context |

**Restart Claude Code** after running the installer.

---

## Step 4: Mark your project directory

In the root of the project you want to track:

```bash
waystone hook-init myproject
```

Or manually:

```bash
echo 'myproject' > /path/to/your/project/.waystone
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
waystone extract myproject project_brief.md
```

You'll get 20–50 nodes covering the decisions and constraints you wrote down. Every session from that point forward will have those facts available.

> **Tip:** Design documents, ADRs, a README, or existing specifications work just as well — `waystone extract` handles any markdown file, not just conversation transcripts.

---

## Step 4C: Set a project brief in the orchestrator static prompt (orchestrator mode only)

If you're using `waystone orchestrate` instead of the hooks/MCP path, add a 1–2 sentence project brief to the `static` field in your config. This gives the model orientation before it sees any retrieved graph context — particularly important on the first turn of a session when the graph may return nothing relevant.

Open `~/.waystone/config.yaml` and find the `orchestrator.system_prompt` section:

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
~/.waystone/transcripts/<project>/YYYYMMDD_HHMMSS_<id>.md
~/.waystone/transcripts/<project>/latest.md  ← always points to most recent
```

No action needed — it happens automatically at the end of every session.

---

## Step 6: Extract your first transcript

After a session (or using any existing transcript):

```bash
waystone extract myproject ~/.waystone/transcripts/myproject/latest.md
```

You'll see output like:
```
Extracted 47 nodes, 23 edges from latest.md  [density=3.2/1kc  avg_tags=7.1  edge/node=0.49]
```

The graph is now stored at `~/.waystone/projects/myproject/context.db`.

> **Have an existing transcript?** You can also extract from any exported Claude conversation (File → Export in Claude.ai, or a manually written markdown file). The format should use `**Name**: message` speaker labels, but the extractor handles most common formats.

---

## Step 7: Verify retrieval is working

```bash
waystone query myproject "how does the authentication work" --stats
```

Then check what the hook would inject for that query:

```bash
waystone last-context
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
Session ends → transcript auto-saved → run waystone extract → next session has context
```

After a few sessions, accumulate transcripts and re-extract to grow the graph:

```bash
waystone extract myproject ~/.waystone/transcripts/myproject/20260309_*.md
```

Or extract each new session as it happens:

```bash
waystone extract myproject ~/.waystone/transcripts/myproject/latest.md
```

---

## Useful commands

```bash
# One-click import from recent Claude Code sessions
waystone onboard myproject

# Import specific .jsonl session files
waystone import-claude-sessions myproject ~/.claude/projects/abc123/session.jsonl

# List discoverable sessions without importing
waystone import-claude-sessions myproject --list-only

# Start the MCP server (for Claude Code integration)
waystone mcp-serve                  # stdio (default, for Claude Code)
waystone mcp-serve --transport sse  # HTTP SSE (for other clients)

# See what's in your graph
waystone show myproject

# Query manually (useful for testing)
waystone query myproject "describe the data pipeline" --stats

# See exactly what was injected into the last prompt
waystone last-context

# Export the full graph as markdown
waystone export myproject

# Initialize a fresh empty graph
waystone init myproject

# Run a preflight check
waystone doctor          # check only
waystone doctor --fix    # check + automatically fix what's possible
```

---

## Uninstalling / Rolling Back

If Waystone doesn't work as expected and you want to remove it completely:

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

In any project directory where you ran `waystone hook-init` (or manually created `.waystone`):

```bash
rm /path/to/your/project/.waystone
```

### Step 3: Remove Waystone data (optional)

This deletes all graphs, transcripts, and state:

```bash
rm -rf ~/.waystone/
```

To remove only a specific project's graph:

```bash
rm -rf ~/.waystone/projects/myproject/
rm -rf ~/.waystone/transcripts/myproject/
```

### Step 4: Uninstall the package

```bash
pip uninstall waystone
```

---

## Troubleshooting

**`waystone configure` crashes with "Permission denied" or "already a directory"**
→ You ran `waystone configure` from your home directory. `~/.waystone/` already exists there as Waystone's data folder, so it can't also be a project marker file.
→ Fix: `cd` into your project directory first, then re-run:
```bash
cd /path/to/your/project
waystone configure
```
→ Alternatively, create the marker manually: `echo 'myproject' > /path/to/your/project/.waystone`

**`waystone doctor` shows ✗ for UserPromptSubmit / Stop hooks**
→ Doctor output depends on what you chose during `waystone configure`:
- **MCP-only (option 1)**: hooks show as `–  optional — MCP-only mode` — this is correct, not an error.
- **Hooks-only (option 2)** or **Both (option 3)**: hooks are required and the ✗ is real. Re-run `waystone configure` from your project directory and choose option 2 or 3.
- **No configure run yet**: run `waystone configure` first.
→ If you see ✗ after running configure, try `waystone doctor --fix` to auto-install the hooks.

**`waystone doctor` shows ✗ for MCP server registered**
→ If you chose hooks-only (option 2), doctor shows `–  not selected — hooks-only mode` — this is informational, not a failure.
→ If you chose MCP (option 1 or 3): re-run `waystone configure` to register it, or run manually:
```bash
claude mcp add waystone waystone mcp-serve
```
→ If `claude` isn't in PATH, Waystone writes the config directly to `~/.claude/claude_desktop_config.json`. Check that file — it should contain a `"waystone"` entry under `mcpServers`.

**"No graph found" in the status line**
→ Run `waystone onboard myproject` or `waystone extract myproject <transcript>` to build the graph first.

**MCP server not showing up in Claude Code**
→ Confirm `waystone mcp-serve` runs without error: `waystone mcp-serve --help`
→ Check that `~/.claude/claude_desktop_config.json` has the correct JSON syntax.
→ Restart Claude Code after editing the config.

**`waystone onboard` finds no sessions**
→ Sessions appear in `~/.claude/projects/` after using Claude Code at least once.
→ Check: `ls ~/.claude/projects/`

**Hook not firing / no status line**
→ Make sure you restarted Claude Code after running `hooks/install.py`.
→ Check `~/.claude/settings.json` for the hook entries.

**"No relevant context found" on every query**
→ Try `waystone show myproject` to confirm nodes exist, then try a broader query term.
→ Check that the `.waystone` file in your project directory contains the correct project name.

**Extraction fails with auth error**
→ Verify your `api_key` in `~/.waystone/config.yaml` or confirm `OPENAI_API_KEY` is set in your shell.

**Large transcript extraction fails or times out**
→ Add `--timeout 600` to extend the LLM timeout.
→ Add `--chunk-size 30000` to split the file into smaller pieces.

**Want to see the raw transcript the hook saved?**
```bash
cat ~/.waystone/transcripts/myproject/latest.md | head -50
```
