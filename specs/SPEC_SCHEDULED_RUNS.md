# Spec: Scheduled Runs — Cron-based Headless Orchestrator Executions

**Status:** Design  
**Priority:** P2 — workflow automation  
**Summary:** A cron mechanism that fires Pilot's headless mode (`waystone orchestrate --print PROMPT`) on a schedule and routes the output to Discord, files, webhooks, or stdout. Use cases: nightly graph health summaries, post-extract question review passes, daily standup briefings from the graph.

---

## Problem

Developers today run `waystone orchestrate my_project --print "summarize open questions"` manually to inspect graph state. But the most valuable insights are **summary reports**, not ad-hoc queries:

- **Nightly graph health:** "We closed 12 questions, opened 3 new ones, 1 constraint violated"
- **Post-extract review:** After `waystone extract`, an automated pass reviews what was extracted, flags missing patterns
- **Daily standup briefing:** Every morning, email a digest of open decisions and blockers (automatically)
- **Weekly pattern review:** `waystone patterns` output in Slack every Friday

Today these require manual invocation. Orchestrator has the execution primitive (headless mode works), but there's no scheduler.

---

## Goal

Build a **config-driven scheduler** that:

1. **Runs on a cron schedule** (e.g., `0 9 * * *` for 9am daily, or interval-based like `every 1h`)
2. **Accepts a prompt template** (e.g., "summarize decisions made since yesterday") which can reference dynamic context (date, project name)
3. **Invokes headless mode** (`Conversation.chat()` with no user history — fresh context injection)
4. **Routes output** to Discord (webhook), file, Slack webhook, or stdout
5. **Logs executions** for debugging and auditing
6. **Handles errors gracefully** (retry logic optional, failure notifications)

The design is intentionally simple: **no state machine, no task queue**. Just cron + orchestrator headless call + output routing. Local execution only v1.

---

## Scope

**In scope (v1):**
- Cron schedule configuration (cron expression or `every Xh/Xm`)
- Prompt template with substitution (`{PROJECT_NAME}`, `{DATE}`, `{TIME}`)
- Output routing: Discord webhook, file, stdout (with append mode for files)
- CLI: `waystone-orchestrate schedule` subcommand (list, add, run, remove)
- Config file: `orchestrator.schedules` in config.yaml (list of schedule objects)
- Execution logging (timestamps, prompt used, status, output size)
- Error handling: log errors, notify via webhook if available

**Out of scope for v1:**
- Task queue (Celery, RQ, etc.) — use local cron or systemd timer
- Database of past runs — log to file only
- A/B testing schedules
- Conditional triggers (e.g., "only if new nodes were added since last run")
- Multi-project aggregation (one schedule per project)
- Retry with exponential backoff (log and move on)
- OAuth refresh for Discord/Slack (user provides static webhook URL)

---

## New Config Keys

Add to `orchestrator.schedules` section in `config.yaml`:

```yaml
orchestrator:
  schedules:
    # Each schedule runs independently, own project context
    - name: "nightly-summary"
      enabled: true
      project: "my_project"
      schedule: "0 9 * * *"                # cron expression (9am daily)
      # OR:
      # interval: "1h"                     # every 1 hour
      prompt_template: |
        Summarize the standing graph state for {PROJECT_NAME} today:
        - How many open questions?
        - What constraints are active?
        - Any decisions made since yesterday?
        - Any blockers or conflicts?
      output:
        destination: "discord"
        webhook_url_env: "DISCORD_WEBHOOK_URL"  # reads from env var
        # OR:
        # destination: "file"
        # path: "~/.waystone/reports/{PROJECT_NAME}_{DATE}.md"
        # OR:
        # destination: "stdout"
      error_notification:
        destination: "discord"
        webhook_url_env: "DISCORD_WEBHOOK_URL"
        message_template: "Schedule {SCHEDULE_NAME} failed: {ERROR}"
    
    - name: "weekly-patterns"
      enabled: false
      project: "multi_project"
      schedule: "0 10 * * 5"                # 10am Friday
      prompt_template: "Run cross-project pattern analysis"
      output:
        destination: "file"
        path: "/tmp/patterns_{DATE}.md"
```

---

## CLI Interface

### List schedules

```bash
waystone-orchestrate schedule list [--project PROJECT]
```

Output:
```
Schedules for project my_project:

nightly-summary (ENABLED)
  Schedule: 0 9 * * * (9am daily)
  Next run: 2026-05-17 09:00:00 UTC
  Prompt: Summarize the standing graph state...
  Output: discord → DISCORD_WEBHOOK_URL

weekly-patterns (DISABLED)
  Schedule: 0 10 * * 5 (Friday 10am)
  Next run: never (disabled)
```

### Add schedule (interactive or CLI flags)

```bash
waystone-orchestrate schedule add \
  --name nightly-summary \
  --project my_project \
  --schedule "0 9 * * *" \
  --prompt "Summarize open questions" \
  --output-destination discord \
  --output-webhook-env DISCORD_WEBHOOK_URL
```

### Run schedule immediately

```bash
waystone-orchestrate schedule run nightly-summary
```

Output: prints prompt, executes, shows result, displays routing destination.

### Remove schedule

```bash
waystone-orchestrate schedule remove nightly-summary
```

### Daemon mode (experimental, v1.1+)

```bash
waystone-orchestrate schedule daemon [--config CONFIG] [-v]
```

Runs in foreground, fires schedules at cron times. Logs to stderr. Ctrl-C to stop.

---

## How It Integrates

### Execution Flow

1. **Schedule triggers** (via cron, systemd timer, or `schedule daemon` mode)
2. **Prompt is rendered** (substitutions: `{PROJECT_NAME}`, `{DATE}`, `{TIME}`, `{TIMESTAMP}`)
3. **Fresh `Conversation` is created** for the project (no history)
4. **Headless call:** `await conversation.chat(rendered_prompt)`
5. **Tool rounds run** (if any tools are enabled and the model calls them)
6. **Output is formatted** (markdown by default, optionally JSON for webhooks)
7. **Routed to destination** (Discord, file, stdout, webhook)
8. **Error reported** (if configured, via error_notification webhook)
9. **Execution logged** (timestamp, status, tokens, duration)

### Config Integration

- Schedules live in the same `config.yaml` as orchestrator/llm/strategies blocks
- Each schedule has its own project reference (can run same prompt on multiple projects by creating multiple schedule objects)
- Output destination is lazy-loaded; missing webhook URLs are caught at execution time (not at config parse time)

---

## Implementation: New CLI Subcommand

### File: `orchestrator/schedule_cli.py`

```python
"""CLI for managing and running scheduled orchestrator tasks."""

import click
from pathlib import Path
from .scheduler import Scheduler, ScheduleConfig


@click.group("schedule")
def schedule_group():
    """Manage scheduled orchestrator runs."""
    pass


@schedule_group.command("list")
@click.option("--project", default=None, help="Filter by project (optional)")
@click.option("--config", "config_path", default=None)
def list_schedules(project, config_path):
    """List all configured schedules."""
    cfg = load_config(config_path)
    schedules = cfg.get("orchestrator", {}).get("schedules", [])
    
    if project:
        schedules = [s for s in schedules if s.get("project") == project]
    
    if not schedules:
        click.echo("No schedules configured.")
        return
    
    for sched in schedules:
        name = sched.get("name", "unnamed")
        enabled = "ENABLED" if sched.get("enabled", True) else "DISABLED"
        project_name = sched.get("project", "unknown")
        cron = sched.get("schedule", sched.get("interval", "unknown"))
        click.echo(f"{name} ({enabled})")
        click.echo(f"  Project: {project_name}")
        click.echo(f"  Schedule: {cron}")
        click.echo()


@schedule_group.command("run")
@click.argument("schedule_name")
@click.option("--config", "config_path", default=None)
@click.option("-v", "--verbose", is_flag=True)
def run_schedule(schedule_name, config_path, verbose):
    """Execute a schedule immediately (one-shot)."""
    # Load config, find schedule, instantiate Conversation, run prompt, route output
    pass


@schedule_group.command("daemon")
@click.option("--config", "config_path", default=None)
@click.option("-v", "--verbose", is_flag=True)
def daemon_mode(config_path, verbose):
    """Run the scheduler daemon (watches all schedules, fires on cron)."""
    # Infinite loop: check cron times, fire matching schedules
    pass


@schedule_group.command("add")
@click.option("--name", required=True)
@click.option("--project", required=True)
@click.option("--schedule", required=True, help="Cron expression or 'every Xh/Xm'")
@click.option("--prompt", required=True)
@click.option("--output-destination", type=click.Choice(["discord", "slack", "file", "stdout"]), required=True)
@click.option("--output-path", default=None, help="For file destination")
@click.option("--output-webhook-env", default=None, help="Env var name for webhook URL")
@click.option("--config", "config_path", default=None)
def add_schedule(name, project, schedule, prompt, output_destination, output_path, output_webhook_env, config_path):
    """Add a new schedule to config (interactive wizard or flags)."""
    pass


@schedule_group.command("remove")
@click.argument("schedule_name")
@click.option("--config", "config_path", default=None)
def remove_schedule(schedule_name, config_path):
    """Remove a schedule from config."""
    pass
```

### File: `orchestrator/scheduler.py`

```python
"""Scheduler for executing orchestrator prompts on cron schedules."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
import json
import os

from waystone.config import load_config, get_db_path
from waystone.store import GraphStore

from .conversation import Conversation

log = logging.getLogger(__name__)


@dataclass
class ScheduleConfig:
    """One scheduled run configuration."""
    name: str
    enabled: bool
    project: str
    schedule: str | None  # cron expression
    interval: str | None  # "1h", "30m", etc.
    prompt_template: str
    output: dict  # {destination, webhook_url_env|path, ...}
    error_notification: dict | None = None
    
    def render_prompt(self) -> str:
        """Substitute placeholders in prompt template."""
        now = datetime.utcnow()
        return self.prompt_template.format(
            PROJECT_NAME=self.project,
            DATE=now.strftime("%Y-%m-%d"),
            TIME=now.strftime("%H:%M:%S"),
            TIMESTAMP=now.isoformat(),
        )


class Scheduler:
    """Manages scheduled orchestrator executions."""
    
    def __init__(self, cfg: dict):
        self._cfg = cfg
        self._schedules: list[ScheduleConfig] = []
        self._load_schedules()
    
    def _load_schedules(self) -> None:
        """Parse schedules from config."""
        raw_schedules = self._cfg.get("orchestrator", {}).get("schedules", [])
        for raw in raw_schedules:
            sched = ScheduleConfig(
                name=raw.get("name", "unnamed"),
                enabled=raw.get("enabled", True),
                project=raw.get("project"),
                schedule=raw.get("schedule"),
                interval=raw.get("interval"),
                prompt_template=raw.get("prompt_template", ""),
                output=raw.get("output", {}),
                error_notification=raw.get("error_notification"),
            )
            self._schedules.append(sched)
    
    async def run_schedule(self, schedule_name: str) -> dict:
        """Execute a schedule by name. Returns result dict."""
        matching = [s for s in self._schedules if s.name == schedule_name]
        if not matching:
            return {"status": "error", "message": f"Schedule {schedule_name} not found"}
        
        sched = matching[0]
        if not sched.enabled:
            return {"status": "error", "message": f"Schedule {schedule_name} is disabled"}
        
        return await self._execute_schedule(sched)
    
    async def _execute_schedule(self, sched: ScheduleConfig) -> dict:
        """Execute one schedule. Returns {status, output, tokens, duration, ...}."""
        start_time = datetime.utcnow()
        result = {
            "schedule_name": sched.name,
            "project": sched.project,
            "status": "pending",
            "prompt": sched.render_prompt(),
            "output": "",
            "error": None,
        }
        
        try:
            db_path = get_db_path(self._cfg, sched.project)
            if not db_path.exists():
                raise FileNotFoundError(f"Project {sched.project} not found")
            
            store = GraphStore(db_path)
            conversation = Conversation(
                cfg=self._cfg,
                store=store,
                project_name=sched.project,
            )
            
            # Run the prompt
            rendered_prompt = sched.render_prompt()
            reply = await conversation.chat(rendered_prompt)
            
            result["status"] = "ok"
            result["output"] = reply
            result["tokens"] = estimate_tokens(reply)
            result["duration_seconds"] = (datetime.utcnow() - start_time).total_seconds()
            
            # Route output
            await self._route_output(sched, reply)
            
            store.close()
            
        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)
            log.error(f"Schedule {sched.name} failed: {e}")
            
            # Notify of error if configured
            if sched.error_notification:
                await self._send_error_notification(sched, e)
        
        return result
    
    async def _route_output(self, sched: ScheduleConfig, output: str) -> None:
        """Send output to configured destination."""
        dest = sched.output.get("destination", "stdout")
        
        if dest == "discord":
            webhook_url = os.getenv(sched.output.get("webhook_url_env", ""))
            if webhook_url:
                await self._send_discord_webhook(webhook_url, output, sched.name)
            else:
                log.warning(f"Discord webhook URL not configured for {sched.name}")
        
        elif dest == "file":
            path = sched.output.get("path", "").format(
                PROJECT_NAME=sched.project,
                DATE=datetime.utcnow().strftime("%Y-%m-%d"),
            )
            if path:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_text(output + "\n", encoding="utf-8")
                log.info(f"Output written to {path}")
        
        elif dest == "stdout":
            print(output)
        
        else:
            log.warning(f"Unknown output destination: {dest}")
    
    async def _send_discord_webhook(self, webhook_url: str, message: str, schedule_name: str) -> None:
        """Send message to Discord webhook (async)."""
        # Truncate to 2000 chars if needed (Discord limit is 2000 per message)
        if len(message) > 1900:
            message = message[:1900] + "\n...[truncated]"
        
        import aiohttp
        payload = {
            "content": f"**{schedule_name}**\n\n{message}"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=payload) as resp:
                if resp.status not in (200, 204):
                    log.error(f"Discord webhook returned {resp.status}")
    
    async def _send_error_notification(self, sched: ScheduleConfig, error: Exception) -> None:
        """Notify of schedule failure via webhook."""
        if not sched.error_notification:
            return
        
        dest = sched.error_notification.get("destination", "discord")
        if dest == "discord":
            webhook_url = os.getenv(sched.error_notification.get("webhook_url_env", ""))
            if webhook_url:
                message = sched.error_notification.get("message_template", "Schedule failed: {ERROR}").format(
                    SCHEDULE_NAME=sched.name,
                    ERROR=str(error),
                )
                await self._send_discord_webhook(webhook_url, message, f"FAILED: {sched.name}")


async def run_daemon(cfg: dict, verbose: bool = False) -> None:
    """Run the scheduler daemon in foreground. Ctrl-C to stop."""
    import signal
    
    if verbose:
        logging.basicConfig(level=logging.DEBUG)
    
    scheduler = Scheduler(cfg)
    
    log.info(f"Scheduler daemon started with {len(scheduler._schedules)} schedule(s)")
    
    shutdown = asyncio.Event()
    
    def _signal_handler(sig, frame):
        log.info("Shutdown signal received")
        shutdown.set()
    
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    
    while not shutdown.is_set():
        try:
            # Check each schedule
            for sched in scheduler._schedules:
                if sched.enabled and _should_run(sched):
                    log.info(f"Firing schedule: {sched.name}")
                    await scheduler._execute_schedule(sched)
            
            # Sleep a bit before checking again (every 30 seconds)
            await asyncio.wait_for(shutdown.wait(), timeout=30)
        except asyncio.TimeoutError:
            continue  # Timeout is expected; check schedules again
    
    log.info("Scheduler daemon stopped")


def _should_run(sched: ScheduleConfig) -> bool:
    """Check if schedule should run now (cron or interval-based)."""
    import croniter
    
    now = datetime.utcnow()
    
    if sched.schedule:
        try:
            cron = croniter.croniter(sched.schedule, now)
            last_run = cron.get_prev(datetime)
            # Should run if last scheduled time is within the last 2 minutes
            # (accounts for daemon check interval of 30s, allows for small delays)
            return (now - last_run).total_seconds() < 120
        except Exception as e:
            log.error(f"Error parsing cron for {sched.name}: {e}")
            return False
    
    elif sched.interval:
        # Parse "1h", "30m", etc. — simplified: just store last-run timestamp somewhere
        # For v1, recommend cron expression instead
        log.warning(f"Interval-based scheduling not yet implemented; use cron for {sched.name}")
        return False
    
    return False
```

---

## Security Considerations

1. **Prompt Template Injection:** Prompt templates can include `{PROJECT_NAME}`, `{DATE}`, etc. Only these placeholders are allowed. No evaluation of arbitrary Python code. Validated at config load time.
2. **Webhook URL from env vars:** Never store webhook URLs in config files. Always read from `DISCORD_WEBHOOK_URL` style env vars. Logged as `***` in debug output.
3. **Output path traversal:** Output paths can include `{PROJECT_NAME}` and `{DATE}` but are resolved relative to `~/.waystone/reports/` or similar (configurable safe base). No `../` traversal allowed.
4. **Tool access:** Scheduled runs inherit the same tool sandbox as interactive runs. Tools respect `sandbox_root` config.
5. **Execution context:** Scheduled daemon should run as a dedicated user with minimal privileges. Recommend systemd service with `User=waystone-daemon`.

---

## Success Criteria

- Schedule configuration is parsed correctly from config.yaml on startup.
- `waystone-orchestrate schedule run SCHEDULE_NAME` executes immediately and routes output to configured destination.
- `waystone-orchestrate schedule daemon` runs in foreground, fires schedules at their cron times, and logs execution.
- Discord webhook receives formatted output message within 5 seconds of schedule firing.
- File output appends to a file with `{DATE}` in the filename, creating parent directories if needed.
- Missing webhook URLs are caught at execution time (not config parse time) and logged clearly.
- Schedules can run in parallel (if two fire at the same time, both execute without blocking each other).
- Prompt template substitution works: `{PROJECT_NAME}`, `{DATE}`, `{TIME}`, `{TIMESTAMP}` are replaced correctly.
- Errors in one schedule don't crash the daemon; they're logged and the daemon continues.

---

## Config Example

```yaml
orchestrator:
  schedules:
    - name: "nightly-graph-summary"
      enabled: true
      project: "my_project"
      schedule: "0 9 * * *"  # 9am daily
      prompt_template: |
        You are reviewing the {PROJECT_NAME} project graph as of {DATE}.
        Summarize:
        1. Open questions (how many, what are the key ones?)
        2. Active constraints that might affect decisions
        3. Recent decisions made in the last week
        4. Any potential conflicts or rollback patterns
        Be concise and actionable.
      output:
        destination: "discord"
        webhook_url_env: "DISCORD_WEBHOOK_URL"
      error_notification:
        destination: "discord"
        webhook_url_env: "DISCORD_WEBHOOK_URL"
        message_template: "Schedule {SCHEDULE_NAME} failed at {TIMESTAMP}: {ERROR}"
```

---

## Open Questions

1. **Interval-based scheduling?** Spec includes `interval: "1h"` syntax but implementation uses cron only (with per-schedule "last run" tracking needed for interval mode). Recommendation: v1 uses cron expressions only. Add interval support in v1.1 if needed.
2. **Multi-project aggregation?** Should one schedule be able to run on all projects and aggregate results? Recommendation: no v1 — one schedule = one project. If user wants multi-project reports, create multiple schedules that feed into a single Discord channel.
3. **Conditional triggers?** Run schedule only if `N new nodes were added since last run`? Recommendation: no v1 — just cron. Add conditional triggers as a v1.1 extension if needed.
4. **Task queue vs cron?** Spec recommends local systemd timer or cron, not Celery. Recommendation: keep it simple. If user needs distributed scheduling later, they can deploy multiple `schedule daemon` instances pointing to the same webhook.
5. **Prompt versioning?** If the prompt template changes, do old schedules keep the old template? Recommendation: no — config.yaml is source of truth. User edits config, schedule uses new template immediately.
