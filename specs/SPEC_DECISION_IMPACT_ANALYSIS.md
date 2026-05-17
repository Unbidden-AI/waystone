# Spec: Decision Impact Analysis

**Status:** Draft  
**Priority:** P1 — demo closer, graph-unique feature  
**Summary:** When a new decision node is written (or any decision is queried), Waystone traces the graph forward from that node and reports which other constraints, decisions, and implementation notes it intersects. "This change to your auth approach intersects 5 other constraints you've recorded. Here are the ones that may need revisiting."

---

## Problem

Every non-trivial decision has downstream consequences. In a large project graph, those consequences are distributed across dozens of nodes that share no obvious textual connection — they're linked by edges. Today, `waystone query` returns subgraph context for the current task but gives no signal about the blast radius of a proposed decision. A developer makes a call, doesn't know it touches 3 constraints they recorded six weeks ago, and discovers the conflict at code review.

---

## Goal

Given a decision node ID (or a proposed decision text), traverse the graph forward via `depends_on` and `flows_to` edges, collect all reachable nodes of type `decision`, `constraint`, and `implementation`, and report them as an impact map. The LLM (or developer) then knows what to re-examine before proceeding.

---

## Scope

**In scope (v1):**
- `waystone impact <project> <node_id>` — impact map for an existing node
- `waystone impact <project> --query "<text>"` — impact map for a proposed decision (BFS from tag-matched entry nodes)
- Forward traversal via `depends_on` and `flows_to` edges (outgoing from the target node)
- Backward traversal option (`--reverse`) via incoming `depends_on` edges — "what does this node depend on?"
- Output: grouped by node type, with edge path shown (how we got there)
- Integration with `waystone query`: append impact section when a retrieved node has known dependents

**Out of scope for v1:**
- Impact scoring / weighting edges by criticality
- Visual graph rendering (ASCII or otherwise)
- Automatic "needs revisiting" flag written back to the DB
- Cross-project impact

---

## New CLI Interface

```
waystone impact <project> <node_id>|--query "<text>"
               [--hops N]          # traversal depth (default: 3)
               [--reverse]         # traverse incoming edges instead of outgoing
               [--types TYPE,...]  # filter output to these node types (default: decision,constraint,implementation)
               [--format markdown|json]
               [-o output.md]
```

### Example output

```
Impact Analysis: n_a1b2c3d4
Fact: "Switched auth token format to RS256 JWT"
Type: decision | Tags: auth, jwt, rs256

Directly connected (hop 1):
  [constraint] Redis Pub/Sub not available on Railway — must use polling (via depends_on)
  [decision]   Session expiry set to 15 min web / 30 days mobile (via flows_to)

Reachable at depth 2:
  [implementation] Refresh token rotation implemented in auth-service/token.py (via flows_to → depends_on)
  [constraint]     All tokens must be verifiable without shared secrets across services (via depends_on)

Reachable at depth 3:
  [decision]   API gateway (Kong) handles token validation — app servers never see raw JWT (via flows_to → flows_to)

Summary: 5 nodes intersected across 2 decision, 2 constraint, 1 implementation.
Review the constraints above before proceeding.
```

---

## Implementation

### New function: `compute_impact()` in `waystone/retriever.py`

```python
def compute_impact(
    store: "GraphStore",
    node_ids: list[str],
    hops: int = 3,
    reverse: bool = False,
    node_types: set[str] | None = None,
) -> dict:
    """
    Traverse the graph from node_ids via depends_on/flows_to edges.
    Returns a dict keyed by hop depth (1, 2, 3) → list of (node, path) tuples.
    """
```

**Algorithm:**
1. Load the target nodes from the store.
2. Run a modified BFS that tracks the **edge path** to each reached node (e.g., `["flows_to", "depends_on"]`).
3. Traverse only `depends_on` and `flows_to` edges (outgoing, or incoming if `reverse=True`).
4. Collect all reachable nodes within `hops` depth.
5. Filter to `node_types` (default: `decision`, `constraint`, `implementation`).
6. Exclude the seed nodes from results.
7. Group by hop depth.

**Note:** Do NOT traverse `supersedes` or `conflicts_with` edges — those are relational, not dependency edges. Impact should only follow the dependency/data-flow graph.

### New function: `format_impact_report()` in `waystone/retriever.py`

```python
def format_impact_report(
    seed_nodes: list[dict],
    impact: dict,  # {depth: [(node, path), ...]}
    fmt: str = "markdown",
) -> str:
```

Renders the impact map as clean markdown (or JSON for tooling use). Includes:
- Seed node header (fact, type, tags)
- Per-depth sections with edge paths shown
- Summary line (N nodes intersected, broken down by type)

### Changes to `waystone/cli.py`

New `impact` command registered on the `waystone` group:

```python
@waystone.command("impact")
@click.argument("project")
@click.argument("node_id", required=False)
@click.option("--query", "query_text", default=None)
@click.option("--hops", default=3, type=int)
@click.option("--reverse", is_flag=True, default=False)
@click.option("--types", "node_types", default="decision,constraint,implementation")
@click.option("--format", "fmt", type=click.Choice(["markdown", "json"]), default="markdown")
@click.option("-o", "--output", default=None)
@click.pass_context
def impact_cmd(ctx, project, node_id, query_text, hops, reverse, node_types, fmt, output):
    """Trace what a decision touches — show the blast radius before proceeding."""
```

**Logic:**
- If `node_id` given: load that node directly as the seed.
- If `--query` given: run tag-based BFS entry node lookup (same as `waystone query`) to find matching nodes, use those as seeds.
- Exactly one of `node_id` or `--query` must be provided; error if neither or both.

### Integration with `waystone query` (Phase 2)

After the primary BFS retrieval in `retrieve_context()`, for each retrieved `decision` node check if it has any outgoing `depends_on` / `flows_to` edges. If the impact count is > 0, append a compact impact note to the node's display:

```
> Impact: this decision has 3 known dependents — run `waystone impact <project> <id>` for the full map.
```

This is a breadcrumb, not a full traversal, to keep query output readable.

---

## What `depends_on` and `flows_to` mean for impact

- `depends_on`: B depends on A → changing A may break B. Forward traversal: "what does this node affect?"
- `flows_to`: A flows_to B → data or control flows forward from A to B. Forward traversal: "what is downstream of this?"

These two relations together capture the dependency + data-flow graph. `relates_to` edges are informational and not traversed for impact (too loose — every node relates to something).

---

## Success Criteria

- `waystone impact <project> <node_id>` completes in < 500ms on a 40K-node graph.
- Edge path is shown for each result so the developer can understand the connection.
- `--reverse` correctly inverts the traversal direction.
- When `--query` is provided, entry node selection matches the `waystone query` tag-matching logic exactly.
- No LLM calls — this is a pure graph traversal.

---

## Open Questions

1. Should impact traversal follow `conflicts_with` edges in the reverse direction? Recommendation: no for v1 — keep it dependency-only. Conflict surfacing is handled by `find_conflicts()`.
2. Should impact results be cached / stored as edges? Recommendation: no — traversal is fast enough and results change as the graph evolves.
3. Should the `waystone query` integration (Phase 2) be a config flag? Recommendation: yes — `impact_hints: true` in the `strategies` block, defaulting to false until the UX is validated.
