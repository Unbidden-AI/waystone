# Waystone: Lance + Tantivy Graph Store — Development Plan

> Status: Draft v1 — May 2026  
> Research baseline: LanceDB v0.30.2 (March 2026), Lance SDK v1.0.0, tantivy-py v0.26.0 (April 2026)

---

## Executive Summary

Waystone's current SQLite `GraphStore` hits a hard ceiling at scale: B-tree adjacency means one SQL query per BFS hop, no ANN vector search, and no multi-writer support. This plan defines the migration to a three-primitive composition — **Apache Lance** (columnar MVCC storage + vector ANN), **Tantivy** (BM25 full-text search), and a **CSR in-memory graph index** (O(1) BFS) — that satisfies all four core requirements under permissive licenses.

**One critical architecture update from May 2026 research:** LanceDB has shipped native FTS (BM25 + hybrid RRF) and removed its Tantivy dependency. This creates an architectural choice point: use LanceDB's bundled FTS (simpler, single store, eliminates cross-store atomicity) or keep standalone Tantivy (more control, tunable, separated concerns). The plan evaluates both paths in Phase 1 and defers the final decision to empirical recall comparison.

**Solo developer feasibility:** Yes. Phase 1 is ~3–4 months with AI tooling. Total Phases 1–2 is 6–10 months. Phase 3 is ongoing.

---

## Background: Why Migrate

| Requirement | SQLite (`GraphStore`) | Lance + Tantivy + CSR |
|-------------|----------------------|----------------------|
| Multi-writer (concurrent agents) | No — global write lock | Yes — MVCC fragment model |
| Vector ANN search | No — no vector index | Yes — IVF_HNSW_FLAT native |
| Incremental BM25 FTS | No — LIKE queries only | Yes — Tantivy or Lance native FTS |
| Graph BFS at scale | Slow — SQL per hop | O(1) — CSR dict lookups |

The migration trigger condition is met: reference knowledge ingestion (YC library, 455+ documents, 30K+ nodes) puts Waystone in a regime where SQLite's limitations become a retrieval ceiling, not just a future concern.

---

## May 2026 Research Findings

### LanceDB (v0.30.2)

- **Lance SDK v1.0.0** — API stability milestone; safe to build on.
- **IVF_HNSW_FLAT** — ANN via IVF partitions with HNSW sub-indices per partition. Tuning: `nprobes` (25–50 for 0.9–0.95 recall), `reranking_factor` (default 30). Benchmark: ~178 QPS at 1M vectors with 0.95 recall and 5ms p99 latency.
- **Native FTS added; Tantivy dependency removed.** LanceDB now ships BM25 keyword scoring and hybrid search (FTS + vector with built-in RRF reranking), stress-tested on 41M Wikipedia documents. This is a significant change from the original architecture — cross-store atomicity may be solved by using LanceDB FTS rather than maintaining a separate Tantivy index.
- **Async Python API** — `lancedb.connect_async()`, `AsyncTable.vector_search()`, `AsyncTable.query()`. Full async support; synchronous API wraps async internally.
- **`lance-graph` Rust crate** — separate crate enabling graph queries on Lance data. Needs evaluation for Waystone's BFS pattern before committing to custom CSR.
- **MVCC behavior** — fragment-based isolation; readers see consistent snapshot from transaction start; writers append new fragments independently. Formal snapshot isolation semantics not published; edge conflict behavior under concurrent writes not specified. **This is a design-time risk.**
- **Storage backends** — local, S3, GCS, Azure, plus multi-bucket layout for parallel reads/writes across S3 regions (March 2026). Remote backends require credentials via environment.
- **macOS x86 dropped** — arm64 only on macOS.
- **Performance ceiling** — ~25ms vector search (marketing spec); ~178 QPS at 1M/0.95 recall. At 10M vectors with 1024 dims: ~1K QPS at p99 <100ms.

### tantivy-py (v0.26.0, April 2026)

- **Single writer constraint** — exactly one `IndexWriter` per index. Multiple writers require external serialization (mutex or queue). **Critical for Waystone's multi-agent write path.**
- **No async support** — synchronous API only. Async wrappers must be built at the application layer.
- **NRT not native** — no soft commit. `writer.commit()` required for new documents to be visible. Readers must call `index.reload()` after commit. Round-trip write-to-visible latency: 1 commit cycle.
- **Document "updates"** — delete-by-term + re-add. No in-place update. A superseding node requires delete old + add new.
- **BM25 parameters** — k1=1.2, b=0.75 (Lucene defaults, hardcoded). No Python API to tune. Rust-level `Bm25StatisticsProvider` required for custom params.
- **No hybrid vector scoring** — text-only. RRF combination with LanceDB ANN must be application-layer.
- **Performance** — ~45K docs/sec indexing throughput; sub-10ms query latency at scale.
- **Breaking changes (2024–2026)** — `TantivyDocument` → `CompactDoc` (v0.24); Python 3.9 dropped (v0.26). Backward-compatible index format (v0.24+ reads v0.21+).

### Architectural Choice: Tantivy vs. LanceDB Native FTS

| Criterion | Tantivy (standalone) | LanceDB Native FTS |
|-----------|----------------------|-------------------|
| Cross-store atomicity | Required — WAL repair pass needed | Eliminated — single store |
| BM25 tuning | Possible via Rust (not from Python) | Not publicly exposed |
| Recall baseline | Proven — BM25 industry standard | Tested on 41M docs (Wikipedia) |
| Single writer constraint | Yes — serialization required | LanceDB handles internally |
| Integration complexity | High — two write paths, index sync | Low — one write path |
| Async support | No — must wrap | Yes — native |
| Operational risk | NRT requires commit+reload cycle | Same MVCC as vector store |

**Recommendation for Phase 1:** Implement both paths behind a `FTSBackend` abstraction (`LanceFTS` and `TantivyFTS`). Run LOCOMO benchmark against both to compare recall. Default to LanceDB native FTS unless Tantivy produces meaningfully better recall (>2% absolute). The cross-store atomicity savings alone justify the default.

---

## Architecture

### Component Map

```
┌──────────────────────────────────────────────────────────────┐
│                     Waystone API Layer                       │
│   extract() / query() / reflect() / hook()                  │
└────────────────────────┬─────────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────────┐
│                   LanceStore (new)                           │
│                                                              │
│  ┌─────────────────┐   ┌─────────────────┐                  │
│  │  Lance Tables   │   │  Tantivy Index  │  (optional path) │
│  │  nodes, edges,  │   │  or Lance FTS   │                  │
│  │  embeddings     │   │  (FTSBackend)   │                  │
│  └────────┬────────┘   └────────┬────────┘                  │
│           │                     │                            │
│  ┌────────▼────────────────────▼────────┐                   │
│  │        Write-Ahead Log (WAL)         │  (Tantivy path    │
│  │  repair pass on startup              │   only)           │
│  └──────────────────────────────────────┘                   │
│                                                              │
│  ┌──────────────────────────────────────┐                   │
│  │   CSR In-Memory Graph Index          │                   │
│  │   node_id → [neighbor_ids] dict      │                   │
│  │   loaded from Lance edges at startup │                   │
│  └──────────────────────────────────────┘                   │
└──────────────────────────────────────────────────────────────┘
```

### Data Model (Lance tables)

```
nodes
  id              string (UUID, PK)
  fact            string
  type            string  -- decision/transition/implementation/...
  confidence      float32
  tags            string  -- JSON array
  supersedes      string  -- JSON array of node IDs
  source_file     string
  occurred_at     timestamp
  valid_to        timestamp
  is_active       bool
  embedding_model_version  string  -- REQUIRED from day one
  tenant_id       string           -- REQUIRED from day one
  fact_hash       string  -- dedup key (normalized text SHA256)
  embedding       vector(1536)     -- float32, for ANN
  embedding_q     binary           -- quantized, for fast ANN candidate fetch

edges
  from_id         string
  to_id           string
  relation        string
  tenant_id       string

conflict_log
  id              string (UUID)
  node_a_id       string
  node_b_id       string
  conflict_type   string  -- duplicate/contradiction/confidence_tie
  created_at      timestamp
  resolved_at     timestamp
  resolution      string
  tenant_id       string
```

### BFS Pattern

```
query() call:
  1. Extract keywords from task → tag list
  2. Lance FTS query (BM25) on tags/fact → entry node candidates
  3. Lance ANN query (IVF_HNSW_FLAT) on task embedding → ANN candidates
  4. RRF merge of FTS + ANN candidate sets
  5. BFS over CSR index from merged candidates (hops=N, O(1) per hop)
  6. Strategy pipeline: superseded_pruning → confidence_threshold → recency_decay → top_k → token_budget
  7. Assemble markdown context
```

---

## Phase 1 — Foundation (3–4 months)

**Goal:** All four core requirements satisfied. Design-time decisions locked in. Feature parity with SQLite `GraphStore`.

### 1.1 Schema and Storage Layer

- [ ] Create `waystone/lance_store.py` — `LanceStore` class implementing the same interface as `GraphStore`
- [ ] Lance table schemas: `nodes`, `edges`, `conflict_log` — with `tenant_id`, `embedding_model_version`, `fact_hash` from day one
- [ ] `add_node()`: write to Lance, dedup on `fact_hash`, return existing ID on collision
- [ ] `add_edge()`: write to Lance, update CSR index in-place
- [ ] `merge_extraction()`: batch write with ID remapping; wrap Lance writes in unit-of-work with WAL entry (Tantivy path) or single Lance transaction (LanceDB FTS path)
- [ ] Connection pool: `lancedb.connect_async()` for async callers; synchronous wrapper for current CLI path

### 1.2 FTS Backend Abstraction

- [ ] Define `FTSBackend` protocol in `waystone/fts.py`
  - `add_document(node_id, fact, tags) → None`
  - `delete_document(node_id) → None`
  - `search(query, top_k) → list[tuple[str, float]]`  (node_id, score)
  - `commit() → None`
  - `reload() → None`
- [ ] `LanceFTSBackend` — delegates to LanceDB native FTS on the `nodes` table
- [ ] `TantivyFTSBackend` — wraps tantivy-py with single-writer mutex, commit+reload cycle
  - Schema: `node_id` (text, stored), `fact` (text, indexed), `tags` (text, indexed)
  - Use `IndexWriter` with thread lock; one writer per process
  - `commit()` + `reader.reload()` after each batch write

### 1.3 CSR Graph Index

- [ ] `waystone/csr.py` — `CSRIndex` class
  - `build(edges: list[tuple[str,str]]) → None` — load all edges from Lance on startup
  - `neighbors(node_id) → list[str]` — O(1) dict lookup
  - `add_edge(from_id, to_id) → None` — in-place append; no rebuild required
  - `remove_edge(from_id, to_id) → None` — for supersedes pruning
  - Thread-safe read path; write path protected by `threading.Lock`
- [ ] Evaluate `lance-graph` crate as alternative to custom CSR before committing. If lance-graph exposes BFS via Python bindings and shows comparable latency, prefer it over custom code.

### 1.4 ANN Vector Search

- [ ] Embedding generation at extraction time: `text-embedding-3-small` (1536-dim) stored in `nodes.embedding`
- [ ] Binary quantization: `nodes.embedding_q` (bitwise repr); used for fast ANN candidate fetch
- [ ] Build IVF_HNSW_FLAT index on `nodes.embedding` via Lance API: `nprobes=25`, `reranking_factor=30`
- [ ] `retrieve_ann(embedding, top_k=100) → list[str]` — returns node IDs for BFS seeding
- [ ] Re-rank top-100 ANN candidates with exact float32 cosine before BFS

### 1.5 Write-Ahead Log (Tantivy path only)

- [ ] `waystone/wal.py` — SQLite-backed WAL (separate from main store)
  - `record_lance_write(node_id, op)` — before Tantivy write
  - `record_tantivy_write(node_id, op)` — after Tantivy write
  - `find_gaps() → list[node_id]` — nodes in Lance but not in Tantivy
  - `repair() → int` — re-index gap nodes; called on `LanceStore.__init__`
- [ ] Startup sequence: open Lance → open Tantivy → `wal.repair()` → build CSR → ready

### 1.6 Conflict Resolution

- [ ] On `add_node()`: if `fact_hash` exists and confidence differs by < 0.1, silently keep existing
- [ ] If confidence gap > 0.1: write the higher-confidence node, log the conflict to `conflict_log`
- [ ] `waystone show <project> --conflicts` — surface unresolved conflicts
- [ ] Policy is `higher-confidence-wins`; review queue for manual inspection above threshold

### 1.7 Multi-Tenancy

- [ ] All queries filter on `tenant_id` at the Lance query layer (not application layer)
- [ ] `LanceStore.__init__(project: str)` sets `self.tenant_id = project`
- [ ] CSR index is per-`LanceStore` instance (no cross-project leakage)
- [ ] Tantivy index is per-project directory (one index per project, not shared)

### 1.8 GraphStore Parity and Toggle

- [ ] `LanceStore` passes all existing `tests/test_store.py` tests
- [ ] Config flag `store_backend: lance | sqlite` (default: `sqlite` until Phase 2 validation)
- [ ] `waystone init <project> --backend lance` creates Lance store
- [ ] Existing SQLite projects continue to work unchanged

### 1.9 Recall Validation

- [ ] Run LOCOMO benchmark against `LanceStore` with both FTS backends
- [ ] Target: recall parity with SQLite baseline (≥85.7%)
- [ ] If Tantivy recall > LanceDB FTS recall by ≥2%: keep Tantivy as default and implement WAL
- [ ] Otherwise: deprecate Tantivy path, use LanceDB FTS, remove WAL complexity

### Phase 1 Definition of Done

- [ ] `waystone extract <project> <file>` with `--backend lance` works end-to-end
- [ ] `waystone query <project> <task>` returns results with recall ≥ SQLite baseline
- [ ] All four core requirements (multi-writer, ANN, FTS, BFS) validated
- [ ] All design-time fields (`tenant_id`, `embedding_model_version`, `fact_hash`) in schema
- [ ] `from waystone.lance_store import LanceStore` imports cleanly
- [ ] LOCOMO benchmark comparison complete; FTS backend decision recorded

---

## Phase 2 — Production Hardening (2–3 months)

**Goal:** Multi-user, multi-agent, long-running deployments. Migrate production Waystone instances from SQLite.

### 2.1 Tiered Storage / Hot-Cold Archiving

- [ ] Add `last_accessed_at` and `access_count` fields to `nodes` Lance table
- [ ] Cold tier: Lance partition or separate table for nodes not accessed in `cold_ttl` days (configurable, default 90)
- [ ] Cold-tier nodes: still queryable, excluded from ANN index hot path, loaded on demand
- [ ] Background archiver task: runs daily, demotes idle nodes

### 2.2 Embedding Compression

- [ ] Binary quantization already in Phase 1 (`embedding_q`)
- [ ] Add product quantization (PQ) option via config flag (`quantization: binary | pq | none`)
- [ ] PQ subspace config: 96 subspaces × 256 centroids (standard for 1536-dim)
- [ ] Benchmark recall tradeoff at 1M nodes before enabling PQ as default

### 2.3 Query Result Cache

- [ ] Cache key: `sha256(embedding_bytes + graph_version + strategy_config_json)`
- [ ] Lance versioned fragment ID serves as `graph_version` (cheap to read)
- [ ] TTL: 5 minutes (configurable)
- [ ] Cache backend: in-process dict with LRU eviction (no external Redis dependency for solo deployments)
- [ ] Cache invalidation: any write to the relevant `tenant_id` increments version

### 2.4 Change Data Capture (CDC)

- [ ] `LanceStore.changes_since(version: int) → list[NodeDelta]` — diff Lance fragment versions
- [ ] `NodeDelta`: `{node_id, op: added|updated|deleted, fields_changed}`
- [ ] Used for: cache invalidation, incremental embedding refresh, audit logs

### 2.5 Time-Travel Queries

- [ ] `LanceStore.at_time(iso8601: str)` — Lance fragment checkout at timestamp
- [ ] Thin wrapper; Lance versioned fragments make this nearly free
- [ ] Exposed as `waystone query <project> <task> --at-time <iso8601>`

### 2.6 Background Maintenance Scheduler

- [ ] `waystone/maintenance.py` — priority-lane scheduler
  - **High priority** (user-initiated or hook-initiated): query, extract writes, CSR hot-reload
  - **Low priority** (background): Lance compaction, Tantivy segment merge, cold-tier archiving, re-embedding
- [ ] Lance compaction: run after every N fragment writes (configurable, default 100)
- [ ] CSR hot-reload: triggered by any edge write; no full rebuild; append to existing dict
- [ ] Tantivy merge: run after every M commits (configurable)

### 2.7 Property Filtering on ANN

- [ ] Predicate pushdown: `confidence > threshold AND is_active = true AND tenant_id = X`
- [ ] Lance `IVF_HNSW_FLAT` supports filtering; pass filter at query time
- [ ] Expose as `retriever.py` strategy: `filtered_ann(confidence_threshold, recency_filter)`

### 2.8 Production Migration

- [ ] `waystone migrate <project> --from sqlite --to lance` — reads SQLite, writes to LanceStore
- [ ] Validate node/edge count and recall on migrated store before cutover
- [ ] LOCOMO benchmark on migrated `YC` project (455+ documents, ~30K nodes)
- [ ] Deprecate `GraphStore` for new projects; keep for read-only migration source

### Phase 2 Definition of Done

- [ ] `YC` project migrated to Lance, LOCOMO recall ≥ SQLite baseline
- [ ] Cold-tier archiving running for projects with > 10K nodes
- [ ] Query cache showing cache hit rate > 60% under typical workload
- [ ] Background scheduler non-blocking under concurrent query+write load
- [ ] `waystone migrate` tested on production Waystone instances

---

## Phase 3 — Scale and Ecosystem (ongoing)

**Goal:** >5M edges, external queryability, ecosystem adoption.

### 3.1 Rust BFS Extension (trigger: >5M edges in production)

- [ ] PyO3 crate: `waystone_bfs` — Rust implementation of CSR BFS
- [ ] Python-visible: `from waystone_bfs import CSRIndex` — drop-in replacement for `waystone/csr.py`
- [ ] Expected speedup: 10–50× over Python dict BFS at >5M edges
- [ ] Hold until production crosses 5M-edge threshold; current scale (30K nodes) does not need it

### 3.2 Tantivy Index Sharding (trigger: >10M nodes in single project)

- [ ] Split Tantivy index into N shards by `node_id` hash
- [ ] Parallel query across shards, merge results before RRF
- [ ] Shard count configurable; default 1 until needed

### 3.3 Approximate Graph Analytics

- [ ] PageRank over CSR index — identify hub nodes (facts with many dependents)
- [ ] Orphan detection — isolated nodes with no edges (likely extraction artifacts)
- [ ] Community detection (label propagation) — topic cluster identification
- [ ] `waystone analytics <project>` — report graph health metrics
- [ ] Output feeds into v6 observability roadmap and cold-tier archiving priority ranking

### 3.4 Hybrid Query — Single Pass

- [ ] Research track: single-pass ANN + BFS + FTS with predicate pushdown
- [ ] Approximate solution first: IVF partition selection + BFS-bounded FTS filter
- [ ] Evaluate `lance-graph` crate for native graph+vector join capability
- [ ] Exact solution requires tight storage co-location of all three index types; deferred to this phase

### 3.5 Import / Export Interoperability

- [ ] Export: Parquet (Lance native), Arrow IPC, JSON-L (for streaming), NetworkX GraphML
- [ ] Import: JSON-L (for bulk migration), Parquet (for analytics pipelines)
- [ ] Lance gives Parquet nearly for free; graph topology (edges) is the additional work

### 3.6 Cypher Query Layer

- [ ] Required only if external API queryability is a product requirement (v4+ roadmap)
- [ ] `kuzu` as embedded Cypher engine over Lance tables, or manual Cypher parser
- [ ] Deferred until business case established

### 3.7 Embedding Model Upgrade Path

- [ ] `waystone reembed <project> --model <new_model_id>` — background re-embedding job
- [ ] Mixed-version store: old and new embeddings coexist during migration; ANN queries prefer same version
- [ ] Tracking field `embedding_model_version` already in schema from Phase 1
- [ ] Cutover: mark migration complete when 100% of active nodes have new-version embeddings

---

## Technology Stack Summary

| Component | Package | Version (May 2026) | License | Notes |
|-----------|---------|-------------------|---------|-------|
| Columnar storage + MVCC + ANN | `lancedb` | 0.30.2 | Apache 2.0 | Lance SDK v1.0.0 stable |
| FTS (primary candidate) | LanceDB native FTS | bundled in lancedb | Apache 2.0 | BM25 + RRF; no cross-store atomicity |
| FTS (alternative) | `tantivy-py` | 0.26.0 | MIT | Single-writer; requires WAL repair pass |
| Graph BFS | `waystone/csr.py` | custom | Proprietary | O(1) dict lookups; evaluate lance-graph first |
| Graph BFS (future) | `waystone_bfs` PyO3 | Phase 3 | Proprietary | Rust extension when >5M edges |
| Python version | `python3.13` | 3.13.x | PSF | 3.14 breaks sqlite-vec on macOS |

---

## Risk Register

| Risk | Severity | Likelihood | Mitigation |
|------|----------|-----------|------------|
| LanceDB MVCC snapshot isolation not formally specified — edge write conflicts undefined behavior | High | Medium | Test concurrent write scenarios early in Phase 1; file issue with LanceDB if semantics unclear |
| LanceDB native FTS recall lower than Tantivy | Medium | Low | Dual-backend A/B test on LOCOMO before committing; WAL complexity kept but not default |
| tantivy-py single-writer constraint causes write queue backlog under multi-agent load | Medium | High (if Tantivy path) | Use LanceDB FTS path by default; if Tantivy required, implement async write queue with bounded buffer |
| CSR in-memory index becomes stale on process restart (writes lost) | High | Low | CSR is always rebuilt from Lance on startup; Lance is source of truth |
| Embedding model upgrade breaks ANN recall during migration | High | Low | `embedding_model_version` field enables per-version ANN index; query routes to correct version |
| Lance compaction blocks reads under high write load | Medium | Low | Lance MVCC allows reads during compaction; readers see pre-compaction snapshot |
| Cold-tier archiving prunes nodes still needed for retrieval | Medium | Medium | Archiving predicated on `last_accessed_at`, not age alone; query path can re-activate cold nodes |
| macOS x86 LanceDB incompatible (arm64 only since 2026) | Low | N/A | Justin's dev machine is arm64; non-issue |
| tantivy-py `TantivyDocument` → `CompactDoc` rename (v0.24) breaks existing code | Low | Low | No existing tantivy-py code in codebase; start fresh on v0.26.0 API |

---

## Design Review Gaps (addressed May 2026)

The following gaps were identified in design review and must be resolved before Phase 1 begins.

### Critical — Must Fix Before Phase 1

**Gap 1: CSR edge visibility under concurrent BFS (Section 1.3)**  
The plan says `add_edge()` "requires no rebuild" but does not define whether an in-flight BFS traversal sees a newly appended edge. Python's GIL makes individual dict operations atomic, but a BFS that starts between two `add_edge()` calls may see a partially-updated graph.  
**Resolution:** CSR reads use a snapshot taken at BFS start. Implement CSR with a `threading.RLock`: `neighbors()` acquires a read lock; `add_edge()` acquires a write lock. BFS holds the read lock for its entire traversal. Edge writes are therefore invisible to in-flight BFS (snapshot semantics). Define hot-reload as: after `merge_extraction()` completes (all nodes+edges written), call `csr.flush_pending()` once, not per-edge. Buffer edge writes during extraction; apply in a single lock acquisition.

**Gap 2: WAL repair must validate content, not just presence (Section 1.5)**  
A process crash mid-`merge_extraction()` after a Lance commit but before WAL recording leaves the node in Lance but unrecorded. The startup repair would skip it. Also, if a node's fact was updated in Lance after the original Tantivy indexing, the Tantivy doc has stale content.  
**Resolution:** WAL repair pass computes `fact_hash` for every Lance node and compares to the Tantivy-indexed doc. If missing OR hash mismatch: delete old Tantivy doc (if any) + re-index from Lance. Startup cost at 30K nodes: ~5ms (30K hash comparisons). Document this bound.

**Gap 3: FTS benchmark must include YC library eval, not just LOCOMO (Section 1.9)**  
LOCOMO tests conversational memory recall where semantic overlap is dense. The YC library has sparse domain-specific terms where BM25 k1/b tuning has the most impact. Running only LOCOMO may show FTS parity even if Tantivy is meaningfully better on reference knowledge retrieval.  
**Resolution:** Create a second eval set: 20 ground-truth lookup questions against the YC library (e.g., "what advice does YC give on pricing," "how does YC recommend splitting equity"). Run both LOCOMO and YC eval before finalizing FTS backend. Decision threshold: Tantivy wins if it beats LanceDB FTS by ≥2% on EITHER benchmark.

**Gap 4: `merge_extraction()` atomicity model undefined (Section 1.1)**  
If extraction writes 50 nodes and fails on node 30, the caller does not know which nodes succeeded. For the Tantivy path, partial success means Lance and Tantivy are partially in sync. For the LanceDB FTS path, Lance's MVCC handles atomicity natively.  
**Resolution:** Define explicitly: LanceDB FTS path — `merge_extraction()` is atomic (Lance MVCC; all-or-nothing). Tantivy path — `merge_extraction()` is NOT atomic per-call; individual nodes can partially succeed. Callers must treat extraction as idempotent: `add_node()` deduplicates on `fact_hash`, so re-running extraction on failure is safe. Document this in `LanceStore` docstring. Add a `rollback()` stub that logs a warning (Tantivy path: partial success is not reversible; only Lance has version rollback).

### Warning — Should Clarify Before Phase 1

**Gap 5: Binary quantization algorithm unspecified (Section 1.4)**  
The plan mentions `embedding_q` binary field but doesn't specify how quantization is applied or whether Lance's Python API exposes it.  
**Resolution:** Defer binary quantization to Phase 2. Phase 1 stores float32 embeddings only and uses Lance's native IVF_HNSW_FLAT with float32. At Waystone's current scale (<100K nodes), float32 RAM footprint is ~600MB — acceptable on a laptop. Quantization becomes necessary at 1M+ nodes; benchmark at that threshold.

**Gap 6: Tantivy single-writer lock scope (Section 1.2)**  
Not clear if the write lock is held for the full `commit()` + `reload()` cycle (1–10ms) or just during `add_document()`.  
**Resolution:** Lock is held from `add_document()` through `commit()`. The `reload()` call is on the reader, not the writer, and does NOT require the write lock. Single-threaded extraction path (one extraction job at a time per project) makes this a non-issue at current scale.

**Gap 7: CSR hot-reload batching undefined (Section 2.6)**  
`merge_extraction()` may write 100+ edges; triggering CSR reload per-edge causes excessive lock contention.  
**Resolution:** CSR writes are buffered. `add_edge()` appends to a pending buffer list (no lock needed for append). `flush_pending()` acquires write lock once and applies all buffered edges. Called at end of `merge_extraction()`. Periodic background flush: every 5 seconds if buffer non-empty (catches single-edge writes from session hooks).

**Gap 8: Conflict resolution policy matrix incomplete (Section 1.6)**  
The plan handles confidence gap ≥0.1 but not <0.1 ties, nor field-level conflicts (same tags, different fact text).  
**Resolution:** Policy matrix:
- `fact_hash` match, confidence gap ≥0.1: keep higher-confidence node, log to `conflict_log` as `auto_resolved`
- `fact_hash` match, confidence gap <0.1: keep existing, log as `low_confidence_tie` (no action required)
- Different fact, high tag overlap (Jaccard ≥0.8): log as `semantic_duplicate`, do NOT auto-resolve; surface in `waystone show --conflicts`
- All other cases: insert both nodes (no conflict)

### Info — Non-Blocking Research Tasks

**Gap 9: `lance-graph` evaluation timing (Section 1.3)**  
Decision deadline: end of Phase 1 Week 2. Evaluation: run `lance-graph` Python bindings on a 5K-node test graph, measure BFS latency. If ≥2× slower than Python CSR dict, use custom Python CSR.

**Gap 10: WAL startup latency at scale**  
Measure WAL repair pass latency on 30K-node store before production migration. Target: <500ms. If exceeded, implement incremental WAL (track last-repaired Lance version, only scan new fragments).

---

## Open Questions

1. **`lance-graph` crate** — does it expose BFS-compatible Python bindings? Evaluate before building custom CSR; if yes, prefer it over custom code.
2. **LanceDB MVCC edge semantics** — what happens when two agents write conflicting edges (A→B and B→A with different relations) simultaneously? File issue or test empirically before Phase 1 complete.
3. **LanceDB FTS tuning** — are BM25 k1/b parameters exposed in the Python API? If not, Tantivy may still be required for precision-sensitive projects.
4. **Tantivy single-writer** — is it per-process or per-`Index` object? Clarify before design finalized. (Per research: per `IndexWriter` instance, not per process — but only one `IndexWriter` can be open per index directory.)
5. **Embedding model selection** — `text-embedding-3-small` (1536-dim, $0.02/1M tokens) vs `text-embedding-3-large` (3072-dim, $0.13/1M tokens). Which gives better node recall on Waystone's tag+fact retrieval pattern? Run ablation before locking 1536-dim into schema.

---

## Appendix: Current Waystone Scale (May 2026)

- YC project: ~30K nodes (455 articles extracted)
- Business project: ~1K nodes
- LOCOMO baseline: 85.7% recall (gpt-4o-mini judge, April 2026)
- SQLite `GraphStore` performance at 65K+ nodes: sub-second BFS+ANN (validated)
- Migration trigger met: reference knowledge ingestion at scale is in scope for v1
