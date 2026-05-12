# Agent Orchestration Plan

**Created:** 2026-04-28  
**Status:** Planning  
**Related:** `DEVELOPMENT_PLAN.md` (orchestrator REPL), `FINDINGS.md` (DB architecture), `engram-roadmap.md`

---

## What This Is

A plan for building an agent orchestration stack where Engram serves as the routing brain — surfacing accumulated institutional knowledge before agent tasks run, extracting new knowledge after they complete, and compounding both over time.

This is architecturally distinct from `DEVELOPMENT_PLAN.md`, which covers the `engram chat` REPL and compaction loop. That plan is about Engram as a conversation assistant. This plan is about Engram as a pre/post-dispatch layer in an autonomous agent workflow.

---

## The Stack

Four layers, four phases of a task lifecycle:

```
1. Pre-dispatch (Engram)
   ↓ query DECISION, LESSON_LEARNED, PROCEDURE, open QUESTION nodes in task scope
   ↓ produce context block + concrete blocker list
   ↓ route: proceed / request human review

2. Context injection (Graphify — optional)
   ↓ AST-based code graph for relevant codebase module
   ↓ function signatures, call relationships, dependencies
   ↓ combined with Engram context into single context block

3. Execution (agent-orchestrator)
   ↓ isolated worktree, CI feedback loop, PR
   ↓ before_run hook → Engram routing gate fires
   ↓ after_run hook → Engram extraction fires

4. Post-run (Engram extraction + parallel review)
   ↓ extract DECISION/LESSON/PROCEDURE nodes from session log (not just diff)
   ↓ parallel domain reviewers validate extracted nodes
   ↓ high-confidence nodes promoted to graph
   ↓ skills promoted to PROCEDURE nodes
```

---

## Key Decisions (locked in 2026-04-28)

| Decision | Rationale |
|---|---|
| **agent-orchestrator** over Symphony spec | Working TypeScript code with plugin slots and `beforeEachRun`/`afterEachRun` hooks vs. a spec you have to implement from scratch. Start with agent-orchestrator, use Symphony spec as design reference. |
| **Extract from session log, not diff** | The diff shows what survived. The log shows what was tried, abandoned, and why — that's where LESSON_LEARNED and DECISION nodes live. |
| **Skills as PROCEDURE nodes** | No separate skill registry. Generated skills stored as `process` node type with high retrieval weight. One retrieval call for memory + skills. |
| **Parallel review pattern from CompoundEngineering** | Adapted: domain-specialized validators (fact-checker, contradiction-checker, scope-validator, temporal-consistency-checker) run in parallel over extracted nodes post-run. Not auto-magic — each is an LLM call with a specific prompt. |
| **Do NOT use CompoundEngineering's compound phase** | It's manual documentation work with no automation. The flywheel we want requires extraction from agent run logs, not human-curated summaries. |
| **Concrete blocker lists over confidence floats** | Pre-dispatch output is "here are three things that blocked similar tasks before" not "confidence: 0.73." Actionable, not probabilistic. |
| **LESSON_LEARNED as the strongest routing signal** | DECISION nodes say what was chosen. LESSON_LEARNED nodes say what went wrong. The latter is more predictive of whether a task needs human review. |
| **ANNS with re-ranking** | Binary or PQ quantized ANN for candidate fetch → exact float32 cosine re-ranking over top-100 → top-k to BFS pipeline. Gains are in RAM and search speed, not disk. See FINDINGS.md for full analysis. |
| **Graphify is optional, not load-bearing** | Don't absorb it into Engram. Run it separately, extend Engram's retrieval pipeline to query its output as a second source. Validate the 71.5× token reduction claim on your actual codebase before designing token budgets around it. |

---

## Two-Tier Knowledge Architecture

### Tier 1: Project Memory (episodic)

What we have today. Extracted from conversation transcripts and agent session logs. Typed nodes, supersession tracking, BFS traversal, high precision.

- Node types: `decision`, `constraint`, `implementation`, `transition`, `preference`, `process`, `question`, `lesson_learned`
- Retrieval: BFS from tag-matched entry nodes + ANNS re-ranking
- Storage: SQLite (current) → Lance+Tantivy (scale threshold)
- Precision target: 95%+ (current benchmark baseline)

### Tier 2: Reference Knowledge (semantic)

**New.** Ingested from external sources: code cookbooks, framework best practices, Stack Overflow pitfall answers, internal runbooks, technical documentation.

- Node type: `reference` (new) — no supersession, no project scope, has TTL
- Required fields: `source_url`, `source_date`, `ingested_at`, `domain_tags`, `quality_score`
- Retrieval: FTS (Tantivy) + ANN, lower weight than tier 1
- Storage: Lance+Tantivy required — reference volume immediately exceeds SQLite comfort zone

**Retrieval weighting:** Tier 1 (project memory) always outranks tier 2 (reference) when both are relevant. Reference fills gaps; project memory answers the specific question.

### Reference node quality gate

Not all external content is equal. Ingestion must gate on:
- Stack Overflow: vote threshold (e.g., score ≥ 20) + accepted answer flag
- Documentation: official source only (not blog reposts)
- Date: reject reference nodes older than configurable TTL (default: 2 years)
- Optional: LLM quality score at crawl time for ambiguous sources

### Reference knowledge scope control

"Ingest Stack Overflow" = millions of nodes. Scope must be explicit:
- Domain-specific ingestion: `engram ingest-reference <project> --domain python-async --source stackoverflow --limit 200`
- Internal runbooks: `engram ingest-reference <project> --path ./docs/runbooks/`
- Framework docs: `engram ingest-reference <project> --url https://fastapi.tiangolo.com/`

Open-ended crawl is not a supported mode in the initial implementation.

---

## Cold-Start Onboarding

A fresh Engram install has no LESSON_LEARNED or DECISION nodes. The routing gate routes everything at minimum confidence — which means "always human review." This is the correct safe default but it produces a bad first impression.

**Required onboarding step (must ship with v1):**
```bash
# Bootstrap LESSON_LEARNED nodes from commit/PR history before first deployment
engram extract <project> <path/to/commit_log.txt>
engram extract <project> <path/to/pr_descriptions.txt>
```

**Minimum graph density gate:**
Pre-dispatch routing gate should not activate until the graph has at least N LESSON_LEARNED nodes (suggested: 10). Below threshold, skip the routing gate entirely and proceed directly to execution. Display: "Routing gate inactive — graph density below threshold (3/10 LESSON_LEARNED nodes). Run more tasks to activate."

**Tiered activation:**
Not every task needs all four layers. The routing gate determines the tier at pre-dispatch:
- Low complexity (no matching blockers, no open questions): skip Graphify, skip parallel review
- Medium: Engram gate + execution only
- High (known blockers, unresolved questions): full four-layer stack

---

## Parallel Review Architecture

Adapted from CompoundEngineering's `/ce-code-review` pattern (14 domain-specialized agents) applied to post-run node validation rather than code quality.

Post-run parallel validators (run simultaneously over extracted nodes):

| Validator | What it checks |
|---|---|
| **Fact checker** | Does the extracted fact accurately reflect what the session log says? |
| **Contradiction checker** | Does this node contradict an existing high-confidence node in the graph? |
| **Scope validator** | Is this node scoped to the right project/domain, or did it cross-contaminate? |
| **Temporal consistency** | Is the timing/sequence information internally consistent? |
| **Supersession detector** | Does this node render an existing node obsolete? Should supersedes edge be added? |

All five run in parallel on the batch of extracted nodes. Majority agreement promotes a node to the graph. Contradiction detected → node goes to `conflict_log` for review above confidence threshold.

Node-count cap: run parallel review only if extracted batch ≥ 5 nodes. Single-node extractions skip parallel review (too noisy/expensive for one node).

---

## Database Trigger Conditions

The Lance+Tantivy+graph layer migration (documented in FINDINGS.md) is not premature if reference knowledge ingestion is in scope. Trigger conditions:

| Trigger | Threshold | Notes |
|---|---|---|
| Reference knowledge ingestion | Day 1 if in scope | Even 50K reference nodes exceeds sqlite-vec comfort zone |
| Concurrent write contention | ~5 parallel agents writing simultaneously | SQLite WAL serializes writes |
| Graph scale | ~500K nodes | sqlite-vec ANN degrades noticeably |
| SaaS / multi-tenant | Day 1 of cloud deployment | Per-user SQLite doesn't scale operationally |

**If reference knowledge ingestion is NOT in scope for v1 orchestration stack:** SQLite continues to serve adequately. Build orchestration stack MVP first, migrate DB when a trigger condition is hit.

**If reference knowledge ingestion IS in scope for v1:** Lance+Tantivy is a prerequisite, not a follow-on. Build it before the orchestration stack integration work begins.

---

## Build Sequence

### Phase 0: Prerequisite decision (do now)
**Is reference knowledge ingestion in scope for v1?**

- Yes → Build Lance+Tantivy first (Phase 1), then orchestration stack
- No → Build orchestration stack MVP first (Phase 1), migrate DB when triggered

---

### Phase 1A: Orchestration Stack MVP
*Prerequisite: Phase 0 decision made. If reference ingestion in scope, Lance+Tantivy must be complete first.*

**Goal:** Validate the routing gate hypothesis. Does surfacing Engram context before agent tasks run change outcomes?

**Deliverables:**
- `before_run` hook in agent-orchestrator → calls `engram query <project> "<task description>"` → injects result into agent context
- Blocker list formatter: transform retrieved LESSON_LEARNED nodes into concrete "watch out for X" list
- `after_run` hook → writes session log to temp file → calls `engram extract <project> <tempfile>`
- Cold-start onboarding: `engram extract <project> <commit_log>` + density gate
- `engram ingest-reference` command (basic) — file and URL ingestion for tier 2 nodes

**Language bridge:** agent-orchestrator is TypeScript, Engram is Python. `before_run` and `after_run` hooks call `engram` CLI as subprocess, or call the Engram REST API (`/v1/query`, `/v1/extract`). Subprocess works today with zero new infrastructure.

**Validation experiment:** Run 50 tasks with routing gate active vs. 50 tasks without. Measure: tasks requiring human intervention, tasks hitting known blockers, tasks that produce LESSON_LEARNED nodes (novel failures vs. known ones).

---

### Phase 1B: Parallel Review Integration
*Prerequisite: Phase 1A stable and generating extracted nodes.*

**Deliverables:**
- Five parallel validator agents (fact, contradiction, scope, temporal, supersession)
- Conflict log table and `engram conflicts` CLI command
- Node confidence adjustment based on validator consensus
- PROCEDURE node promotion for high-confidence, repeated-pattern skills

---

### Phase 2: Reference Knowledge Tier
*Prerequisite: Lance+Tantivy migration complete (or Phase 0 decision deferred this to now).*

**Deliverables:**
- `reference` node type with required metadata fields (source_url, source_date, ingested_at, quality_score, ttl)
- `engram ingest-reference` with domain, source, and limit controls
- Stack Overflow ingestor (votes threshold + accepted answer gate)
- Documentation ingestor (URL or local path)
- Two-tier retrieval weighting in query pipeline
- TTL expiry background job (prune stale reference nodes)

---

### Phase 3: Graphify Integration
*Prerequisite: Phase 1A validated. Validate 71.5× token reduction claim before this phase begins.*

**Deliverables:**
- Extend Engram retrieval pipeline to query Graphify output as second source
- Combined context assembly: Engram graph + Graphify code graph → single context block
- Lazy cache invalidation: regenerate only subgraph for changed files
- Tiered activation: Graphify layer only for medium/high complexity tasks (not default)

---

## Success Metrics

### Phase 1A
- Routing gate fires in <100ms (does not add meaningful pre-dispatch latency)
- `after_run` extraction completes in <10s for typical session logs (<50K chars)
- 50-task validation experiment: routing gate reduces human intervention rate vs. baseline
- Cold-start onboarding: graph density gate activates within 10 tasks on a new project

### Phase 1B
- Parallel review adds <5s to post-run extraction (five validators, parallel)
- Contradiction detection precision ≥ 80% (few false positives)
- PROCEDURE node accumulation: measurable skill reuse within 20 tasks

### Phase 2
- Reference ingestion: 50K nodes ingested at <1 hour (local machine)
- Two-tier retrieval: tier 1 nodes surface above tier 2 in mixed-relevance queries
- Reference node TTL: stale nodes expire on schedule without manual intervention

---

## Competitive Context

| Tool | Routing gate | Auto skill extraction | Typed memory | Supersession |
|---|---|---|---|---|
| **Engram (this plan)** | ✅ | ✅ | ✅ | ✅ |
| CompoundEngineering | ✗ | ✗ (manual compound) | ✗ (markdown) | ✗ |
| GSD | ✗ (manual checkpoints) | ✗ | ✗ | ✗ |
| arscontexta | ✗ | ✗ | ✗ (untyped) | ✗ |
| agent-orchestrator (base) | ✗ | ✗ | ✗ | ✗ |

The routing gate is the gap. Nobody else has: *before the agent runs, query what we know about this class of task and decide whether to proceed.*

---

## Open Questions

- [ ] Is reference knowledge ingestion in scope for v1? (Phase 0 decision — answers whether Lance+Tantivy is a prerequisite)
- [ ] Validate Graphify 71.5× token reduction claim against actual codebase before Phase 3 planning
- [ ] What is the right minimum graph density for cold-start gate activation? (suggested: 10 LESSON_LEARNED nodes)
- [ ] TypeScript/Python bridge: subprocess vs REST API for before/after hooks? (subprocess works now, REST API is cleaner long-term)
- [ ] What is the right parallel review validator count? (5 proposed above — can be tuned)
