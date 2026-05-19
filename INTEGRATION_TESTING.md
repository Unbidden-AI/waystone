# Integration Testing Protocol

Testing procedures for all Waystone integration paths and Sentry error monitoring.

---

## Pre-flight: Is Waystone on PyPI?

Before testing `pip install waystone`, confirm it's published:
```bash
pip index versions waystone
```
If not yet published, substitute `pip install git+https://github.com/cj7wilson/Context-broker.git` in all steps below.

---

## Path 1: CLI (baseline)

**Install:**
```bash
pip install waystone
waystone --help
```

**Test:**
```bash
waystone init test-project
echo "We decided to use PostgreSQL because of JSONB support." > /tmp/test.md
waystone extract test-project /tmp/test.md
waystone query test-project "database choice"
```

**Pass criteria:** query returns the PostgreSQL decision node.

---

## Path 2: Claude Code — Hooks (zero-config)

**Install:** Claude Code is already installed.

**Configure** — add to `~/.claude/settings.json`:
```json
{
  "hooks": {
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "waystone hook query test-project"}]}],
    "Stop": [{"hooks": [{"type": "command", "command": "waystone hook extract test-project"}]}]
  }
}
```

**Test:**
1. Start a new Claude Code session in any project
2. Ask Claude something related to PostgreSQL
3. Check that `[Waystone: retrieved N nodes...]` appears in the system reminder
4. After Claude responds and the session ends, run `waystone show test-project` and verify new nodes were added

**Pass criteria:** context injection appears in the prompt; node count increases after session.

---

## Path 3: Claude Code — MCP server

**Configure** — add to `~/.claude/settings.json` under `mcpServers`:
```json
{
  "mcpServers": {
    "waystone": {
      "command": "waystone",
      "args": ["mcp-serve"],
      "env": { "WAYSTONE_PROJECT": "test-project" }
    }
  }
}
```

**Test:**
1. Start Claude Code, run `/mcp` — confirm `waystone` shows as connected
2. Ask Claude: *"Use the waystone_query tool to check what we know about database choices"*
3. Verify Claude calls `waystone_query` and returns results

**Pass criteria:** `waystone` listed as active MCP server; `waystone_query` tool callable.

---

## Path 4: Cursor

**Install:** [cursor.com](https://cursor.com) → download and install

**Configure** — open Cursor Settings → MCP → Add server:
```json
{
  "waystone": {
    "command": "waystone",
    "args": ["mcp-serve"],
    "env": { "WAYSTONE_PROJECT": "test-project" }
  }
}
```

Or edit `~/.cursor/mcp.json` directly.

**Test:**
1. Open Cursor, open Agent mode (Cmd+I)
2. Ask: *"Check waystone for any context on this project"*
3. Confirm `waystone_query` is called in the tool calls panel

**Pass criteria:** Cursor shows waystone tools available; query returns results.

---

## Path 5: Windsurf

**Install:** [codeium.com/windsurf](https://codeium.com/windsurf) → download

**Configure** — edit `~/.codeium/windsurf/mcp_config.json`:
```json
{
  "mcpServers": {
    "waystone": {
      "command": "waystone",
      "args": ["mcp-serve"],
      "env": { "WAYSTONE_PROJECT": "test-project" }
    }
  }
}
```

**Test:** Open Cascade (Windsurf's agent), ask it to use waystone_query.

**Pass criteria:** MCP tools available in Cascade.

---

## Path 6: Continue.dev (VS Code)

**Install:**
1. Install VS Code if needed
2. Install Continue extension from VS Code marketplace

**Configure** — edit `~/.continue/config.json`, add under `experimental.modelContextProtocolServers`:
```json
{
  "experimental": {
    "modelContextProtocolServers": [{
      "transport": {
        "type": "stdio",
        "command": "waystone",
        "args": ["mcp-serve"],
        "env": { "WAYSTONE_PROJECT": "test-project" }
      }
    }]
  }
}
```

**Test:** Open Continue chat in VS Code, ask it to query waystone.

**Pass criteria:** waystone tools visible; query returns results.

---

## Path 7: Cline (VS Code)

**Install:** Install Cline extension from VS Code marketplace

**Configure** — Cline Settings → MCP Servers → Add:
- Command: `waystone`
- Args: `mcp-serve`
- Env: `WAYSTONE_PROJECT=test-project`

**Test:** Open Cline, ask it to use waystone_query tool.

**Pass criteria:** waystone tools visible; query returns results.

---

## Path 8: Zed

**Install:** [zed.dev](https://zed.dev) → download

**Configure** — Zed settings (`cmd+,`), add to `assistant.context_servers`:
```json
{
  "assistant": {
    "context_servers": {
      "waystone": {
        "command": {
          "path": "waystone",
          "args": ["mcp-serve"],
          "env": { "WAYSTONE_PROJECT": "test-project" }
        }
      }
    }
  }
}
```

**Test:** Open Zed Assistant panel, use `@waystone` to call tools.

**Pass criteria:** waystone context server listed; tools callable.

---

## Sentry Integration Test

**Setup:**
1. Go to [sentry.io](https://sentry.io) → create a new project (Python → FastAPI)
2. Copy the DSN (looks like `https://abc123@o123.ingest.sentry.io/456`)
3. Add `SENTRY_DSN=<your-dsn>` as a Railway env var
4. Redeploy Railway

**Test 1 — Verify initialization:**
Check Railway deploy logs for Sentry initializing without errors. Silent success is fine — `init_sentry()` returns a bool but doesn't log.

**Test 2 — Trigger a real error:**
```bash
curl -sk https://api.waystone.unbidden.ai/account/key \
  -H "Authorization: Bearer invalid.jwt.token"
```
This hits JWT validation and raises an exception that Sentry captures.

**Test 3 — Check Sentry dashboard:**
- Go to Sentry → Issues
- An unhandled exception should appear within ~30 seconds
- Verify the stack trace points to `_validate_clerk_jwt` in `api_server.py`

**Pass criteria:** Error appears in Sentry with correct file/line attribution.

---

## Test Matrix

| Path | Installed | Configured | Query works | Extract works |
|------|-----------|------------|-------------|---------------|
| CLI | | | | |
| Claude Code hooks | | | | |
| Claude Code MCP | | | | |
| Cursor | | | | |
| Windsurf | | | | |
| Continue.dev | | | | |
| Cline | | | | |
| Zed | | | | |
| Sentry | | | n/a | n/a |
