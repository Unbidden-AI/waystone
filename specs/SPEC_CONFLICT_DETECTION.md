# Spec: Conflict Detection

**Status:** Draft  
**Priority:** P0 — core product differentiator  
**Summary:** When Waystone retrieves context for a decision, surface any prior decisions that contradict it — proactively, without being asked.

---

## Problem

Developers re-litigate the same decisions. Teams make inconsistent architecture calls across sessions. The LLM sees only what's in the current context window; it has no way to know that three weeks ago this service rejected the exact technology it's now recommending. Waystone has all of that information in the graph — it just doesn't surface contradictions.

---

## Goal

When a new `decision` or `transition` node is being written to the graph — or when the user queries for context — Waystone checks whether any existing decision nodes contradict the incoming fact and surfaces them as a flagged warning before the LLM responds.

One catch and the user understands the product in 10 seconds.

---

## Scope

**In scope:**
- Conflict detection at query time (surface warnings in retrieval output)
- Conflict detection at extraction time (flag in hook output)
- `waystone conflicts <project>` — standalone CLI command to audit the full graph for contradictions
- Phase 1: tag-overlap heuristic (no new dependencies)
- Phase 2: semantic similarity (requires `sqlite-vec`, optional)

**Out of scope for v1:**
- Auto-resolving conflicts
- User-facing conflict resolution UI
- Creating `conflicts_with` edges automatically (manual tagging only)
- Trained classifier for contradiction detection

---

## Data Model

No schema changes required for Phase 1. Phase 2 uses the existing `vec_nodes` virtual table (already in `store.py` when `semantic=true`).

Optional new edge relation: `conflicts_with` — can be written manually by the LLM or user after a conflict is surfaced. Already supported by the edge schema (freeform `relation` string).

---

## Implementation: Phase 1 — Tag Overlap Heuristic

### New function: `find_conflicts()` in `waystone/retriever.py`

```python
def find_conflicts(
    store: GraphStore,
    candidate_tags: list[str],
    candidate_fact: str,
    exclude_ids: set[str] | None = None,
    min_tag_overlap: int = 2,
) -> list[Node]:
    """Return active decision/transition nodes whose tags overlap with
    candidate_tags by at least min_tag_overlap, excluding exclude_ids.

    These are *potential* conflicts — the caller or LLM determines whether
    the facts actually contradict each other. No NLP required at this layer.
    """
```

**Algorithm:**
1. Call `store.get_nodes_by_tags(candidate_tags, node_types=["decision", "transition"])` for each tag pair combination until we have candidates.
2. Filter to nodes with `len(set(node.tags) & set(candidate_tags)) >= min_tag_overlap`.
3. Exclude `exclude_ids` (e.g., the nodes just extracted in this pass).
4. Return sorted by overlap count descending, then confidence descending.

### Integration point 1: query-time (retrieval output)

In `retrieve_with_stats()`, after BFS traversal and strategy pipeline, call `find_conflicts()` using the query's extracted keywords as `candidate_tags`. Prepend conflict nodes to the output as a separate section:

```markdown
## ⚠ Potential Conflicts

The following prior decisions may conflict with this query context. Verify before proceeding.

**[decision]** Decided against Redis for the notification service due to deployment complexity on Railway. (confidence: 0.92)
  Tags: redis, notification-service, deployment, railway
  Recorded: 2026-03-14

---
```

Conflict section only appears if `find_conflicts()` returns ≥1 result. Controlled by a new strategy flag: `conflict_detection: true` (default on).

### Integration point 2: extraction-time (hook output)

In `hooks/waystone_submit.py`, after `retrieve_with_stats()` returns, also run `find_conflicts()` against the top candidate tags from the prompt. If conflicts are found, append a `## ⚠ Potential Conflicts` section to the context block injected into the LLM prompt.

This is the proactive mode: the developer hasn't asked about Redis — Waystone notices that the current prompt contains tags that overlap with a prior "decided against Redis" node and flags it before the LLM responds.

### Integration point 3: `waystone conflicts` CLI command

```
waystone conflicts <project> [--min-overlap N] [--type decision|transition] [-o output.md]
```

Scans the full graph for node pairs with ≥N overlapping tags and opposite-polarity keywords. Outputs a report grouping potential conflict pairs by component/technology cluster.

**Opposite-polarity signal (simple heuristic):**
- "against", "rejected", "dropped", "removed", "decided not to", "chose not to" → negative polarity
- "decided", "chose", "adopted", "switched to", "use", "selected" → positive polarity
- If one node in a pair has negative polarity and the other has positive polarity on the same tags → flag as HIGH CONFIDENCE conflict
- Otherwise → flag as POTENTIAL conflict (same tags, polarity unknown)

---

## Implementation: Phase 2 — Semantic Similarity (optional, requires `semantic` extra)

When `strategies.semantic: true` and `sqlite-vec` is loaded:

1. Embed the incoming query/fact using the existing `SentenceTransformer` pipeline.
2. Run ANN search: `store.search_vec(embedding, top_k=20, node_types=["decision", "transition"])`.
3. Among ANN results with cosine similarity > 0.65, apply the opposite-polarity heuristic.
4. Merge with Phase 1 results (deduplicate by node ID).

Phase 2 catches conflicts that use different terminology for the same concept (e.g., "Postgres" vs "relational database") without tag overlap.

---

## Config

New key in `config.yaml` under `strategies`:

```yaml
strategies:
  conflict_detection: true        # Surface prior contradicting decisions (default: true)
  conflict_min_tag_overlap: 2     # Min shared tags to consider a conflict candidate
  conflict_semantic: false        # Use ANN similarity in addition to tag overlap (requires semantic extra)
```

CLI overrides: `--enable conflict_detection` / `--disable conflict_detection`

---

## Output Format

Conflict warnings appear as a fenced section above the main context block. They do NOT replace retrieved nodes — they supplement them. Format:

```
⚠ POTENTIAL CONFLICT — 3 overlapping tags: [redis, notification-service, deployment]

Prior decision (2026-03-14, confidence 0.92):
  "Decided against Redis for the notification service due to deployment complexity on Railway."

Current query context includes: redis, notification-service

Confirm this decision is intentional or retrieve the prior rationale before proceeding.
```

---

## Success Criteria

- A query that includes tags overlapping with a "decided against X" node surfaces the prior decision in the output.
- The conflict section is omitted when no overlap exists (no false-positive noise on unrelated queries).
- `waystone conflicts` produces a readable audit report with no false positives at `min_overlap=2`.
- Conflict detection adds < 10ms to query latency at the tag-overlap level (Phase 1 uses existing `get_nodes_by_tags` LIKE queries, no new DB access pattern).

---

## What We Are Not Building

- No automatic conflict resolution. The LLM sees the conflict and the user decides.
- No `conflicts_with` edges written automatically. The graph doesn't mutate on a read.
- No UI. This is CLI + hook output only for v1.
- No trained classifier. The heuristic is good enough to ship and provides training data for a future classifier.

---

## Open Questions

1. Should conflict warnings appear in `waystone query` output by default, or only when `--enable conflict_detection` is passed? Recommendation: on by default, suppressible.
2. Should Phase 2 (semantic) be enabled in hook mode? Current constraint: semantic=false in hooks due to 3.5s cold-load. Conflict detection should respect this constraint.
3. Should `conflicts_with` edges be a first-class relation in the schema, or remain informal? Recommendation: add to the valid relation enum for v1 so users can manually tag them; auto-creation can come later.
