# Spec: Layer 0 Auto-Injection — Standing World State

**Status:** Design  
**Priority:** P1 — foundational context orientation  
**Summary:** A static "world state" block injected once at conversation start before the user speaks. Layer 0 pre-fetches the most critical graph context (open questions, active constraints, unresolved decisions) and pins it into the system prompt so the model is oriented from turn 1, eliminating the delay from per-turn retrieval.

---

## Problem

In the current orchestrator design, the first user message triggers retrieval keyed to that message, which means:

1. **Cold start latency:** The model's first turn begins without project context. It sees only the static instructions and recent turns (from prior sessions if any). The relevant graph context arrives milliseconds later.
2. **Context brittleness:** Per-turn retrieval is keyed to keywords in the user message. A user asking "what should we do?" has no task keywords → retrieval returns little or nothing → the model starts blind to standing open questions or constraints it should be aware of.
3. **Disorientation on first turn:** For collaborative workflows (e.g., standup briefing, decision review), the model should start with a sense of the project's standing state: what's open, what's blocked, what decisions are pending.

Today, the model works *around* this by making tool calls to inspect the graph. But that adds latency and is reactive rather than proactive.

---

## Goal

Add a Layer 0 pre-fetch that runs at conversation start and injects standing world state into the system prompt before any user message arrives. The Layer 0 block is:

- **Static per session** — computed once when `Conversation` is instantiated (or reset), not recomputed on every turn
- **Standing facts** — high-confidence open questions, active constraints, unresolved decisions, and recent activity summaries
- **Pinned to system prompt** — injected before the dynamic per-turn context, so the model always knows the standing state
- **Token-budgeted** — capped at a configurable fraction of the system prompt token budget (default: 1000 tokens) so it doesn't squeeze out per-turn context
- **Refreshed on `reset()`** — when the conversation is cleared, Layer 0 is recomputed from the current graph state

---

## Scope

**In scope (v1):**
- Layer 0 pre-fetch at conversation start (eager; blocks `Conversation.__init__`)
- Node selection: open questions, active constraints, high-confidence decisions (>= threshold), recent decisions (last N days)
- Markdown assembly: grouped by type, pinned section in system prompt before dynamic context
- Config: `layer0_enabled`, `layer0_token_budget`, `layer0_recency_days`, `layer0_confidence_threshold`, `layer0_question_limit`, `layer0_constraint_limit`, `layer0_decision_limit`
- Refresh on `Conversation.reset()`

**Out of scope for v1:**
- Caching Layer 0 between sessions (recomputed fresh each start)
- Scheduled Layer 0 refreshes during a conversation (static per session)
- Smart filtering by "relevance to the user" — Layer 0 is project-wide standing state, not query-specific
- Pinning specific nodes as "always show" (per CLAUDE.md guidance: don't add features for hypothetical use cases)
- A/B testing or adaptive Layer 0 construction (fixed strategy v1)

---

## New Config Keys

Add to `orchestrator.system_prompt` section in `config.yaml`:

```yaml
orchestrator:
  system_prompt:
    # Layer 0: standing world state injected at conversation start
    layer0_enabled: true                    # global enable/disable
    layer0_token_budget: 1000               # max tokens for Layer 0 block (soft limit)
    layer0_confidence_threshold: 0.7        # min confidence to include decisions
    layer0_recency_days: 30                 # include decisions from last N days (0 = all)
    layer0_question_limit: 5                # max open questions to show
    layer0_constraint_limit: 10             # max active constraints to show
    layer0_decision_limit: 8                # max recent/high-confidence decisions to show
    layer0_append_context_footer: true      # if true, append "Layer 0 refreshed: T0, see /stats for updates"
    
    context_token_limit: 2000               # per-turn dynamic context (unchanged)
```

---

## How It Integrates

### Conversation lifecycle

1. **Instantiation** (`__init__`):
   - After `SystemPromptBuilder` is created, call `_build_layer0()` (see below)
   - Store result in `self._layer0_markdown`
   - Log: "Layer 0 pre-fetched: X questions, Y constraints, Z decisions (~A tokens)"

2. **Reset** (`reset()`):
   - After clearing history, call `_build_layer0()` again
   - Update `self._layer0_markdown`
   - Log: "Layer 0 refreshed on reset"

3. **System prompt assembly** (`SystemPromptBuilder.build()`):
   - **First turn only:** Inject Layer 0 block *before* dynamic context
   - After subsequent turns, Layer 0 remains pinned in the static system prompt context (i.e., it does *not* change mid-conversation)
   - Flow: static instructions + Layer 0 + dynamic context + recent turns

**Design decision:** Layer 0 is not part of the dynamic context — it's part of the system prompt. This avoids competing with per-turn retrieval for token budget. Per-turn retrieval can always still happen, and Layer 0 + per-turn context coexist.

### Token budget accounting

- `SystemPromptBuilder.overhead_tokens()` already estimates framing overhead
- Add `overhead_tokens_with_layer0()` method that includes Layer 0 in the estimate
- CLI tools can report Layer 0 tokens separately in `/stats`

---

## Implementation: Conversation Changes

### New method: `_build_layer0()` in Conversation

```python
def _build_layer0(self) -> str:
    """Build standing world state block.
    
    Returns markdown string suitable for injecting into system prompt.
    Called at __init__ and on reset().
    """
    from .layer0_builder import build_layer0
    
    cfg = self._prompt_builder._layer0_cfg if hasattr(self._prompt_builder, '_layer0_cfg') else {}
    if not cfg.get('enabled', True):
        return ""
    
    return build_layer0(
        store=self._store,
        token_budget=cfg.get('token_budget', 1000),
        confidence_threshold=cfg.get('confidence_threshold', 0.7),
        recency_days=cfg.get('recency_days', 30),
        question_limit=cfg.get('question_limit', 5),
        constraint_limit=cfg.get('constraint_limit', 10),
        decision_limit=cfg.get('decision_limit', 8),
    )
```

### Modify `Conversation.__init__`

```python
def __init__(self, cfg: dict, store: GraphStore, project_name: str, project_root=None):
    # ... existing code ...
    self._prompt_builder = SystemPromptBuilder(sp_cfg, project_root=Path(project_root) if project_root else None)
    
    # NEW: Pre-fetch Layer 0 standing state
    self._layer0_markdown = self._build_layer0()
    if self._layer0_markdown:
        log.info("Layer 0 pre-fetched (%d tokens)", estimate_tokens(self._layer0_markdown))
```

### Modify `Conversation.reset()`

```python
def reset(self) -> None:
    """Clear history (start a new logical session, keep the graph store)."""
    self._context_mgr.reset()
    
    # NEW: Refresh Layer 0 on reset
    self._layer0_markdown = self._build_layer0()
    
    log.info("Conversation reset for project %r", self._project_name)
```

### Modify `Conversation.chat()` and `Conversation.chat_stream()`

In both methods, change the `_prompt_builder.build()` call to:

```python
system = self._prompt_builder.build(
    layer0_markdown=self._layer0_markdown,  # NEW
    context_markdown=context_md,
    task_description=user_message,
    recent_turns=recent_turns,
)
```

---

## Implementation: SystemPromptBuilder Changes

### Modify `SystemPromptBuilder.__init__`

```python
def __init__(self, cfg: dict, project_root: Path | None = None):
    # ... existing code ...
    # NEW: Store Layer 0 config
    self._layer0_cfg = cfg.get('layer0', {})
```

### Modify `SystemPromptBuilder.build()`

```python
def build(
    self,
    context_markdown: str = "",
    task_description: str = "",
    recent_turns: str = "",
    layer0_markdown: str = "",  # NEW
) -> str:
    """Assemble system prompt with Layer 0 standing state + dynamic context.
    
    Order:
    1. Static instructions
    2. Layer 0 standing state (pinned, token-budgeted)
    3. Dynamic per-turn context (keyed to current task)
    4. Recent turns (hot cache)
    """
    parts: list[str] = []
    
    if self._static:
        parts.append(self._static)
    
    # NEW: Inject Layer 0 before dynamic context
    if layer0_markdown and layer0_markdown.strip():
        layer0_section = "## Standing World State\n\n" + layer0_markdown + "\n\n---\n"
        parts.append(layer0_section)
        log.debug("SystemPromptBuilder: layer0_tokens=%d", estimate_tokens(layer0_markdown))
    
    if self._include_context:
        ctx = self._trim_context(context_markdown or "")
        context_section = _CONTEXT_HEADER + (ctx if ctx else _NO_CONTEXT_MSG) + _CONTEXT_FOOTER
        parts.append(context_section)
        log.debug(
            "SystemPromptBuilder: task=%r ctx_tokens=%d",
            (task_description or "")[:80],
            estimate_tokens(ctx),
        )
    
    if recent_turns and recent_turns.strip():
        parts.append(recent_turns.strip() + "\n---\n")
    
    prompt = "\n\n".join(parts)
    log.debug("SystemPromptBuilder: total prompt tokens ~%d", estimate_tokens(prompt))
    return prompt
```

---

## Implementation: New Module `orchestrator/layer0_builder.py`

```python
"""Layer 0 standing world state builder — pre-fetched at conversation start."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from waystone.store import GraphStore

from .llm_adapter import estimate_tokens

log = logging.getLogger(__name__)


def build_layer0(
    store: GraphStore,
    token_budget: int = 1000,
    confidence_threshold: float = 0.7,
    recency_days: int = 30,
    question_limit: int = 5,
    constraint_limit: int = 10,
    decision_limit: int = 8,
) -> str:
    """Build standing world state block from the graph.
    
    Selects:
    - Open questions (no supersedes edge pointing at them)
    - Active constraints with high confidence
    - Recent/high-confidence decisions (last N days, confidence >= threshold)
    
    Returns markdown string, possibly empty if graph is empty.
    """
    lines: list[str] = []
    total_tokens = 0
    
    # Fetch candidate nodes from store
    questions = _fetch_open_questions(store, limit=question_limit)
    constraints = _fetch_active_constraints(store, confidence_threshold, limit=constraint_limit)
    decisions = _fetch_recent_decisions(store, confidence_threshold, recency_days, limit=decision_limit)
    
    # Questions section
    if questions:
        lines.append("### Open Questions\n")
        for node in questions:
            line = f"- {node['fact']}"
            if _would_exceed_budget(total_tokens, line, token_budget):
                break
            lines.append(line)
            total_tokens += estimate_tokens(line)
        lines.append("")
    
    # Constraints section
    if constraints:
        lines.append("### Active Constraints\n")
        for node in constraints:
            line = f"- {node['fact']} (confidence: {node['confidence']:.2f})"
            if _would_exceed_budget(total_tokens, line, token_budget):
                break
            lines.append(line)
            total_tokens += estimate_tokens(line)
        lines.append("")
    
    # Decisions section
    if decisions:
        lines.append("### Recent Decisions\n")
        for node in decisions:
            ts = node.get('occurred_at', 'unknown')
            line = f"- [{ts}] {node['fact']}"
            if _would_exceed_budget(total_tokens, line, token_budget):
                break
            lines.append(line)
            total_tokens += estimate_tokens(line)
        lines.append("")
    
    result = "\n".join(lines).strip()
    if result:
        log.debug(
            "Layer0Builder: %d questions, %d constraints, %d decisions (~%d tokens)",
            len(questions),
            len(constraints),
            len(decisions),
            total_tokens,
        )
    
    return result


def _fetch_open_questions(store: GraphStore, limit: int) -> list[dict]:
    """Return open (non-superseded) question nodes."""
    # SQL: SELECT * FROM nodes WHERE type='question' AND id NOT IN (SELECT from_id FROM edges WHERE relation='supersedes')
    # Simpler: fetch all questions, filter in Python
    query = "SELECT id, fact, confidence, occurred_at FROM nodes WHERE type='question' AND is_active=1 ORDER BY occurred_at DESC LIMIT ?"
    rows = store._conn.execute(query, (limit,)).fetchall()
    return [dict(row) for row in rows] if rows else []


def _fetch_active_constraints(store: GraphStore, confidence_threshold: float, limit: int) -> list[dict]:
    """Return active constraint nodes with confidence >= threshold."""
    query = (
        "SELECT id, fact, confidence, occurred_at FROM nodes "
        "WHERE type='constraint' AND is_active=1 AND confidence >= ? "
        "ORDER BY confidence DESC, occurred_at DESC LIMIT ?"
    )
    rows = store._conn.execute(query, (confidence_threshold, limit)).fetchall()
    return [dict(row) for row in rows] if rows else []


def _fetch_recent_decisions(
    store: GraphStore,
    confidence_threshold: float,
    recency_days: int,
    limit: int,
) -> list[dict]:
    """Return recent (or high-confidence) decision nodes."""
    cutoff = None
    if recency_days > 0:
        cutoff = (datetime.utcnow() - timedelta(days=recency_days)).isoformat()
    
    if cutoff:
        query = (
            "SELECT id, fact, confidence, occurred_at FROM nodes "
            "WHERE type='decision' AND is_active=1 AND confidence >= ? AND occurred_at >= ? "
            "ORDER BY confidence DESC, occurred_at DESC LIMIT ?"
        )
        rows = store._conn.execute(query, (confidence_threshold, cutoff, limit)).fetchall()
    else:
        query = (
            "SELECT id, fact, confidence, occurred_at FROM nodes "
            "WHERE type='decision' AND is_active=1 AND confidence >= ? "
            "ORDER BY confidence DESC, occurred_at DESC LIMIT ?"
        )
        rows = store._conn.execute(query, (confidence_threshold, limit)).fetchall()
    
    return [dict(row) for row in rows] if rows else []


def _would_exceed_budget(current_tokens: int, next_line: str, budget: int) -> bool:
    """Check if adding next_line would exceed token budget."""
    return estimate_tokens(next_line) + current_tokens >= budget
```

---

## Config Example

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
    layer0_append_context_footer: false
```

---

## Success Criteria

- Layer 0 block is injected at conversation start, visible in the system prompt before any user message.
- Open questions, active constraints, and recent high-confidence decisions are accurately selected from the graph.
- Layer 0 block respects token budget (soft limit; can exceed by one item if needed).
- Layer 0 is refreshed on `reset()` to reflect current graph state.
- Per-turn retrieval (dynamic context) still works and coexists with Layer 0 without token conflicts.
- System prompt total tokens increase by ~Layer 0 tokens (no double-counting, no loss of context).
- Headless mode (`--print PROMPT`) shows Layer 0 in the system prompt.
- Conversation that starts with no context in the graph produces an empty Layer 0 block (no errors).

---

## Edge Cases & Mitigations

1. **Empty graph:** Layer 0 returns empty string. No error. System prompt degrades gracefully.
2. **All questions/constraints recent:** If all nodes are recent, Layer 0 fills with recent data. Expected behavior.
3. **Confidence threshold too high:** Fewer nodes selected. User can tune down `layer0_confidence_threshold` in config.
4. **Token budget too small:** Items trimmed to fit. Log warning if budget exceeded by >10%.
5. **Conversation reset in rapid succession:** Each `reset()` recomputes Layer 0 fresh. No stale state.
6. **Graph mutation during Layer 0 build:** Read snapshot from store. Subsequent turns may see different dynamic context. Expected.

---

## Open Questions

1. **Layer 0 refresh during conversation?** Spec says static per session, but should the model ask to refresh Layer 0 mid-conversation if it detects new constraints were added? Recommendation: no v1 — keep it simple. The per-turn retrieval catches new context. If the user manually resets, Layer 0 refreshes.
2. **Should Layer 0 include edges (supersedes chain context)?** Recommendation: no v1 — the markdown is facts only. Edges are structural; users care about the nodes themselves. Per-turn retrieval can still traverse edges.
3. **Layered filtering (Layer 0 vs per-turn context)?** Should Layer 0 suppress nodes that will appear in per-turn context? Recommendation: no — Layer 0 is standing state (pinned overview), per-turn is task-specific (detailed). Duplication is fine and expected.
