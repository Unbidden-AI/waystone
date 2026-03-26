# Graph Hygiene Plan

Two complementary improvements to prevent noise accumulation and allow surgical cleanup.

---

## Problem 1: Meta-discussion noise extraction

**Root cause:** The Stop hook and the orchestrator's own extraction pipeline both fire on sessions where CB itself is the subject of discussion. This produces nodes like:
- "A test query is proposed: 'What models were benchmarked?'"
- "The orchestrator extracts keywords from the query"
- "GPT-4o-mini was considered and tested" (hallucinated during an orchestrator debug session)

These nodes appear with `source: live:N` (orchestrator sessions) or `source: assistant:N` (Claude Code sessions about CB).

**Fix: extraction prompt rule**

Add Rule 14 to both `EXTRACTION_PROMPT` and `INCREMENTAL_EXTRACTION_PROMPT` in `prompts.py`:

> Do NOT extract facts that describe the operation of the extraction/retrieval system itself — e.g. "a test query was run", "synthesis was executed", "retrieval returned X nodes", "a query was proposed", or real-time debugging observations about context retrieval behavior. These are transient operational artifacts, not durable project knowledge. Benchmark results, architectural decisions, and analytical findings ARE still valid facts.

**Why two prompts:** `EXTRACTION_PROMPT` is used for full-transcript batch extraction; `INCREMENTAL_EXTRACTION_PROMPT` is used for per-turn extraction (the hook path). Both need the rule since both produce noise.

**Risk:** Low. The rule targets a specific class of meta-operational content. Benchmark results ("Gemini 2.5 Flash = 95% recall") are explicitly excluded from the rule's scope.

---

## Problem 2: No way to clean up existing graph noise

**Root cause:** There's no CLI command to selectively remove nodes by age, confidence, or source. Manual deletion requires raw SQLite access.

**Fix: `engram prune` command**

```
engram prune <project> [--older-than DAYS] [--confidence-below FLOAT]
                    [--source PATTERN] [--execute]
```

- Runs in **preview mode by default** — prints what would be removed, with facts truncated to 80 chars.
- Requires `--execute` to actually delete.
- All criteria are ANDed: `--older-than 90 --confidence-below 0.5` removes nodes that are BOTH old AND low-confidence.
- `--source` does substring match on `source_transcript` (e.g. `live` matches `live:0`, `live:12`, etc.)

**Immediate use cases:**
- `engram prune ContextBroker --source live --execute` → remove all orchestrator-session noise
- `engram prune ContextBroker --older-than 180 --confidence-below 0.4 --execute` → purge stale low-signal nodes
- `engram prune ContextBroker --older-than 90 --dry-run` → audit before committing

**Implementation:**
- `store.py`: add `prune_nodes(older_than_days, confidence_below, source_pattern, dry_run) -> list[str]`
- `cli.py`: add `prune_cmd` after `reconcile_cmd`

---

## What this does NOT fix

- Hallucinated facts that look like legitimate project facts (no automated way to detect)
- Synthesis near-duplicate nodes (addressed by `engram reconcile` + dedup)
- Retrieval quality degradation at 20K+ nodes (addressed later by vector DB)

---

## Future: `engram stats` (not in scope now)

A graph health dashboard showing:
- Node count by age bucket (< 7d, 7-30d, 30-90d, 90d+)
- Node count by confidence bucket
- Node count by source type
- Superseded vs. active ratio

This would make drift visible before it hurts retrieval quality.
