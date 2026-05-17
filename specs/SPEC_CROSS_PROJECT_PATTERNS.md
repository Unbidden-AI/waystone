# Spec: Cross-project Pattern Detection

**Status:** Draft  
**Priority:** P2 — retention hook; requires cross-project query mode  
**Summary:** Waystone watches across all projects in a workspace. When a developer makes a decision on Project B that they previously made *and rolled back* on Project A, Waystone says so. "You've made this same architectural choice on three projects and rolled it back twice." Nobody can sell that insight. Only a tool that has been watching across sessions can.

---

## Problem

Developers repeat mistakes across projects. The same "we'll use Redis for this" decision appears in four projects, rolls back in two, and the developer never connects the dots because each project lives in its own context silo. Waystone has the data to close that loop — every project's graph is in the same projects directory — but today the query scope is always a single project.

---

## Goal

Add a cross-project analysis layer that detects recurring patterns across projects in a workspace. Specifically:
- Decisions that appear in multiple projects with the same conclusion
- Decisions that appear in multiple projects with *different* conclusions (cross-project conflict)
- Decisions that were made, superseded, and repeated (rollback patterns)

The primary use case is the "rollback pattern" alert: "You chose X on projects A and C. On project B you chose X then switched away from it. Here's why."

---

## Scope

**In scope (v1):**
- `waystone patterns [--workspace DIR]` — scan all projects in the workspace, find recurring decision clusters
- Cross-project matching via tag overlap (same algorithm as `find_conflicts()`)
- Rollback pattern detection: `decision → supersedes → decision` chains where the original decision recurs in another project
- Output: grouped by pattern type (recurring / conflicting / rollback)

**Out of scope for v1:**
- Real-time cross-project hooks (fires only on explicit `waystone patterns` invocation, not at query time)
- Cross-workspace analysis (single `projects_dir` only)
- Pattern persistence / indexing (recomputed fresh each run)
- LLM-assisted pattern labeling (purely structural)

---

## New CLI Interface

```
waystone patterns [--workspace DIR]     # default: projects_dir from config
                  [--min-overlap N]     # min shared tags for matching (default: 2)
                  [--types TYPE,...]    # node types to match (default: decision,transition)
                  [--rollback-only]     # only show rollback patterns
                  [--since ISO8601]     # only consider nodes after this date
                  [-o output.md]
```

### Example output

```
# Cross-project Pattern Report
Workspace: ~/.waystone/projects | 4 projects | 847 nodes

---

## Rollback Patterns (2)

### Pattern: redis-pubsub
Detected in: api-design, auth-system, waystone

  api-design  (2026-01-15) → "Use Redis Pub/Sub for event fan-out" [SUPERSEDED 2026-02-03]
              Superseded by: "Switched to polling — Redis Pub/Sub unreliable on Railway"
  auth-system (2026-02-20) → "Use Redis Pub/Sub for session invalidation" [ACTIVE]
  waystone    (2026-03-01) → "Use Redis Pub/Sub for hook triggers" [SUPERSEDED 2026-03-15]
              Superseded by: "Dropped Redis Pub/Sub — ephemeral networking on Railway"

  ⚠ You have an active decision in auth-system that matches a twice-rolled-back pattern.

---

### Pattern: in-process-cache
Detected in: api-design, data-pipeline

  api-design    (2026-01-10) → "Cache token validation results in-process" [SUPERSEDED 2026-01-28]
                Superseded by: "Moved validation cache to Redis — in-process cache not shared across instances"
  data-pipeline (2026-03-05) → "Cache schema lookups in-process per worker" [ACTIVE]

  ⚠ In-process caching was rolled back in api-design. Review data-pipeline decision.

---

## Recurring Decisions (1 across 3+ projects)

### Pattern: kong-api-gateway
Detected in: api-design, auth-system, waystone (3 projects, consistent)
  "Rate limiting / token validation enforced at Kong gateway layer"
  All three projects reached the same conclusion. No conflicts detected.

---

## Cross-project Conflicts (1)

### Pattern: jwt-expiry
  api-design  → "JWT expiry: 24 hours" (confidence: 0.88)
  auth-system → "JWT expiry: 15 minutes web, 30 days mobile" (confidence: 0.95)
  These projects have active, contradictory decisions about JWT expiry.
  If they share an auth layer, review before proceeding.
```

---

## Implementation

### New function: `scan_workspace_patterns()` in `waystone/retriever.py`

```python
def scan_workspace_patterns(
    project_stores: dict[str, "GraphStore"],  # project_name → GraphStore
    min_tag_overlap: int = 2,
    node_types: set[str] | None = None,
) -> dict:
    """
    Scan multiple project stores for cross-project patterns.
    Returns:
      {
        "rollback": [PatternMatch, ...],
        "recurring": [PatternMatch, ...],
        "conflict": [PatternMatch, ...],
      }
    """
```

**Algorithm — rollback detection:**
1. For each project, extract all `decision` nodes that have at least one outgoing `supersedes` edge. These are "rolled-back" decisions.
2. Build a tag-fingerprint for each rolled-back node: the frozenset of its tags.
3. For each other project, find active `decision` nodes whose tag overlap with the fingerprint is ≥ `min_tag_overlap`.
4. If any match exists in another project, it's a rollback pattern candidate.
5. Include the superseding node's fact text so the "why we rolled it back" reason is shown.

**Algorithm — recurring detection:**
1. Cluster all active `decision` nodes across all projects by tag overlap (≥ `min_tag_overlap`).
2. Groups that appear in 3+ projects with consistent fact polarity = "recurring (consistent)".
3. Groups that appear in 2+ projects with opposing fact polarity = "cross-project conflict".

**Polarity** is computed the same way as in `find_conflicts()` — positive keywords ("use", "chose", "adopt") vs negative keywords ("avoid", "rejected", "dropped", "against").

### New helper: `_load_all_project_stores()` in `waystone/cli.py`

```python
def _load_all_project_stores(config: dict) -> dict[str, GraphStore]:
    """Open read-only connections to all project DBs in the workspace."""
    projects_dir = Path(config.get("projects_dir", "~/.waystone/projects")).expanduser()
    stores = {}
    for db_path in sorted(projects_dir.glob("*/waystone.db")):
        project_name = db_path.parent.name
        stores[project_name] = GraphStore(db_path)
    return stores
```

### New `patterns` command in `waystone/cli.py`

```python
@waystone.command("patterns")
@click.option("--workspace", default=None, help="Override projects_dir from config")
@click.option("--min-overlap", default=2, type=int)
@click.option("--types", "node_types", default="decision,transition")
@click.option("--rollback-only", is_flag=True, default=False)
@click.option("--since", default=None)
@click.option("-o", "--output", default=None)
@click.pass_context
def patterns_cmd(ctx, workspace, min_overlap, node_types, rollback_only, since, output):
    """Detect recurring and rolled-back decision patterns across all projects."""
```

---

## Data Model Notes

No schema changes required. All data lives in existing `nodes` and `edges` tables. Pattern detection is a read-only multi-store scan.

The `supersedes` edge is the key: a rolled-back decision is any node with an outgoing `supersedes` edge. The reason for the rollback is the superseding node's `fact` text.

---

## Performance

At 40K total nodes across 4 projects (10K avg each):
- Loading all nodes for all projects: 4 SQLite full-table scans, < 200ms total.
- Tag fingerprint clustering: O(N²) in the worst case, but N here is the count of `decision`/`transition` nodes, which is typically 5–15% of total nodes. At 2,000 decision nodes across all projects, 2M comparisons = fast enough for a CLI command (< 2 seconds).
- No incremental caching needed for v1.

---

## Configuration

Add to `config.yaml` under a new `patterns` block (all optional):

```yaml
patterns:
  min_tag_overlap: 2
  node_types: ["decision", "transition"]
  rollback_lookback_days: 0    # 0 = no limit
```

---

## Success Criteria

- `waystone patterns` with 4 projects and 800 total nodes completes in < 5 seconds.
- Rollback patterns are detected when: node X in project A was superseded, and node Y in project B has tag overlap ≥ min_overlap with X.
- The "why it was rolled back" reason (superseding node fact) is always shown.
- Cross-project conflicts surface when two projects have active decisions with opposite polarity on the same tag cluster.
- Output is clean markdown, pasteable into a doc or issue comment.

---

## Open Questions

1. Should rollback patterns fire at `waystone extract` time (inline warning)? Recommendation: not v1 — keep it as an explicit audit command. The cross-project scan at extract time adds latency and may be noisy when a project is just starting.
2. Should patterns be stored as edges (`cross_project_match` relation)? Recommendation: no — patterns are ephemeral analysis, not facts. The graph stores first-class knowledge; derived analysis belongs in CLI output.
3. What's the right `min_overlap` default? Tag overlap of 2 is the same as `find_conflicts()`. 3 would reduce noise at the cost of missing some patterns. Default to 2, let the user tune up.
