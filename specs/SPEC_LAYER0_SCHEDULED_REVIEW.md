# Spec Review: Layer 0 Auto-Injection + Scheduled Runs

**Status:** Architectural Review  
**Purpose:** Skeptical senior architect assessment of both specs before implementation  
**Reviewer Notes:** Flags underspecified areas, edge cases, integration hazards, and conflicting assumptions

---

## Layer 0 Auto-Injection — Critical Issues

### Issue 1: Blocking Init on SQLite Query

**Severity: HIGH**

Layer 0 is computed eagerly in `Conversation.__init__()` via `_build_layer0()`. This means:

- Every conversation instantiation blocks until SQLite returns the Layer 0 nodes
- If the graph is large (65K+ nodes) and the query is slow (e.g., full table scan on `is_active`), conversation start hangs
- No timeout specified; query can block indefinitely

**Evidence from code:**
```python
# conversation.py __init__
self._layer0_markdown = self._build_layer0()
if self._layer0_markdown:
    log.info("Layer 0 pre-fetched (%d tokens)", estimate_tokens(self._layer0_markdown))
```

**Mitigations:**
- Add a `layer0_fetch_timeout_seconds` config (default: 2.0)
- If timeout expires, Layer 0 is empty string (logged as `layer0_timeout`)
- Alternatively, make Layer 0 lazy (computed on first call to `build()`, cached in `_layer0_markdown`, computed once per session) — **recommended**

**Decision:** Make Layer 0 computation lazy. On first `build()` call, compute and cache. This avoids blocking at `__init__`, allows the first turn to start quickly, and Layer 0 is still pinned for the entire session.

**Revised code:**
```python
def __init__(self, ...):
    self._layer0_markdown = None  # lazy
    self._layer0_computed = False

def _ensure_layer0(self) -> None:
    if not self._layer0_computed:
        self._layer0_markdown = self._build_layer0()
        self._layer0_computed = True

def reset(self) -> None:
    self._layer0_computed = False
    self._context_mgr.reset()
    self._ensure_layer0()  # recompute eagerly on reset
```

---

### Issue 2: Layer 0 Competes with Per-Turn Context Token Budget

**Severity: MEDIUM**

Layer 0 has its own token budget (default 1000 tokens). Per-turn context has its own (default 2000 tokens). But they both go into the system prompt, which is shared across all turns.

**Scenario:**
- System prompt static: 500 tokens
- Layer 0: 1000 tokens
- Per-turn context (trimmed to 2000 limit): 2000 tokens
- Recent turns: 100 tokens
- **Total system prompt: 3600 tokens**

If the model context window is tight (4096 tokens), the system prompt alone consumes 88% of capacity. The first turn's history is squeezed.

**Design conflict:** The spec says "Layer 0 + per-turn context coexist without token conflicts" but doesn't define how conflicts are resolved.

**Mitigations:**
1. Document that Layer 0 + context + history token budgets should sum to <= 60% of model context (per CLAUDE.md pattern)
2. Add a `layer0_adaptive_budget` mode: if total system prompt + history > threshold, shrink Layer 0 (not per-turn context)
3. Add config guidance: "Typical use case: layer0_token_budget=500, context_token_limit=1500, overhead budget leaves 2000 for history"

**Decision:** Document the interaction. Add a config example showing recommended splits. No dynamic trimming of Layer 0 based on per-turn context v1.

---

### Issue 3: Layer 0 on Reset — Nondeterministic Selection

**Severity: MEDIUM**

When `reset()` is called, Layer 0 is recomputed from the current graph. But which nodes are "open questions" or "recent decisions" depends on what's in the graph at that moment.

**Scenario:**
- User starts session, Layer 0 shows 5 open questions
- User compacts history; async background extraction adds a sixth question to the graph
- User calls `reset()`
- New Layer 0 now shows 6 questions (different from before)

The user's context shifts mid-session after reset. This is probably fine (reset is rare), but the spec doesn't mention it.

**Mitigation:** Document in spec that `reset()` recomputes Layer 0 fresh from current graph state. Layer 0 is a snapshot, not persistent.

---

### Issue 4: No Cache Invalidation When Graph Mutates

**Severity: MEDIUM**

Layer 0 is computed once at the start of a session (or on reset). But the graph can mutate asynchronously:

- Background compaction extracts new nodes
- Tool calls like `ctx_update_node` modify confidence or status
- Graph can grow, constraints can become inactive

Layer 0 never refreshes. The model is making decisions based on stale Layer 0 data.

**Design decision:** Spec says "static per session" — intentionally static. But this can mislead users ("Layer 0 should be accurate" vs "Layer 0 is a session-start snapshot").

**Mitigation:** Add config key `layer0_refresh_every_N_turns` (default: 0 = never). If > 0, recompute Layer 0 every N turns. For v1, recommend 0 (no refresh); per-turn context catches mutations.

---

### Issue 5: Layer 0 Queries Missing Indices

**Severity: MEDIUM**

The `_fetch_*` functions in `layer0_builder.py` do full table scans:

```python
query = "SELECT id, fact, confidence, occurred_at FROM nodes WHERE type='question' AND is_active=1 ORDER BY occurred_at DESC LIMIT ?"
```

If the `nodes` table has 65K rows and only 100 are questions, this scans all 65K. Repeating this on every session (or every N turns) adds up.

**Mitigation:** Layer 0 queries should use indices:
- Add index on `(type, is_active, confidence, occurred_at)` in GraphStore migration
- Or rewrite queries to be covered by existing indices

This is a schema-level change, so coordinate with `store.py` maintainers.

---

## Scheduled Runs — Critical Issues

### Issue 1: Daemon Mode Implies Distributed Execution But Spec Doesn't Cover It

**Severity: HIGH**

The spec says:
> Runs on a cron schedule (via cron, systemd timer, or `schedule daemon` mode)

And recommends:
> systemd timer or cron, not Celery. If user needs distributed scheduling later, they can deploy multiple `schedule daemon` instances

But `schedule daemon` is a single-machine foreground process. If the user wants HA (two machines, only one runs the daemon at a time), the spec doesn't address:

- How to coordinate between two daemon instances (don't fire the same schedule twice)
- Leader election / distributed lock
- Failure recovery (if one daemon dies, the other picks up)

**Recommendation:** Spec is clear that v1 is single-machine only. Document this explicitly. Recommend users run daemon via systemd on one machine, or use a cron job. If they want HA, that's v1.1 (needs distributed lock + etcd or similar).

---

### Issue 2: Daemon Scheduling Logic Is Crude

**Severity: MEDIUM**

The `_should_run()` function uses `croniter` to check "did the cron expression fire in the last 2 minutes?"

```python
cron = croniter.croniter(sched.schedule, now)
last_run = cron.get_prev(datetime)
return (now - last_run).total_seconds() < 120
```

This approach has problems:

1. **Daemon stops for 3+ minutes** → missed the 2-minute window, schedule doesn't fire that day
2. **Daemon starts just after cron time** → last_run is 1 minute ago, fires immediately even though it already ran earlier today
3. **Two daemons running** → both fire because both see the cron time in the last 120 seconds

**Better approach:** Use a "last executed timestamp" file or env var:

```python
last_execution_file = f"/tmp/schedule_{sched.name}.timestamp"
if os.path.exists(last_execution_file):
    last_executed = float(Path(last_execution_file).read_text())
    if croniter_match and (now - last_executed) > 60:  # only fire if > 60s since last
        execute_and_write_timestamp()
```

**Mitigation:** Recommend users prefer system cron or systemd timer (v1) and mark daemon as experimental (v1.1+). If using daemon, document the limitations.

---

### Issue 3: Output Routing Has No Validation at Config Load Time

**Severity: MEDIUM**

The spec says:
> Missing webhook URLs are caught at execution time (not at config parse time) and logged clearly.

This means a typo in `webhook_url_env` (e.g., `DISCORD_WEBHOOK_URL_TYPO`) isn't caught until a schedule fires. The user might not notice for 24 hours.

**Scenario:**
```yaml
output:
  destination: "discord"
  webhook_url_env: "DISCRD_WEBHOOK_URL"  # typo: DISCRD instead of DISCORD
```

Schedule fires at 9am. User doesn't get the message. Checks logs 8 hours later.

**Mitigation:**
- At config load time, validate that all referenced env vars exist (warn if missing, don't error)
- Or add a `waystone-orchestrate schedule validate` command
- Or add a `--dry-run` flag to `schedule run` that checks everything without executing

**Decision:** Add validation at config load time. Warn for missing env vars. Spec should say "missing webhook URL env vars are caught at config load time and logged as warnings."

---

### Issue 4: No Rate Limiting on Webhook Posts

**Severity: LOW**

If a schedule's output is huge (100K tokens = 25KB JSON), posting to Discord every 5 minutes could hit Discord's rate limits. Spec doesn't mention backoff or batching.

**Mitigation:** For v1, document that output is truncated to 1900 chars (Discord limit). If user wants larger output, use file destination. Add rate limiting in v1.1 if needed.

---

### Issue 5: No Differentiation Between "Schedule Not Found" and "Schedule Disabled"

**Severity: LOW**

The `run_schedule()` function treats them the same:

```python
if not matching:
    return {"status": "error", "message": f"Schedule {schedule_name} not found"}

sched = matching[0]
if not sched.enabled:
    return {"status": "error", "message": f"Schedule {schedule_name} is disabled"}
```

For `--dry-run` or `schedule list`, it's important to distinguish. For `schedule run`, both are errors.

**Mitigation:** Return different status codes or status strings: `"not_found"` vs `"disabled"` vs `"error"`. Minimal change, improves debuggability.

---

## Integration Issues: Layer 0 + Scheduled Runs

### Issue 1: Layer 0 Staleness in Scheduled Runs

**Severity: MEDIUM**

A scheduled run fires at 9am. Layer 0 is computed fresh (good). But the prompt says:

```
You are reviewing the my_project project graph as of {DATE}.
Summarize: 1. Open questions...
```

By the time the user reads the output at 10am, Layer 0 is 1 hour stale. During that hour, new questions might have been added, constraints satisfied, etc.

**Design decision:** Is this acceptable for v1? Probably yes (scheduled reports are summaries, not real-time). But the prompt template should acknowledge the staleness:

```
As of {DATE} {TIME}, the project had:
- X open questions
- Y active constraints
[This snapshot may be stale by the time you read it. Run 'waystone-orchestrate my_project --print "current status"' for real-time view.]
```

**Mitigation:** Document in spec that scheduled reports are point-in-time snapshots. Recommend prompt templates include a caveat.

---

### Issue 2: Layer 0 in Headless Mode

**Severity: MEDIUM**

Headless mode (`--print PROMPT`) invokes `Conversation.chat()` without any prior history. Layer 0 will be injected. But is this desired?

**Scenario:**
```bash
echo "quick status check" | waystone orchestrate my_project --print -
```

User expects just the per-turn response to "quick status check", not Layer 0 + dynamic context + per-turn response. The output might be longer than expected.

**Mitigation:** Add a `--no-layer0` flag to headless mode. Default to including Layer 0 (since it's useful context), but allow opting out.

---

### Issue 3: Scheduled Runs Can't Reuse Conversation State

**Severity: LOW**

Each scheduled run creates a fresh `Conversation` with no prior history. This is efficient (no state to manage) but means each run:

- Can't refer to "the previous scheduled run" (e.g., "compare today's questions to yesterday's")
- Can't track metric deltas (e.g., "5 more questions opened since the last run")

Workaround: Store previous report in a file, next schedule compares by reading the file. Clunky.

**Mitigation:** For v1, recommend prompt templates include context they need (e.g., "list all open questions"). For v1.1, consider optional schedule state persistence.

---

### Issue 4: Scheduled Runs Inherit Tool Config

**Severity: MEDIUM**

Scheduled runs use the full orchestrator tool set from config. If the user has tools enabled (bash, write_file, etc.), a scheduled prompt could invoke them:

```yaml
prompt_template: |
  Analyze the project. If critical issues found, write a summary to /tmp/alert.md
```

Tool could execute during the schedule, modifying the filesystem. Is this desired?

**Recommendation:** Scheduled runs should inherit tool config but with a safety default. Add `schedules.tools_enabled_override` to config, defaulting to `[]` (no tools). User must explicitly enable tools per schedule if desired. Example:

```yaml
schedules:
  - name: nightly-summary
    tools_enabled_override: []  # disable all tools for this schedule
  
  - name: critical-issue-handler
    tools_enabled_override: [bash, write_file]  # explicitly enable only these
```

---

### Issue 5: Error Notification Can Itself Fail Silently

**Severity: LOW**

If a schedule fails and error notification is configured, but the webhook URL env var is wrong:

```python
webhook_url = os.getenv(sched.error_notification.get("webhook_url_env", ""))
if webhook_url:
    await self._send_discord_webhook(webhook_url, message, ...)
else:
    log.warning(f"Discord webhook URL not configured for {sched.name}")
```

The error is logged but the user might not see it. It's a warning, not loud.

**Mitigation:** For schedules with error_notification configured, validate the env var at config load time (same as Issue #3 above). Or raise an error at config load time if error_notification is configured but webhook env var is missing.

---

## Architectural Misalignments

### Issue 1: "Eager" vs "Lazy" Philosophy Inconsistency

**Severity: MEDIUM**

- **Layer 0 spec:** Originally eager (computed in `__init__`), should be lazy (see Issue #1 above)
- **Scheduled runs spec:** Lazy (executed on demand via cron daemon)
- **Compaction in Conversation:** Currently eager for history pruning, lazy for extraction (runs in background)

The orchestrator mixes eager and lazy strategies. This is fine, but needs consistency in the mental model.

**Recommendation:** Document each major operation's eagerness:
- Layer 0: lazy (computed on first prompt assembly, cached per session)
- Compaction: eager history pruning, lazy background extraction
- Per-turn retrieval: eager (runs on every turn)
- Scheduled runs: lazy (fires on cron, not on-demand)

---

### Issue 2: Config Complexity Growing

**Severity: MEDIUM**

Adding Layer 0 + Scheduled Runs config to `config.yaml` creates a lot of nesting:

```yaml
orchestrator:
  system_prompt:
    layer0_enabled: true
    layer0_token_budget: 1000
    layer0_confidence_threshold: 0.7
    layer0_recency_days: 30
    layer0_question_limit: 5
    layer0_constraint_limit: 10
    layer0_decision_limit: 8
  
  schedules:
    - name: ...
      schedule: ...
      prompt_template: ...
      output: ...
      error_notification: ...
```

This is okay for v1. But if we add 5 more features, the config file becomes unwieldy. Recommendation for v2: split into separate files (e.g., `~/.waystone/layer0.yaml`, `~/.waystone/schedules.yaml`) and include them.

---

### Issue 3: No Explicit "Conversation Modes"

**Severity: LOW**

The Conversation class now supports:
- Interactive REPL (with Layer 0)
- Headless mode (with Layer 0, but should be optional)
- Scheduled runs (with Layer 0, fresh state each time)

These are variants on the same Conversation class. But there's no explicit "mode" parameter. The code just reads config flags and behaves differently.

**Recommendation:** Add a `mode` parameter to `Conversation.__init__`:

```python
def __init__(self, cfg, store, project_name, project_root=None, mode="interactive"):
    # mode: "interactive", "headless", "scheduled"
    self._mode = mode
    # ... different defaults for layer0_enabled etc based on mode
```

This makes the intent explicit and allows per-mode tuning of defaults.

---

## Design Decisions That Are Underspecified

### Decision 1: Layer 0 Markdown Format

**Underspecified:** The spec shows an example markdown format, but doesn't specify:
- How many lines per node? (just the fact, or fact + confidence + date?)
- Grouping by type (questions | constraints | decisions) or chronological?
- Sorting within each group? (by confidence, by date, by text length?)

**Recommendation:** Specify the exact markdown format. Example:

```markdown
## Standing World State

### Open Questions (3)
- Question 1 fact text
- Question 2 fact text
- Question 3 fact text [older; from 2026-05-10]

### Active Constraints (2)
- Constraint fact (confidence: 0.88, set 2026-05-15)
- Constraint fact (confidence: 0.76, set 2026-05-12)

### Recent Decisions (2)
- [2026-05-16] Decision fact (confidence: 0.95)
- [2026-05-15] Decision fact (confidence: 0.82)
```

---

### Decision 2: Prompt Template Syntax

**Underspecified:** The spec mentions `{PROJECT_NAME}`, `{DATE}`, `{TIME}`, `{TIMESTAMP}` substitutions but doesn't handle:
- Case sensitivity (`{project_name}` vs `{PROJECT_NAME}`)?
- Escaping (what if user wants literal `{` in prompt)?
- Date format? `{DATE}` → `2026-05-17` or `May 17, 2026`?
- Timezone for `{TIME}` / `{TIMESTAMP}`? (spec says UTC, should be explicit)

**Recommendation:** Specify exact syntax. Use Python format string (`str.format()`) and document the supported placeholders:
- `{PROJECT_NAME}` → project slug (e.g., `my_project`)
- `{DATE}` → ISO 8601 (e.g., `2026-05-17`)
- `{TIME}` → HH:MM:SS UTC (e.g., `09:00:00`)
- `{TIMESTAMP}` → ISO 8601 with time (e.g., `2026-05-17T09:00:00Z`)
- No other placeholders allowed (fail at config load time if unknown)

---

### Decision 3: Concurrency & Race Conditions

**Underspecified:** If two scheduled runs fire at the same time (e.g., a 1-minute interval schedule and a 60-minute schedule both firing at minute 60):

- Do they run in parallel (`asyncio.gather`)? Or sequentially?
- Do they share the same SQLite connection? (SQLite is designed for serial access, but concurrent readers are okay)
- If concurrent reads both fetch Layer 0, do they block each other?

**Recommendation:** Document concurrency model:
- Scheduled runs execute sequentially via an asyncio Queue (one at a time)
- This avoids SQLite contention and keeps logs clean
- If a run takes 30s and two schedules fire at the same second, the second queues behind the first

---

## Missing From Both Specs

### Missing: Observability / Metrics

Neither spec includes metrics for:
- How many turns per session before Layer 0 becomes stale?
- What's the token distribution? (system prompt overhead, Layer 0 %, context %)
- How long do scheduled runs take?
- What's the error rate?

**Recommendation:** Add logging (already in code) and suggest integrating with CloudWatch / Datadog / Prometheus for production. v1 logs to stderr; v1.1 can add metric exports.

---

### Missing: Backward Compatibility

What happens if a user has an old `config.yaml` (before Layer 0 and Scheduled Runs were added)?

**Recommendation:** Both features should be disabled by default if not mentioned in config (graceful degradation). Add config validation that warns on missing optional keys but doesn't error.

---

### Missing: Testing Strategy

No mention of:
- How to test Layer 0 node selection (mock GraphStore?)
- How to test scheduled run execution without actually waiting for cron times
- How to test daemon mode (systemd integration tests?)

**Recommendation:** Add a testing section to each spec, including:
- Unit tests for Layer 0 builders and query functions
- Integration tests for Scheduled runs (mock time, fire schedule, check output)
- CLI tests for `schedule list`, `schedule run`, etc.

---

## Summary of Severity Levels

| Severity | Layer 0 | Scheduled Runs | Integration | Total |
|----------|---------|---|---|---|
| HIGH | 1 | 1 | 0 | 2 |
| MEDIUM | 4 | 3 | 3 | 10 |
| LOW | 1 | 2 | 1 | 4 |

**Recommendation:** Fix the two HIGH issues before implementation:
1. **Layer 0:** Make computation lazy, not eager in `__init__`
2. **Scheduled Runs:** Clarify daemon mode is experimental v1.1+; recommend cron/systemd for v1

---

## Implementation Order

1. **Layer 0** (first)
   - Add `layer0_builder.py` module
   - Modify `SystemPromptBuilder` to accept `layer0_markdown` parameter
   - Modify `Conversation` to build and cache Layer 0 (lazy)
   - Add indices to `nodes` table for Layer 0 queries
   - Test with small and large graphs

2. **Scheduled Runs** (second, depends on Layer 0 being stable)
   - Add `scheduler.py` module
   - Add `schedule_cli.py` subcommand
   - Implement `schedule list`, `schedule run` (daemon is optional v1.1)
   - Test with mock time and capture output routing

3. **Integration tests** (last)
   - Scheduled run that injects Layer 0 and makes a prompt
   - Verify output is routed correctly
   - Verify error handling

---

## Final Verdict

Both specs are **implementable** with the fixes noted above. Layer 0 is solid conceptually (eager startup problem aside). Scheduled Runs is workable but the daemon mode needs to be marked experimental and the scheduling logic simplified for v1.

The biggest risk is **Layer 0 blocking on SQLite**. Make it lazy and add query indices, and this risk goes away.

**Recommendation:** Proceed with implementation of both specs after addressing the HIGH-severity issues.
