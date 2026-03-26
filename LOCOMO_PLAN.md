# LOCOMO Benchmark Plan

*Engram vs. Zep & Mem0 — competitive analysis and improvement roadmap.*

---

## What LOCOMO Tests

**Dataset**: snap-research/locomo — 10 long-term personal conversations, ~27 sessions each, ~22 turns/session, ~199 QA pairs per conversation. Each conversation spans 6-12 simulated months. The benchmark tests whether a memory system can answer questions about a person's life and relationships across sessions.

**QA categories:**
| Category | ID | Weight | What it tests |
|---|---|---|---|
| Temporal | 1 | ~20% | "When did X happen?", date ordering, relative time |
| Explicit memory | 2 | ~30% | Direct fact recall — names, numbers, stated preferences |
| Adversarial | 3 | ~15% | Contradictory/updated info — should return latest state |
| Multi-session | 4 | ~25% | Facts that span sessions, need cross-session synthesis |
| Open domain | 5 | ~10% | Inference/reasoning over stored facts |

**Scoring:**
- **Keyword recall** (fast, no API cost): tokenize answer, check exact + partial word overlap ≥ 0.7
- **LLM judge** (accurate, Claude Haiku): YES=1.0 / PARTIAL=0.5 / NO=0.0

**Competitors (published results on 10-conv subset, LLM judge):**
- Zep: ~72–75%
- Mem0: ~87–90%
- Full context (oracle baseline): ~92–95%
- **Engram target**: ≥ 87% (match Mem0), stretch goal ≥ 90%

---

## What's Already Built

The LOCOMO benchmark harness is complete and ready to run:

```
benchmarks/locomo/
├── harness.py                  # main entry point
├── ablation_configs.py         # ABLATION_CONFIGS dict (engram_default, engram_tight, full_context, etc.)
├── splits.py                   # DEV (conv-40..43), TEST (conv-44..50), ALL
├── loaders/
│   ├── locomo_dataset.py       # LocomoDataset, LocomoConversation, QAPair
│   └── ingestion_pipeline.py   # ingest_conversation() → GraphStore
└── evaluation/
    ├── scoring.py               # score_keyword_recall(), score_llm_judge(), aggregate()
    └── token_counter.py         # TokenBudget, estimate_tokens()
```

**Domain profile** (`episodic_personal`) is fully defined in `engram/domain_profiles.py`:
- Node types: `event`, `person`, `place`, `fact`, `plan`, `outcome`, `preference`, `relationship_update`
- Edge relations: `involves`, `located_at`, `follows`, `updates`, `references`
- `node_types_note` includes the anchor-node rule: every named person gets exactly one `person` node, and every fact gets that person's name in its tags.

**Retrieval pipeline** (`engram/retriever.py`) is domain-agnostic — it already handles BFS traversal, superseded pruning, recency decay, confidence threshold, token budget, and relevance scoring.

**Baseline benchmark command** (run this first to establish numbers before any changes):
```bash
python -m benchmarks.locomo.harness \
  --dataset /path/to/locomo10.json \
  --configs full_context engram_default \
  --split dev \
  --llm-judge \
  --output benchmarks/results/locomo_baseline_$(date +%Y%m%d).json
```

---

## The Four Gaps

### Gap 1 — Temporal Context Loss (highest impact, ~20% of QA)

**Problem**: `ingestion_pipeline.py` lines 78-81 build turns as:
```python
for session in conv.sessions:
    for turn in session.turns:
        line = f"{turn.speaker}: {turn.text}"  # datetime_str is DROPPED
```

`session.datetime_str` (e.g., `"2023-04-15"`) is never passed to the LLM during extraction. The model cannot extract temporal facts, cannot anchor events to dates, and cannot order events in time. This directly causes ~20% of QA to fail.

**Fix** (Phase 1):
```python
for session in conv.sessions:
    # Inject session boundary marker as synthetic turn
    if session.datetime_str:
        all_turns.append((f"[Session: {session.session_id} | Date: {session.datetime_str}]", session.session_id))
    for turn in session.turns:
        line = f"{turn.speaker}: {turn.text}"
        all_turns.append((line, session.session_id))
```

The session boundary markers land inside the buffer window, so the LLM sees temporal context when extracting each chunk.

---

### Gap 2 — Dev-Optimized Extraction Prompts

**Problem**: `EXTRACTION_PROMPT` and `INCREMENTAL_EXTRACTION_PROMPT` in `engram/prompts.py` hardcode rules aimed at software development conversations:
- Rules 1-10 reference "numeric thresholds", "named tools/libraries/frameworks", "HTTP headers", "config keys", "rejected alternatives with rationale"
- The main prompt's tagging guidance (`rule 7`) uses dev examples: "for a node about 'Redis as cache layer', tags = ['redis', 'cache', 'layer']"

Only rules 11 and 12 are domain-parameterized via `{node_types_section}` and `{edge_relations_section}`. The rest are dev-specific and actively mislead the model when processing personal conversations.

**Fix** (Phase 1):
1. Add `extraction_focus: str = ""` field to `DomainProfile` dataclass
2. Add `{extraction_focus_section}` placeholder to both prompts (after the node types section)
3. Write `episodic_personal`'s `extraction_focus`:
   ```
   EPISODIC PERSONAL FOCUS:
   - Extract EVERY named person mentioned (name, role/relationship to speaker, any attributes)
   - Extract EVERY event with its date/time if mentioned — even approximate ("last week", "in March")
   - Extract plan/intention statements: "I'm going to", "I want to", "I'm thinking about"
   - Extract outcome statements: what actually happened vs. what was planned
   - When a belief or preference changes across turns, create a relationship_update node
   - Tag every node with the full name of every person it involves
   ```

---

### Gap 3 — Session Boundary Information Lost in Buffer Flushes

**Problem**: The `ExtractionBuffer` in `ingestion_pipeline.py` flushes on size/word-count thresholds without carrying session metadata. When a buffer spans a session boundary, the LLM sees two sessions concatenated with no demarcation. This hurts multi-session QA (category 4, ~25% of questions).

**Fix** (Phase 1): Flush the buffer at every session boundary, regardless of size:
```python
for session in conv.sessions:
    # Flush pending buffer before starting new session
    if buffer.turns and current_session_id != session.session_id:
        _flush_buffer(buffer, store, ...)
    current_session_id = session.session_id
    # ... add turns
```

This ensures each buffer window is session-pure — extraction always has clean temporal context.

---

### Gap 4 — No Vector/Semantic Search

**Problem**: Entry nodes are found via tag matching only (`get_nodes_by_tags` uses SQLite JSON LIKE queries). Queries that use different vocabulary than extraction-time tags return zero entry nodes, collapsing BFS to nothing.

Example: extraction stores `{"fact": "Alice is allergic to shellfish", "tags": ["alice", "allergic", "shellfish"]}`. Query "what foods should Alice avoid?" — keyword extraction gives `["foods", "avoid", "alice"]`. Tag "alice" matches, but "foods" and "avoid" don't. BFS starts from Alice's anchor node but may miss the allergy fact entirely.

**Fix** (Phase 2): sqlite-vec integration (already mentioned in `AGENT_STACK.md` as "the v2 sqlite-vec work"):
1. Add `embedding BLOB` column to `nodes` table
2. Generate embeddings at extraction time (local: `nomic-embed-text`, or API: `text-embedding-3-small`)
3. In retrieval: hybrid search — keyword tags OR cosine similarity ≥ threshold
4. Fall back gracefully if no embedding available

---

## Improvement Phases

### Phase 1 — Fix the obvious gaps (expected: +8–12% LLM judge)

1. **Temporal context injection** in `ingestion_pipeline.py` — session boundary markers
2. **Session boundary flushes** — flush buffer at session transitions
3. **`extraction_focus` field** in `DomainProfile` — inject domain-specific extraction guidance
4. **Write `episodic_personal.extraction_focus`** in `domain_profiles.py`
5. Re-run dev split, compare keyword and LLM-judge scores vs. baseline

Estimated post-Phase-1 target: **~75–80% LLM judge** (up from expected ~65% baseline)

---

### Phase 2 — Semantic retrieval (expected: +5–8%)

1. Add sqlite-vec extension loading to `store.py`
2. Add `embedding BLOB` column to `nodes` table in migration
3. Embed at extraction time (batched, async)
4. Hybrid retrieval: tag match OR vector similarity ≥ 0.7
5. New ablation config: `engram_semantic`

Estimated post-Phase-2 target: **~82–87% LLM judge**

---

### Phase 3 — Temporal reasoning layer (expected: +3–5%)

Temporal QA requires not just finding events but answering "when" and ordering relative events. Current retrieval returns facts as unordered markdown.

1. Add `event_date: str | None` field to node schema (populated from extracted temporal markers)
2. Sort `event` and `outcome` nodes by `event_date` in retrieval output
3. Add a "timeline" section to retrieval markdown output when temporal nodes are present:
   ```
   ## Timeline
   2023-02-10 — Alice started new job at Acme Corp
   2023-04-15 — Alice mentioned tension with manager
   2023-06-20 — Alice quit Acme, started job search
   ```
4. New ablation config: `engram_temporal`

Estimated post-Phase-3 target: **~87–90% LLM judge**

---

### Phase 4 — Adversarial / contradiction handling (expected: +2–3%)

Adversarial questions test that the system returns the *latest* state, not an earlier contradicted fact. The `supersedes` mechanism already exists — the question is whether it fires correctly for personal facts.

1. Add `relationship_update` node type explicitly to `INCREMENTAL_EXTRACTION_PROMPT` guidance
2. Verify `superseded_pruning` strategy fires for `relationship_update` nodes (may need type-aware pruning)
3. Test against category-3 QA pairs specifically

---

## Engram Self-Hosting

**Question**: Should Engram use itself to track Engram development?

**Yes.** Use two separate projects:

```bash
engram init engram_dev        # Engram source code decisions, architecture, PR notes
engram init locomo_benchmark  # LOCOMO test results, scoring runs, ablation notes
```

Do NOT mix them — development facts and benchmark results have different retrieval needs. Use `software_dev` domain profile for `engram_dev` and `episodic_personal` (or a new `benchmark` profile) for `locomo_benchmark`.

Hook install (`hooks/install.py`) should point the stop hook at `engram_dev` when working in this repo.

---

## Competitive Positioning

| System | Architecture | LOCOMO score | Tradeoff |
|---|---|---|---|
| Zep | Graph + vector hybrid (Neo4j + pgvector) | ~72–75% | Heavy infra, self-hosted or paid cloud |
| Mem0 | LLM-generated memory cards + vector search | ~87–90% | Stateless cards lose graph structure; requires per-query LLM call |
| **Engram** | DAG graph + BFS + SQLite | ~65% baseline (pre-fixes) | Lightweight, offline-capable; gaps in temporal + semantic |
| **Engram (Phase 3)** | + temporal + semantic | ~87–90% target | Same lightweight stack |

**Engram's moat over Mem0**: The graph structure captures *relationships between facts*, not just isolated memory cards. A `supersedes` edge means you never serve stale data. Mem0's flat card model can't express "X was true until Y; now Z is true." This matters most for adversarial category questions, where Engram should structurally outperform Mem0 once Phase 1 fixes temporal context.

**Engram's moat over Zep**: No Neo4j dependency. SQLite runs on a laptop, in a container, or embedded in a desktop app. The hosted API (Phase 2 in `ROADMAP.md`) layers on top without changing the core store.

---

## Files to Modify (Phase 1)

| File | Change |
|---|---|
| `engram/domain_profiles.py` | Add `extraction_focus: str = ""` to `DomainProfile`; write `episodic_personal.extraction_focus` |
| `engram/prompts.py` | Add `{extraction_focus_section}` placeholder to `EXTRACTION_PROMPT` and `INCREMENTAL_EXTRACTION_PROMPT`; inject in `build_extraction_prompt()` and `build_incremental_prompt()` |
| `benchmarks/locomo/loaders/ingestion_pipeline.py` | Inject session boundary markers; flush buffer at session transitions |
| `benchmarks/locomo/ablation_configs.py` | Add `engram_episodic` config that sets `domain="episodic_personal"` |
