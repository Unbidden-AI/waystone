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

**Step 2a — LLM provider**: choose Gemini, OpenAI, Anthropic, Local, or Custom; enter your API key; the wizard tests the connection immediately.

**Step 2b — Integration targets**: select which tools to integrate (space-separated numbers):

```
  [1] Claude Code  —  hooks / MCP / both — you'll choose next
  [2] Google Antigravity  —  hooks + MCP
  [3] OpenAI Codex CLI  —  hooks + MCP
  [4] OpenHands  —  hooks
  [5] OpenCode  —  JS plugin

  Tools: 1

  → Claude Code — choose integration method:
    [1] Hooks (recommended) — auto-inject context before every prompt
    [2] MCP server — Claude calls Waystone as a tool on-demand
    [3] Both hooks + MCP
```

**Step 2c — Project marker**: optionally marks the current directory so Waystone knows which graph to use.

Then verify everything is working:

```bash
waystone doctor       # check only
waystone doctor --fix # check + automatically fix what's possible
```

> **Important:** Run `waystone configure` from inside your project directory, not your home directory. Waystone stores its data in `~/.waystone/` — running configure there causes a conflict with the project marker file. If you see a "Permission denied" or "already a directory" error, `cd` into your project first and re-run.

---

## Step 2: Update your LLM API key later

`waystone configure` handles the initial API key setup. To update it later:

```bash
# macOS / Linux
open ~/.waystone/config.yaml

# Windows
notepad %USERPROFILE%\.waystone\config.yaml
```

> **Tip:** If you prefer environment variables, set `GEMINI_API_KEY` (or `OPENAI_API_KEY`) in your shell and leave `api_key` out of the config.

---

## Step 3: Seed your graph

**Option A — Import existing sessions (recommended)**

If you've already used Claude Code, import your recent sessions in one step:

```bash
waystone onboard myproject
```

You'll see a menu of your recent sessions, pick which to import, and it builds the graph immediately.

**Option B — Extract a project brief (new projects)**

If you have no sessions yet, write a short `project_brief.md` covering what the project does, your tech stack, and any hard constraints, then:

```bash
waystone extract myproject project_brief.md
```

You'll get 20–50 nodes. Every session from that point forward will have those facts as context.

> Design docs, ADRs, a README, or existing specs work equally well — `waystone extract` handles any markdown file.

---

## Step 4: Start working

Just work normally in your project. The hooks automatically:
- Inject relevant graph context before every prompt (UserPromptSubmit)
- Save each session transcript at the end (Stop hook)
- Background-extract new facts from each transcript

The status line shows live metrics (hooks integration):
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

# Replay the project's story — a timeline of session summaries
waystone story myproject

# Back-fill that story from a project's existing saved transcripts (one-time)
waystone catchup-summarize myproject

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
- The `UserPromptSubmit` entry containing `waystone`
- The `Stop` entry containing `waystone`
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
- **MCP-only**: hooks show as `–  optional — MCP-only mode` — this is correct, not an error.
- **Hooks or Both**: hooks are required and the ✗ is real. Re-run `waystone configure` and select Claude Code with hooks.
- **No configure run yet**: run `waystone configure` first.
→ If you see ✗ after running configure, try `waystone doctor --fix` to auto-install the hooks.

**`waystone doctor` shows ✗ for MCP server registered**
→ If you chose hooks-only, doctor shows `–  not selected — hooks-only mode` — this is informational, not a failure.
→ If you chose MCP or Both: re-run `waystone configure` to register it, or run manually:
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
