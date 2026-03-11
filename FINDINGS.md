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
- Document model tiers explicitly: Tier 1 (local 7B–14B models, free, ~40% recall), Tier 2 (Gemini Flash / GPT-4o Mini, cheap, ~55% recall), Tier 3 (Gemini Pro / GPT-4o / Claude Sonnet, quality, ~60%+ recall). Let users choose based on their cost tolerance.
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

If recall needs to improve beyond 66%, the most promising directions (not yet tried) are:
- Post-processing: parse numeric values out of `fact` text at ingest time, add as tags automatically
- Query-side: detect numeric tokens in the query and add them to the keyword set before tag matching
- Hybrid retrieval: fall back to full-text search on `fact` text when tag matching returns nothing
