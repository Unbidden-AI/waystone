# Context Broker: Cleanup, Monetization & Scaling Plan

## Context

All four ROADMAP phases are implemented (MCP server, REST API, onboarding, Docker). 432 tests pass, 95% recall with Gemini 2.5 Flash. The codebase is feature-complete for a local/self-hosted tool. What's missing is the infrastructure to charge money and handle growth. This plan covers three things: immediate repo cleanup, a monetization path optimized for a solo developer, and a scaling roadmap ordered by when each bottleneck actually hits.

---

## Part 0: Immediate Cleanup (do first)

### 0a. Update `.gitignore`

Add:
```
benchmarks/results/
.DS_Store
.context-broker
.claude/
```

### 0b. Commit `SALES_PITCHES.md`
Stage only `SALES_PITCHES.md` and the updated `.gitignore`. Do NOT stage `.claude/`, `.context-broker`, or benchmark results.

---

## Part 1: Monetization

### 1a. Fastest path to first revenue (Week 1-2)

**Landing page + LemonSqueezy checkout.** No custom billing code yet.

- Create LemonSqueezy products: Pro ($20/mo), Team ($80/mo)
- Landing page with pricing table -> LemonSqueezy checkout links
- On payment webhook -> manually provision an API key and email it
- This is a "Wizard of Oz" approach -- automate the payment, manual-provision the key

**Why LemonSqueezy over Stripe:** Merchant-of-record handles tax/VAT/compliance. Solo developer doesn't deal with Stripe Tax or international invoicing.

### 1b. Per-user API keys + tier enforcement (Week 2-3)

**Files to modify:**
- `context_broker/api_server.py` -- auth overhaul
- `context_broker/store.py` -- add admin DB schema
- NEW: `context_broker/billing.py` -- key validation, limit checks

**Auth changes in `api_server.py`:**
- Replace single `CB_API_KEY` env-var check with lookup against `api_keys` table
- Admin DB at `/data/admin.db` (separate from per-project graph DBs)
- `api_keys` table: `key_hash TEXT PRIMARY KEY, tier TEXT, org_id TEXT, max_projects INT, max_nodes INT, rate_limit INT, created_at TEXT, last_used TEXT, is_revoked BOOL`
- Hash incoming Bearer token with SHA-256, look up in table
- Set `request.state.tier` and `request.state.key_id` for downstream checks
- Keep `CB_API_KEY` as a fallback for self-hosted/local-dev mode (no admin DB)

**Tier enforcement in `billing.py`:**

| Limit | Check location | How |
|-------|---------------|-----|
| Projects | `init_project()` | Count project dirs owned by key_id |
| Nodes | `extract_project()` | `store.get_stats()["node_count"]` vs tier max |
| Rate | FastAPI middleware | `slowapi` token-bucket per key_hash. Free: 10/min, Pro: 100/min, Team: 500/min |

**Free tier:** No payment. Rate-limited. 1 project, 500 nodes. Can use an anonymous key or IP-based limiting.

### 1c. Usage logging (Week 4)

NEW: `usage_log` table in admin DB: `key_id, project, action, input_chars, timestamp`

Log every extract/query call. No billing action yet -- just data collection to validate pricing.

### 1d. MCP registry submission (Week 3)

Submit `context-broker` to:
- Anthropic MCP directory
- Cursor marketplace
- Windsurf marketplace

The MCP server already works (`engram mcp-serve`). This is just writing marketplace listings using existing `GETTING_STARTED.md` and `SALES_PITCHES.md` as source material.

### What NOT to build for monetization yet

| Feature | Why defer |
|---------|-----------|
| OAuth / web dashboard | API keys + email fine for first 50 customers |
| Usage-based overage billing | Flat tiers simpler. Gemini costs ~$0.01/extraction -- margins are huge at $20/mo |
| SSO / SAML / audit log | Enterprise features. Build when an enterprise asks and will pay |
| Programmatic key management API | Manual provisioning fine until 50+ keys |

---

## Part 2: Scaling (ordered by when it hurts)

### Phase S1: Quick wins for 10k nodes (Week 1-2, ship alongside monetization)

**S1a. Batch commits in `merge_extraction()`**

Currently `add_node()` calls `self.conn.commit()` on every insert and `merge_extraction()` also commits per-supersedes update. For 258 nodes = 258+ fsyncs.

Fix: Add `_commit` parameter to `add_node()` and `add_edge()`:
```python
def add_node(self, node: dict, *, _commit: bool = True) -> str:
    ...
    self.conn.commit() if _commit else None
    ...

def merge_extraction(self, nodes, edges):
    for node in nodes:
        canonical_id = self.add_node(node, _commit=False)
        ...
    for edge in edges:
        self.add_edge(..., _commit=False)
    self.conn.commit()  # single fsync
```

10-50x faster writes. Backward-compatible (existing callers still auto-commit).

**S1b. `busy_timeout` pragma**

Add after WAL mode pragma in `store.py`:
```python
self.conn.execute("PRAGMA busy_timeout=5000")
```

Prevents `SQLITE_BUSY` errors when two requests hit the same project DB concurrently.

**S1c. FTS5 for tag/fact search**

Add FTS5 virtual table in `init_db()`:
```sql
CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
    fact, tags, content=nodes, content_rowid=rowid
);
-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS nodes_ai AFTER INSERT ON nodes BEGIN
    INSERT INTO nodes_fts(rowid, fact, tags) VALUES (new.rowid, new.fact, new.tags);
END;
-- Similar for UPDATE and DELETE
```

Replace `get_nodes_by_tags()` LIKE queries with FTS5 MATCH. Changes tag search from O(N) full scan to O(log N).

**S1d. Fix `prune_superseded()` N+1**

Currently calls `store.get_edges_to(node_id)` per node. Replace with batch query using existing `store.get_edges_for_nodes()`, then filter for `relation == "supersedes"` in Python.

### Phase S2: Medium-term for 100k nodes (Month 2-3)

**S2a. Denormalized `node_tags` table**
- `node_tags(node_id TEXT, tag TEXT)` with index on `tag`
- Exact tag match via indexed lookup, FTS5 for fuzzy/keyword
- Populated from JSON tags column on insert

**S2b. Streaming exports**
- Replace `get_all_nodes()` with batched generator (`LIMIT/OFFSET`)
- Update `export_project` endpoint and CLI to stream
- Prevents OOM at 100k+ nodes

**S2c. Automatic pruning**
- After extraction, if node count exceeds tier limit by 10%, auto-prune lowest-confidence superseded nodes
- Add `max_age_days` config: nodes older than N days with confidence < 0.3 auto-pruned

### Phase S3: Multi-tenant SaaS (Month 4+, only if needed)

**S3a. PostgreSQL migration** -- only when concurrent writes to same project are a real problem
- Replace `GraphStore` internals; keep same interface
- Use `asyncpg` for API server path
- Keep SQLite for local CLI mode (`is_remote()` already handles the split)

**S3b. Multi-tenancy columns**
- `org_id` on projects registry
- Row-level filtering in all queries

**S3c. Read replicas** -- route query/export to replica, extract to primary
