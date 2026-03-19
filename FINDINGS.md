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

Three extraction modes were benchmarked against the same retrieval evaluation:

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

---

## Recall Analysis

### Why full extraction wins

Full extraction gives the LLM complete global context — it sees all 42 messages in a design transcript and can reason about the entire arc. Incremental and buffered modes give the LLM only a window, plus a retrieved snapshot of what's been extracted so far. That snapshot is good but imperfect (recall on the snapshot itself is ~60%), so errors compound over turns.

### Where all modes struggle (0% recall questions)

Six questions had 0% recall in the full mode run:
- `q_pipe_02`, `q_pipe_07`: Data pipeline questions about specific numeric values (e.g., deduplication window size, backpressure thresholds)
- `q_auth_04`, `q_auth_06`: Authentication questions about security edge cases mentioned briefly in conversation

The pattern: **specific numeric values and briefly-mentioned edge cases are under-tagged.** The extraction LLM assigns tags based on primary topic keywords, but a fact like "30-second deduplication window using Redis sorted sets" may only get tagged `["deduplication", "redis", "window"]` — and a query about "event replay" or "idempotency" won't match those tags.

### Recall improvements implemented

**1. Fact-text fallback search**

When `get_nodes_by_tags` finds no tag matches, fall back to substring search against the `fact` column. This catches vocabulary mismatches where the query terms and the stored tags diverge. For example, querying "idempotency handling" can still find a node tagged `["deduplication", "exactly-once"]` if those words appear in the fact text.

**2. Two-keyword overlap threshold for BFS seeding**

Before BFS traversal, entry nodes are filtered to those matching ≥2 query keywords. Single-keyword matches are too generic and can seed the BFS in the wrong cluster of the graph. If no nodes pass the ≥2 threshold, fall back to all candidates. This prevents a query about "API versioning strategy" from seeding BFS at an unrelated node that happens to be tagged `["api"]`.

These two changes were applied to `retriever.py` and `store.py` after the initial baseline benchmarks. The improvements are visible in the current benchmark results.

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

## Claude Code Hook Integration Design

Context Broker can integrate with the Claude Code CLI via hooks to silently inject project context into every prompt without user effort.

### Hook architecture

**`UserPromptSubmit` hook** — runs before each user prompt reaches the model:
1. Receives the user's prompt text on stdin as JSON
2. Runs `ctx query <project> "<prompt>"` locally (SQLite lookup, <5ms)
3. Writes the retrieved context block to stdout
4. Claude Code prepends this to the prompt automatically

**`Stop` hook** — runs after each assistant response:
1. Receives the last assistant message and last user message
2. Formats them as a turn: `**User**: ... \n\n**Assistant**: ...`
3. Pipes the turn to `ctx extract-turn <project>`
4. The buffered extractor decides whether to flush to the LLM

### What this achieves

- **Zero user friction.** The user works normally. Every prompt is silently enriched with project context. Every response is silently extracted into the graph.
- **Compounding quality.** The graph grows richer with each exchange. By conversation 10, retrieval is meaningfully better than conversation 1 because more architectural context has been accumulated.
- **Cross-session memory.** When the user starts a new Claude Code session, the hook immediately reloads project context. The model behaves as if it has been working on the project continuously.

### The intent amplification effect

An underappreciated benefit: the injected context often conveys user intent more accurately than the user's own prompt. Users operate with tacit knowledge — constraints, decisions, and history they hold in their heads but don't re-state. The broker surfaces this knowledge explicitly. A prompt like "add rate limiting to the endpoints" becomes much more actionable when the model also receives: "Rate limits: 1000 req/min authenticated, 100 req/min unauthenticated, enforced by Kong via X-RateLimit headers." The broker bridges the gap between what the user said and what they meant.

---

## Known Limitations

### Recall ceiling (~60% on current benchmarks)

Full extraction with Gemini 2.5 Flash achieves 60% recall on structured transcripts designed for extraction. Real-world transcripts are messier; practical recall may be lower. Missing 40% of facts is a meaningful failure rate — the model may confidently apply wrong or incomplete context.

This is partly an extraction quality problem (some facts are under-tagged or missed) and partly a retrieval problem (the BFS neighborhood doesn't always contain what's needed). Both have headroom for improvement.

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

The buffer persists to `buffer.json` in the project directory between `ctx extract-turn` invocations. This means a buffer can span multiple shell sessions — a turn added via hook in one process is still buffered when the next prompt arrives. The `ctx query` command auto-flushes any pending buffer before retrieval to ensure the graph reflects all available conversation content.

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
ctx extract myproject transcript.md --lessons

# Multiple targeted passes
ctx extract myproject transcript.md --verify --lessons --decisions

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

The single most impactful extraction improvement would be richer synonym and value-level tagging. The extraction prompt already instructs the LLM to "tag richly with every keyword a future query might use," but specific numeric values and edge-case terminology are still commonly missed. A dedicated tagging pass or post-processing step that adds numeric values and known synonyms from a domain vocabulary could meaningfully improve the 60% recall ceiling.

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

Extend the `ctx reconcile` command (which already detects supersedes relationships) with a dedup phase. The LLM is shown clusters of nodes with similar fact text (pre-filtered by token overlap or BM25 similarity) and asked to identify which are duplicates vs. genuinely distinct facts. For duplicates, it selects the canonical fact and flags the rest for merge or deletion.

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
| Full extraction recall | 60% | Gemini 2.5 Flash, default strategies |
| Buffered extraction recall | 47% | ~18% call rate vs per-turn |
| Incremental cross-turn edges | ~27/transcript | api_design + data_pipeline average |
| Avg retrieval latency | <5ms | Local SQLite, all modes |
| Avg context output | ~540 tokens | top_k=10, default strategies |
| Token reduction vs full transcript | ~90–95% | vs 8k–20k token transcripts |
| Break-even (token ROI) | ~7 downstream prompts | At avg 540 token injection vs 4000 token history |
| Extraction time (full, 3 transcripts) | 230s | Gemini 2.5 Flash |
| Extraction time (buffered, 2 transcripts) | 196s | 7 flushes across 39 turns |

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

**Git-tracked JSON export** is the path of least resistance. The `ctx export` command already writes a graph snapshot. If that snapshot is committed to the shared repo, any developer can import it locally. The limitation is that SQLite binary files don't merge in git — switching `ctx export` to a line-oriented JSON format (one node per line, one edge per line) would make diffs readable and merges tractable.

**PostgreSQL backend** is the right long-term answer. The `GraphStore` API (`add_node`, `add_edge`, `get_nodes_by_tags`, etc.) maps directly to PostgreSQL with no interface changes. Every developer connects to the shared instance. Concurrent writes are handled natively. This also enables a `ctx serve` REST layer so developers without direct database access (different networks, managed environments) can push extractions and pull queries over HTTP.

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

3. **Conflict flagging on cross-author supersedes** — instead of silently pruning, mark conflicts with a `conflict: true` field. The `superseded_pruning` strategy skips conflicted nodes; a `ctx conflicts` command lists them for resolution.

4. **`ctx sync` command** — pushes the local buffer to the shared graph and pulls nodes added by other contributors since the last sync. Enables async collaboration without requiring always-on connectivity.

5. **Per-contributor transcript namespacing** — convention only, no code change: `contributor/topic_date.md`. Attribution is already implicit in `source_transcript`.

### The team use case value proposition

From the LLM's perspective, a shared multi-contributor graph is just a richer single-user graph. BFS traversal across nodes extracted from five different developers' conversations works identically to traversal across one developer's conversations. The model receives structured facts regardless of their origin.

The practical effect: a developer asking "what are the auth constraints for my new endpoint?" retrieves not just their own prior decisions, but the auth team's design decisions, the security team's constraints, and any open questions flagged by the architecture review — all from different contributors' conversations, assembled in one context block. The broker becomes a team-wide working memory, not a personal one.

This compounds more aggressively than the single-user case. A single developer's graph grows linearly with their conversations. A five-developer team's graph grows five times as fast and captures cross-domain relationships that no single developer's conversation would contain.

---

## Future Directions

Listed in rough priority order based on expected impact relative to implementation complexity.

**1. Hook script implementation**
Write `~/.claude/hooks/ctx-inject.sh` (UserPromptSubmit) and `~/.claude/hooks/ctx-extract.sh` (Stop) to activate seamless Claude Code integration. The architecture is fully designed; the scripts are the remaining step to making the broker operational in daily development use.

**2. Re-run benchmarks with the `KeyError` fix**
The `project_auth_system.md` transcript failed in both incremental and buffered modes. Now that the malformed-node filter is in place, a re-run would give complete three-way benchmark data for all 23 evaluation questions.

**3. Hook impact benchmark (`bench_hook_impact.py`)**
The benchmark harness exists but has not been run against a real project graph. Running it would quantify the recall delta between brokered and unbrokered prompts, providing empirical evidence for the intent amplification hypothesis.

**4. Richer synonym tagging**
Add domain synonyms to the extraction prompt or as a post-processing pass. Target: specific numeric values, acronym expansions, and common paraphrase pairs (rate limit / throttling, authentication / auth / authn, etc.).

**5. Extraction quality scoring**
A per-transcript extraction quality score (node count relative to transcript length, tag density, edge-to-node ratio) would make it easy to identify transcripts that need re-extraction or a different model.

**6. PostgreSQL backend for shared team graphs**
Swap `GraphStore`'s SQLite connection for PostgreSQL. The store API is unchanged; PostgreSQL handles concurrent multi-developer writes natively and enables a `ctx serve` REST layer for teams without direct database access. See the Multi-Developer Shared Graph section for full architecture.

**7. Git-exportable graph format**
Switch `ctx export` output from a single JSON blob to a line-oriented format (one node/edge per line) so graph snapshots can be diff'd and merged in git. Enables async team collaboration without any server infrastructure.

**8. Multi-project graph queries**
Currently each project is an isolated SQLite database. Cross-project queries (e.g., "what auth patterns have we used across all projects?") would require either a merged graph or a query federation layer.

---

## Productization

### Issues and Mitigation Plans

#### 1. Recall quality is not product-ready

**Issue:** Full extraction achieves 60% recall on purpose-built, structured transcripts. Real-world conversations are messier; production recall is likely lower. Missing 40% of facts means the broker occasionally injects stale or incomplete context silently and confidently. A single incident where injected context causes a real bug is enough to destroy user trust.

**Plan:**

*Short term — raise the recall floor before shipping:*
- Add synonym and value-level tagging: instruct the extraction LLM to include numeric thresholds, acronym expansions, and common paraphrase pairs directly in tags. Target the known failure class: specific values and edge cases that are under-tagged today.
- Add a post-extraction validation pass that flags nodes with fewer than 3 tags, facts under 10 words, or a confidence score that contradicts the node type (e.g., an "implemented" node at confidence 0.3). Flag for re-extraction rather than silent acceptance.
- Add extraction quality scoring per transcript (node count / transcript length, tag density, edge-to-node ratio). Surface the score to the user so they know which transcripts produced weak graphs.

*Medium term — close the vocabulary gap:*
- Implement an optional embedding-based fallback retrieval step. When keyword and fact-text search both return no results, fall back to cosine similarity over node fact embeddings. This is opt-in infrastructure (requires an embedding model) but closes the remaining synonym mismatch gap.
- Fine-tune or prompt-engineer a dedicated extraction model on a labeled dataset of transcripts and ground-truth node sets. A model that has seen thousands of correctly-extracted graphs will tag more consistently and miss fewer edge cases than a general-purpose LLM given a prompt.

*Product-level guard:*
- Set an explicit internal recall target of ≥80% on the benchmark suite before any public release. Make the benchmark reproducible by any contributor so the threshold is continuously verified.

---

#### 2. Cold start kills day-one experience

**Issue:** The graph is worthless until it has content. New users get no value on day one. There is no graceful degradation, no preview of what the tool will do, and no incentive to invest in extraction before the payoff is visible.

**Plan:**

*Onboarding flow:*
- Ship a `ctx demo` command that populates a sample project graph from a bundled transcript, then runs a set of example queries against it. Users see the output format and retrieval quality before investing any extraction effort.
- On first `ctx query` against an empty graph, print an actionable message: "No context found. Run `ctx extract <project> <transcript>` to build your graph."

*Progressive extraction:*
- For Claude Code hook users, the graph builds automatically in the background from the first conversation. Make this visible: after each buffered flush, print a brief status line — "Context Broker: +8 nodes extracted (42 total)" — so users see the graph growing without any manual steps.
- Offer a `ctx bootstrap` command that takes an existing codebase and generates a starter graph from README files, architecture docs, and any markdown in the repo. Not as rich as conversation extraction, but provides immediate value.

*Seeded starter graphs:*
- For common tech stacks (React/Node, Django, Rails, Go microservices), provide downloadable starter graphs containing common architectural constraints and best practices. Users merge these into a new project to get instant useful context, then their conversations enrich and override it over time.

---

#### 3. Extraction cost sits on the user

**Issue:** Every extraction call requires a capable LLM (Gemini 2.5 Flash, GPT-4o, Claude Sonnet or better). This means API key management, real money, and 30–120 second latency per transcript. Absorbing this cost as a cloud service is expensive; passing it to the user creates friction and configuration burden.

**Plan:**

*Tiered extraction quality:*
- Document model tiers explicitly: Tier 1 (local 7B–14B models, free, ~40–64% recall), Tier 2 (Gemini 2.5 Flash, cheap, ~92–94% recall), Tier 3 (Claude Sonnet / Gemini Pro, higher cost, untested ceiling). GPT-4o benchmarked at only ~50% — do not place in Tier 3. Let users choose based on their cost tolerance.
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

*Phase 1 — Claude Code (now):*
The hook integration is fully designed. Ship the hook scripts and document the setup. Establish the pattern, collect feedback, and build the user base within the Claude Code ecosystem before expanding.

*Phase 2 — VS Code extension:*
A VS Code extension can integrate with GitHub Copilot Chat, Cursor, and Continue.dev (among others) via the Language Model API or by reading from the workspace chat history. The extension calls `ctx query` on every prompt submission and injects the result as a context message. Reaches the largest IDE user base without requiring CLI tool adoption.

*Phase 3 — Browser extension:*
For web-based LLM tools (ChatGPT, Claude.ai, Gemini), a browser extension intercepts prompt submission, calls a local Context Broker server (`ctx serve --port 7070`), and injects the retrieved context block. This covers the remaining surface area. Technically feasible but requires maintaining extension manifests across Chrome/Firefox and adapting to UI changes in each web app.

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
- `ctx last` — show the exact context block that was injected into the most recent query, with node IDs, confidence scores, and source transcripts. Makes the broker's contribution visible on demand without making it noisy by default.
- `ctx explain "<query>"` — show which keywords were extracted from the query, which entry nodes matched, how BFS traversed the graph, and which strategies pruned what. Full retrieval trace for debugging.

*Optional verbose mode for hooks:*
Add a `--verbose` flag to the hook integration that appends a collapsed summary to each prompt: "Context Broker injected 6 nodes (42 tokens) — run `ctx last` to inspect." Users who want visibility get it; users who prefer silence stay silent by default.

*Confidence surfacing in output:*
The markdown output already includes confidence scores. Make them more prominent and add a color-coded indicator (in terminals that support it) so users can glance at the reliability of injected context. A block of 0.9+ confidence nodes reads differently than a block of 0.4–0.6 nodes.

*Conflict visibility:*
For the multi-developer case, flagged cross-author conflicts should appear prominently in retrieval output: "⚠ Conflicting decisions found — run `ctx conflicts` to resolve." Silent conflict suppression is worse than noisy conflict surfacing.

---

#### 8. Who pays, and for what

**Issue:** Individual developers are difficult to monetize directly. The natural paying customer is the team or enterprise, which requires significant pre-revenue infrastructure investment (PostgreSQL backend, access controls, SSO, audit logs). Individual developers are the primary adopters but may not convert to paid.

**Plan:**

*Freemium individual tier (acquisition):*
- Local-only, SQLite, unlimited projects, full CLI — permanently free.
- Builds the user base, generates word-of-mouth, and creates the pool of users who become team purchasers when they advocate for the tool at their company.
- No extraction cost to the company on this tier (users supply their own API keys).

*Team tier (primary revenue):*
- Shared PostgreSQL graph, contributor attribution, conflict resolution, `ctx sync`.
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

**Baseline state entering this session:** Gemini 2.5 Flash at ~57–66% recall on the standard benchmark (3 transcripts × 23 questions × 4 presets).

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
| Gemini 2.5 Flash | ~297 | **92%** | **19/23** | ~195s | Reference; best overall (2026-03-13) |
| Gemini 2.5 Flash + verify | ~295 | **94%** | **20/23** | ~396s | Best with verification pass (2026-03-13) |
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
ctx extract auth_debug benchmarks/transcripts/project_auth_system.md --verify
ctx orchestrate auth_debug
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
ctx extract auth_debug benchmarks/transcripts/project_auth_system.md --verify --decisions
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

`ctx synthesize <project>` is a post-extraction maintenance command that runs an LLM pass over **existing graph nodes** (not a transcript). It looks for clusters of 3+ parallel facts about the same metric across different subjects and creates cross-cutting summary nodes — e.g., a single "Overall Recall Ranking" node that aggregates all model results.

This solves the **survey query problem**: BFS retrieval with top_k=20 may explore only one model's cluster at a time. "Rank all models" needs a single node tagged with all model names for retrieval to surface the complete answer.

### Observed behavior (2026-03-18)

Run: `ctx synthesize ContextBroker --tags benchmark --tags recall --tags gpt-4o --tags nemotron`
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
| **A. Periodic maintenance** | `ctx synthesize` | Full graph (filtered by type/tags) | High (~30–120s) | After multiple sessions have accumulated; before demo/query-heavy sessions |
| **B. Post-extract (full graph)** | `ctx extract --synthesize` | Full graph | Adds ~30–120s to extraction | When a new transcript is likely to complete a cluster already partially in the graph |
| **C. Post-extract (recent only)** | `ctx extract --synthesize-recent` *(not yet implemented)* | `get_recent_nodes(limit=100)` | Low (~5–15s) | Lightweight per-session synthesis; won't catch cross-session patterns |

**Current recommendation:** Use option A (periodic `ctx synthesize`) as the primary pattern. Run with `--tags` to target specific subject clusters when you know what you're looking for. Option B (`--synthesize` on extract) is viable when running full re-extractions. Option C is a potential future improvement.

### Synthesis is additive

Synthesis nodes accumulate — each run with different `--tags` adds new summary nodes without deleting previous ones. The existing fact-hash deduplication prevents exact duplicates. If you need to prune stale synthesis nodes, a future `ctx prune --synthesis` command can use the `source_transcript = "__synthesis__"` tag to identify them.

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
| **RAPTOR** (arXiv:2401.18059) | Recursive abstractive tree construction — hierarchical synthesis that clusters leaf nodes into progressively abstract summaries; complements `ctx synthesize` |
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
