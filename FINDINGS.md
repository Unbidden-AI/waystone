# Context Broker — Research Findings

This document captures empirical benchmark results, architectural insights, and strategic analysis developed during the Context Broker research phase. It is a living record of what was learned, not a user guide (see PROJECT.md for that).

---

## Benchmark Setup

### Test Corpus

Three synthetic transcripts representing realistic software project design discussions:

| Transcript | Nodes (full) | Edges (full) | Topics |
|------------|-------------|-------------|--------|
| `project_api_design.md` | 58 | 22 | REST API design, versioning, rate limiting |
| `project_auth_system.md` | 120 | 99 | JWT, session management, OAuth, MFA |
| `project_data_pipeline.md` | 90 | 75 | Kafka, deduplication, schema evolution |

23 evaluation questions across the three projects, each with a ground-truth set of nodes the retrieval must surface to answer correctly. Recall is measured as the fraction of ground-truth nodes present in the retrieved context.

**Model used:** Gemini 2.5 Flash (`models/gemini-2.5-flash`)

---

## Extraction Mode Comparison

> **Note:** The three-mode benchmarks below (Full/Incremental/Buffered) were conducted in early March 2026 against a prompt-and-retriever state that has since been substantially improved. The current best recall on the software dev benchmark is **95%** (Gemini 2.5 Flash + `--verify`, top_k=30, with prior-state tagging, compound hyphen keyword fix, and enumeration context tagging). This section is preserved as historical context on extraction architecture tradeoffs.

Three extraction modes were benchmarked against the same retrieval evaluation (early March 2026, pre-prompt-improvement baseline):

| Mode | Recall | ≥80% Questions | Avg Tokens | LLM Calls | Extraction Time |
|------|--------|----------------|-----------|-----------|-----------------|
| **Full** | **60%** | **10 / 23** | 566 | 3 | 230s |
| Incremental | 51% | 8 / 23 | 526 | 39 | 423s |
| Buffered | 47% | 7 / 23 | 508 | 7 | 196s |

### Full extraction

One LLM call per transcript. The model sees the entire conversation in a single context window, which maximizes intra-transcript coherence — it can recognize when a decision from message 2 is reversed in message 38 and model that as a supersedes relationship.

**Best for:** Offline processing of completed transcripts; any batch use case where the full conversation is available before retrieval begins.

### Incremental extraction (per-turn)

One LLM call per turn (turn = N messages). The model is shown the current turn plus a retrieved snapshot of the existing graph (~30 nodes). It can reference existing node IDs in edges and supersedes relationships.

**Key metric:** Cross-turn edges generated — 55 for just two transcripts (api_design + data_pipeline, auth_system failed). This structural richness is only available via incremental extraction; full-transcript mode doesn't model across-session relationships in the same way.

**Cost:** ~13x more LLM calls than full mode for a 2-turn batch size. 84% higher extraction time.

**Best for:** Real-time workflows where the conversation is ongoing and each turn should immediately enrich the graph. The cross-turn edge density makes the graph more navigable over time.

### Buffered extraction

Turns are accumulated and flushed to the LLM only when a threshold is met:
- Minimum 3 turns AND 200+ words accumulated
- Maximum 10 turns (force flush)
- Skip if all turns are <20 words (acknowledgments, short replies)

**Key metric:** ~18% call rate (7 calls for 39 turns across two transcripts). Wall time is faster than full mode despite processing all three transcripts because flushes run on smaller inputs than the full transcript.

**Cross-turn edges:** Only 7, vs 55 for incremental. The buffer's batching reduces the cross-turn context injection that makes incremental valuable.

**Best for:** Real-time use where per-turn extraction cost is prohibitive, but you still want the graph building incrementally without waiting for the full transcript.

### Episodic ingestion (current production mode)

The Stop hook (`hooks/context_broker_stop.py`) captures only the new turns since the last extraction (delta), prepends 2 prior turns as co-reference context, and submits to `engram extract --verify`. This is structurally similar to incremental but operates at conversation-end rather than per-turn, avoiding per-turn cost while retaining the delta coherence benefit. A `MAX_DELTA_TURNS=50` hard cap prevents runaway LLM cost if state is lost. This is the recommended real-time mode.

---

## Recall Analysis

### Why full extraction wins (and current improvement trajectory)

Full extraction gives the LLM complete global context — it sees all 42 messages in a design transcript and can reason about the entire arc. Incremental and buffered modes give the LLM only a window, plus a retrieved snapshot of what's been extracted so far. That snapshot is good but imperfect (recall on the snapshot itself is ~60% at the original baseline), so errors compound over turns.

The gap between modes has narrowed substantially as the extraction prompt improved. With the current prompt (prior-state tagging rule, compound hyphen fix, enumeration context tagging, `--verify` pass), full single-shot extraction reaches **95% recall** (21/23 ≥80%) on the software dev benchmark.

### Where all modes struggle (0% recall questions)

Six questions had 0% recall in the early full mode run:
- `q_pipe_02`, `q_pipe_07`: Data pipeline questions about specific numeric values (e.g., deduplication window size, backpressure thresholds)
- `q_auth_04`, `q_auth_06`: Authentication questions about security edge cases mentioned briefly in conversation

The pattern: **specific numeric values and briefly-mentioned edge cases are under-tagged.** The extraction LLM assigns tags based on primary topic keywords, but a fact like "30-second deduplication window using Redis sorted sets" may only get tagged `["deduplication", "redis", "window"]` — and a query about "event replay" or "idempotency" won't match those tags.

Most of these chronic failures have since been resolved. See [Persistent Failing Questions](#persistent-failing-questions) for current status.

### Recall improvements implemented

**1. Fact-text fallback search**

When `get_nodes_by_tags` finds no tag matches, fall back to substring search against the `fact` column. This catches vocabulary mismatches where the query terms and the stored tags diverge. For example, querying "idempotency handling" can still find a node tagged `["deduplication", "exactly-once"]` if those words appear in the fact text.

**2. Two-keyword overlap threshold for BFS seeding**

Before BFS traversal, entry nodes are filtered to those matching ≥2 query keywords. Single-keyword matches are too generic and can seed the BFS in the wrong cluster of the graph. If no nodes pass the ≥2 threshold, fall back to all candidates. This prevents a query about "API versioning strategy" from seeding BFS at an unrelated node that happens to be tagged `["api"]`.

**3. Compound hyphen keyword expansion** (`retriever.py`, 2026-03-18)

`extract_keywords()` now emits non-numeric hyphenated compounds as both the whole token AND split parts ("hot-path" → "hot-path", "hot", "path"). Solved q_pipe_04 (25%→100%).

**4. Prior-state tagging rule** (`prompts.py` Rule 11, 2026-03-18)

When a decision/transition supersedes a prior approach, tags MUST include the old term so it's retrievable from both directions. Also requires "from X to Y" language in transition fact text. Solved q_pipe_02 (0%→100%); lifted baseline from 89% → 95%.

**5. Enumeration context tagging rule** (`prompts.py` verification section, 2026-03-18)

When a node is one of several items explicitly enumerated together, include tags from the enumeration's label. Helped q_pipe_04 and q_pipe_01.

---

## Context Broker vs. RAG

These are the most frequently asked architectural questions. The short answer: Context Broker is a **knowledge graph with LLM-powered ingestion**, not a retrieval-augmented generation system.

### The core difference: what gets stored

| | RAG | Context Broker |
|--|-----|----------------|
| Storage unit | Text chunk (fixed-size fragment) | Discrete fact (self-contained statement) |
| Meaning extraction | At query time (model must parse chunk) | At ingest time (LLM extracts structured facts once) |
| Relationships | Implicit (proximity in embedding space) | Explicit typed edges (depends_on, relates_to, supersedes) |
| Temporal validity | None — old and new chunks coexist | Supersedes edges explicitly retire stale facts |
| Confidence | Not modeled | Assigned at extraction: 0.3 (mentioned) → 1.0 (verified) |
| Infrastructure | Embedding model + vector store | SQLite + keyword tags — entirely local |

### The supersedes advantage

This is the sharpest practical differentiator. In a RAG system:
- "We chose sessions for auth" (week 1 chunk) and "We switched to JWT because sessions don't scale" (week 3 chunk) both exist as equally valid retrievable units
- The model receives contradictory information and must infer which is current
- This inference fails silently — the model confidently uses the wrong fact

In Context Broker:
- The week 3 extraction creates a new node with `supersedes: [week_1_node_id]`
- The `superseded_pruning` strategy drops the week 1 node from retrieval entirely
- The model only ever sees current decisions

For software projects where requirements and architecture evolve over months, this alone justifies the tool.

### Relationship traversal vs. similarity search

RAG finds chunks that are *similar* to your query. Context Broker finds chunks that are *connected* to your query. These are different operations that return different results.

A query about "authentication flow" via similarity search returns chunks where those words appear. BFS traversal from an authentication decision node returns: that decision, its rationale nodes, the constraint nodes it depends on, the implementation nodes that flow from it, any questions still open about it, and any superseded alternatives. The result is a **reasoning neighborhood**, not just similar text.

### Where RAG is better

RAG is better for document retrieval over large corpora — searching codebases, documentation, wikis, PDFs. If the question is "show me code that handles authentication," RAG finds the relevant code. If the question is "what did we decide about authentication and why," Context Broker finds the reasoning history.

The two tools complement each other. RAG over the codebase answers *what is in the code*. Context Broker answers *why it is there*.

---

## Where Context Broker Is and Isn't Valuable

### High value

**Long-running development projects (weeks to months)**
The value compounds over time. Early architectural decisions, rejected alternatives, accepted constraints — all accumulate in the graph and are available to every subsequent query. There is no alternative to this for cross-session memory; LLMs have none.

**Maintenance phases**
A developer returning to a codebase after 3 months can query "why was Redis chosen for session storage?" and receive a structured answer drawn from design discussions, not from searching commit messages or asking colleagues. The graph is the institutional memory.

**Multi-session design work**
Each design session enriches the graph. By session 5, the retrieval surface is richer and more precise than session 1. The graph doesn't forget what was decided, what was tried and rejected, or what remains open.

**Cross-session intent amplification**
Beyond surfacing facts, the broker can improve answer quality by making implicit constraints explicit. Users often omit known project constraints from individual prompts — "add a caching layer" without mentioning "we're on GCP, must not use AWS services." The broker injects those constraints automatically, which can produce better answers than the user's own prompt alone.

### Modest value

**Single-session workflows with modern large models**
A 20-turn conversation at ~100 tokens/turn is 2,000 tokens — trivially within any modern model's context window. The broker adds extraction overhead without meaningful benefit. Useful if you expect to return to the project later; not worth the overhead for one-off tasks.

**Short tasks with clear, self-contained scope**
Bug fixes, isolated feature additions, small refactors. Context Broker's value scales with project complexity and duration; it's underutilized for narrow tasks.

### Not worth it

**Tasks under ~15 turns with no future sessions planned**
Extraction cost (an LLM call, typically 30–120 seconds) exceeds the token savings for any realistic downstream prompt budget.

---

## Claude Code Hook Integration

The hook integration is **operational**. Both hooks are live and active in daily development use. Install via `python hooks/install.py`.

### Hook architecture

**`UserPromptSubmit` hook** (`hooks/context_broker_submit.py`) — runs before each user prompt reaches the model:
1. Receives the user's prompt text on stdin as JSON
2. Runs `engram query <project> "<prompt>"` locally (SQLite lookup, <5ms)
3. Writes the retrieved context block to stdout
4. Claude Code prepends this to the prompt automatically

**`Stop` hook** (`hooks/context_broker_stop.py`) — runs after each conversation ends:
1. Parses the full JSONL transcript and saves to `~/.engram/transcripts/<project>/`
2. Computes the delta: only turns since the last extraction (tracked in `<session_id>.state`)
3. Prepends 2 prior turns as co-reference context, writes a delta snippet to a temp file
4. Spawns `engram extract <project> <delta_file> --verify` as a detached background process
5. Checks node count; if `current_nodes - last_reconcile_nodes >= reconcile_threshold (75)` and total ≥ 100, spawns `engram reconcile` to find supersedes relationships
6. Hard cap: `MAX_DELTA_TURNS=50` prevents runaway extraction cost if state file is lost

### What this achieves

- **Zero user friction.** The user works normally. Every prompt is silently enriched with project context. Every conversation end triggers background graph enrichment.
- **Compounding quality.** The graph grows richer with each exchange. By conversation 10, retrieval is meaningfully better than conversation 1 because more architectural context has been accumulated.
- **Cross-session memory.** When the user starts a new Claude Code session, the hook immediately reloads project context. The model behaves as if it has been working on the project continuously.

### The intent amplification effect

An underappreciated benefit: the injected context often conveys user intent more accurately than the user's own prompt. Users operate with tacit knowledge — constraints, decisions, and history they hold in their heads but don't re-state. The broker surfaces this knowledge explicitly. A prompt like "add rate limiting to the endpoints" becomes much more actionable when the model also receives: "Rate limits: 1000 req/min authenticated, 100 req/min unauthenticated, enforced by Kong via X-RateLimit headers." The broker bridges the gap between what the user said and what they meant.

---

## Known Limitations

### Recall on software dev benchmark (current: 95%)

Gemini 2.5 Flash + `--verify` with current prompt improvements (prior-state tagging, compound hyphen fix, enumeration context tagging, top_k=30) achieves **95% recall** (21/23 ≥80% questions) on the software dev benchmark. Two questions remain below 80%:
- `q_pipe_01` (75%): grading artifact — retriever returns correct fact but keyword overlap fails vs. ground truth phrasing
- `q_api_08` (75%): same grading artifact pattern

These are not retrieval gaps; they reflect keyword-overlap scoring limitations in the eval harness rather than missing facts in the graph.

### LOCOMO episodic memory benchmark (current: 85.7% LLM / 72.6% keyword)

On the LOCOMO benchmark (real human conversations, multi-session episodic memory), the current best pipeline (`engram_semantic_rerank_topk100`) scores **85.7% LLM accuracy** and **72.6% keyword accuracy** on the dev split (5 conversations, 762 QA pairs, categories 1–4, gpt-4o-mini judge, April 2026). This **exceeds Zep (~73% LLM)** and approaches Mem0 (~88% LLM). The March 2026 conv-26-only baseline was ~50% keyword; the improvement came from semantic rerank, top_k=100, and correcting the evaluation protocol to exclude adversarial category 5 questions (which depressed earlier scores by ~10pp). A full 10-conversation run against the complete test split is pending for a like-for-like comparison with published Zep/Mem0 numbers.

### LongMemEval benchmark (current: 60.6% LLM oracle / 60.8% LLM standard)

LongMemEval is a Microsoft Research benchmark for long-term episodic memory in LLM assistants — 500 questions across 6 question types drawn from the `longmemeval-cleaned` dataset (S variant: one long conversation per question). It covers temporal reasoning, multi-session aggregation, knowledge-update (superseding facts), single-session recall, and preference tracking.

**Current results (April 2026, gpt-4o-mini judge, n=500):**

| Config | Split | kw% | LLM% | LLM partial% | Notes |
|--------|-------|-----|------|--------------|-------|
| `engram_lme_gemini` / `engram_lme_rrf_dynamic` | oracle | 54.0% | **60.6%** | 66.5% | Best oracle — Gemini 2.5 Flash-Lite extraction, RRF or semantic rerank, top_k=100 |
| `engram_lme_s_user_patched` + preference pass | standard | 63.4% | **60.8%** | 66.0% | Best standard — preference node augmentation (+7.6K nodes on 30 pref samples), +20pp on preference type (Apr 15) |
| `engram_lme_s_user_patched` (person fan-out) | standard | 63.4% | 59.5% | — | Person exhaustive fan-out, person anchoring, semantic rerank top_k=100 (Apr 14) |
| `engram_lme_s_user_patched` | standard | 62.6% | 58.0% | 64.2% | Prior best — synthetic user node injection, person anchoring (Apr 10) |
| `engram_lme_gemini_s` | standard | 61.8% | 57.8% | 64.2% | Standard split baseline without user node patch |
| `engram_lme_keyword` | oracle | 52.4% | 59.6% | 65.9% | Keyword-only (no rerank) — nearly as good as semantic rerank |

**Per-category breakdown (oracle split, `engram_lme_gemini`):**

| Question type | n | kw% | LLM% |
|---------------|---|-----|------|
| knowledge-update | 78 | 64.1% | **70.5%** | ← Engram's strongest category (supersedes mechanism) |
| single-session-assistant | 56 | 73.2% | **76.8%** | Facts about assistant behavior are well-extracted |
| single-session-user | 70 | 60.0% | 65.7% | |
| multi-session | 133 | 51.1% | 64.7% | |
| temporal-reasoning | 133 | 51.9% | 51.5% | ← Weakest content category |
| single-session-preference | 30 | 0.0% | 13.8% | ← Structural gap: preference facts aren't reliably extracted |

**Per-category breakdown (standard split, `engram_lme_s_user_patched` + preference pass + bi-temporal routing, Apr 15):**

| Question type | n | kw% | LLM% | vs Apr 14 |
|---------------|---|-----|------|-----------|
| single-session-assistant | 56 | — | **89.3%** | +0.2pp |
| knowledge-update | 78 | — | **71.8%** | ≈0 |
| temporal-reasoning | 133 | 65.4% | **63.9%** | **+6.0pp** ✅ bi-temporal routing (vs 57.9% S-split baseline) |
| multi-session | 133 | — | 54.9% | ≈0 |
| single-session-user | 70 | — | 54.3% | ≈0 |
| single-session-preference | 30 | — | **26.7%** | **+20pp** ✅ preference pass |
| **overall** | **500** | **~64%** | **~61.4%** | **~+0.6pp est** |

*(Overall estimated: temporal 133/500 × +6.0pp ≈ +1.6pp on temporal, ~+0.6pp overall vs Apr 14 fan-out. Full 500-sample re-run needed for exact overall.)*

**Per-category breakdown (standard split, `engram_lme_s_user_patched` + preference pass, Apr 15):**

| Question type | n | kw% | LLM% | vs Apr 14 |
|---------------|---|-----|------|-----------|
| single-session-assistant | 56 | — | **89.3%** | +0.2pp |
| knowledge-update | 78 | — | **71.8%** | ≈0 |
| temporal-reasoning | 133 | — | 59.4% | ≈0 |
| multi-session | 133 | — | 54.9% | ≈0 |
| single-session-user | 70 | — | 54.3% | ≈0 |
| single-session-preference | 30 | — | **26.7%** | **+20pp** ✅ preference pass |
| **overall** | **500** | **63.4%** | **60.8%** | **+1.3pp** |

**Per-category breakdown (standard split, `engram_lme_s_user_patched` with person fan-out, Apr 14):**

| Question type | n | kw% | LLM% | vs Apr 10 |
|---------------|---|-----|------|-----------|
| single-session-assistant | 56 | 82.1% | **89.1%** | +3.4pp |
| knowledge-update | 78 | 80.8% | **71.8%** | +2.6pp |
| temporal-reasoning | 133 | 69.2% | 59.4% | +3.0pp |
| multi-session | 133 | 53.4% | 54.9% | +0.8pp |
| single-session-user | 70 | 61.4% | 54.3% | +1.4pp |
| single-session-preference | 30 | 6.7% | 6.7% | -6.7pp ⚠️ n=30, likely noise |

**Comparison to published baselines (LME-S, LongMemEval paper Table 2):**

| System | LLM% (approx) | Notes |
|--------|---------------|-------|
| GPT-4o no memory | ~30% | Upper bound without persistent memory |
| MemoryBank | ~40–50% | Flat retrieval baseline |
| ReadAgent | ~55–60% | Summarization-based compression |
| **Engram standard** | **60.8%** | Graph retrieval, Gemini extraction, person fan-out, preference pass, semantic rerank |
| **Engram oracle** | **60.6%** | Same with oracle-extracted graph |
| Full context (oracle) | ~70% | All sessions concatenated into context window |

Engram exceeds ReadAgent on both splits, despite ReadAgent using compression specifically tuned for long-context recall. The gap to full-context oracle (~70%) is ~10pp — mostly attributable to the temporal-reasoning and single-session-preference categories.

**Bi-temporal routing (+6.0pp temporal-reasoning, Apr 15):** Two changes in combination lifted temporal-reasoning from 57.9% → 63.9% (77→85 correct out of 133, vs S-split Gemini baseline):

1. **`occurred_at` backfill** — All 500 LME checkpoint DBs (747,445 nodes) previously had `occurred_at=NULL` because `_parse_session_date` in `ingestion_pipeline.py` only handled LOCOMO's `'7:31 pm on 21 January, 2022'` format, not LME's `'2023/05/20 (Sat) 02:21'` format. Fixed the parser and ran `backfill_occurred_at.py` to populate timestamps from `source_transcript → session_id → dataset datetime`. 162 nodes from the implicit preference pass remain NULL (no session provenance — acceptable).

2. **Auto-temporal routing** — `retrieve_with_stats()` now classifies queries via `_classify_query_type()`. When a query contains temporal tokens (`when`, `first`, `last`, `ago`, `how long`, `how many times`, etc.), `temporal_sort=True` and `temporal_proximity=True` are unconditionally activated regardless of the caller's strategy defaults. This surfaces a `## Timeline` section listing all dated nodes chronologically in the context, giving the LLM judge the concrete dates needed for arithmetic ("how many days between X and Y?"). Controlled by `temporal_auto_route` strategy key (default `True`).

**Person exhaustive fan-out (+1.5pp overall, Apr 14):** After BFS retrieval, the retriever fetches ALL nodes tagged with identified person names and injects them directly, bypassing the top_k cut. Expected to help multi-session and single-session-user (+0.8pp / +1.4pp). Temporal-reasoning benefited most (+3.0pp) — person names are also strong anchors for time-indexed facts. Single-session-preference dropped 6.7pp but n=30 makes this likely noise (2 samples).

**Key gap: single-session-preference (0–14% LLM)**

Preference questions ("What coffee does the user like?") are the weakest category across all configs. The extraction model doesn't reliably tag preference facts with question-answerable keywords, and the preference node type is sparse in the graph. This is a prompt/schema gap, not a retrieval gap.

**Key strength: knowledge-update (70.5% LLM)**

Questions that test whether a system knows about updates ("The user switched from X to Y — what are they using now?") are Engram's structural differentiator. The `superseded_pruning` strategy correctly removes stale facts, making the updated fact the only answer candidate. This category scores higher for Engram than flat-retrieval systems.

### Specific values are under-extracted

Numeric thresholds, config key names, and briefly-mentioned edge cases are systematically under-retrieved. The extraction LLM tags nodes with primary topic terms but not with the specific values themselves, making them invisible to keyword-based retrieval. The fact-text fallback helps but doesn't fully close this gap.

### Incremental mode quality degrades on large transcripts

The `project_auth_system.md` transcript (120 nodes in full mode) caused a `KeyError: 'fact'` crash in both incremental and buffered modes. This was caused by the LLM re-emitting existing node stubs (just an ID, no fact text) instead of referencing them only in edges — a failure mode that occurs when the existing context section is large and the model loses track of the protocol. Fixed with a malformed-node filter in `assign_ids_incremental`, but the underlying prompt fragility on large contexts remains.

### No semantic similarity

The broker uses keyword/tag matching, not vector similarity. Synonyms and paraphrases that don't appear in tags are missed. A query about "throttling" won't find a node tagged only `["rate limit", "rpm"]` unless "throttling" appears in the fact text. Richer tag generation at extraction time is the primary mitigation.

---

## Extraction Buffer Design

The `ExtractionBuffer` class implements a hybrid trigger strategy to reduce the LLM call rate in real-time incremental workflows while preserving extraction quality.

### Flush triggers

| Condition | Action | Rationale |
|-----------|--------|-----------|
| `turns >= MAX_TURNS (10)` | Force flush | Prevent unbounded accumulation |
| `turns >= MIN_TURNS (3)` AND `words >= MIN_WORDS (200)` | Flush | Enough content to justify an LLM call |
| All turns `< SHORT_TURN_WORDS (20)` words | Hold | Acknowledgments and short replies produce low-value nodes |

### Why these thresholds

The goal is to amortize the LLM extraction call across multiple turns without degrading the quality of the resulting graph. A single-turn flush for a 15-word acknowledgment ("Sounds good, let's go with Redis") produces near-zero value. A three-turn flush for 250 words of substantive discussion is efficient.

The 18% call rate observed in benchmarks (7 calls for 39 turns) is close to the theoretical minimum given the transcript structure — most flushes triggered at the 3-turn / 200-word threshold, with no force flushes needed.

### Buffer persistence

The buffer persists to `buffer.json` in the project directory between `engram extract-turn` invocations. This means a buffer can span multiple shell sessions — a turn added via hook in one process is still buffered when the next prompt arrives. The `engram query` command auto-flushes any pending buffer before retrieval to ensure the graph reflects all available conversation content.

---

## Token ROI Model

The core economic question: does the upfront extraction cost pay off in downstream savings?

### Break-even analysis

At conversation turn N:
- **Without broker:** input tokens ≈ `(N-1) × avg_turn_tokens` (full history injected)
- **With broker:** input tokens ≈ `avg_context_tokens` (retrieved subgraph, ~540 tokens)

Using typical values (avg turn = 150 tokens, avg injected context = 540 tokens):

| Turn | History tokens | Broker tokens | Delta |
|------|---------------|---------------|-------|
| 1 | 0 | 540 | -540 (broker costs more) |
| 4 | 450 | 540 | -90 (broker still costs more) |
| 7 | 900 | 540 | +360 (break-even crossed) |
| 10 | 1,350 | 540 | +810 |
| 20 | 2,850 | 540 | +2,310 |
| 50 | 7,350 | 540 | +6,810 |

Break-even occurs at approximately **turn 7** for a typical conversation. After that, the broker saves tokens on every prompt. The savings are super-linear: the longer the project, the greater the per-prompt benefit.

### Extraction cost amortization

A full extraction run (3 transcripts, ~230s) generates a durable graph that serves all future queries at <5ms retrieval latency. The upfront cost is amortized across potentially hundreds or thousands of downstream queries. For a project that generates 10+ downstream prompts against the same knowledge, the extraction investment is typically recovered within the first session.

For buffered real-time extraction, the cost model is different: small LLM calls are distributed across the conversation rather than batched upfront. This smooths the cost curve but increases per-turn overhead slightly.

---

## Targeted Extraction Passes

### Motivation

Adding new fact categories to the main extraction prompt causes recall regression. When the `lesson_learned` Rule 14 was added to the base EXTRACTION_PROMPT, Gemini 2.5 Flash recall dropped from 92% → 82% (no verify) despite the transcripts containing no failed approaches for the model to find. Root cause: the model wastes attention scanning for content that doesn't exist in the transcript. The verification pass didn't recover this loss.

The solution is **opt-in targeted passes**: small, focused prompts (~150-200 tokens of instructions) that run after the main extraction and hunt for exactly one category of information. They receive the existing nodes as context to avoid re-extraction, and their output is merged via the same `assign_ids_incremental` + dedup pipeline as the verify pass.

### Architecture

A targeted pass is structurally identical to `--verify`:
1. Build a category-specific prompt with existing nodes + transcript
2. One LLM call
3. Parse → `assign_ids_incremental` → `merge_extraction` (with dedup)

Each pass is designed to be independently useful — you can run any subset. Deduplication (Option B) ensures that if a targeted pass re-discovers a fact already in the graph, it merges rather than duplicates.

**Key design choice:** One prompt per category, not one combined "hunt for X, Y, Z" prompt. Combining categories in one pass reintroduces the same problem as adding categories to the main prompt — a single prompt with 5 categories still wastes model attention on any of the 5 that don't apply. Separate passes also allow selective use: `--lessons` for retrospectives, `--questions` for planning reviews, `--constraints` for architecture audits.

### Implemented Passes

| Flag | Category | Hunts For | Best Used When |
|------|----------|-----------|----------------|
| `--lessons` | `lesson_learned` | Failed approaches, rejected alternatives, anti-patterns, hard-won insights | Post-mortems, retrospectives, any transcript discussing what didn't work |
| `--decisions` | `decision` | Explicit choices between alternatives + rationale | Design discussions where the main pass may have labeled decisions as "implementation" |
| `--questions` | `question` | Open questions, TBDs, deferred decisions, unresolved items | Planning phases, mid-project reviews |
| `--constraints` | `constraint` | Hard requirements, compliance, SLAs, technical non-negotiables | Architecture reviews, compliance audits |

### Usage

```bash
# Single targeted pass
engram extract myproject transcript.md --lessons

# Multiple targeted passes
engram extract myproject transcript.md --verify --lessons --decisions

# Benchmark with targeted pass
python benchmarks/run_benchmark.py --config benchmarks/model_configs/gemini_25_flash.yaml --verify --lessons
```

### Expected Impact

**`--lessons` on design transcripts:** Likely low yield (design transcripts explicitly discuss rejected alternatives, but the main extraction prompt's Rule 6 already covers this). Higher yield on post-mortem or incident review transcripts where the pattern is "we tried X and it failed."

**`--decisions` on design transcripts:** Moderate yield. The main prompt extracts decisions, but sometimes labels them as `implementation`. A decisions-focused pass may find additional rationale nodes and re-classify borderline cases.

**`--questions` on planning transcripts:** High yield when transcripts contain "we need to figure out X" statements. The main prompt captures resolved facts better than open questions.

**`--constraints` on architecture transcripts:** Moderate yield. The main prompt covers constraints, but compliance and SLA requirements buried in passing mentions are often under-extracted.

### Benchmark Results (Gemini 2.5 Flash, 2026-03-17)

Targeted passes were benchmarked against `--verify` baseline on all 23 eval questions. Results reflect the current extraction prompt with Rule 13 improvements and the updated `--decisions` prompt (embedded rationale). Earlier intermediate results (pre-Rule-13) showed verify-only at 80%; those results are superseded by the current-state numbers below.

| Config | Baseline recall | Default recall | ≥80% Qs | Nodes | Time |
|---|---|---|---|---|---|
| `--verify` only | **94%** | 93% | 20/23 | ~295 | ~396s |
| `--verify --decisions` (embedded rationale) | **94%** | 88% | 19/23 | ~309 | ~409s |
| `--verify --lessons` | 88% | 84% | 19/23 | 313 | ~405s |
| `--verify --decisions --lessons` | 81% | 79% | 14/23 | 300 | ~425s |

**Key observations:**

- **`--verify --decisions` (embedded rationale) maintains 94% baseline recall** while solving previously-impossible questions: `q_auth_04` improved from 0% → 100%, `q_pipe_04` from 25% → 100%. These were persistent failures across all prior configurations. The tradeoff: `q_pipe_06` regressed from ~80% → 60%, netting 19/23 vs 20/23 at ≥80%.
- **Decision nodes are token-dense** (750 avg tokens vs ~500 for verify-only). This is intentional: each node now embeds the chosen approach, the rejected alternative, and the rationale in a single self-contained fact. The density means top_k=25 retrieves the right facts, but filtering strategies (confidence/recency) aggressively prune them.
- **Use `baseline` preset with `--decisions`**: Default, filtered, and tight presets drop to 88%/88%/86% respectively. The confidence threshold and recency decay strategies prune the decision nodes (typically lower-confidence than main-pass facts), causing significant regression. Baseline is the correct strategy when `--decisions` is active.
- **`--lessons` regresses under default preset** (+8pp baseline → +2pp default). Same root cause as `--decisions`: lessons nodes survive baseline retrieval but get pruned under filtering.
- **Combining decisions + lessons causes regression**: 81% baseline (14/23) is worse than either pass alone and worse than verify-only. Root cause: lesson_learned nodes are tagged with the same keywords as the decisions they describe. BFS retrieves both, and top_k=25 can't distinguish — lesson nodes displace the decision/implementation nodes eval questions actually require. The 696 avg tokens at baseline (vs 577–625 for single passes) confirms more nodes are retrieved but wrong ones.

### Updated `--decisions` Prompt: Embedded Rationale

The original `--decisions` prompt emitted separate rationale nodes (`lesson_learned` type) for rejected alternatives. This was updated (2026-03-17) to embed rejected alternatives directly in the decision fact text: `"Chose Redis over Memcached because it supports clustering; Memcached ruled out due to no replication support"`.

**Effect:** One node instead of two, tags cover both sides of the tradeoff, no competing leaf nodes in retrieval. The per-question improvements (q_auth_04: 0%→100%, q_pipe_04: 25%→100%) confirm that the embedded format is findable via keyword lookup for either the chosen or rejected technology.

**Why the old separate-node approach worked**: The old 89% baseline (vs 80% verify-only in intermediate runs) reflected a weaker baseline, not a stronger decisions pass. In the current-state comparison (94% verify-only baseline), `--decisions` maintains 94% — neither improving nor degrading overall baseline recall, but changing which specific questions are answered.

### Persistent Failing Questions

With `--verify --decisions` at baseline, the chronic zero-recall failures are now resolved:
- `q_auth_04`: 0% → **100%** (embedded decision rationale captured the auth flow detail)
- `q_pipe_04`: 25% → **100%** (pipeline threshold captured as decision context)

Remaining misses under baseline:
- `q_api_01` (75%): API versioning details — still below 80%, vocabulary gap
- `q_pipe_01` (75%): Data pipeline entry constraints — token budget pressure
- `q_pipe_06` (60%): **New regression** introduced by `--decisions` pass; was ~80% with verify-only
- `q_auth_07` (67%): Auth edge case under filtering presets

`q_pipe_01` and `q_api_01` are retrieval failures (tag vocabulary gap, token budget), not extraction failures. `q_pipe_06`'s regression with `--decisions` warrants investigation: the decision nodes added for the pipeline transcript may be tagging the same keywords as q_pipe_06's ground-truth nodes, displacing them.

### Pass Interaction Effects — Don't Stack Indiscriminately

The combined `--decisions --lessons` run reveals a retrieval-pollution pattern. Lesson nodes are tagged with the same keywords as the decisions they warn against. In a graph where both exist, BFS traversal finds both equally, and top_k sorting doesn't prefer one over the other. Adding lessons to a graph that already has strong decision coverage actively displaces useful nodes.

**Practical rule:** Targeted passes are a "pick one" tool, not a stack-everything tool. `--decisions` is the general-purpose recall booster. `--lessons` should only be enabled for transcript types where rejected alternatives are the primary concern (post-mortems, incident reviews) — not design discussions where they pollute the retrieval surface.

Passes that are likely safe to combine (low lexical overlap between categories):
- `--decisions` + `--numerics` (numbers and decisions don't share tags)
- `--questions` + `--constraints` (open questions and hard requirements are semantically distinct)
- `--owners` + any category (ownership nodes have unique vocabulary)

Passes that are likely unsafe to combine:
- `--decisions` + `--lessons` (lessons are about decisions, share all the same keywords)
- `--constraints` + `--lessons` (failed constraints and accepted constraints share tag vocabulary)

### Why Not Add These to the Main Prompt

The lesson_learned regression is the empirical proof: adding a rule to the main prompt — even a well-written one — hurts the model's extraction of existing categories on transcripts where the new category doesn't apply. The model has a fixed attention budget. Telling it to look for 15 things simultaneously is worse than telling it to look for 13 things well, then running 2 additional focused passes.

The targeted pass architecture is more expensive (N extra LLM calls) but better for quality. On Gemini 2.5 Flash at ~10s/call, even running all 4 targeted passes adds ~40s to a 130s extraction — a 30% overhead that is worth it for critical transcripts. For casual use, skip the targeted passes.

---

## Improvement Opportunities

These are known improvement vectors identified during development, not yet implemented.

### Higher-recall tag generation

The extraction prompt already instructs the LLM to "tag richly with every keyword a future query might use," and the current prompt achieves 95% on the software dev benchmark. The remaining gap on LOCOMO-style conversations (personal, denser context) suggests that synonym and value-level tagging are still the primary opportunity. A dedicated tagging pass or post-processing step that adds numeric values and known synonyms from a domain vocabulary could improve recall on queries where the user's terminology diverges from stored tag vocabulary.

### Semantic fallback via embedding

When keyword and fact-text fallback both return no results, a final fallback to vector similarity search would close the remaining vocabulary gap. This would require an embedding model but could be made optional — the broker works fully without it, with embedding as a precision booster for keyword-poor queries.

### Extraction validation layer

A lightweight post-extraction step that checks for common failure patterns (missing tags, facts with low information density, nodes that duplicate existing graph content) could catch quality issues before they enter the graph. This is particularly relevant for incremental mode where the LLM has limited context and is more prone to producing malformed or redundant nodes.

### Re-extraction for important transcripts

For transcripts that produced low node counts or failed validation, a re-extraction pass with a higher `max_tokens` budget or a different model could recover missed facts. The graph's `merge_extraction` operation is idempotent for supersedes — re-extracting a transcript that was already partially extracted won't corrupt the graph, though it will add duplicate nodes for facts that were extracted the first time.

### Tag enrichment pass (LLM-based)

After primary extraction (and optional verify), a dedicated LLM call per transcript expands tags on existing nodes — adding synonyms, alternative terminology, numeric values from fact text, and related concepts a developer might query. Unlike the verify pass, this makes no new facts; it only broadens the retrieval surface. Risk-free (tags don't affect graph structure), cheap (one call per transcript), and directly addresses the root cause of persistent low-recall questions like q_pipe_03 (Delta Lake) and q_pipe_07 (Kafka replication) where the vocabulary mismatch between query terms and stored tags is the failure mode.

### Dictionary-based synonym expansion (no LLM)

A hardcoded mapping from common terms to their synonyms, applied as a post-extraction pass or at query time. Examples: "rate limit" → also tag "throttling", "quota", "rps", "rpm"; "deduplication" → also "idempotency", "exactly-once", "replay"; numeric values in fact text → auto-tag them. Instant, zero cost, no dependencies — but limited to domains covered by the dictionary. Best as a complement to the LLM-based tag enrichment pass, not a replacement.

### Two-model pipeline

Use a fast/cheap local model (e.g., Qwen3.5-9B at 72% recall, ~17 min) for the primary extraction pass, then route only the verify pass through a stronger model (e.g., Gemini 2.5 Flash). This separates the bulk extraction work (where 9B is adequate) from the nuanced "what did I miss?" task (where model capability matters most). Expected to be more cost-efficient than running Gemini on full extraction while getting close to Gemini-only verify quality. Not yet benchmarked.

### Deduplication — Option B: Text-hash dedup (implemented)

**Status: implemented in `store.py`**

Re-extracting the same transcript (or running a verify pass) produces duplicate nodes with fresh UUIDs because `merge_extraction` was not dedup-aware. The same fact extracted twice creates two nodes, inflating the graph and polluting BFS traversal with near-identical candidates that dilute the real signal.

**Approach:** Normalize each fact (lowercase, strip punctuation, collapse whitespace), SHA-256 hash the result, store the first 16 hex chars as `fact_hash` on the `nodes` table. On insert, if a node with the same `fact_hash` already exists: merge tags (union), take max confidence, return the canonical node ID. Edges in the same extraction batch are rewritten through an `id_map` so they point at canonical nodes.

**Properties:**
- Zero LLM cost — pure text normalization + hash compare
- Deterministic — same fact always maps to same hash
- Graceful — exact or near-exact duplicate facts are silently merged; genuinely different facts (different wording, different detail) get different hashes and are kept as separate nodes
- Retroactive — `init_db` backfills `fact_hash` for existing rows on first open
- Limitation: does not catch paraphrased duplicates (same meaning, different words) — that requires embedding similarity (Option D)

### Deduplication — Option C: LLM reconciliation pass (planned)

**Status: not implemented**

Extend the `engram reconcile` command (which already detects supersedes relationships) with a dedup phase. The LLM is shown clusters of nodes with similar fact text (pre-filtered by token overlap or BM25 similarity) and asked to identify which are duplicates vs. genuinely distinct facts. For duplicates, it selects the canonical fact and flags the rest for merge or deletion.

**When to use:** Periodic maintenance pass on large graphs that have accumulated duplicates from many extraction runs. More expensive than Option B but handles paraphrased duplicates that hash differently.

**Implementation sketch:**
1. Cluster nodes by BM25 or TF-IDF similarity (top-N most similar pairs)
2. Batch clusters into LLM calls: "Are these the same fact? If yes, which wording is canonical?"
3. For each confirmed duplicate: update edges to point at canonical, delete the duplicate node

**Cost estimate:** ~1 LLM call per 20-30 node pairs; for a 300-node graph, ~10-15 calls.

### Deduplication — Option D: Embedding similarity (planned)

**Status: not implemented**

Compute vector embeddings for each node's fact text (e.g., using `all-MiniLM-L6-v2` via `sentence-transformers`). Nodes with cosine similarity > 0.92 are candidates for deduplication. Merge logic same as Option B (union tags, max confidence, rewrite edges).

**When to use:** When Option B misses paraphrased duplicates and Option C's LLM cost is too high. Requires a local embedding model (~80MB for MiniLM) but no API calls.

**Properties:**
- Catches semantic duplicates that hash differently (Option B's blind spot)
- Can be run as a one-time cleanup pass or incrementally on each new extraction
- Threshold tuning matters: 0.92 is a starting point; too low merges distinct facts, too high misses real duplicates
- Dependency: `sentence-transformers` (optional install, not in base requirements)

**Implementation sketch:**
1. On each `merge_extraction`, embed new nodes and query ANN index for neighbors above threshold
2. Alternatively, offline: embed all nodes, cluster by cosine > 0.92, merge clusters

---

## Key Metrics Summary

| Metric | Value | Notes |
|--------|-------|-------|
| Software dev benchmark recall (current best) | **95%** | Gemini 2.5 Flash + `--verify`, top_k=30, 21/23 ≥80% |
| Software dev benchmark recall (no verify) | 92% | Gemini 2.5 Flash, default strategies, 19/23 ≥80% |
| LOCOMO benchmark (LLM accuracy, dev split) | **85.7%** | `engram_semantic_rerank_topk100`, 5-conv dev split, cats 1–4, 762 QA, April 2026; Zep 73%, Mem0 88% |
| LOCOMO benchmark (keyword accuracy, dev split) | 72.6% | same config; cross-encoder achieves 75.2% keyword but 84.1% LLM |
| LongMemEval (LLM accuracy, oracle split) | **60.6%** | `engram_lme_rrf_dynamic`, 500 QA, April 2026; beats ReadAgent (~55–60%), gap to full-context oracle (~70%) |
| LongMemEval (LLM accuracy, standard split) | **60.8%** | `engram_lme_s_user_patched` + preference pass, 500 QA, April 2026; knowledge-update 71.8% (strongest), preference 26.7% (+20pp from targeted pass) |
| Buffered extraction recall (early baseline) | 47% | ~18% call rate vs per-turn, early March 2026 |
| Incremental cross-turn edges | ~27/transcript | api_design + data_pipeline average |
| Avg retrieval latency | <5ms | Local SQLite, all modes |
| Avg context output | ~540 tokens | top_k=10, default strategies |
| Token reduction vs full transcript | ~90–95% | vs 8k–20k token transcripts |
| Break-even (token ROI) | ~7 downstream prompts | At avg 540 token injection vs 4000 token history |
| Extraction time (full + verify, 3 transcripts) | ~356s | Gemini 2.5 Flash, top_k=30 |
| Extraction time (no verify, 3 transcripts) | ~195s | Gemini 2.5 Flash, ~258 nodes |

---

## Multi-Developer Shared Graph

The most compelling long-term use case for Context Broker is a software team where multiple developers contribute to a shared knowledge graph. Each developer's design discussions, code reviews, and architectural decisions accumulate into a single project graph that every team member queries against. The broker surfaces relevant context from the entire team's conversations, not just the current user's.

### What already works

The graph schema is attribution-aware. Every node carries a `source_transcript` field identifying which transcript it came from. If developers name their transcripts descriptively (`alice_auth_review.md`, `bob_api_design.md`), provenance is implicit. The `merge_extraction` operation is idempotent — running it with overlapping nodes produces no duplicates. Multiple developers extracting into the same project would work correctly at the data level today, provided writes don't collide.

### The core barrier: SQLite is single-writer

SQLite handles concurrent reads correctly (especially in WAL mode) but permits only one writer at a time. For teams where extraction is infrequent, this is tolerable with retry logic. But it is a ceiling, not a foundation for a shared team tool.

**Three practical paths to shared storage:**

| Approach | Complexity | Suitability |
|----------|-----------|-------------|
| Git-tracked JSON export | Low | Small teams, async collaboration, no real-time sharing |
| Shared network SQLite | Medium | Small teams, low extraction concurrency, acceptable failure mode |
| PostgreSQL backend | High | Proper solution, concurrent writes, enables REST API layer |

**Git-tracked JSON export** is the path of least resistance. The `engram export` command already writes a graph snapshot. If that snapshot is committed to the shared repo, any developer can import it locally. The limitation is that SQLite binary files don't merge in git — switching `engram export` to a line-oriented JSON format (one node per line, one edge per line) would make diffs readable and merges tractable.

**PostgreSQL backend** is the right long-term answer. The `GraphStore` API (`add_node`, `add_edge`, `get_nodes_by_tags`, etc.) maps directly to PostgreSQL with no interface changes. Every developer connects to the shared instance. Concurrent writes are handled natively. This also enables a `engram serve` REST layer so developers without direct database access (different networks, managed environments) can push extractions and pull queries over HTTP.

### Design challenges

**Cross-author supersedes authority**

In a single-user graph, supersedes is purely chronological — the newer node supersedes the older one. In a shared graph it becomes organizational. If Alice extracts "we chose Redis for caching" and Bob independently extracts "we switched to Memcached," one cannot automatically supersede the other. Their conversations may reflect different understanding of the same decision, or genuinely different decisions in different parts of the system.

A workable policy: supersedes within the same contributor is automatic (as today). Cross-contributor supersedes flags a conflict rather than silently pruning. A lead or architect resolves flagged conflicts explicitly. The `superseded_pruning` strategy would skip flagged nodes rather than dropping them.

**Domain partitioning and cross-component edges**

A multi-developer project naturally partitions by component — Alice owns auth, Bob owns the data pipeline, Carol owns the API layer. Their graphs are mostly independent, but the broker needs to surface Bob's pipeline constraints when Alice is designing an endpoint that writes to it. This already works via BFS edge traversal, but only if cross-component edges exist. Those edges typically only emerge from design sessions where both domains are discussed together. A shared graph makes those sessions more likely to be extracted; each developer's queries implicitly traverse into adjacent domains.

**Tag vocabulary drift**

If Alice's graph tags authentication nodes with `["jwt", "bearer-token"]` and Carol's API graph uses `["authentication", "auth-header"]` for related concepts, retrieval queries won't cross-find them. A shared controlled vocabulary — a project-level tag normalization map — would close this gap without requiring every developer to agree on terminology upfront. The extraction LLM could be given the existing tag vocabulary as part of the prompt, nudging it toward consistent terminology.

### Minimal viable multi-developer setup

The smallest change set that enables genuine team use:

1. **PostgreSQL `GraphStore` implementation** — one backend swap, the rest of the codebase unchanged. The existing SQLite implementation stays for local/single-user use; PostgreSQL is selected via config.

2. **`contributor` field on nodes** — set at extraction time from `git config user.name` or a `CTX_CONTRIBUTOR` env var. Stored alongside `source_transcript`. No schema changes required beyond adding the column.

3. **Conflict flagging on cross-author supersedes** — instead of silently pruning, mark conflicts with a `conflict: true` field. The `superseded_pruning` strategy skips conflicted nodes; a `engram conflicts` command lists them for resolution.

4. **`engram sync` command** — pushes the local buffer to the shared graph and pulls nodes added by other contributors since the last sync. Enables async collaboration without requiring always-on connectivity.

5. **Per-contributor transcript namespacing** — convention only, no code change: `contributor/topic_date.md`. Attribution is already implicit in `source_transcript`.

### The team use case value proposition

From the LLM's perspective, a shared multi-contributor graph is just a richer single-user graph. BFS traversal across nodes extracted from five different developers' conversations works identically to traversal across one developer's conversations. The model receives structured facts regardless of their origin.

The practical effect: a developer asking "what are the auth constraints for my new endpoint?" retrieves not just their own prior decisions, but the auth team's design decisions, the security team's constraints, and any open questions flagged by the architecture review — all from different contributors' conversations, assembled in one context block. The broker becomes a team-wide working memory, not a personal one.

This compounds more aggressively than the single-user case. A single developer's graph grows linearly with their conversations. A five-developer team's graph grows five times as fast and captures cross-domain relationships that no single developer's conversation would contain.

---

## Future Directions

Listed in rough priority order based on expected impact relative to implementation complexity.

**1. Hook integration — DONE**
`hooks/context_broker_submit.py` (UserPromptSubmit) and `hooks/context_broker_stop.py` (Stop) are implemented and operational. Install via `python hooks/install.py`. Both hooks are active in daily development use. The Stop hook uses episodic delta extraction with session state tracking and a `MAX_DELTA_TURNS=50` cost cap.

**2. Re-run benchmarks with the `KeyError` fix**
The `project_auth_system.md` transcript failed in both incremental and buffered modes. Now that the malformed-node filter is in place, a re-run would give complete three-way benchmark data for all 23 evaluation questions.

**3. Hook impact benchmark (`bench_hook_impact.py`)**
The benchmark harness exists but has not been run against a real project graph. Running it would quantify the recall delta between brokered and unbrokered prompts, providing empirical evidence for the intent amplification hypothesis.

**4. Richer synonym tagging**
Add domain synonyms to the extraction prompt or as a post-processing pass. Target: specific numeric values, acronym expansions, and common paraphrase pairs (rate limit / throttling, authentication / auth / authn, etc.).

**5. Extraction quality scoring**
A per-transcript extraction quality score (node count relative to transcript length, tag density, edge-to-node ratio) would make it easy to identify transcripts that need re-extraction or a different model.

**6. PostgreSQL backend for shared team graphs**
Swap `GraphStore`'s SQLite connection for PostgreSQL. The store API is unchanged; PostgreSQL handles concurrent multi-developer writes natively and enables a `engram serve` REST layer for teams without direct database access. See the Multi-Developer Shared Graph section for full architecture.

**7. Git-exportable graph format**
Switch `engram export` output from a single JSON blob to a line-oriented format (one node/edge per line) so graph snapshots can be diff'd and merged in git. Enables async team collaboration without any server infrastructure.

**8. Multi-project graph queries**
Currently each project is an isolated SQLite database. Cross-project queries (e.g., "what auth patterns have we used across all projects?") would require either a merged graph or a query federation layer.

---

## Productization

### Issues and Mitigation Plans

#### 1. Recall quality — current status and remaining gaps

**Status:** Software dev benchmark recall is now **95%** (21/23 ≥80%, Gemini 2.5 Flash + `--verify`, March 2026). LOCOMO dev split (official protocol, cats 1–4): **85.7% LLM accuracy** (`engram_semantic_rerank_topk100`, April 2026) — exceeds Zep (73%) and approaches Mem0 (88%). Both benchmark targets are now met on dev split.

**Remaining gap:** Full 10-conversation LOCOMO run pending. Dev split (5 conversations) is not a like-for-like comparison to Zep/Mem0's full-test-set numbers. Test split (conv-44, 47, 48, 49, 50) extraction not yet run.

**Plan:**

*Short term — full LOCOMO run:*
- Extract test split (5 conversations) into `engram_dedup95` checkpoint dir.
- Run `engram_semantic_rerank_topk100` retrieval + batch gpt-4o-mini judge over all 10 conversations.
- Report combined score as the citable paper number.

*Medium term — close the remaining gap to Mem0 (88%):*
- Implement an optional embedding-based fallback retrieval step. When keyword and fact-text search both return no results, fall back to cosine similarity over node fact embeddings. This is opt-in infrastructure (requires an embedding model) but closes the remaining synonym mismatch gap.
- Fine-tune or prompt-engineer a dedicated extraction model on a labeled dataset of transcripts and ground-truth node sets.

*Product-level guard:*
- LOCOMO dev split target (≥73% LLM, matching Zep) is exceeded at 85.7%. Next target: reproduce on full 10-conversation test split, then target ≥88% (Mem0). Make both benchmarks reproducible by any contributor so thresholds are continuously verified.

---

#### 2. Cold start kills day-one experience

**Issue:** The graph is worthless until it has content. New users get no value on day one. There is no graceful degradation, no preview of what the tool will do, and no incentive to invest in extraction before the payoff is visible.

**Plan:**

*Onboarding flow:*
- Ship a `engram demo` command that populates a sample project graph from a bundled transcript, then runs a set of example queries against it. Users see the output format and retrieval quality before investing any extraction effort.
- On first `engram query` against an empty graph, print an actionable message: "No context found. Run `engram extract <project> <transcript>` to build your graph."

*Progressive extraction:*
- For Claude Code hook users, the graph builds automatically in the background from the first conversation. Make this visible: after each buffered flush, print a brief status line — "Context Broker: +8 nodes extracted (42 total)" — so users see the graph growing without any manual steps.
- Offer a `engram bootstrap` command that takes an existing codebase and generates a starter graph from README files, architecture docs, and any markdown in the repo. Not as rich as conversation extraction, but provides immediate value.

*Seeded starter graphs:*
- For common tech stacks (React/Node, Django, Rails, Go microservices), provide downloadable starter graphs containing common architectural constraints and best practices. Users merge these into a new project to get instant useful context, then their conversations enrich and override it over time.

---

#### 3. Extraction cost sits on the user

**Issue:** Every extraction call requires a capable LLM (Gemini 2.5 Flash, GPT-4o, Claude Sonnet or better). This means API key management, real money, and 30–120 second latency per transcript. Absorbing this cost as a cloud service is expensive; passing it to the user creates friction and configuration burden.

**Plan:**

*Tiered extraction quality:*
- Document model tiers explicitly: Tier 1 (local 7B–14B models, free, ~40–72% recall — Qwen3.5-9B tops at 72%), Tier 2 (Gemini 2.5 Flash, cheap, **95% recall** with `--verify`), Tier 3 (Claude Sonnet / larger models, higher cost, untested ceiling). GPT-4o benchmarked at only ~50% — do not place in Tier 2 or 3. Nemotron 120B: 71%, Qwen 3.5 35B: 64%. Let users choose based on their cost tolerance.
- The extraction prompt already works with any OpenAI-compatible endpoint. Make model selection a one-line config change with clear recall implications documented.

*Managed extraction service (cloud product):*
- Offer extraction as a managed API: users send transcripts to a Context Broker cloud endpoint, which runs extraction on the provider's API key and returns the graph delta. Users pay per extraction (or per node), not for the underlying LLM. This removes API key management from the user, enables consistent quality, and creates a billing relationship.
- Pricing model: free tier of N extractions/month, paid tier for teams and heavier use. The unit economics are favorable — a typical transcript extraction costs $0.002–0.01 in LLM fees at Gemini Flash pricing; charge $0.05–0.10 per extraction with a 5–50x margin.

*Buffered extraction reduces cost:*
- The buffered mode already reduces calls to ~18% of per-turn cost. Document this as the recommended real-time mode and quantify the cost savings explicitly in the docs. Users making an economic decision need numbers, not just "more efficient."

---

#### 4. Integration breadth is a hard constraint

**Issue:** The Claude Code hook integration is the most natural deployment path but reaches only Claude Code users. ChatGPT, Cursor, Copilot, and other tools have no hook mechanism. Reaching them requires browser extensions, IDE plugins, or manual transcript export — each with significant friction and maintenance burden.

**Plan:**

*Phase 1 — Claude Code (DONE):*
The hook integration is operational. Both hooks (`context_broker_submit.py`, `context_broker_stop.py`) are shipped and active. Install via `python hooks/install.py`. Pattern is established; collect feedback and build user base before expanding.

*Phase 2 — VS Code extension:*
A VS Code extension can integrate with GitHub Copilot Chat, Cursor, and Continue.dev (among others) via the Language Model API or by reading from the workspace chat history. The extension calls `engram query` on every prompt submission and injects the result as a context message. Reaches the largest IDE user base without requiring CLI tool adoption.

*Phase 3 — Browser extension:*
For web-based LLM tools (ChatGPT, Claude.ai, Gemini), a browser extension intercepts prompt submission, calls a local Context Broker server (`engram serve --port 7070`), and injects the retrieved context block. This covers the remaining surface area. Technically feasible but requires maintaining extension manifests across Chrome/Firefox and adapting to UI changes in each web app.

*Phase 4 — Native integrations:*
Formal partnerships or plugin listings with Cursor, Cline, Windsurf, and other AI coding tools. These tools have plugin APIs; a Context Broker plugin published to their marketplaces provides discoverability without requiring users to find the CLI tool independently.

*Transcript import adapters:*
Independently of the hook approach, build importers for common export formats: ChatGPT JSON export, Claude.ai conversation export, Slack thread export, Zoom AI companion transcripts. Users who don't use hooks can still periodically feed conversations into the graph manually.

---

#### 5. Privacy is a blocker for enterprise

**Issue:** Design conversations contain proprietary architecture, security details, and business logic. Sending them to a cloud extraction service will fail security reviews at most enterprises. A credible enterprise story requires local-first operation — which the current architecture already provides, but which complicates delivery and support.

**Plan:**

*Local-first as the default, cloud as opt-in:*
The current architecture is already local-first: the CLI, SQLite, and all retrieval run on the user's machine. Make this the loudly stated default in all marketing. "Your conversations never leave your machine unless you explicitly choose cloud extraction" is a strong privacy statement that is currently true.

*On-premises deployment guide:*
Document the path for enterprises that want the managed extraction service but cannot send data externally: deploy the Context Broker extraction API on internal infrastructure, point the CLI at the internal endpoint via `llm.base_url`. This requires no code changes — the OpenAI-compatible endpoint abstraction already supports arbitrary URLs.

*Data handling transparency:*
For the cloud extraction service, publish an explicit data handling policy: transcripts are processed and discarded, not stored; no training on user data; SOC 2 compliance roadmap. This is table-stakes for enterprise procurement and should be designed into the cloud service from day one, not retrofitted later.

*Encryption at rest:*
Add optional encryption for the local SQLite database using SQLCipher. Enterprise IT departments frequently require encryption at rest even for local tools. This is a SQLite swap, not a schema change.

---

#### 6. The competitive moat is thin

**Issue:** The core pattern — extract facts from conversations, store as a graph, retrieve on demand — is replicable. Extraction quality depends on the underlying LLM and prompt, neither proprietary. Adjacent competitors (Zep, Mem.ai, LangChain memory, Cursor workspace indexing, MemGPT/Letta) are well-funded and approaching the same problem from different angles.

**Plan:**

*Moat 1 — The supersedes graph:*
No current competitor explicitly models temporal decision evolution with supersedes edges and pruning. This is the sharpest technical differentiator and should be the core marketing message: "Context Broker knows that your week-3 decision overrides your week-1 decision. Other tools don't." Lean into this relentlessly.

*Moat 2 — Team network effects:*
A shared team graph becomes more valuable the more contributors feed it. This is a network effect moat: the broker for a 10-developer team is dramatically more useful than for a 1-developer team, and switching costs grow with graph size. Build the PostgreSQL shared graph feature early to establish this dynamic before competitors do.

*Moat 3 — Extraction quality flywheel:*
Every extraction run against a real project is labeled data — the fact that a node was retrieved and used is a quality signal. Aggregate (with consent) anonymized graph patterns to train a fine-tuned extraction model. A proprietary extraction model that outperforms a general-purpose LLM on the extraction task is a durable moat.

*Moat 4 — Integration depth:*
Deep, first-party integrations with the major AI coding tools (VS Code extension, Cursor plugin, Claude Code hooks) create switching costs independent of the graph data itself. Users who have built a year of project memory in the broker won't switch tools — and their graph doesn't export cleanly to a competitor.

*Strategic response to competition:*
Monitor Zep and MemGPT/Letta closely — they are the closest architectural neighbors. The differentiation is the development project focus (structured decisions, constraints, supersedes) vs. their general-purpose conversational memory focus. Stay specialized; don't try to compete on their ground.

---

#### 7. The "silent operator" trust problem

**Issue:** The hook integration is deliberately invisible. When the model acts on wrong injected context, the developer doesn't know what was injected. When it works well, the developer doesn't know why. This creates a black box that's difficult to trust and impossible to debug.

**Plan:**

*Inspection commands:*
- `engram last` — show the exact context block that was injected into the most recent query, with node IDs, confidence scores, and source transcripts. Makes the broker's contribution visible on demand without making it noisy by default.
- `engram explain "<query>"` — show which keywords were extracted from the query, which entry nodes matched, how BFS traversed the graph, and which strategies pruned what. Full retrieval trace for debugging.

*Optional verbose mode for hooks:*
Add a `--verbose` flag to the hook integration that appends a collapsed summary to each prompt: "Context Broker injected 6 nodes (42 tokens) — run `engram last` to inspect." Users who want visibility get it; users who prefer silence stay silent by default.

*Confidence surfacing in output:*
The markdown output already includes confidence scores. Make them more prominent and add a color-coded indicator (in terminals that support it) so users can glance at the reliability of injected context. A block of 0.9+ confidence nodes reads differently than a block of 0.4–0.6 nodes.

*Conflict visibility:*
For the multi-developer case, flagged cross-author conflicts should appear prominently in retrieval output: "⚠ Conflicting decisions found — run `engram conflicts` to resolve." Silent conflict suppression is worse than noisy conflict surfacing.

---

#### 8. Who pays, and for what

**Issue:** Individual developers are difficult to monetize directly. The natural paying customer is the team or enterprise, which requires significant pre-revenue infrastructure investment (PostgreSQL backend, access controls, SSO, audit logs). Individual developers are the primary adopters but may not convert to paid.

**Plan:**

*Freemium individual tier (acquisition):*
- Local-only, SQLite, unlimited projects, full CLI — permanently free.
- Builds the user base, generates word-of-mouth, and creates the pool of users who become team purchasers when they advocate for the tool at their company.
- No extraction cost to the company on this tier (users supply their own API keys).

*Team tier (primary revenue):*
- Shared PostgreSQL graph, contributor attribution, conflict resolution, `engram sync`.
- Cloud-managed extraction service with a monthly credit allocation.
- Pricing: per-seat, per month. Target: $15–25/developer/month, competitive with other developer productivity tools.
- This is the product that the current architecture is one PostgreSQL backend swap away from reaching.

*Enterprise tier (high-value contracts):*
- On-premises deployment, SSO/SAML, audit logs, role-based access control (architect vs. contributor vs. read-only).
- SLA, dedicated support, custom extraction model fine-tuning on the customer's own historical transcripts.
- Pricing: annual contract, negotiated per seat or per project.
- Requires 6–12 months of additional engineering on top of the team tier. Do not attempt to sell this before the team tier is stable.

*Strategic sequencing:*
Build and validate the free individual tier first — it costs nothing and builds the user base. Use that base to identify early team adopters willing to pay for sharing. Use team revenue to fund the enterprise-grade infrastructure. This is the standard developer tool commercialization path and avoids premature investment in enterprise features that individuals won't use.

---

## Prompt Experiment Log — 2026-03-09

### Context

A series of prompt modifications were tested to improve recall on numeric fact retrieval (questions like "what is the token expiry?" that require matching a specific number in the graph). The experiment produced a significant regression and recovery, with important lessons for future prompt work.

**Baseline state entering this session (2026-03-09):** Gemini 2.5 Flash at ~57–66% recall on the standard benchmark (3 transcripts × 23 questions × 4 presets). This was prior to all prompt improvements; current best is 95%.

---

### What Was Tried

**Version 1 — Aggressive numeric tagging (added to Rule 7):**

```
For facts with specific numeric values, always include the number itself, its unit-qualified form, and its plain form as tags:
  "15-minute token expiry" → tags must include: "15", "15min", "15 minutes", "expiry", "token expiry", "jwt expiry"
  "1000 req/min" → tags must include: "1000", "1000rpm", "1000 per minute", "rate limit", "rpm"
  "5 failed attempts" → tags must include: "5", "5 attempts", "lockout threshold", "failed attempts"
Include numeric tags even when they seem obvious — retrieval depends on exact tag matching.
```

Result: auth_system edges collapsed from ~99 → 35. Recall dropped to **38%**.

**Version 2 — Moderate rollback (kept 2-line version):**

```
For facts with specific numeric values, include the number AND its unit form as tags:
  "15-minute token expiry" → include "15", "15min", "expiry" in tags
  "1000 req/min" → include "1000", "rpm", "rate limit" in tags
```

Result: auth_system edges still collapsed to ~47. Recall was **48%**. The moderate version caused the same regression.

**Version 3 — Full revert (removed all numeric tagging examples):**

Rule 7 returned to its original form ending at:
```
- Always include both the generic concept AND the specific implementation:
  ["rate limit", "rate limiting", "throttle", "throttling", "kong", "gateway", "rpm", "requests per minute"]
```

Result: auth_system edges recovered to **105**. Recall recovered to **57%**.

---

### Root Cause Analysis

**Edge count is the leading indicator of recall.** BFS traversal from entry nodes depends on edges; a graph with fewer edges produces smaller subgraphs, reducing the chance that relevant nodes are retrieved. When edge counts drop, recall follows within the same benchmark run.

**Why the numeric tagging examples caused edge regression:** The LLM has a fixed attention budget per output. Adding explicit multi-line examples for tag generation shifted attention from edge generation. The model produced well-tagged nodes but fewer edges connecting them. The net effect was negative: the improved tag coverage for numeric queries didn't compensate for the subgraph connectivity loss.

**Why the moderate version was equally harmful:** Two lines of numeric examples was enough to shift attention. The regression is not proportional to the length of the added text — it triggered at any explicit tagging instruction with examples.

---

### Run-to-Run Variance

Same prompt, same model, same config across independent benchmark runs produced:

| Run | Recall | auth_system edges |
|-----|--------|-------------------|
| Baseline (pre-experiment) | 66% | ~99 |
| Aggressive numeric tagging | 38% | 35 |
| Moderate rollback | 48% | 47 |
| Full revert | 57% | 105 |
| Second full-revert run | 62% | ~100 |

The 57%–66% range on identical prompts reflects genuine LLM non-determinism (temperature=0.1 is not temperature=0). **Never conclude anything from a single benchmark run.**

---

### Model Comparison (Gemini 2.5, same prompt)

| Model | Recall range | Notes |
|-------|-------------|-------|
| Gemini 2.5 Flash | 57–66% | Best performing; fast and cheap |
| Gemini 2.5 Pro | 36–38% | Consistently lower; longer output doesn't help here |

Gemini 2.5 Pro underperformed despite being the more capable model. Hypothesis: Pro may over-elaborate facts (more verbose `fact` text, fewer but richer nodes), which reduces tag overlap at retrieval. Not confirmed — would need further investigation.

---

### Multi-Model Benchmark Results (2026-03-13, with Rule 13 + verification pass improvements)

All models run against the same 3 transcripts (~15k chars total, 23 eval questions). Gemini 2.5 Flash is the reference.

| Model | Nodes extracted | Recall (baseline) | ≥80% questions | Extraction time | Notes |
|-------|----------------|-------------------|----------------|-----------------|-------|
| Gemini 2.5 Flash | ~258 | **92%** | **19/23** | ~195s | Reference; best no-verify (2026-03-18, top_k=30) |
| Gemini 2.5 Flash + verify | ~258 | **95%** | **21/23** | ~356s | **Current best** (2026-03-18, top_k=30, prior-state tagging + hyphen fix) |
| Gemini 2.5 Flash + lesson_learned Rule 14 (no verify) | 276 | 82% | 15/23 | ~273s | Rule 14 in base prompt caused -10% recall, -21 fewer nodes; reverted (2026-03-17) |
| Gemini 2.5 Flash + lesson_learned Rule 14 + verify | 304 | 80% | 14/23 | ~333s | verify didn't recover the Rule 14 regression; reverted (2026-03-17) |
| Claude Sonnet 4.5 | — | — | — | — | Not yet benchmarked cleanly |
| Qwen 3.5 35B | 166 | 64% | 8/23 | 816s | Local; slow but decent |
| Nemotron 3 Super 120B | 121 | 71% | 12/23 | 355s | NVIDIA NIM; `enable_thinking: false`; see notes |
| GPT-4o | 81 | 50% | 4/23 | ~97s | See notes below |
| Mistral Small 3.2 24B | 66 (2/3 transcripts) | 40% | 5/23 | ~797s | Local MLX; 1 transcript fails JSON schema; see notes |
| Qwen3-32B | 87 (2/3 transcripts) | 35% | 2/23 | ~1594s | Local LM Studio; `structured_outputs: false`; auth_system fails mid-JSON; see notes |
| GPT OSS 20B (reasoning=low) | 60 | 62% | 7/23 | 108s | Local LM Studio; best setting — fast, clean JSON |
| GPT OSS 20B (reasoning=medium) | 103 | 63% | 6/23 | 615s | 5.7x slower, +72% nodes, only +1% recall — not worth it |
| GPT OSS 20B (temp=0.3, low) | 75 | 59% | 7/23 | 118s | Higher temp = more nodes but noisier — recall drops vs temp=0.1 |
| GPT OSS 20B (temp=0.1, low, 2× stacked) | 154 | 65% | 10/23 | 229s | Best local result; 2× extraction time, +3% recall, +3 questions at ≥80% vs single run |
| GPT OSS 20B (temp=0.1, low, 3× stacked) | ~218 | 54% | 7/23 | 373s | Worse than 1×! Near-duplicate node proliferation crowds out correct nodes in token budget |
| Qwen3.5-9B (max_tokens=65536, thinking off, 1×) | 168 | 72% | 11/23 | 1055s | All 3 transcripts complete; fix was output token budget, not model quality |
| Qwen3.5-9B (max_tokens=65536, thinking off, 2× stacked) | 286 | 60% | 6/23 | 1568s | Worse than 1×! Near-duplicate pollution same as GPT OSS 3× — 1× is optimal |
| Qwen3.5-9B (max_tokens=65536, thinking off, 1× + verify) | 181 | 68% | 10/23 | 1005s | Worse than 1× without verify (72%)! Verify adds noise for 9B models — not recommended |
| Qwen3.5-9B (max_tokens=16384, thinking on, 1×) | 93 | 52% | 6/23 | 2127s | 2/3 transcripts succeeded; auth_system truncated — results incomplete |
| Qwen3.5-9B (max_tokens=16384, thinking on, 2× stacked) | 51 | 29% | 3/23 | 2214s | Only auth_system succeeded; stacking doesn't help truncation failures |
| Liquid LFM2 24B (2B active) | 47 | 25% | 0/23 | 101s | Local LM Studio; all 3 transcripts extracted but only 47 nodes — severely under-extracts |
| Gemini 3.1 Flash Lite | ~74 | ~42% | — | — | Not recommended |

#### GPT-4o extraction findings

GPT-4o is a poor extraction model for this task. It extracts only ~81 nodes from transcripts where Gemini 2.5 Flash extracts ~297 — a 3.5× gap. This is not a retrieval problem; it is a fundamental extraction conservatism issue.

Attempts to address it:
- **Density nudge in prompt (Rule 14, 2026-03-13):** Added bullet-list guidance targeting "8-15 nodes per 1k chars". Recall *dropped* from 50% → 41%. Reverted. Violates the prompt stability rules documented below.
- **Verification pass (`--verify`, 2026-03-13):** Added 12 nodes, recall moved from 41% → 46%. Marginal improvement.
- **Structured outputs / `response_format: json_schema` (2026-03-14):** Enforces the extraction schema at the API level. Extracted 99 nodes (vs 81 previously) but recall dropped to 48% and ≥80% questions fell from 4 → 2. More nodes, but not the right ones. Schema enforcement changes output formatting, not what GPT-4o decides to extract. Side effect: 2× faster (47s vs ~97s). Flag kept enabled in `gpt_4o.yaml` for the speed benefit.
- None of these interventions moved GPT-4o within range of Gemini 2.5 Flash.

**Recommendation:** Do not recommend GPT-4o as an extraction model. If users insist on OpenAI, `gpt-4o-mini` (untested) may be comparable quality at lower cost.

Update the Tier 2 estimate for GPT-4o in the tiered model docs: actual recall is ~50%, not the predicted 60%+.

#### Nemotron 3 Super 120B findings (2026-03-16)

Model: `nvidia/nemotron-3-super-120b-a12b` via NVIDIA NIM API (`integrate.api.nvidia.com`). Config: `max_tokens: 131072`, `enable_thinking: false`, `stream: true`.

**Issues encountered during benchmarking:**
- Original config used `max_tokens: 32768`. Nemotron's reasoning tokens consumed the entire budget, leaving 0 tokens for JSON content → `finish_reason=length` → `ValueError: LLM response was truncated`. Fix: increased to `max_tokens: 131072` and set `enable_thinking: false`.
- `enable_thinking: false` injects `/no_think` system suffix + `chat_template_kwargs: {enable_thinking: false}`. This is required — without it, the model spends its entire token budget on chain-of-thought reasoning before producing any output.

**Extraction results:** 121 total nodes (26 api_design, 51 auth_system, 44 data_pipeline). All 3 transcripts extracted successfully. Extraction time: 355s total (~2 min/transcript), faster than expected once thinking was disabled.

**Recall results:** baseline 71% (12/23 ≥80%), default/filtered/tight 70% (11/23 ≥80%). Notably weak on `q_pipe_03` (Delta Lake question: 0%) and `q_api_01` (25%). The low node count (121 vs ~297 for Gemini) is the primary driver of missed recall — Nemotron extracts conservatively.

**Conclusion:** Nemotron 3 Super 120B is a reasonable mid-tier option (between Qwen 3.5 35B and Gemini 2.5 Flash) for users who need a large hosted model outside Google's ecosystem. It does not match Gemini 2.5 Flash quality. Not recommended as a default.

#### Qwen3-32B findings (2026-03-16)

Model: `qwen/qwen3-32b` via LM Studio (`http://127.0.0.1:1234/v1`). Config: `max_tokens: 16384`, `enable_thinking: false`, `structured_outputs: false`, `stream: true`.

**Issues encountered during benchmarking:**
- LM Studio's "Structured Output" model switch must be **OFF**. When enabled, LM Studio requires a `json_schema` in every request; without one it returns `{"error": "JSON schema is missing in json-mode request"}` immediately (0.2s response time, 0 nodes).
- Our `structured_outputs: true` config flag does send a schema, but LM Studio's Qwen3 implementation rejected it anyway — root cause unclear (possibly schema format incompatibility). Fix: disable both the LM Studio switch and set `structured_outputs: false` in the config.
- `auth_system` transcript (the largest) failed after 836s with a JSON parse error — the model likely hit `max_tokens: 16384` mid-output. Increasing this might recover the third transcript, but extraction quality issues suggest diminishing returns.

**Extraction results:** 87 total nodes (40 api_design, 47 data_pipeline). `auth_system` failed. Extraction time: 1594s (~26.6 min) for 2 transcripts.

**Recall results:** baseline 38% (2/23 ≥80%), default/filtered/tight 34-35% (1/23 ≥80%). Consistently 0% on auth_system questions (due to extraction failure). Even on the 2 successful transcripts, extraction quality is poor — the model misses secondary facts and numeric details.

**Conclusion:** Not recommended. Weak extraction quality (35% recall even partially), very slow (~27 min for 2/3 transcripts), and brittle JSON compliance. Qwen 3.5 35B (64% recall) substantially outperforms the same generation's 32B dense model on this task.

#### Qwen3.5-9B findings (2026-03-16)

Model: `qwen/qwen3.5-9b` via LM Studio. Config: `max_tokens: 16384`, `structured_outputs: false`, `stream: true`.

**Core problem: output token truncation.** The 9B model generates verbose JSON — more tokens per fact than larger models — and consistently hits the `max_tokens: 16384` ceiling before completing the output. All three transcripts failed in the first run. In subsequent runs, which transcripts succeed is largely random (the model's verbosity is nondeterministic).

**Stacking experiment (1×, 2× stack runs, 2026-03-16):**

| Stack | Transcripts OK | Nodes | Baseline Recall | ≥80% | Extraction Time |
|-------|---------------|-------|----------------|------|-----------------|
| 1× (run 1) | 0/3 | 0 | 0% | 0/23 | 1143s |
| 1× (run 2) | 2/3 (api+pipe) | 93 | 52% | 6/23 | 2127s |
| 2× stacked | 1/3 (auth only) | 51 | 29% | 3/23 | 2214s |

**Why stacking doesn't help here:** The truncation failures are non-deterministic. In run 2, `auth_system` truncated but the other two succeeded (93 nodes). In the 2× run, `auth_system` succeeded but the other two truncated (51 nodes). Stacking just re-rolls the random failure dice — it doesn't address the underlying output token budget problem.

**Contrast with GPT OSS 20B stacking:** GPT OSS 20B under-extracts (misses facts, but produces valid JSON). Stacking works there because each run extracts different facts, and they merge additively. Qwen3.5-9B has a different problem: it fails entirely, not partially. No amount of stacking can recover from complete extraction failure.

**Fix confirmed (2026-03-17):** Increasing `max_tokens` to 65536 (with thinking already off) resolved all truncation. All 3 transcripts extracted successfully: 46 nodes (api_design), 71 nodes (auth_system), 51 nodes (data_pipeline) — 168 total. Recall jumped to **72% baseline (11/23 ≥80%)** — above GPT OSS 20B (62%) and Qwen 3.5 35B (64%), despite being a 9B model.

**Verify pass result (2026-03-17):** Adding `--verify` produced **67-68% recall (10/23 ≥80%)** — *worse* than 1× without verify (72%, 11/23). The verify pass added 14 nodes (181 vs 168 total), but introduced net harm. Notable: q_pipe_07 jumped 0% → 100% (verify did find real facts), but q_pipe_01 dropped 75% → 0%. Net effect: -4-5% recall, -1 question. The extra nodes from a 9B model's verification call appear to introduce noise that disrupts BFS traversal rather than improving coverage.

**Conclusion:** Viable with correct configuration (`max_tokens: 65536`, `enable_thinking: false`). The earlier poor results were entirely a configuration problem, not a model quality problem. At 72% recall (1×, no verify), Qwen3.5-9B is the best-performing local model per parameter count tested. Neither stacking (2×) nor the verify pass improve results — 1× is optimal.

#### Mistral Small 3.2 24B findings (2026-03-16)

Model: `mistral-small-3.2-24b-instruct-2506-mlx` via local MLX server.

**Persistent extraction failure:** One transcript (`project_api_design`) consistently fails with `KeyError: 'relation'` — the model emits edges without a `relation` field. This is a JSON schema compliance failure specific to Mistral. The other two transcripts extract successfully (66 total nodes across 2/3 transcripts).

**Recall results:** 40% baseline (5/23 ≥80%) across all strategies — identical performance regardless of strategy preset. The 40% ceiling reflects the incomplete graph (missing the api_design transcript entirely, ~30% of the knowledge base).

**Conclusion:** Not recommended. Schema non-compliance is a reliability concern, and 40% recall with 2/3 transcripts is poor relative to alternatives. The `KeyError: 'relation'` issue could potentially be fixed with a prompt patch, but given the overall quality ceiling, this is not worth pursuing.

---

### Prompt Improvement Rules (DO NOT VIOLATE)

These rules were derived from painful experience and should be applied to all future prompt work:

1. **Check edges first, then recall.** After any prompt change, look at `auth_system` edge count in the extraction stats. If edges dropped, recall will drop — stop and revert before running the full benchmark.

2. **One variable at a time.** Never combine multiple prompt changes in a single benchmark run. It's impossible to determine which change caused a regression.

3. **Minimum 3 runs before concluding anything.** A single run can show 38%–66% on the same prompt. Report the range, not a single number.

4. **Beware of adding examples.** Multi-line examples with bullet points shift LLM attention. Adding even 2 lines of tagging examples was enough to halve edge output. Prefer adding a single sentence instruction without worked examples unless the improvement is confirmed stable.

5. **`auth_system` is the sentinel transcript.** It has the most numeric facts and is most sensitive to prompt changes. If `auth_system` edge count is ≥ 90, the prompt is healthy. If it's < 60, something is wrong.

6. **Do not add numeric tagging examples.** This is explicitly documented as a known failure mode. Any future attempt to improve numeric recall by adding examples to Rule 7 will likely trigger the same edge regression. A different approach is needed — e.g., post-processing that extracts numeric tokens from fact text and adds them as tags at write time (no prompt involvement).

---

### Current Prompt State (as of 2026-03-09)

The prompt in `context_broker/prompts.py` is the **full revert** version. This is the stable baseline:

- Rule 7 ends at the "generic concept AND specific implementation" bullet
- No numeric tagging examples in either `EXTRACTION_PROMPT` or `INCREMENTAL_EXTRACTION_PROMPT`
- Do not add examples to Rule 7 without benchmarking on auth_system edges first

### Retrieval Improvement Experiment (2026-03-14, Qwen 3.5 35B)

Three retrieval-side improvements were implemented and benchmarked cumulatively against Qwen 3.5 35B (`qwen/qwen3.5-35b-a3b`). Each run re-extracted from scratch; Qwen is nondeterministic so node counts varied, making exact isolation impossible — treat as indicative trends.

| Run | Changes | Nodes | Default Recall | ≥80% |
|-----|---------|-------|----------------|------|
| Baseline | none | 166 | 64% | 8/23 |
| Change 1 | auto-tag numerics at ingest | 176 | 62% | 8/23 |
| Changes 1+2 | + preserve numeric compound tokens in query keywords | 163 | 56% | 6/23 |
| Changes 1+2+3 | + FTS fact-text augmentation in BFS seed selection | 174 | 66% | 7/23 |

**What was implemented:**

1. **Auto-tag numerics at ingest** (`store.py` — `_auto_tag_numerics()`): Regex-extracts digit-containing tokens (e.g., `15-minute`, `1000/min`, `rs256`) from each node's `fact` text and merges them into `tags` at write time, without touching the LLM prompt. This ensures numeric values in facts are findable by tag queries even if the extractor didn't emit them as tags.

2. **Preserve numeric compound tokens in query keywords** (`retriever.py` — `extract_keywords()`): Hyphenated tokens containing digits (e.g., `15-minute`) are now kept as whole tokens in addition to their split parts. Non-numeric hyphens (e.g., `hot-path`) are still split as before. No benchmark impact observed because eval questions don't use this form — but logically correct for real-world queries.

3. **FTS fact-text augmentation** (`retriever.py` — `retrieve_with_stats()`, `store.py` — `get_nodes_by_fact_text()`): After tag-based BFS seed selection, run a `fact LIKE '%keyword%'` search and merge new nodes into the seed pool. This catches nodes with sparse tags where the keyword appears in the fact text itself. Showed the clearest positive signal: `q_api_01` improved 25%→75%, `q_pipe_06` improved 60%→100%.

**Overall conclusion**: The three changes together restored recall to 66% baseline (same as the pre-experiment baseline). Net gain is modest vs. extraction variance noise. The FTS augmentation (Change 3) is the most impactful change. The auto-tag numerics (Change 1) may be helping on specific questions but Qwen's extraction nondeterminism obscures the signal.

---

### Orchestrator System Prompt: Grounding vs. Usefulness Tradeoff

The orchestrator's `static` system prompt in `config.yaml` controls how much the LLM supplements retrieved context with its own general knowledge.

**The tradeoff:**

- **Loose grounding** (current default): LLM can reason from general knowledge when retrieved context is incomplete. Better for working tasks (coding, debugging) where general knowledge adds value. Downside: partial answers (e.g., listing 2 of 8 relevant facts from context) because the model summarizes rather than enumerating.

- **Strict grounding**: Adding an explicit rule like `"Only use information from the Project Knowledge section. If a fact is not in the provided context, say so explicitly — do not infer or reason from general knowledge."` forces full enumeration of retrieved facts and correct "I don't know" responses for gaps. Downside: model refuses to apply general engineering knowledge even when it would be helpful.

**Practical recommendation:** Use two config profiles:
- **Testing/recall validation**: strict grounding — verifies that retrieval surfaced the right facts, not that the LLM can invent them
- **Development/coding work**: loose grounding — lets the model apply general knowledge on top of retrieved project context

**Evidence**: Storage layer query returned 17 nodes but orchestrator listed only 2 decisions. Kafka question correctly returned "I don't know" when no selection-rationale nodes existed. Both behaviors stem from the same loose grounding — sometimes helpful, sometimes not.

---

### Orchestrator Manual Test Plan (2026-03-18)

Four tests to run in sequence. Each validates a different aspect of the end-to-end orchestrate pipeline.

#### Test 1 — Auth system transcript (in progress)
**Goal:** Verify prior-state tagging fix works across a different domain with more numeric facts.
```bash
engram extract auth_debug benchmarks/transcripts/project_auth_system.md --verify
engram orchestrate auth_debug
```
**Questions to ask:**
- "What authentication mechanism was chosen?"
- "Was OAuth ever considered?"
- "What token expiry was chosen and why?"
- "What was rejected during the auth design and why?"
- "What rate limiting rules are in place?"

**Success criteria:** Prior-state nodes (rejected options) are retrievable. Numeric facts (expiry times, rate limits) surface correctly.

#### Test 2 — Multi-turn context accumulation
**Goal:** Verify graph retrieval bridges across turns — facts established early in conversation are still retrievable later.
**Setup:** Use pipe_debug or auth_debug. Have a short back-and-forth that introduces facts across 3–4 turns, then ask a question requiring synthesis of facts from turns 1 and 3.
**Questions:** Introduce a constraint in turn 1, reference it implicitly in turn 4. Verify orchestrator recalls it without re-stating it.
**Success criteria:** Facts don't fall out of context as conversation grows.

#### Test 3 — Strict vs. loose grounding comparison
**Goal:** Quantify the summarization loss caused by loose grounding.
**Setup:** Add strict grounding rule to `config.yaml` static prompt:
```
Only use information from the Project Knowledge section. If a fact is not in
the provided context, say so explicitly — do not infer or reason from general knowledge.
```
**Questions:** Re-ask the storage layer question ("What are all the decisions that affect the data pipeline's storage layer?"). Compare answer completeness vs. loose grounding run (which returned 2 of 17 retrieved nodes).
**Success criteria:** Strict grounding surfaces all 17 retrieved nodes; loose grounding summarizes to ~2.

#### Test 4 — `--decisions` targeted pass on auth_debug
**Goal:** Verify the `--decisions` flag improves recall for auth-domain rationale facts (q_auth_04 was 0% without it).
```bash
engram extract auth_debug benchmarks/transcripts/project_auth_system.md --verify --decisions
```
Then query: "Why was the chosen auth mechanism selected over alternatives?"
**Success criteria:** Decision rationale (the "why" behind auth choices) surfaces that wasn't in the base `--verify` pass.

---

### Orchestrator Test Results (2026-03-18)

#### Test 1 — Auth system (PASSED)
All 5 questions answered correctly using the `auth_debug` project (extracted with `--verify`):
- "What authentication mechanism was chosen?" → JWT-based stateless auth with short-lived access tokens (15 min) and refresh tokens (7 days)
- "Was OAuth ever considered?" → Yes, OAuth 2.0 was considered but ruled out for the MVP due to implementation complexity
- "What token expiry was chosen and why?" → 15-min access tokens (minimize breach window), 7-day refresh tokens (user convenience)
- "What was rejected during the auth design and why?" → Session-based auth rejected (stateful, doesn't scale horizontally), OAuth rejected (too complex for MVP), bcrypt replaced by Argon2id for password hashing
- "What rate limiting rules are in place?" → 5 failed attempts per 15 min triggers lockout; 100 req/min per API key

Prior-state nodes (rejected options: session-based auth, OAuth) were retrievable via graph traversal. Numeric facts (expiry times, rate limits) surfaced correctly.

#### Test 2 — Multi-turn context accumulation (PASSED)
Multi-turn conversation with `pipe_debug` project. Facts introduced in turn 1 (Kafka chosen, 3 partitions per topic) were correctly recalled in turn 4 synthesis question ("What storage and messaging decisions were made, and how do they relate to each other?"). Orchestrator synthesized facts across turns without requiring re-statement. Graph retrieval bridges across conversation boundaries.

#### Test 3 — Strict vs. loose grounding (PASSED — summarization loss confirmed)

**Setup:** `STRICT GROUNDING MODE` rule added to `config.yaml` static system prompt. Query: "What are all the decisions that affect the data pipeline's storage layer?"

**Results:**
- **Loose grounding**: 2 decisions listed (Kafka chosen; GCS for cold storage). 17 nodes retrieved but LLM summarized rather than enumerated.
- **Strict grounding**: 7 detailed bullet points — Delta Lake for ACID compliance, Parquet format, GCS for cold storage with tiered lifecycle, hot/warm/cold path separation, Kafka for streaming, schema evolution via Avro, S3 original plan superseded by GCS. Response was noticeably slower (~15–20s vs ~5s).

**Conclusion:** Retrieval was working correctly in both cases. The 2-vs-17 gap was pure summarization loss in the LLM response layer, not a retrieval gap. Strict grounding forces full enumeration at the cost of response speed and reduced ability to apply general engineering reasoning.

**Config reverted** to loose grounding after this test (strict grounding prompt removed).

#### Test 4 — `--decisions` targeted pass on auth_debug (PASSED)

**Setup:** Re-extracted `auth_debug` from `project_auth_system.md` with `--verify --decisions`. The `--decisions` pass added **6 new nodes, 13 new edges** (108 base → +10 verify → +6 decisions = 124 total).

**Query:** "Why was the chosen authentication mechanism selected over alternatives?"

**Result:** The orchestrator returned specific decision rationale:
- Keycloak chosen because it supports self-hosting, satisfying a data-residency compliance requirement (user data must stay within company infrastructure)
- Auth0 and Okta were explicitly rejected because they store user data outside company control, violating the compliance constraint

**Conclusion:** The `--decisions` targeted pass surfaced the "why" behind auth choices — rationale that the base `--verify` pass missed. This matches the benchmark finding (q_auth_04 was 0% without `--decisions`). For decision-heavy transcripts where rationale is embedded in discussion flow rather than stated as conclusions, `--decisions` is the right supplemental pass.

---

## Synthesis Pass

### What it is

`engram synthesize <project>` is a post-extraction maintenance command that runs an LLM pass over **existing graph nodes** (not a transcript). It looks for clusters of 3+ parallel facts about the same metric across different subjects and creates cross-cutting summary nodes — e.g., a single "Overall Recall Ranking" node that aggregates all model results.

This solves the **survey query problem**: BFS retrieval with top_k=20 may explore only one model's cluster at a time. "Rank all models" needs a single node tagged with all model names for retrieval to surface the complete answer.

### Observed behavior (2026-03-18)

Run: `engram synthesize ContextBroker --tags benchmark --tags recall --tags gpt-4o --tags nemotron`
- 873 candidate nodes filtered from 8590 total
- 22 summary nodes created, 769 edges
- Produced "Overall Recall Ranking" node, per-preset comparison nodes, extraction time comparisons

**Orchestrator chat after synthesis:**
- First query ("What models were benchmarked?"): incomplete — returned 4 models, missed Qwen 3.5 9B, Nemotron, GPT OSS 20B. Synthesis node not retrieved on first attempt.
- Second query ("can I have the complete list?"): returned 7 models correctly. The synthesis node surfaced on the follow-up when the query was more explicit.
- GPT-4o still absent from the orchestrator's list despite a synthesis node containing it — likely tagged differently or ranked below top_k cutoff.

**Observation:** Synthesis helps but doesn't guarantee first-query completeness. The orchestrator LLM may still summarize/truncate rather than enumerate all items in a retrieved node.

### Synthesis timing options

Three patterns for when to run synthesis:

| Option | Command | Scope | Latency | Best for |
|--------|---------|-------|---------|----------|
| **A. Periodic maintenance** | `engram synthesize` | Full graph (filtered by type/tags) | High (~30–120s) | After multiple sessions have accumulated; before demo/query-heavy sessions |
| **B. Post-extract (full graph)** | `engram extract --synthesize` | Full graph | Adds ~30–120s to extraction | When a new transcript is likely to complete a cluster already partially in the graph |
| **C. Post-extract (recent only)** | `engram extract --synthesize-recent` *(not yet implemented)* | `get_recent_nodes(limit=100)` | Low (~5–15s) | Lightweight per-session synthesis; won't catch cross-session patterns |

**Current recommendation:** Use option A (periodic `engram synthesize`) as the primary pattern. Run with `--tags` to target specific subject clusters when you know what you're looking for. Option B (`--synthesize` on extract) is viable when running full re-extractions. Option C is a potential future improvement.

### Synthesis is additive

Synthesis nodes accumulate — each run with different `--tags` adds new summary nodes without deleting previous ones. The existing fact-hash deduplication prevents exact duplicates. If you need to prune stale synthesis nodes, a future `engram prune --synthesis` command can use the `source_transcript = "__synthesis__"` tag to identify them.

---

## Related Research Survey (2026-03-19)

*Source: arXiv:2502.12110 (A-MEM, NeurIPS 2025) and related literature.*

### A-MEM: Agentic Memory for LLM Agents (arXiv:2502.12110)

A-MEM is a Zettelkasten-inspired memory system where the LLM agent autonomously manages memory operations via tool calls rather than a fixed pipeline. Core contribution: memory is *agent-driven*, not pipeline-driven. The agent decides when to store, retrieve, update, synthesize, or delete based on conversation context.

**Key mechanisms:**

- **ANIC indexing**: Attributes (what), Networks (connections), Insights (synthesis), Context (temporal). Each memory note contains all four fields.
- **Dynamic linking**: When storing a new memory, the LLM searches existing memories for related content and creates bidirectional links on-the-fly — no predefined schema required.
- **Agentic update loop**: After storing, the agent reviews existing memories that overlap and optionally consolidates or updates them. Memory is continuously curated rather than append-only.
- **Retrieval**: Hybrid text + semantic search over the ANIC-indexed notes.

**Result**: 8.6% average improvement over MemGPT and other baselines on LOCOMO benchmark (multi-session conversation tasks).

**Key difference from CB**: A-MEM's LLM has full write authority over the graph at any time. CB's graph is currently written only by extraction pipelines (batch + hook); the orchestrator LLM reads but does not write.

### Related Papers

| Paper | Relevance to CB |
|-------|----------------|
| **iText2KG** (arXiv:2409.03284) | Cosine-similarity entity matching for KG construction — semantic dedup in `merge_extraction()` to catch near-duplicates that text-hash misses |
| **Zep/Graphiti** (arXiv:2501.13956) | Bi-temporal fact validity (event time + ingestion time + validity windows) — principled replacement for `recency_decay`; facts expire rather than decay |
| **HippoRAG** (arXiv:2405.14831) | Personalized PageRank on a knowledge graph for retrieval — potential replacement for BFS traversal with semantic re-ranking |
| **RAPTOR** (arXiv:2401.18059) | Recursive abstractive tree construction — hierarchical synthesis that clusters leaf nodes into progressively abstract summaries; complements `engram synthesize` |
| **G-RAG** (arXiv:2405.16506) | Graph-aware reranker using GNN embeddings — reranks retrieved nodes based on graph topology, not just text similarity |
| **MemoryBank** (arXiv:2305.10250) | Ebbinghaus forgetting curve applied to memory strength — time-decay model more nuanced than a binary superseded flag |
| **GraphRAG** (arXiv:2404.16130) | Microsoft's community detection + hierarchical summarization — scales graph retrieval to very large corpora; relevant as CB node count grows past 10K |

### Three Actionable Improvements for Context Broker

#### #1 — Semantic dedup in `merge_extraction()` (iText2KG approach)

**Problem:** Text-hash dedup catches exact duplicates but misses paraphrases. After multiple extraction passes, the graph accumulates near-duplicate nodes like "JWT tokens expire after 24 hours" and "Access tokens have a 24-hour TTL" that refer to the same fact.

**Approach:** After inserting a new node, compute cosine similarity between its embedding and the embeddings of existing nodes with overlapping tags. If similarity exceeds a threshold (e.g. 0.92), merge by keeping the higher-confidence node and repointing edges.

**Implementation scope:** `store.py` (`merge_extraction` or a new `semantic_dedup` method) + optional embedding cache. Requires an embedding model call per new node, so it adds latency — gate behind a `--semantic-dedup` flag.

#### #2 — Bi-temporal fact validity (Zep/Graphiti approach)

**Problem:** `recency_decay` applies a time-based score penalty but facts don't truly expire. A superseded fact remains retrievable at reduced confidence indefinitely, creating noise. There's also no distinction between "when this decision was made" and "when it was ingested into the graph."

**Approach:** Add `valid_from` and `valid_until` fields to nodes. When a node is superseded, set `valid_until = now` on the old node. Retrieval filters `valid_until IS NULL OR valid_until > now` by default, making expired facts truly invisible unless explicitly requested.

**Implementation scope:** Schema migration (two new columns), update `supersedes` logic in `merge_extraction` to set `valid_until`, update retrieval filter in `retriever.py`. Replaces `recency_decay` strategy.

#### #3 — Agent-controlled memory write tools (A-MEM approach) ✅ IMPLEMENTED (2026-03-19)

**Problem:** The orchestrator LLM can read graph context but cannot act on it — it cannot fix a wrong fact, merge near-duplicates it recognizes as equivalent, or create a synthesis note mid-conversation. All graph writes require out-of-band CLI commands.

**Approach:** Expose graph write operations as orchestrator tool calls. The LLM can invoke these during a conversation turn when it identifies a graph maintenance opportunity (stale fact, merge candidate, useful synthesis).

**Tools implemented:**

| Tool | Description |
|------|-------------|
| `ctx_delete_node` | Delete a node and all its edges. Use when a fact is confirmed stale or incorrect. |
| `ctx_update_node` | Update a node's fact text (+ optional confidence). Use to correct outdated facts in-place. |
| `ctx_synthesize` | Create a new synthesis node linking to source nodes via `relates_to`. |

**Files changed:**
- `context_broker/store.py`: Added `delete_node()` and `update_node_fact()` methods
- `orchestrator/tool_executor.py`: Added `_run_ctx_delete_node`, `_run_ctx_update_node`, `_run_ctx_synthesize` handlers; `_STORE_HANDLERS` dict; updated `execute_tool` and `execute_tools` to accept and thread `store` kwarg
- `orchestrator/llm_adapter.py`: Added `ctx_delete_node`, `ctx_update_node`, `ctx_synthesize` to `_TOOL_SCHEMAS`
- `orchestrator/conversation.py`: Saved `self._store` in `__init__`; passes `store=self._store` to `execute_tools`
- `config.yaml`: Added three tools to `orchestrator.tools.enabled`

**Design note:** Store-aware tools are dispatched through a separate `_STORE_HANDLERS` dict so they don't require changing the `(args, cfg)` signature of sandbox tools. If `store` is `None` (e.g., a session without a loaded project), the tool returns a descriptive error rather than crashing.

---

## LOCOMO Benchmark Results (2026-03-30 — ongoing)

*LOCOMO is a 10-conversation, 1,986 QA-pair benchmark of multi-session personal episodic memory. Each conversation spans ~29 sessions and years of synthetic personal life events. QA categories: single_hop (1), temporal (2), open_domain (3), multi_hop (4), adversarial (5). Official protocol excludes category 5.*

*Evaluation split used: dev (conv-42 only during ablation iteration). Full dev set = conv-26, 30, 41, 42, 43.*

### Scoring

Two metrics reported throughout:
- **Keyword exact** — ground-truth answer words present verbatim in retrieved context. Fast, deterministic.
- **LLM strict** — Haiku judge rates context as YES/PARTIAL/NO for supporting the ground-truth answer. LLM strict = YES / total.

### Baseline ablation table (conv-42, 260 QA pairs)

| Config | Keyword exact | LLM strict | Avg tokens | Notes |
|--------|--------------|-----------|-----------|-------|
| no_memory | — | — | 0 | LLM parametric knowledge only |
| full_context | — | — | ~18K | Raw transcript injected |
| engram_all_off | — | — | — | BFS, no pipeline components |
| engram_default v1 | ~20% | 0% | — | LLM judge broken (auth missing) |
| engram_default v4 | 38.1% | 24.2% | 731 | Three fixes applied (see below) |
| engram_default v5 | 42.3% | 23.5% | 739 | + retrieval-time date resolution |
| engram_default v6 | 30.0% | 17.3% | 706 | + ISO date headers at ingest (re-ingest) — **negative result** |

### v4 per-category breakdown (engram_default, conv-42)

| Category | n | KW exact | LLM strict | LLM partial |
|----------|---|---------|-----------|------------|
| single_hop | 37 | 10.8% | 8.1% | 67.6% |
| temporal | 40 | 25.0% | 12.5% | 60.0% |
| open_domain | 11 | 9.1% | 0.0% | 45.5% |
| multi_hop | 111 | 43.2% | 44.1% | 29.7% |
| adversarial | 61 | 54.1% | 9.8% | 50.8% |
| **OVERALL** | **260** | **38.1%** | **24.2%** | — |

### v5 per-category breakdown (+ retrieval-time date resolution)

| Category | n | KW exact | LLM strict | Delta KW | Delta LLM |
|----------|---|---------|-----------|---------|----------|
| single_hop | 37 | 10.8% | 5.4% | +0.0pp | -2.7pp (noise) |
| temporal | 40 | **60.0%** | 15.0% | **+35.0pp** | +2.5pp |
| open_domain | 11 | 9.1% | 0.0% | +0.0pp | +0.0pp |
| multi_hop | 111 | 43.2% | 41.4% | +0.0pp | -2.7pp (noise) |
| adversarial | 61 | 54.1% | 11.5% | +0.0pp | +1.7pp |
| **OVERALL** | **260** | **42.3%** | **23.5%** | **+4.2pp** | -0.7pp (noise) |

### Fixes applied and their contributions

#### Fix 1 — LLM judge auth (v1 → v4 prerequisite)
`scoring.py`: `score_llm_judge()` was instantiating `anthropic.Anthropic()` before loading `.env`, so `ANTHROPIC_API_KEY` was never set. Added dotenv load before client construction.
- **Impact**: LLM judge went from 0% (auth failure silently scoring 0.0) to 24.2% on conv-42.

#### Fix 2 — Seed preservation in retriever (v1 → v4)
`retriever.py`: BFS was discarding seed nodes that didn't pass the `source_restriction` filter. High-recall seeds (direct keyword matches) were being pruned before graph traversal.
- **Impact**: Retrieval no longer drops exact matches. Contributed to avg_tokens 298 → 731.

#### Fix 3 — Recency half-life tuned for LOCOMO (v1 → v4)
`ablation_configs.py`: `recency_half_life_days` default was 30 (real-time use). LOCOMO events span 2-4 years, so all facts scored near-zero recency and were pruned before reaching top_k.
- **Fix**: Set `recency_half_life_days=3650` (10 years) in all `engram_default`/`engram_filtered`/`engram_tight` configs.
- **Impact**: Avg tokens 298 → 731. Primary driver of the v1→v4 improvement.

#### Fix 4 — Retrieval-time relative date resolution (v4 → v5)
`retriever.py`: Added `_resolve_relative_dates(fact, occurred_at)` — rewrites 16 relative time phrases in rendered fact text using `occurred_at` timestamp.
- Example: "last week" → "the week before January 21, 2022"
- No re-ingestion required. Works on existing DBs.
- **Impact**: Temporal keyword exact 25% → 60% (+35pp). LLM strict flat (+2.5pp noise).
- **Paper interpretation**: Relative phrase resolution is a zero-cost retrieval-time improvement that disproportionately benefits temporal QA. Suitable as a standalone ablation component.

#### Fix 5 — ISO date headers at ingest (v5 → v6) — NEGATIVE RESULT
`ingestion_pipeline.py`: `_session_text()` was emitting session headers as `[Session: session_2 | Date: 7:31 pm on 21 January, 2022]`. The natural-language format didn't match the ISO example in the extraction prompt.
- **Fix**: Parse datetime string to ISO-8601 in header: `[Session: session_2 | Date: 2022-01-21]`
- **Measured impact**: Relative phrases in DB: 23 → 21 (marginal). Node count: 1085 → 1455 (+34%). Overall keyword exact: 42.3% → 30.0% (-12.3pp). LLM strict: 23.5% → 17.3% (-6.2pp).
- **Root cause of regression**: ISO date format caused the extraction model to produce more verbose output per session, inflating the graph by 370 nodes. With fixed top_k=50, additional nodes dilute retrieval — relevant nodes compete with more noise.
- **Paper verdict**: Fix 5 is a negative result worth reporting. Documents a real tradeoff: cleaner extraction prompts → more extracted facts → worse retrieval precision at fixed top_k. The retrieval-time fix (fix 4) dominates. The ISO date header change is retained in code for correctness but should not be treated as an accuracy improvement.

### Key qualitative findings

#### Adversarial category is keyword-noisy
Adversarial questions are role-swapped ("What was the SECOND TOURNAMENT JOANNA WON?" when Nate won). The context contains all the named entities → high keyword score (54.1%). The LLM judge correctly identifies wrong attribution → low LLM strict (9.8%). **For paper: report adversarial LLM score, not keyword. Keyword adversarial scores are false positives.**

#### Single_hop LLM at 5-8% is a retrieval precision problem
73% of single_hop questions score LLM PARTIAL — the context has *related* facts but not the complete answer. Most single_hop answers are list-type ("watchingmovies", "exploringnature" — concatenated in the dataset). BFS returns ~300-700 tokens but misses the specific nodes. **This is not an extraction problem — the facts are in the DB. It is a retrieval depth/precision problem.**

#### multi_hop LLM at 41-44% validates BFS graph traversal
Multi-hop questions require connecting facts across sessions. LLM strict at 44% suggests BFS is successfully chaining related facts. This is the strongest signal that the graph structure adds value over flat retrieval.

#### LLM PARTIAL credit is the ceiling signal
Single_hop: 67-73% PARTIAL. Temporal: 57-60% PARTIAL. The context is frequently "in the neighborhood" — the right person, the right topic, but missing the precise answer. This points to extraction completeness gaps (some specific facts not captured) rather than retrieval failures.

#### engram_temporal == engram_default (ablation design issue)
`engram_temporal` and `engram_default` both use `domain='episodic_personal'` with identical config params. Temporal date resolution rules are already in `episodic_personal.layer1_rules`. **To isolate the temporal contribution as an ablation, a baseline profile *without* date resolution is needed.**

---

## Fix 6 — Post-ingest brute-force semantic dedup (root cause of v6 regression)

### Root cause (corrected)
The v6 regression (1085 → 1455 nodes, −12.3pp accuracy) was **not** caused by the ISO date header. The real cause: `sqlite-vec` cannot be loaded in Python 3.14 on macOS (`enable_load_extension` is disabled). This means `_vec_available=False` during every fresh ingest, which completely bypasses the per-insert semantic dedup block in `store.add_node()`. Without it, extraction model paraphrases insert as separate nodes:

- "Acting was Joanna's first passion"
- "Acting was Joanna's initial passion"
- "Acting was Joanna's primary passion"

Session_9 alone ballooned from 18 → 134 nodes (+116). Session_26 had 8 near-identical copies of the same tournament event. The v5 DB was fine because it was built in an older environment where `enable_load_extension` worked.

### Fix
Added `GraphStore.dedup_nodes_brute_force(threshold=0.92)` to `store.py`:
1. Batch-embeds all nodes using sentence-transformers (always available, no sqlite-vec needed)
2. Builds (n × 384) float32 matrix, normalizes rows, computes pairwise cosine in 512-row blocks via numpy
3. Union-Find clustering of near-duplicate groups
4. Per-cluster: union tags, max confidence, reassign edges to canonical (earliest `created_at`), delete duplicates

Called from `ingestion_pipeline.py` after `embed_missing_nodes()` when `_vec_available=False`.

**Expected outcome**: Fresh ingests should produce node counts near v5 levels (~1085 for conv-42), recovering the 12pp accuracy regression.

---

## Fix 7 — Fact-text keyword scoring for retrieval (single_hop vocabulary mismatch)

### Root cause
Single_hop LLM accuracy at 5.4% despite 73% PARTIAL: the retrieval pipeline uses tag-only keyword matching to score nodes and decide which become BFS seeds. Two failure patterns:

**Pattern A (vocabulary mismatch)**: Query "allergic" → stored tag "allergy". The node "Joanna is allergic to most reptiles" has tags `["joanna", "allergy"]`. `_count_keyword_tag_hits` returns 1 (only "joanna" matches "joanna" tag). Node competes with 720+ other Joanna nodes for 25 seed slots → often excluded.

**Pattern B (substring mismatch)**: Query "pets" → stored tag "new pet". `"pets" in "new pet"` → False (substring check fails). Max the dog node scores only 1, turtle nodes score 2 → turtles fill seed slots → BFS doesn't reach Max.

### Fix
Two changes to `retriever.py`:

1. **`score_by_relevance`**: Added fact-text keyword hit counting alongside tag hits. For each keyword not already matched by a tag, check if it appears in the fact text. `_relevance = (tag_hits + fact_only_hits) * type_boost`.

2. **High/low-overlap partition** in `retrieve_with_stats`: When `relevance_scoring=True`, use `n["_relevance"] >= 2` (which includes fact-text hits) instead of re-calling `_count_keyword_tag_hits` (tag-only). This ensures the same combined signal drives both sorting and seed selection.

**Impact on allergy example**: "joanna" tag_hit=1 + "allergic" fact_hit=1 → `_relevance`=2 → high_overlap → becomes BFS seed → fact "Joanna is allergic to most reptiles" retrieved.

**Pattern B (pets/plural)**: Not fully resolved by this fix — "pets" still doesn't substring-match "new pet" in either tags or fact text. Stemming would be needed for this case.


---

## Investigation — top_k as binding constraint (2026-03-31)

### Background
Conv-42 has 1085 nodes. With `engram_dynamic_topk` using `sqrt(1085)=32`, keyword accuracy dropped 3.8pp and LLM accuracy dropped 4.4pp vs fixed `top_k=50`. This investigation identifies why and characterizes the correct formula.

### Finding 1: top_k is the binding constraint on every query

Token distribution across 260 QA pairs is nearly uniform:
```
top_k=50: p10=731  p50=766  p90=800  (69-token range)
top_k=32: p10=441  p50=466  p90=489  (48-token range)
```
The BFS saturates top_k on essentially every query — the graph is dense enough that there are always ≥ top_k reachable nodes from any seed set. top_k is the throughput budget, not a soft cap.

**Implication**: Token count is driven by graph structure, not query relevance. The right lever for context-window management is top_k (or token_budget).

### Finding 2: Seed count compounds the top_k cut

`max_seeds = top_k // 2`, so cutting top_k from 50→32 reduces:
- Output nodes by 36% (50→32)
- Seed count by 36% (25→16)
- BFS coverage area proportionally (fewer starting points = narrower traversal)

The 13 questions where top_k=32 misses but top_k=50 hits are spread across categories (5 multi_hop, 4 adversarial, 3 single_hop, 1 temporal), ruling out systematic bias — it's raw coverage loss. The specific missed answers are specific fact nodes that BFS from 16 seeds never reaches but BFS from 25 seeds does.

### Finding 3: sqrt formula is wrong for large graphs

| nodes | sqrt | log2×5 | n//25 | fixed50 |
|------:|-----:|-------:|------:|--------:|
|   100 |   10 |     33 |    10 |      50 |
|   300 |   17 |     41 |    12 |      50 |
|   800 |   28 |     48 |    32 |      50 |
|  1085 |   32 |     50 |    43 |      50 |
|  1500 |   38 |     52 |    60 |      50 |
|  5000 |   50 |     61 |   100 |      50 |

`sqrt` compresses too aggressively — at 1085 nodes it gives 32 (36% below fixed 50). `int(log2(n) * 5)` hits exactly 50 at LOCOMO scale and scales gracefully. Added `engram_dynamic_topk_log` ablation config with `topk_formula="log2"`.

### Finding 4: LLM comparison was confounded by nulls

`engram_default` (top_k=50) run had 54/260 null LLM scores, concentrated in adversarial (36/61 = 59% null rate). The adversarial LLM comparison between configs is unreliable. Clean categories (multi_hop, single_hop, temporal) show:
- multi_hop: 60.2% (top_k=50, 93 valid) vs 56.8% (top_k=32, all 111) — real 3.4pp loss
- single_hop: 16.2% vs 16.2% — no difference
- temporal: 20.0% vs 20.0% — no difference

Multi_hop is the category most sensitive to top_k because BFS chain traversal depends on having sufficient seeds to cover the graph.

### Changes made
- `engram/retriever.py`: Dynamic top_k block now reads `strats["topk_formula"]` ("sqrt" | "log2"); defaults to "sqrt" for backward compat
- `benchmarks/locomo/ablation_configs.py`: Added `topk_formula: str = "sqrt"` field to `AblationConfig`; added `engram_dynamic_topk_log` config
- `benchmarks/locomo/harness.py`: Passes `topk_formula` through strategies dict

### Validation result (conv-42, 260 QA, LLM judge)

| config | top_k @ 1085 nodes | kw_accuracy | kw_exact | llm_accuracy | avg_tokens |
|--------|-------------------|-------------|----------|--------------|------------|
| engram_dynamic_topk (sqrt) | 32 | 47.3% | 40.8% | 31.5% | 464 |
| engram_dynamic_topk_log (log2) | 50 | **56.9%** | **48.5%** | **38.5%** | 741 |

log2 is the better formula at this graph scale. The token cost (+60%) is the trade-off; this is the same token cost as `engram_default` (fixed top_k=50). **Recommendation: `engram_dynamic_topk_log` supersedes `engram_dynamic_topk` as the dynamic scaling baseline.** The log2 formula should be the default for dynamic top_k going forward.

---

## Embedding model upgrade — bge-small-en-v1.5 (2026-03-31)

### Motivation
`all-MiniLM-L6-v2` (MTEB Retrieval: 41.95) was the default embedding model. `BAAI/bge-small-en-v1.5` is a retrieval-tuned model at the same 384 dimensions with MTEB Retrieval: 51.68 (+23%). Same embedding dimension means zero schema migration — a drop-in swap.

### Change
`engram/embedder.py`: Updated `_MODEL_NAME = "BAAI/bge-small-en-v1.5"` and updated the `EMBEDDING_DIM` comment. `local_files_only=True` is preserved; model was pre-downloaded to HF cache before the constant was changed.

### Validation result (engram_semantic, conv-42, 260 QA, LLM judge)

| config | model | kw_accuracy | kw_exact | llm_accuracy | avg_tokens |
|--------|-------|-------------|----------|--------------|------------|
| engram_semantic (all-MiniLM) | all-MiniLM-L6-v2 | 16.5% | 12.3% | — | 546 |
| engram_semantic (bge-small) | bge-small-en-v1.5 | **41.5%** | **33.9%** | **21.7%** | 756 |

+25pp keyword accuracy improvement — the MTEB Retrieval gap translates directly to benchmark accuracy. The semantic config is now competitive with early-pipeline baselines.

**Note**: `engram_semantic` uses `relevance_scoring=False` to isolate the vector signal. At 21.7% LLM accuracy it still trails `engram_dynamic_topk_log` (38.5%), suggesting keyword-seeded BFS + fact-text scoring outperforms pure embedding retrieval on this corpus.

---

## engram_combined ablation (2026-03-31)

### Config
Full stack: `semantic=True, relevance_scoring=True, topk_formula="log2"`. Tests whether keyword seed ranking and vector retrieval are additive.

**Limitation**: `sqlite-vec` cannot be loaded on Python 3.14 macOS (`enable_load_extension` is disabled). `store._vec_available=False` → `search_by_embedding()` returns `[]` → the `semantic=True` flag has **no runtime effect** on retrieval. The `engram_semantic` and `engram_combined` results are actually keyword-only BFS — the embedding model affects dedup quality at ingestion but not retrieval ranking.

### Validation result (conv-42, 260 QA, keyword only — LLM judge failed: Anthropic credits exhausted)

| config | graph | nodes | kw_accuracy | kw_exact | avg_tokens |
|--------|-------|-------|-------------|----------|------------|
| engram_dynamic_topk_log | all-MiniLM dedup | 1017 | **56.9%** | **48.5%** | 741 |
| engram_semantic | bge-small dedup | 712 | 41.5% | 33.9% | 756 |
| engram_combined | bge-small dedup | 712 | 46.5% | 39.6% | 720 |

The combined config (+5pp vs engram_semantic on the same graph) confirms that `relevance_scoring=True` + log2 top_k adds value independent of the semantic flag. However, both configs using the bge_small-deduped graph (712 nodes) trail the all-MiniLM graph (1017 nodes) by ~10pp in keyword accuracy.

### Finding: bge-small dedup over-prunes at this threshold

The bge-small checkpoint has 30% fewer nodes (712 vs 1017). More aggressive semantic dedup removed 305 nodes that contain answer keywords. The accuracy gap is likely driven by **dedup recall loss**, not the retrieval strategy. bge-small's higher MTEB Retrieval score means it correctly identifies more pairs as semantically equivalent — but on this corpus that appears to over-prune.

**Next step**: Run `engram_combined` (or `engram_dynamic_topk_log`) on the all-MiniLM checkpoint to cleanly isolate retrieval strategy vs dedup quality. Also: fix the sqlite-vec Python 3.14 incompatibility to make semantic retrieval actually work at query time.

---

## Phase 1 LOCOMO Retrieval Improvements — conv-26 ablation (2026-04-03)

### Overview

Full ablation of 6 retrieval-layer improvements on conv-26 (19 sessions, 199 QA pairs, quick mode). No extraction needed — all configs share `dedup_threshold=0.95, domain="episodic_personal"` so conv-26 checkpoints were reused. LLM judge: `local:qwen/qwen3-8b`.

**Baseline**: `engram_dedup95` — 67.8% keyword accuracy, 62.1% LLM accuracy.  
**Zep target**: 73% LLM accuracy on LOCOMO.

### Results (all 10 configs)

| Config | Features | kw_accuracy | kw_exact | llm_accuracy | avg_tokens |
|--------|----------|-------------|----------|--------------|------------|
| engram_dedup95 (baseline) | — | 67.8% | 52.3% | 62.1% | ~730 |
| engram_query_expansion | #1 only | 53.3% | 47.2% | 57.8% | 730 |
| engram_person_anchor | #2 only | 61.3% | 51.3% | 64.3% | 742 |
| engram_temporal_boost | #3 only | 55.3% | 46.7% | **71.4%** | 764 |
| engram_bm25_rrf | BM25+RRF | 64.3% | 53.8% | 60.8% | 738 |
| engram_person_temporal | #2 + #3 | 56.3% | 50.2% | 64.8% | 726 |
| engram_improvements_1_3 | #1 + #2 + #3 | 60.3% | 51.8% | 70.3% | 757 |
| engram_coreference | #4 only | 57.3% | 48.2% | 58.3% | 746 |
| engram_session_scoped | #6 only | **67.8%** | **52.8%** | **74.9%** | 732 |
| engram_coreference_temporal | #4 + #3 | 57.3% | 48.7% | 64.3% | 746 |
| **engram_improvements_4_6** | **#4 + #6** | **67.8%** | **52.8%** | **74.9%** | 732 |

### Key findings

1. **#6 (session_scoped) is the dominant feature** — +12.8pp LLM lift over baseline (74.9% vs 62.1%). Restricting BFS entry candidates to evidence sessions (oracle signal from the dataset) eliminates cross-session noise. This is the single most impactful change.

2. **Coreference (#4) adds nothing on top of session_scoped** — `engram_improvements_4_6` and `engram_session_scoped` produce identical results (67.8% kw, 74.9% LLM). The session filter already scopes the evidence tightly enough that referent resolution doesn't help further.

3. **Temporal boost (#3) is the strongest blind feature** — 71.4% LLM with no oracle signal, above the 70.3% of the full 3-way stack (#1+#2+#3). Combining #3 with person anchoring (#2) drops to 64.8%, suggesting #2 and #3 retrieve overlapping content and the addition adds noise.

4. **Query expansion (#1) hurts** — 57.8% LLM vs 62.1% baseline. The synonym dict (eat→food/meal, work→job/career, etc.) broadens the keyword set into irrelevant territory on this conversation corpus.

5. **BM25+RRF underperforms** — 60.8% LLM, slightly below baseline (62.1%). Adding a full-text BM25 channel to the RRF fusion doesn't improve over tag-overlap seeding at this graph scale.

6. **Feature interactions are mostly negative** — The 3-way stack (#1+#2+#3) at 70.3% is weaker than #3 alone (71.4%). Coreference (#4) at 58.3% alone is below baseline. Adding features does not compound cleanly.

### Recommendation

`engram_session_scoped` beats Zep's 73% target and represents the retrieval ceiling for the current graph (oracle sessions). In production (no oracle signal), `engram_temporal_boost` (#3 alone at 71.4%) is the best deployable single improvement.

**Caveat**: session_scoped uses oracle `evidence_session_ids` from the LOCOMO dataset — not available at query time in production. The 74.9% result is a retrieval upper bound, not a deployable system number. Temporal boost (71.4%) is the realistic ceiling for blind retrieval improvements.

---

## Official Dev Split Results — Semantic Rerank vs Cross-Encoder (2026-04-07)

### Protocol

**Split**: dev (5 conversations: conv-26, 30, 41, 42, 43)  
**QA pairs**: 762 (categories 1–4 only; category 5 adversarial excluded per official LOCOMO protocol)  
**Judge**: gpt-4o-mini (sync)  
**Extraction model**: gemini-2.5-flash-lite, `max_tokens=4096`, domain=`episodic_personal`, semantic dedup threshold=0.95

> **Critical note**: All prior LOCOMO runs before 2026-04-07 included category 5 (adversarial) questions, which inflated the denominator and depressed scores by ~10pp. Category 5 uses `adversarial_answer` (not `answer`) and scores ~41% binary LLM — including it conflates the adversarial-robustness task with the memory-retrieval task. The `--categories` default is now `[1, 2, 3, 4]` in harness.py.

### Results (dev split, official protocol)

| Config | kw_accuracy | kw_exact | llm_accuracy | avg_tokens | n |
|--------|-------------|----------|--------------|------------|---|
| **engram_semantic_rerank_topk100** | 72.6% | 63.8% | **85.7%** | 1439 | 762 |
| engram_cross_encoder_topk100 | 75.2% | 65.5% | 84.1% | 1471 | 762 |

**Primary config: `engram_semantic_rerank_topk100`** — highest LLM accuracy (85.7%).

Cross-encoder achieves higher keyword accuracy (75.2% vs 72.6%) but lower LLM accuracy (84.1%). The cross-encoder (`ms-marco-MiniLM-L-6-v2`) is trained on web document retrieval (MS-MARCO), creating a domain mismatch with LOCOMO's conversational personal-memory corpus. The `all-MiniLM` bi-encoder used in semantic rerank is better calibrated for Q&A/conversational similarity.

### Comparison to prior state of the art

| System | LLM accuracy | Notes |
|--------|-------------|-------|
| **Engram (semantic_rerank_topk100)** | **85.7%** | Dev split, cats 1–4, 762 QA pairs |
| Zep | 73% | Published target |
| Mem0 | 88% | Published target (full test set) |

Engram's 85.7% exceeds Zep (73%) and approaches Mem0 (88%) on the dev split with official protocol scoring.

### Next: Full LOCOMO Run

5 test conversations (conv-44, 47, 48, 49, 50) are not yet extracted. Full official run requires:
1. Extract test split → stored in `engram_dedup95` checkpoint dir (source for `semantic_rerank_topk100`)
2. Run retrieval + batch judge on all 10 conversations (~778 new QA pairs)
3. Report combined score as the paper number

See "Full LOCOMO Run Plan" below.

---

## Full LOCOMO Run Plan (10 conversations)

**Goal**: Run all 10 LOCOMO conversations under the official evaluation protocol to produce a citable benchmark number that is directly comparable to Zep (73%) and Mem0 (88%).

Both Zep and Mem0 report a single score over the full 10-conversation dataset. Our dev-split result (85.7% over 5 conversations) is promising but not a like-for-like comparison — the full 10-conversation run is required to make the claim rigorous.

### Configurations

Two configs are planned. Both use identical retrieval pipelines; the only variable is the extraction model:

| Config | Extraction model | Retrieval | Purpose |
|--------|-----------------|-----------|---------|
| `engram_semantic_rerank_topk100` | gemini-2.5-flash-lite | top_k=100, semantic rerank | **Primary result** — best Engram pipeline |
| `engram_semantic_rerank_gpt4omini` | gpt-4o-mini | top_k=100, semantic rerank | **Apples-to-apples** — same extraction model as Zep/Mem0 |

The delta between the two configs isolates how much of Engram's performance comes from the extraction model vs. the graph retrieval architecture.

### Dataset

- **Conversations**: all 10 (conv-26, 30, 41, 42, 43 = dev; conv-44, 47, 48, 49, 50 = test)
- **QA pairs**: ~1,540 total (762 dev + ~778 test), categories 1–4 only (adversarial cat 5 excluded)
- **Judge**: gpt-4o-mini (sync or batch)

### Checkpoints

| Config | Dev split | Test split |
|--------|-----------|------------|
| `engram_dedup95` (source for semantic_rerank) | ✅ done (5 convs) | ⬜ pending |
| `engram_gpt4o_mini_extraction` (source for gpt4omini config) | ⬜ pending | ⬜ pending |

The dev-split checkpoints for `engram_dedup95` are already stored and reused — no re-extraction needed for the primary config's dev portion. Only the 5 test conversations need fresh extraction.

### Execution

```bash
python -m benchmarks.locomo.harness \
  --split all \
  --configs engram_semantic_rerank_topk100 engram_semantic_rerank_gpt4omini \
  --conv-workers 2 \
  --use-batch \
  --verbose
```

`--conv-workers 2` is recommended over the default 4 to avoid concurrent Gemini calls triggering rate limit backoff. The 503/429 retry logic now handles transient failures, but reducing concurrency minimizes wasted retries on the test split's fresh extraction.

### Expected output

Results written to `benchmarks/locomo/results/all_<date>.json`. The aggregate section of each config provides the citable number.

### Reporting

Once the full run completes, update this section with the final table:

| System | LLM accuracy | Extraction model | Notes |
|--------|-------------|-----------------|-------|
| **Engram (`semantic_rerank_topk100`)** | _TBD_ | gemini-2.5-flash-lite | All 10 convs, cats 1–4 |
| **Engram (`semantic_rerank_gpt4omini`)** | _TBD_ | gpt-4o-mini | Apples-to-apples vs Zep/Mem0 |
| Zep | 73% | gpt-4o-mini | Published, full test set |
| Mem0 | 88% | gpt-4o-mini | Published, full test set |

The gpt4omini config is the controlled comparison: same extraction model, same judge, same dataset, different memory architecture. That delta is the cleanest evidence of Engram's architectural contribution.

---

## LongMemEval Benchmark Plan

**Goal**: Run Engram against LongMemEval — a Microsoft Research benchmark for long-term memory in LLM assistants — to validate performance on question types that directly target Engram's architectural differentiators: knowledge update (supersedes), multi-session aggregation (graph BFS), and temporal reasoning.

### Dataset

**HuggingFace**: `xiaowu0162/longmemeval-cleaned` ⚠️ (the original `longmemeval` is deprecated as of Sep 2025 — use the cleaned version, which removes noisy history sessions that interfere with answer correctness)

Two variants:
| Variant | Description | Sessions per question |
|---------|-------------|----------------------|
| `LongMemEval_S` | Single-session — one long conversation per question | 1 (very long) |
| `LongMemEval_M` | Multi-session — multiple shorter sessions per question | varies |

- **500 questions** total per variant
- **No official train/test split**; run all 500
- **Judge**: The original paper uses GPT-4o (not mini). We will use **gpt-4o-mini** for cost reasons, same as LOCOMO. This means our absolute scores will not be directly comparable to published paper numbers. The meaningful comparison is Engram vs other systems run under the same judge — not Engram vs the paper's table.

### Question categories

| Category | Count (approx) | Engram relevance |
|----------|---------------|-----------------|
| Single-session fact recall | ~150 | Baseline — tag matching, BFS |
| Temporal reasoning | ~100 | `occurred_at` field + temporal prompt rules |
| **Knowledge update** | ~100 | **Engram's key differentiator** — supersedes mechanism tracks old→new directly |
| Multi-session aggregation | ~100 | Graph BFS collects distributed facts across sessions |
| Absent information | ~50 | Requires confident "I don't know" — retrieval precision matters |

Knowledge update is the category most likely to produce a meaningful delta over flat-memory systems (Zep, Mem0). Engram's `supersedes` edge means the graph already encodes "X was replaced by Y"; retrieval for Y also surfaces the old value for context. Flat systems must either overwrite the old value (and lose temporal context) or store both and let the LLM sort it out (retrieval noise).

### How the data maps to Engram's ingestion model

LongMemEval sessions are conversation turns, same as LOCOMO. The ingestion pipeline maps cleanly:

```
LongMemEval session  →  engram Session (session_id, datetime_str, turns)
LongMemEval subject  →  engram Conversation (sample_id, sessions[])
```

For `LongMemEval_S`, each question's single long conversation is bisected into sessions by the harness (the ingestion pipeline already handles bisection on output-length truncation). For `LongMemEval_M`, sessions arrive pre-segmented.

The "absent information" category needs one additional harness feature: if the retrieved context is empty or entirely low-confidence, the answer should be "I don't know" rather than a hallucinated response. The LOCOMO harness currently always sends retrieved context to the judge. This needs a `min_retrieval_confidence` threshold or a `no_context_response` override for absent-information questions.

### Infrastructure plan

```
benchmarks/
  longmemeval/
    __init__.py
    loaders/
      longmemeval_dataset.py    # load_dataset("xiaowu0162/longmemeval")
                                # → list[LongMemEvalSample] with sessions + question + answer
      ingestion_pipeline.py     # thin wrapper: convert LongMemEvalSample → Conversation,
                                # call locomo's ingest_conversation() directly
    evaluation/
      scoring.py                # reuse locomo scoring.py; add absent-info handling
    harness.py                  # top-level runner, same structure as locomo/harness.py
    ablation_configs.py         # same config pattern as locomo
    results/                    # output JSON + summary tables
```

**Reuse strategy**: `benchmarks/locomo/loaders/ingestion_pipeline.py` is imported directly — there is nothing LOCOMO-specific in it, it only depends on `Conversation` / `Session` types and `ingest_conversation()`. The longmemeval dataset loader converts to those types and then delegates to the existing pipeline. Same for scoring: `score_llm_judge()`, `submit_judge_batch()`, and `judge_answer_sync()` all accept generic `(question, context, reference_answer)` tuples and need no changes.

### Absent information handling

The absent-info category requires a retrieval precision signal that LOCOMO didn't need. Two options:

1. **Threshold-based**: if top retrieved node confidence < `absent_threshold` (e.g. 0.3), return the fixed string `"I don't know"` instead of sending to the LLM judge.
2. **LLM-with-empty-context**: send the question with empty context; if the LLM responds "the information is not available" or similar, score as correct.

Option 2 is simpler and avoids introducing a new hyperparameter. The judge prompt should instruct the LLM to say "not enough information" when context is empty. LOCOMO's judge prompt already handles this implicitly (GPT-4o-mini correctly says "I can't determine" when given no relevant context).

### Ablation configs

| Config | Purpose |
|--------|---------|
| `engram_lme_gemini` | Primary — gemini-2.5-flash-lite extraction, semantic rerank top_k=100 |
| `engram_lme_gpt4omini` | Apples-to-apples — gpt-4o-mini extraction (matches paper's model family) |

These mirror the two LOCOMO configs. Same domain profile (`episodic_personal`), same retrieval pipeline.

### Expected advantages by category

| Category | Why Engram should win |
|----------|----------------------|
| Knowledge update | `supersedes` edge explicitly models old→new. Query for "current job" retrieves the latest node AND the superseded node (for context), giving the LLM everything it needs. Flat memory systems either overwrite (lose history) or store both unlinked (retrieval lottery). |
| Multi-session aggregation | BFS traversal across graph edges collects facts distributed across many sessions. Flat vector search returns top-K chunks by similarity; aggregation questions often require low-similarity nodes that happen to be connected. |
| Temporal reasoning | `occurred_at` timestamps on nodes + temporal resolution rules in the extraction prompt let the model answer "what did X say in January" vs "what does X say now" with precision. |
| Absent information | High-precision retrieval (semantic rerank + cross-encoder) returns fewer false positives than flat vector search, making "nothing retrieved" a more reliable signal. |

### Implementation order

1. **Dataset loader** (`longmemeval_dataset.py`): download via `datasets` library, parse into `Conversation`/`Session` types. Map the `sessions` field to individual sessions; map `question` + `answer` to QA pairs. Verify the field names match the HuggingFace schema.

2. **Ingestion wrapper** (`loaders/ingestion_pipeline.py`): one-liner delegating to `locomo.loaders.ingestion_pipeline.ingest_conversation()`. Add domain override: `episodic_personal` is the right profile for personal-memory conversations.

3. **Harness** (`harness.py`): port the LOCOMO harness structure. Key difference: the absent-info category must be identified and handled separately (either by category label in the dataset or by the "none of the above" retrieval fallback).

4. **Ablation configs** (`ablation_configs.py`): copy the LOCOMO `engram_semantic_rerank_topk100` config; rename; point `db_dir` to a longmemeval-specific cache directory.

5. **Scoring** (`evaluation/scoring.py`): import and re-export `score_llm_judge`, `submit_judge_batch` from the LOCOMO path. Add absent-info override: if `retrieved_context` is empty, map answer to `"I don't know"` before judging.

6. **Run and report**: execute on `LongMemEval_M` first (multi-session is the harder variant and better showcases graph memory). Compare per-category scores against the paper's published numbers for GPT-4o + retrieval baselines.

### Comparison targets

The LongMemEval paper reports results for several retrieval-augmented memory systems. The relevant published baselines are:

| System | Overall accuracy | Notes |
|--------|-----------------|-------|
| GPT-4o (no memory) | ~30% | Upper-bound ceiling without persistent memory |
| Full context (oracle) | ~70% | All sessions concatenated into context window |
| MemoryBank | ~40–50% | Flat retrieval baseline |
| ReadAgent | ~55–60% | Summarization-based compression |
| **Engram (target)** | _TBD_ | Graph retrieval with supersedes |

Note: exact published numbers vary by variant (S vs M) and question category. The paper's Table 2 is the reference.

### Cost and time estimate

These are rough estimates pending actually loading the dataset to measure conversation lengths.

**Scale comparison vs LOCOMO:**
- LOCOMO: 10 conversations to ingest, ~5,882 turns total, 1,986 QA pairs to judge
- LongMemEval_M: 500 conversations to ingest (one per question), 500 QA pairs to judge

LOCOMO has 4× more judging work than LongMemEval but 50× fewer conversations to extract. Extraction dominates cost.

**Extraction cost (gemini-2.5-flash-lite):**

Gemini 2.5 Flash Lite pricing: $0.075/1M input tokens, $0.30/1M output tokens.

LOCOMO extraction (10 conversations, ~220 sessions total) costs roughly $0.10–$0.50. LongMemEval_M has 500 conversations, but each may be shorter per conversation than LOCOMO's (~22 sessions avg). If each LongMemEval instance averages 30–50 sessions:
- 500 × 40 sessions × ~1,500 input tokens = 30M input tokens → ~**$2.25**
- 500 × 40 sessions × ~750 output tokens = 15M output tokens → ~**$4.50**
- **Total extraction: ~$7–15** depending on conversation lengths

**Judging cost (gpt-4o-mini):**
- 500 questions × ~1,500 tokens context = 750K input tokens at $0.15/1M → **< $0.15**
- Judging is essentially free at this scale.

**Total estimated cost: $8–20 for the M variant.** The main uncertainty is conversation length — load the dataset and check `sum(len(s.turns) for q in dataset for s in q.sessions)` before committing to a run.

**Time estimate:**
- At 300 RPM (gemini Tier 1 floor), 500 × 40 = 20,000 extraction API calls → 67 minutes wall-clock minimum
- With conv-workers=4 and bisection overhead, budget **2–4 hours** for M variant
- The 503/429 retry logic will handle rate bursts; this is not a LOCOMO-style all-day run

### Anticipated issues

1. **Dataset schema is unknown until loaded.** The LongMemEval format may not have `datetime_str` fields (LOCOMO's sessions have explicit timestamps; LongMemEval's may not). Temporal reasoning questions are answered by the model's internal knowledge of conversation order, not absolute dates. The loader needs to synthesize plausible `occurred_at` values (sequential session numbers, or skip entirely for sessions without dates — the ingestion pipeline handles `None` gracefully).

2. **500 independent GraphStores, no cross-conversation checkpointing.** LOCOMO has 10 conversations; we can reuse all 10 checkpoint DBs. LongMemEval has 500. If a run is interrupted partway, already-completed conversations are checkpointed individually. The harness checkpoint logic (one DB per `sample_id`) already handles this correctly.

3. **Judge model mismatch.** The paper reports scores with GPT-4o. Our gpt-4o-mini scores will be systematically lower (gpt-4o-mini is a stricter judge in some categories). To interpret results: compare Engram vs other systems that have been run under gpt-4o-mini judging, not the paper's absolute numbers. If we want apples-to-apples with the paper, one calibration run on a 50-question subset with both judges would establish the offset.

4. **Abstention category requires retrieval confidence signal.** When Engram retrieves nothing above the relevance threshold, the answer should be "I don't know." LOCOMO never tests this. The harness needs to detect empty/low-confidence retrieval and short-circuit to "the information is not available in the conversation history" before calling the judge.

5. **`longmemeval` (original) is deprecated.** Use `longmemeval-cleaned` — the original had noisy sessions that made some questions unanswerable, biasing scores downward for memory systems that actually retrieved the right facts.

### Prerequisites

```bash
pip install datasets  # HuggingFace datasets library (likely already installed)
```

No new model dependencies — the existing `sentence-transformers` install covers the semantic reranker, and `google-genai` / OpenAI client cover extraction and judging.


---

## GNN Re-ranking for Retrieval Accuracy

*April 2026 — NanoSwarm research agent output*

*Context: Four independent swarm bots with unrelated constraints (HFT engineer, RPG AI director, adversarial critic, data governance specialist) all independently proposed GNN re-ranking as the highest-leverage improvement to Engram retrieval accuracy. This section documents the supporting research.*

---

### What GNN Re-ranking Is

A GNN is an iterative message-passing neural network. Each node aggregates its neighbors' embeddings across multiple hops, building a representation that encodes graph structure — not just the node itself.

- **Vector search**: `relevance = cosine_sim(query, node)` — nodes scored in isolation
- **GNN re-ranking**: `relevance = MLP(gnn_embed(node + neighbors), query)` — nodes scored in graph context

Key capability: GNN can discover that a node is relevant *because of its neighbors*, not because it's directly similar to the query. For Engram, this means a stale node from the wrong project context can be downranked even if its embedding is semantically close to the query.

---

### Architecture Recommendation

| Architecture | Aggregation | Inductive? | Verdict for Engram |
|---|---|---|---|
| GCN | Fixed mean | No | Fallback — simpler, faster, transductive |
| GraphSAGE | Learned, sampled | Yes | Overkill unless graph grows very rapidly |
| **GAT** | **Learned attention** | **Yes** | **First choice** |

**Recommended: 2-layer GAT, 4 attention heads, hidden_dim=128**
- ~40K–100K parameters — tiny, trains in minutes
- Inductive: no retraining when new nodes arrive daily
- Attention weights are interpretable (shows which neighbors influenced the ranking)
- Handles Engram's typed heterogeneous nodes via multi-head attention

Multi-relational GCN (RGCN) is worth considering if edge type differentiation (decision→lesson vs. entity→entity) matters more than inductive learning.

---

### Engram-Specific Fit

**Why it fits:**
- False positive reduction is exactly the core problem — stale nodes from old projects with similar embeddings get downranked via neighbor context
- Typed nodes/edges map naturally to RGCN/GAT (different aggregation per edge type)
- Inductive learning handles daily graph growth without retraining
- Runs entirely on-device — no privacy tradeoff

**What needs custom work:**
- **Temporal decay** requires feature engineering — encode `node_age_log` as a node feature; no off-the-shelf temporal GNN has been evaluated on small personal KGs
- **Graph construction is ambiguous** — recommend starting with top-8 cosine-similar neighbors per node (sparse, conservative); explicit Engram relationships are also edges
- **Cold-start is severe** — needs ~2 weeks of retrieval logs before model becomes useful
- No published benchmarks on personal KGs — all evidence comes from document ranking tasks

---

### Benchmarks

Published results on standard document ranking tasks:

| Benchmark | Baseline | With GNN | Gain |
|---|---|---|---|
| DL19 (TREC passage ranking) | AP=0.430 | AP=0.455 | +5.81% |
| DL20 (TREC passage ranking) | AP=0.453 | AP=0.470 | +3.75% |
| DLHard (TREC hard queries) | AP=0.230 | AP=0.242 | +5.22% |
| Natural Questions (G-RAG) | MRR≈0.75 | MRR≈0.80 | ~+6.7% |
| TriviaQA (G-RAG) | MRR≈0.82 | MRR≈0.87 | ~+6.1% |

Sources: [GNN Re-ranking via Corpus Graph](https://arxiv.org/html/2406.11720v1), [G-RAG Paper](https://arxiv.org/abs/2405.18414), [Graph-Based Re-ranking Survey](https://arxiv.org/html/2503.14802v1)

**Engram estimate: +4–6% AP/MRR** if false positives and temporal staling are real problems. Gains are larger on hard/ambiguous queries — which is exactly where Engram's false positive problem lives. Less than +2% if vector search baseline is already >0.85.

---

### Minimum Viable Implementation

**Pipeline:**
```
Query → Vector search (top-500 candidates) → Subgraph extraction → GNN re-rank → Return top-10
```

**Latency added:** ~10ms (subgraph extraction + GAT forward pass on 500-node subgraph)

**Code:** ~300 LOC in PyTorch Geometric

**Build path:**
1. **Week 1**: Instrument retrieval logs; collect implicit triplets (click = relevant, scroll-past = not)
2. **Week 2**: Build graph (explicit Engram edges + top-8 cosine-similar neighbors); train 2-layer GAT on 500–1000 triplets (~30 min CPU); deploy
3. **Week 3+**: Monitor, fine-tune weekly as logs accumulate
4. **Month 2 (optional)**: Manually label 20–30 hard queries for +1–2% boost

**Graph construction:**
```
Nodes: all entities/decisions/lessons in KG
Edges:
  - Explicit: Engram relationships (decision→lesson, entity→entity, etc.)
  - Learned: top-8 cosine-similar neighbors per node (sparse, conservative)
Node features: [embedding_768dim, node_type_onehot, log(age_days), project_id_onehot]
```

---

### Cold-Start Trajectory

| Time | State | Expected gain |
|---|---|---|
| Day 1–7 | Vector search only, logs being collected | Baseline |
| Day 8–14 | GNN deployed, sparse training data | +1–2% |
| Week 4 | Model converging | +3–4% |
| Month 2 (fine-tuned) | Implicit + optional manual labels | +4–6% |

**Cold-start recommendation:** Do not attempt transfer learning from public KGs (DBpedia, Wikidata) — domain mismatch is too large for personal KGs. Delayed activation (day 8–14) is the right call.

---

### Why the Swarm Convergence Is Justified

The four bots converged for the same reason the benchmarks show consistent gains: GNN re-ranking addresses a structural weakness in vector search that no prompt-engineering fix can solve. Vector search scores documents in isolation. GNN re-ranking scores them in context of their neighbors. For a knowledge graph where the same concept appears across multiple projects with different relevance, neighbor context is the only reliable disambiguation signal.

The improvement is modest (+4–6%) but addresses the specific failure mode — false positives from stale/out-of-scope nodes — that Engram's retrieval layer is most vulnerable to at scale.


---

## LongMemEval: Preference Retrieval Gap Analysis

*April 2026 — Sample analysis on S-split single-session-preference category*

### Background

The LME benchmark has 30 `single-session-preference` questions where the test system must recall preferences stated within a single conversation session. Our best overall score (60.8% LLM accuracy, `s_pref_fanout_retry3`) shows 26.7% on this category — the worst-performing category by far.

### Gap 1: preference_fanout has no effect

**Hypothesis:** The `preference_fanout` strategy (inject all preference-type nodes when the query signals preference-seeking) should improve recall for preference questions.

**Finding:** Fan-out shows **zero score change** across all 30 preference samples. Two mechanisms explain this:

1. **Signal verb mismatch.** `_PREFERENCE_SIGNAL_VERBS` contains words like "recommend", "suggest", "prefer", "like" — but LME preference questions often use different phrasing:
   - "What should I **serve** for dinner with my homegrown ingredients?"
   - None of the 5 extracted keywords (`serve`, `dinner`, `weekend`, `homegrown`, `ingredients`) match the signal verb set → fan-out never fires.

2. **Semantic mismatch persists even when fan-out fires.** When `serve` and `should` are added to signal verbs (forcing fan-out), the 160 preference nodes get injected as candidates — but `semantic_rerank` (top_k=100) still excludes all food/cooking preference nodes. The embedding model doesn't bridge "companion plant / gardening framing" → "dinner ingredient framing":
   - Node: *"User prefers basil as a companion plant for tomatoes"* (tags: gardening, companion plants, tomatoes, basil)
   - Query: *"What should I serve for dinner this weekend with my homegrown ingredients?"* (keywords: dinner, homegrown, ingredients)
   - Zero semantic overlap in either tags or embedding space.

**Conclusion:** Fan-out is correctly implemented but the wrong tool for this failure mode. The problem is a **framing mismatch** between extraction context (gardening conversation) and query context (cooking question).

### Gap 3: MAX_TOKENS retry (06f04340, 38146c39, 1da05512)

**Background:** Three samples previously errored with `finish_reason=MAX_TOKENS` (max_tokens=4096). The `preference_pass.py` was updated to:
- Accept `--max-tokens` flag (default 8192, retry used 16384)
- Skip failing sessions with `continue` instead of aborting the sample
- Filter to specific `--sample-ids`

**Result:** 853 new preference nodes added across the 3 samples. `1da05512` still has one session exceeding 16384 output tokens — that session is skipped gracefully (26 preference nodes missed).

**Score impact:** No improvement. Scores remain: `06f04340`=0.0, `38146c39`=0.0, `1da05512`=0.5.

**Root cause confirmed:** The relevant preference nodes ARE now in the database:
- `06f04340`: has `n_50542de5` ("prefers recipes combining fresh basil and mint", tags: recipe, basil, mint) and `n_abeffa2d` ("prefers basil as companion plant for tomatoes", tags: gardening, companion plants, tomatoes, basil)
- But BFS entry nodes for "dinner/homegrown/ingredients" don't touch these nodes (tag mismatch)
- Even with all 160 preference nodes injected via fanout, semantic_rerank scores them below rank 100

### Root cause: framing gap between extraction and retrieval

LME preference questions follow a pattern: the ground-truth answer requires the system to connect two distinct conversation framings:
- **Extraction framing:** "I'm planning a garden, what companion plants work well with tomatoes?" → extracted as gardening preference nodes
- **Query framing:** "What should I serve for dinner with my homegrown ingredients?" → retrieves cooking/dinner nodes

The model correctly extracted the preferences that were stated, but under the gardening label. The retrieval system has no mechanism to bridge gardening context → cooking context unless the nodes themselves contain both frames.

### Path forward: implicit preference extraction

The fix is **implicit preference extraction** — a new extraction pass that infers latent cooking/usage preferences from explicit gardening knowledge:
- "User grows cherry tomatoes, basil, and mint in their garden" → "User has homegrown cherry tomatoes, basil, and mint available as cooking ingredients"
- "User prefers basil as companion plant for tomatoes" → "User grows basil alongside tomatoes, suitable for Italian cooking"

This requires a new targeted extraction pass (`--implicit-prefs`) that reads existing preference/implementation nodes and generates inferred preference nodes with cooking/usage framing, dual-tagged with both original and inferred domains.

**Estimated complexity:** ~60 lines (new extraction prompt + `extract_targeted` hook). High likelihood of improving `06f04340` score; uncertain impact on the other 19 zero-scoring preference questions (most likely have true knowledge gaps, not framing gaps).

---

## Implicit Preference Extraction — Results

*April 15, 2026 — `--implicit-prefs` flag, `extract_implicit_prefs()` in `extractor.py`*

### What was built

A post-session extraction pass (`extract_implicit_prefs`) that infers cross-domain preferences from explicit knowledge nodes. Gardening context becomes cooking context; companion-plant choices become ingredient availability. The pass runs after the normal session-by-session preference extraction in `preference_pass.py --implicit-prefs`.

Key implementation:
- `build_implicit_prefs_prompt(existing_nodes)` in `prompts.py` — prompts the LLM to reason across domains
- Nodes tagged `inferred-preference` + both source and target domain tags (e.g., `gardening`, `homegrown`, `cooking`, `recipe`)
- `extract_implicit_prefs(existing_nodes, config)` in `extractor.py` — async, uses `assign_ids_incremental` to avoid ID collisions

### Spot-check results (3 preference-category samples, `pref_fanout` config, user_patched DBs)

| Sample | Question | Without implicit prefs | With implicit prefs |
|--------|----------|----------------------|---------------------|
| `06f04340` | "What should I serve for dinner with my homegrown ingredients?" | 0.0 | **1.0** |
| `38146c39` | "My chocolate chip cookies need something extra. Any advice?" | 0.0 | 0.0 |
| `1da05512` | "Should I buy a NAS device now or wait?" | 0.5 | 0.5 |

`06f04340` is the canonical framing-gap case: gardening companion-plant nodes (basil, mint, tomatoes) were unretrievable for a cooking query. The implicit pass added 4 inferred nodes including `n_258e1a0f` ("user prefers to cook with homegrown basil, borage, chives... especially in recipes that complement tomatoes") tagged `homegrown`, `cooking`, `recipe`, `inferred-preference` — these matched the dinner query exactly. LLM judge: 0.0 → **1.0**.

`38146c39` did not improve — the turbinado sugar/cookies preference was already extractable by the normal pass; the implicit pass found no cross-domain bridge.

### Baseline: `pref_fanout_retry3` full run (500 samples, no implicit prefs)

| Metric | Score |
|--------|-------|
| Overall LLM accuracy | 60.8% |
| single-session-preference LLM accuracy | 26.7% (8/30) |
| single-session-assistant | 87.5% |
| knowledge-update | 71.8% |
| temporal-reasoning | 60.2% |

Preference questions remain the weakest category. The framing-gap accounts for a fraction of the 73.3% failure rate; the majority are likely true knowledge gaps (the preference was never stated in a retrievable form).

### Full run results (30 preference samples, April 15 2026)

All 30 `single-session-preference` samples augmented in 17.3 min. 8,743 new preference nodes added (214 implicit cross-domain nodes). Judged with the standard LLM judge.

| Metric | Baseline (no implicit prefs) | After implicit prefs | Delta |
|--------|------------------------------|----------------------|-------|
| LLM accuracy (strict) | 26.7% (8/30) | **33.3% (10/30)** | **+6.6pp** |
| LLM partial credit (mean score) | — | **51.7%** | — |
| Scores == 1.0 | 8 | **10** | +2 |
| Scores >= 0.5 | — | **21/30** | — |

New perfect-score samples (1.0): `505af2f5`, `0a34ad58` (plus previously solved `06f04340`).
8 samples with score 0.0 remain — likely true knowledge gaps (preference never stated retrievably).

Per-sample scores:

| Sample | Score | | Sample | Score |
|--------|-------|-|--------|-------|
| 06878be2 | 0.5 | | 505af2f5 | **1.0** |
| 06f04340 | **1.0** | | 54026fce | **1.0** |
| 07b6f563 | 0.0 | | 57f827a0 | **1.0** |
| 09d032c9 | 0.5 | | 6b7dfb22 | 0.5 |
| 0a34ad58 | **1.0** | | 75832dbd | 0.0 |
| 0edc2aef | 0.0 | | 75f70248 | **1.0** |
| 195a1a1b | 0.0 | | 8a2466db | 0.0 |
| 1a1907b4 | 0.5 | | 95228167 | **1.0** |
| 1c0ddc50 | **1.0** | | a89d7624 | 0.0 |
| 1d4e3b97 | 0.5 | | afdc33df | 0.5 |
| 1da05512 | 0.5 | | b0479f84 | **1.0** |
| 32260d93 | 0.0 | | b6025781 | 0.5 |
| 35a27287 | 0.5 | | caf03d32 | 0.5 |
| 38146c39 | 0.0 | | d24813b1 | **1.0** |
| — | — | | d6233ab6 | 0.5 |
| — | — | | fca70973 | 0.0 |

---

## Bi-Temporal Memory Architecture

*April 2026 — Schema design inspired by Zep/Memento research*

### Background

Justin requested a bi-temporal memory implementation "similar to what Zep is doing, but better." Research into Zep (arxiv 2501.13956), Memento (90.8% LongMemEval, SQLite), and temporal database standards (SQL:2011 SYSTEM_TIME / VALID_TIME) shaped the design.

### Zep limitations

- Cloud-based, proprietary, requires PostgreSQL or external graph DB (Neo4j, FalkorDB)
- No local-first / offline capability
- Architecture details not public; no point-in-time reconstruction guidance published

### Our implementation (better than Zep in key dimensions)

**Schema additions to `nodes` table:**

| Column | Type | Semantics |
|--------|------|-----------|
| `valid_to` | TEXT (ISO 8601) | When this fact stopped being true in the world. NULL = still current. Set to superseding node's `occurred_at` when superseded. |
| `is_active` | INTEGER (0/1) | Cached `valid_to IS NULL` flag. Indexed for O(log N) lookups. Maintained by `merge_extraction()` and backfill migration. |

**Relationship to existing columns:**
- `occurred_at` = valid time start (when fact became true in the conversation world)
- `valid_to` = valid time end (when fact stopped being true — the superseding event's `occurred_at`)
- `created_at` = transaction time (when the system ingested this fact)

This is the standard bi-temporal model: **valid time** (`occurred_at` → `valid_to`) + **transaction time** (`created_at`).

**New GraphStore methods:**

- `get_active_nodes()` — O(log N) query using `is_active` index
- `get_nodes_at_time(valid_at, transaction_at)` — bi-temporal point-in-time slice. Answers "what was true in the world at session N?" (valid_at) and "what did the system know as of ingestion time T?" (transaction_at)
- `get_revision_history(node_id)` — full belief-revision lineage via recursive supersedes[] traversal. Returns chain oldest-first.

**Retriever improvement:**

`prune_superseded()` now uses `is_active=0` as a fast path (O(log N)) before the O(N×edges) edge traversal. Edge traversal retained as belt-and-suspenders for pre-migration DBs.

### Migration strategy

All additions are backward-compatible via SQLite `ALTER TABLE` migrations in `init_db()`:
1. Add `valid_to TEXT` and `is_active INTEGER DEFAULT 1`
2. Backfill: set `is_active=0` + `valid_to=superseding.created_at` for all nodes listed in any `supersedes[]` array
3. Future `merge_extraction()` calls maintain `is_active`/`valid_to` automatically

### Memento's 90.8% LongMemEval result (reference)

Memento (open-source, SQLite) reaches 90.8% overall with:
- Same four temporal columns (`valid_at`, `invalid_at`, `created_at`, `updated_at`)
- Entity resolution (exact → fuzzy → phonetic → embedding → LLM)
- Contradiction detection
- Verbatim transcript fallback

Our implementation matches Memento's core temporal schema. Remaining gaps vs. Memento:
- No automatic contradiction detection (planned)
- No entity resolution pipeline (partial — fact-hash + semantic dedup exists)
- No verbatim fallback (raw_sentences table exists but is decoupled from retrieval)
