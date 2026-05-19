# LOCOMO Benchmark

*Waystone vs. the field — dataset overview, current results, and improvement roadmap.*

---

## What LOCOMO Tests

**Dataset**: snap-research/locomo — 10 long-term personal conversations, ~27 sessions each, ~22 turns/session, ~199 QA pairs per conversation. Each conversation spans 6-12 simulated months. The benchmark tests whether a memory system can answer questions about a person's life and relationships across sessions.

**QA categories** (verified against `locomo10.json` — note: our code previously had these wrong):
| Category | ID | Count (10 convs) | What it tests |
|---|---|---|---|
| Single-hop | 1 | 282 | Direct single-session recall — names, numbers, stated facts |
| Temporal | 2 | 321 | "When did X happen?", date ordering, relative time |
| Open domain | 3 | 96 | Inference / reasoning over stored facts |
| Multi-hop | 4 | 841 | Facts spanning multiple sessions, cross-session synthesis |
| **Adversarial** | **5** | **446** | **Contradictory / negation — should return "not mentioned" or latest state. Uses `adversarial_answer` field, NOT `answer`. EXCLUDED from official scoring.** |

**Important**: the official LOCOMO protocol **excludes category 5** (adversarial) from accuracy calculations. Correct denominator = cat 1–4 questions only. Always run with `--categories 1 2 3 4`.

**Scoring:**
- **Keyword recall** (fast, no API cost): tokenize answer, check exact + partial word overlap ≥ 0.7
- **LLM judge** (accurate, gemini-2.5-flash-lite): YES=1.0 / PARTIAL=0.5 / NO=0.0

---

## Current Results (conv-26, April 2026)

| Config | Keyword | LLM Judge | Avg tokens |
|---|---|---|---|
| `waystone_default` | **57.3%** | **48.7%** | 746 |
| `waystone_semantic` (vector only) | — | 46.2% | — |
| `waystone_combined` (keyword + vector) | — | 55.3% | — |

**Why LLM score (48.7%) < keyword score (57.3%)**: LLM judge penalizes partial context; keyword recall is generous on word overlap. The LLM score is the truthful one.

---

## Competition Landscape (10-conv subset, LLM judge)

| System | Score | Method |
|---|---|---|
| **Hindsight** | **89.6%** | 4-channel RRF: semantic + BM25 + graph + temporal — current SOTA |
| MemMachine | 84.9% | Dual episodic/semantic memory |
| ENGRAM paper (arXiv:2511.12960) | ~80% | Separate academic project, unrelated to this codebase |
| **Zep** (corrected, self-reported) | **75.1% ± 0.17** | After removing adversarial category; Mem0's re-eval of Zep puts it at ~58% (disputed) |
| **Full context** | ~73% | Entire transcript injected |
| Mem0 graph (corrected) | **68.4%** | Independently measured; self-reported 87–90% is disputed |
| **Waystone (us, conv-26)** | **48.7%** (LLM) | `waystone_default`, April 2026 |

**Target**: ≥ 75% (beat Zep), stretch goal ≥ 89% (match Hindsight)

**Key insight**: Mem0's self-reported 87–90% is disputed — independent measurement puts them at 68.4%, below full context. Hindsight (89.6%) is the actual SOTA and uses the same 4-channel RRF we're building toward. BM25 is now implemented; semantic + temporal remain.

### The Zep/Mem0 Controversy

Zep originally published **84% accuracy** on LOCOMO, but Mem0's co-founder filed a rebuttal identifying a critical methodology error: Zep included adversarial (category 5) questions in the denominator. The official LOCOMO protocol **excludes category 5**. This inflated Zep's score by approximately **+25.56 percentage points**.

Mem0 re-ran Zep with the correct methodology and reported **58.44% ± 0.20**. Zep disputed this and published a corrected self-evaluation of **75.14% ± 0.17**, attributing the gap to three implementation errors in Mem0's test harness (user-model misconfiguration, timestamp handling, sequential vs. parallel search).

**What this means for Waystone**: always exclude category 5 (`--categories 1 2 3 4`), run 3+ trials and report mean ± std, and document config/domain/extraction model explicitly when publishing scores.

---

## Benchmark Harness

```
benchmarks/locomo/
├── harness.py                  # main entry point
├── ablation_configs.py         # ABLATION_CONFIGS dict
├── splits.py                   # DEV / TEST / ALL conversation splits
├── loaders/
│   ├── locomo_dataset.py       # LocomoDataset, LocomoConversation, QAPair
│   └── ingestion_pipeline.py   # ingest_conversation() → GraphStore
└── evaluation/
    ├── scoring.py               # score_keyword_recall(), score_llm_judge(), aggregate()
    └── token_counter.py         # TokenBudget, estimate_tokens()
```

**Domain profile** (`episodic_personal`) is fully defined in `waystone/domain_profiles.py`:
- Node types: `event`, `person`, `place`, `fact`, `plan`, `outcome`, `preference`, `relationship_update`
- Edge relations: `involves`, `located_at`, `follows`, `updates`, `references`
- `node_types_note` includes the anchor-node rule: every named person gets exactly one `person` node, and every fact gets that person's name in its tags.

**Baseline benchmark command**:
```bash
python -m benchmarks.locomo.harness \
  --dataset benchmarks/locomo/data/locomo10.json \
  --configs full_context waystone_default \
  --split dev \
  --categories 1 2 3 4 \
  --llm-judge \
  --output benchmarks/results/locomo_baseline_$(date +%Y%m%d).json
```

**Quick validation (conv-26 only, keyword scoring)**:
```bash
python -m benchmarks.locomo.harness \
  --dataset benchmarks/locomo/data/locomo10.json \
  --configs waystone_default <new_config> \
  --quick
```

---

## Implementation Status

### Phase 1 — COMPLETE ✓

| Item | File |
|---|---|
| Temporal context injection (session boundary markers) | `ingestion_pipeline.py:123-130` |
| Session-pure extraction (one `_extract_chunk` call per session) | `ingestion_pipeline.py:132-134` |
| `extraction_focus` field in `DomainProfile` | `domain_profiles.py` |
| `episodic_personal.extraction_focus` + `layer1_rules` + few-shot examples | `domain_profiles.py` |

### Phase 2 — COMPLETE ✓

| Item | File |
|---|---|
| sqlite-vec semantic search | `store.py` |
| At-insert semantic dedup (cosine ≥ 0.92) | `store.py:add_node()` |
| Hybrid retrieval (tag + vector) | `extractor.py:extract()` |
| `embed_missing_nodes()` at ingestion end | `ingestion_pipeline.py:140` |
| BM25 FTS5 + RRF multi-channel retrieval | `store.py`, `retriever.py` |

### Remaining (Phase 3+)

- Structured `event_date` field (temporal ordering in retrieval output)
- Session-scoped filtering (`session_id` on nodes)
- Adversarial / contradiction verification against category-5 questions
- **Run full dev split baseline** to establish per-category numbers

---

## Root Cause Analysis

Waystone's architecture is sound (DAG + BFS + strategy pipeline), but five gaps are specific to **episodic personal conversations**:

1. **Entity-centric retrieval incomplete** — Queries like "What did Sarah eat?" fail because "eat" and "food/meal" are not synonyms in the keyword index. Semantic search actively hurts (46% vs 57%) because generic embeddings can't capture entity-specific context.

2. **Extraction tuned for software dev** — Retrieval strategies are NOT adapted for personal facts: no person anchoring in BFS seeding, no temporal bucketing, too-aggressive recency decay for LOCOMO's long-span corpora.

3. **Query expansion absent** — "When did John's sister visit?" yields `["john", "sister", "visit"]` with zero synonym expansion. "Visit" could be "came", "trip", "dropped by" — none attempted.

4. **Temporal is present but dormant** — Session dates flow into extraction correctly; `layer1_rules` rule 5 mandates date resolution. But retrieval output doesn't sort by date, doesn't surface a timeline, and lacks temporal proximity scoring.

5. **Multi-hop rewards invisible** — BFS traversal is correct, but distant 2+ hop facts get buried below closer 1-hop neighbors regardless of relevance.

---

## Ranked Improvement Opportunities

### 1. Entity-Scoped Query Expansion (+3–5%, 1–2 days)
**File**: `waystone/retriever.py:extract_keywords()`

Enhance keyword extraction to detect named entities (capitalized tokens) and expand queries with LOCOMO-specific verb synonyms:
- "eat" → ["eat", "consume", "food", "meal", "ate"]
- "visit" → ["visit", "came", "trip", "traveled", "dropping by"]
- "work" → ["work", "job", "employed", "career"]
- "live" → ["live", "location", "moved", "home"]
- "married/dating" → ["married", "wedding", "engaged", "relationship", "together"]

Named-entity detection: capitalize-heuristic (`[w for w in text.split() if w[0].isupper()]`).

**Risk**: Low — additive. Software dev queries still match their existing tags.

---

### 2. Person-Anchored BFS Seeding (+2–3%, 1 day)
**File**: `retriever.py:retrieve_with_stats()` (lines 156–207)

When `person_anchoring=True`: person-typed nodes with ≥1 keyword-tag match become automatic high-confidence seeds, bypassing the ≥2 keyword-overlap requirement. Person nodes gate access to all person-scoped facts downstream.

```python
person_anchors = [n for n in entry_nodes if n.get("type") == "person"
                  and _count_keyword_tag_hits(n.get("tags", []), keyword_set) >= 1]
high_overlap = list(dict.fromkeys(high_overlap + person_anchors))
```

**Risk**: None — person anchors are always valid.

---

### 3. Temporal Proximity Boosting (+3–5%, 3–4 days)
**Files**: `waystone/store.py`, `retriever.py:assemble_markdown()`

Two parts:
1. **Schema**: Add `event_date TEXT` column to nodes table (`ALTER TABLE nodes ADD COLUMN event_date TEXT`) — populated from `layer1_rules` rule 5 date resolution
2. **Sorting**: When `temporal_proximity=True`, boost nodes whose `event_date` is near the query's implied date reference (half-life 180d), sort output by event_date

```
## Timeline
2023-02-10 — Marcus started dating Chloe
2023-03-15 — Priya started new job at biotech startup
2023-04-15 — Marcus graduated from community college
```

**Risk**: Depends on extraction reliably populating `event_date`. Requires testing.

---

### 4. Query Coreference (Relationship → Person Name) (+2–4%, 2–3 days)
**File**: `retriever.py:retrieve_with_stats()`

When query contains relationship terms ("sister", "brother", "friend", "colleague"), look up person nodes tagged with those relationships and add the person's name to keywords. Example: "John's sister" → look up `person` nodes tagged `["john", "sister"]` → add "Emily" to keyword set.

**Risk**: Medium — name/word collisions possible (e.g., "Rose" as person vs flower). Mitigate with type check.

---

### 5. Multi-Hop Bridge Node Importance (+4–6%, 4–5 days)
**File**: `retriever.py:retrieve_with_stats()` (post-BFS, pre-strategy)

Score nodes by how many BFS entry points they connect to. Bridge nodes linking multiple seed facts rise in the ranking.

**Risk**: High — depends on edge extraction quality. Test thoroughly on dev set first.

---

### 6. Session-Scoped Retrieval Filtering (+1–2%, 2–3 days)
**Files**: `ingestion_pipeline.py`, `store.py`, `retriever.py`

Track `session_id` on nodes at extraction time. For single-hop QA (where `relevant_session_ids` has one entry), restrict BFS seeds to that session, reducing noise from cross-session matches.

**Risk**: Low — session IDs come from the dataset itself.

---

### 7. Extraction Prompt Refinement (+0–2%, 1–2 days)
**File**: `waystone/domain_profiles.py:EPISODIC_PERSONAL.extraction_examples`

Add 2–3 more targeted examples for LOCOMO failure patterns:
- Duration facts ("X years together", "met 5 months ago") → `relationship_update` nodes
- Job/role facts ("works as", "thinking of becoming") → separate job + plan nodes
- Change tracking ("moved from X to Y", "used to live") → prior-state + current-state with supersedes

---

### 8. Semantic Dedup Threshold Tuning (+1–3%, <1 day)
Ablations already defined (`waystone_dedup95`, `waystone_dedup97`). Run them and compare. Default 0.92 may be over-merging LOCOMO's personal facts where same-topic variants carry different temporal anchors.

---

### 9. Retrieval Context Tail — Short-Term Window (+1–3%, <1 day)
`waystone_prior20` config already implemented (`prior_turns_window=20`). Enable and benchmark — tests whether a recency window complements graph retrieval for recent events not yet well-represented in the graph.

---

### 10. Fact Popularity Scoring (defer)
Track query-time hits per fact across runs; use as a ranking signal. Requires multiple benchmark runs and statistical analysis. Not actionable yet.

---

## Implementation Priority Matrix

| Improvement | Effort | Confidence | Estimated Lift | Priority |
|---|---|---|---|---|
| 1. Entity-scoped query expansion | 1–2d | High | +3–5% | **1st** |
| 2. Person-anchored BFS seeding | 1d | High | +2–3% | **2nd** |
| 3. Temporal proximity boosting | 3–4d | High | +3–5% | **3rd** |
| 4. Query coreference | 2–3d | Medium | +2–4% | 4th |
| 5. Multi-hop importance ranking | 4–5d | Medium | +4–6% | 5th |
| 6. Session-scoped filtering | 2–3d | Medium | +1–2% | 6th |
| 7. Extraction prompt refinement | 1–2d | Low-medium | +0–2% | 7th |
| 8. Semantic dedup tuning | <1d | Medium | +1–3% | 8th |
| 9. Short-term context tail | <1d | Medium | +1–3% | 9th |
| 10. Fact popularity scoring | 5–7d | Low | +? | Defer |

---

## Estimated Post-Implementation Performance

| After | Keyword | LLM Judge | vs Competitor |
|---|---|---|---|
| Current (waystone_default) | 57.3% | 48.7% | — |
| After #1–3 (4–7 days) | ~63–67% | ~70–75% | Beat Zep (75.1%) |
| After #1–6 (9–15 days) | ~68–75% | ~75–82% | Approach MemMachine (84.9%) |
| After all (BM25 + #1–9) | ~78–85% | ~85–90% | Match Hindsight (89.6%) |

*LLM judge runs ~5–8 points above keyword accuracy because it rates partial context as "supported" even when keyword overlap is incomplete.*

---

## Non-Improvements to Avoid (Known Regressions)

1. **Aggressive semantic-only retrieval** — hurts LOCOMO (46% vs 57%). BGE-small can't distinguish "Sarah is eating pizza" from "Sarah is avoiding pizza". Keep keyword as primary.
2. **Confidence threshold filtering** — prunes needed personal facts (assigned moderate confidence 0.6–0.8). Rely on top_k + recency_decay instead.
3. **Prompt consolidation / merging rules** — fewer nodes → lower recall. Always bias toward splitting facts.

---

## Testing Plan

**Dev split**: `conv-26`, `conv-30`, `conv-41`, `conv-42`, `conv-43` — ~995 QA pairs (categories 1–4)

1. **Baseline** (before any changes): keyword + LLM judge by category
2. **Per improvement**: apply one at a time, rerun, measure delta per category
3. **Combined #1–3**: full dev split, compare to Zep (75.1%)
4. **Final** (test split, once): `conv-44`–`conv-50` with LLM judge only, reserved for paper reporting

---

## Next Steps

1. Wait for BM25 benchmark (PID 93276, conv-26) + improvements 1–3 benchmark (PID 80446) to complete — report results
2. Implement improvement #1 (entity-scoped query expansion, 1–2 days)
3. Implement #2 (person anchoring) in parallel — one-day change
4. Implement #3 (temporal proximity) — requires schema change + extraction update
5. Run dev split ablation sweep; decide whether #4–6 are needed to reach Zep
6. Final test set eval after cutoff

---

## Few-Shot Extraction Examples

`EPISODIC_PERSONAL.extraction_examples` in `domain_profiles.py` contains three targeted examples injected into the extraction prompt. Each targets specific LOCOMO failure modes.

**Example 1** — Person anchors + date tagging + compound splitting
- Every named individual gets a separate `person` node on first mention
- Every named place gets a `place` node
- Compound events split into separate nodes
- Dates appear verbatim in both fact text and tags (`"March 2023"`)
- Session date header `[Session: 1 | Date: 2023-04-15]` shown so model learns to anchor

**Example 2** — Plans + outcomes + `relationship_update`
- Future intentions → `plan` node (confidence 0.7)
- Relationship milestones → `relationship_update` node (not `fact`/`event`)
- `plan` linked to `relationship_update` via `follows` edge

**Example 3** — Fact update / adversarial state change
- Person situation changes → prior-state node + new-state node
- New-state node: `supersedes: ["prior_node_id"]`
- New-state tags **include prior-state vocabulary** (so old-state queries still find the transition)
- Two independent changes produce two separate update chains — not one merged node

---

## Temporal Tracking: Current State and Gap

### What works today

1. **Session date injection** — `ingestion_pipeline.py:_session_text()` prepends `[Session: session_3 | Date: 1:56 pm on 8 May, 2023]` to each session chunk. LLM sees session date and (if prompted correctly) embeds it in fact text and tags.

2. **Keyword-based temporal retrieval** — Tags like `"march 2023"`, `"november 2023"` are retrievable by keyword. Query "what happened in March 2023?" matches nodes with those tags.

### The gap: no structured `event_date` field

`nodes.created_at` = wall-clock UTC time when ingestion ran. For LOCOMO, every node in one benchmark run has nearly identical `created_at`. `recency_decay` using `created_at` is meaningless for LOCOMO and disabled by default (`half_life=3650d`).

There is no `event_date` column. Temporal ordering exists only in extracted fact text — not queryable, sortable, or displayable as a structured timeline.

**Phase 3 fix**: Add `event_date TEXT` column, populate from `layer1_rules` rule 5 extraction, sort and surface as a "Timeline" section in retrieval output.
