# Spec: Institutional Memory Export

**Status:** Draft  
**Priority:** P1 — direct upsell path to Team tier  
**Summary:** `waystone export <project> --format briefing` generates a structured, human-readable document that captures everything Waystone knows about a project — organized for someone who wasn't in any of the sessions.

---

## Problem

When a new developer joins a project, or when a project is handed off, the context built up across months of sessions is trapped in a SQLite graph that only Waystone knows how to read. The existing `waystone export` command dumps all nodes as markdown but gives no structure — it's a data dump, not a briefing. A new developer can't orient themselves from it.

This also matters commercially: teams pay for shared institutional memory immediately because the alternative is weeks of onboarding conversations re-discovering the same constraints and decisions.

---

## Goal

One command produces a complete project briefing: what was built, why, what was rejected, what's still open. Structured for human reading and for LLM context injection. Can be sent as a document, pasted into a new session, or printed and handed to a new team member.

---

## Scope

**In scope:**
- `--format briefing` option on existing `waystone export` command
- `--format handoff` variant (condensed, LLM-optimized)
- Supersedes chain rendering (show full decision evolution, not just current state)
- Component-grouped output (group nodes by their primary tag cluster)
- Confidence indicators (show high-confidence facts prominently, note low-confidence ones)

**Out of scope for v1:**
- PDF generation (markdown → PDF is a user concern; we emit clean markdown)
- Interactive HTML output
- Automatic summarization via LLM (the export is purely graph-driven, no API calls)
- Incremental export (only changes since date X)

---

## New CLI Interface

The existing `waystone export` command gains a `--format` option:

```
waystone export <project> [--format dump|briefing|handoff] [-o output.md]
                          [--include-superseded] [--since ISO8601]
```

| Format | Description |
|--------|-------------|
| `dump` | Current behavior — all nodes as flat markdown, no structure (default for backward compat) |
| `briefing` | Structured document organized for human reading (new) |
| `handoff` | Condensed version optimized for LLM context injection — fits in ~4K tokens (new) |

---

## Briefing Format Structure

### Section 1: Project Header

Auto-generated from graph metadata:
- Project name
- Total node count, edge count
- Date range (oldest node `occurred_at` → newest)
- Export timestamp

### Section 2: Executive Summary (briefing only)

The 5 highest-confidence `decision` nodes across the project — not grouped, just the most important things to know. Each shown as a single sentence with confidence score.

### Section 3: Architecture Decisions

All `decision` nodes, grouped by their **primary tag** (the tag with the most co-occurring nodes — a proxy for component/domain). Within each group, sorted by `occurred_at` ascending (chronological). Superseded decisions are shown in a collapsible subsection labeled "Superseded" — not hidden, but de-emphasized.

Format per decision:
```markdown
### decisions: auth-service

- **JWT with RS256 chosen for auth tokens** (confidence: 0.95, 2026-03-14)
  Rationale: Symmetric HS256 rejected — can't verify without sharing the secret across services.
  → supersedes: "use HS256 for auth tokens" (2026-02-28)

- **Session expiry set to 15 minutes for web, 30 days for mobile** (confidence: 0.90, 2026-03-18)
```

### Section 4: Active Constraints

All `constraint` nodes that are currently active (not superseded). Grouped by primary tag. These are hard limits the project operates under — things a new developer must not violate.

### Section 5: Implementation Notes

All `implementation` nodes, grouped by primary tag, sorted chronologically. These describe how things are built — useful for "why is it built this way" questions.

### Section 6: Transitions (What Changed and Why)

All `transition` nodes — the "we used to do X, now we do Y" nodes. Shown as a timeline. Each entry shows the before state, the after state, and the date of the transition.

```markdown
### Transitions

| Date | From | To | Component |
|------|------|----|-----------|
| 2026-03-10 | JSON logging | Structured JSON via structlog | api-service |
| 2026-03-22 | PostgreSQL | CockroachDB | data layer |
```

### Section 7: Open Questions

All `question` nodes not marked `resolved`. Grouped by primary tag. These are explicit unknowns — things the team hasn't decided yet.

### Section 8: Decision History (Supersedes Chains)

For each active node that supersedes ≥1 prior node, render the full chain showing how the decision evolved. This is the most valuable section for onboarding — it shows not just what was decided but the path to get there.

```markdown
### Evolution: auth-token-format

1. 2026-02-10 — "Use HS256 for auth tokens" (confidence: 0.80) [SUPERSEDED]
   Reason superseded: symmetric key can't be verified without sharing secret cross-service
2. 2026-03-14 — "JWT with RS256 chosen for auth tokens" (confidence: 0.95) [ACTIVE]
```

---

## Handoff Format (condensed)

Designed to fit in ~4,000 tokens for injection into a new LLM session as context. Same structure as briefing but:
- Executive summary: top 3 decisions only
- Architecture decisions: one line per node (no rationale text)
- Constraints: full (never truncate constraints)
- Transitions: list view only, no detail
- Open questions: full (always show what's unresolved)
- No decision history section (space constraints)
- No confidence scores (reduces token count)

Token budget enforced via existing `apply_token_budget()` with a 4,000-token cap.

---

## Implementation

### New function: `assemble_briefing()` in `waystone/retriever.py`

```python
def assemble_briefing(
    nodes: list[Node],
    edges: list[Edge],
    project: str,
    format: Literal["briefing", "handoff"] = "briefing",
    include_superseded: bool = True,
) -> str:
    """Assemble a structured briefing document from a full graph export."""
```

**Algorithm:**
1. Separate nodes by type into buckets: decisions, constraints, implementations, transitions, questions, resolved.
2. For decisions: build supersedes chains by following `supersedes` edges. Mark superseded nodes.
3. Compute primary tag for each node: the node's tag that appears most frequently across all nodes (tag frequency ranking). This becomes the grouping key.
4. Group each bucket by primary tag.
5. Render each section in order, skipping empty sections.
6. For `handoff` format, apply token budget after assembly and truncate non-critical sections.

### Changes to `waystone/cli.py`

Add `--format` option to the existing `export` command:

```python
@click.option("--format", "fmt", type=click.Choice(["dump", "briefing", "handoff"]),
              default="dump", help="Output format")
@click.option("--include-superseded", is_flag=True, default=True,
              help="Include superseded nodes in briefing (default: true)")
```

When `fmt in ("briefing", "handoff")`:
1. Fetch all nodes (`store.get_all_nodes()`) AND all edges (`store.get_all_edges()`).
2. Call `assemble_briefing(nodes, edges, project, format=fmt, include_superseded=include_superseded)`.
3. Write to output path.

### New store method: `get_all_edges()`

The existing store doesn't have a `get_all_edges()` method — needed for supersedes chain reconstruction.

```python
def get_all_edges(self) -> list[tuple[str, str, str]]:
    """Return all edges as (from_id, to_id, relation) tuples."""
    rows = self._conn.execute(
        "SELECT from_id, to_id, relation FROM edges"
    ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]
```

---

## Primary Tag Algorithm

A node's "primary tag" is used for grouping. The algorithm:

1. Build a global tag frequency table: count how many nodes have each tag.
2. For each node, select the tag with the highest global frequency as the primary tag. This clusters nodes by their most commonly co-occurring concept (e.g., "auth-service" appears on 23 nodes → it's a stronger grouping signal than "jwt" which appears on 6).
3. Fallback: if a node has no tags, group it under `(untagged)`.

---

## Example Output (briefing)

```markdown
# Project Briefing: auth-system
Generated: 2026-05-17 | 89 nodes | 134 edges | Active since: 2026-02-10

---

## Executive Summary

The five most important decisions in this project:

1. JWT with RS256 chosen for auth tokens — HS256 rejected due to cross-service secret sharing risk. (0.95)
2. Session expiry: 15 minutes web, 30 days mobile with refresh token rotation. (0.92)
3. Auth service deployed on Railway; cannot use Redis Pub/Sub due to Railway's ephemeral networking. (0.91)
4. Rate limiting enforced at Kong API Gateway, not application layer. (0.88)
5. Postgres chosen over MySQL; CockroachDB migration planned for Q3. (0.85)

---

## Architecture Decisions

### auth-service

- **JWT with RS256 chosen for auth tokens** (0.95 | 2026-03-14)
  → supersedes: "use HS256" (2026-02-28)
...

## Active Constraints

### railway

- **Redis Pub/Sub not available** — Railway's ephemeral networking breaks persistent pub/sub connections. (0.91)
...

## Open Questions

### billing

- How should we handle partial-period proration when a user upgrades mid-cycle? (0.80)
...

## Decision History

### auth-token-format

1. 2026-02-28 — Use HS256 for JWT signing [SUPERSEDED]
2. 2026-03-14 — Use RS256; HS256 rejected due to cross-service secret sharing [ACTIVE]
```

---

## Success Criteria

- `waystone export <project> --format briefing` runs in < 2 seconds on a 40K-node graph.
- The output is self-contained — a new developer can read it without access to the graph.
- `--format handoff` output fits in ≤ 4,000 tokens (verified via `tiktoken`).
- Supersedes chains are correctly reconstructed for all tested projects.
- No LLM API calls are made during export — the command works offline.

---

## Open Questions

1. Should `--format briefing` be the new default instead of `dump`? Recommendation: no — keep `dump` as default for backward compatibility, but document `briefing` as the recommended format.
2. Should the handoff format include a "how to use Waystone" preamble explaining the tool to an LLM that's never seen it? Recommendation: yes, a 3-line preamble as a comment block at the top.
3. Should `get_all_edges()` be paginated for very large graphs? At 40K nodes the edge count could be 60K+. Recommendation: fetch all, it's a single SQLite scan and fits in memory easily at this scale.
