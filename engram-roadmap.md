# Unbidden Engram — Product Roadmap

**Company:** Unbidden (`unbidden.ai`)
**Product:** Unbidden Engram
**CLI:** `engram`
**Document created:** March 22, 2026

---

## What Engram Is

Engram is an AI context middleware layer that extracts structured facts from conversation transcripts, stores them in a SQLite DAG, and surfaces relevant context proactively — before agents have to ask for it. It operates across tools, sessions, and agents, maintaining per-agent databases with self-generated guidelines and a cross-agent shared knowledge layer.

**Core metaphor:** An engram is the physical memory trace left in neural tissue after an experience. Engram does the same for AI agents — encoding the residue of conversation into retrievable, structured memory.

---

## Architecture Overview

- **Storage:** SQLite (per-agent databases + shared knowledge graph)
- **Structure:** DAG (Directed Acyclic Graph) of extracted facts and relationships
- **Integration:** MCP server or lifecycle hooks
- **Extraction:** Reflection passes that distill raw conversation into structured facts
- **Guidelines:** Per-agent self-generated guidelines updated via reflection

---

## Release Phases

---

### v1 — Foundation
*Current state*

**Goal:** Core extraction, storage, and integration working reliably end to end.

- [x] Fact extraction from conversation transcripts
- [x] SQLite DAG storage
- [x] Per-agent databases
- [x] Self-generated agent guidelines via reflection passes
- [x] Cross-agent shared knowledge layer
- [x] MCP server integration
- [x] Lifecycle hooks integration

---

### v2 — Retrieval
*Next priority*

**Goal:** Make the context that reaches agents more accurate, relevant, and timely.

- [ ] Semantic search via sqlite-vec (vector search inside SQLite — zero new dependencies)
- [ ] Relevance ranking — not all context is equally useful
- [ ] Temporal weighting — recent facts weighted higher by default
- [ ] Contradiction detection — flag when new facts conflict with stored ones
- [ ] Confidence scoring — attach certainty level to each extracted fact
- [ ] Fact decay — stale context gracefully deprioritized over time
- [ ] Basic observability — retrieval logs, hit rate, storage growth metrics

**Licensing note:** sqlite-vec is MIT licensed. No new dependency risks introduced.

---

### v3 — Extraction Quality
*Parallel track with v2*

**Goal:** Better facts going in means better context coming out.

- [ ] Improved extraction prompts and reflection pass tuning
- [ ] Entity resolution — "Justin" and "the user" resolve to the same entity
- [ ] Relationship extraction — not just facts but typed relationships between entities
- [ ] Intent inference — what was the user trying to accomplish in this conversation
- [ ] Sentiment and tone tracking — adapt communication style per agent
- [ ] Multi-turn threading — understand conversation arc, not just isolated messages
- [ ] Fact deduplication — recognize when new facts restate existing ones
  - **Semantic dedup pass (priority — graph is at 24K+ nodes):** Extend `engram reconcile` (or add `engram deduplicate`) to scan for paraphrase duplicates using embedding cosine similarity. SHA-256 hashing catches exact duplicates at insert time, but paraphrased restatements accumulate undetected over many sessions. At 24K+ nodes, even a 5% duplicate rate is ~1,200 redundant nodes competing for top_k retrieval slots — causing retrieval dilution, not broken retrieval.
  - **Mechanism:** Pull pairs of nodes with cosine similarity above a threshold (e.g. 0.93+). Auto-merge the lower-confidence node into the higher-confidence one (or mark it superseded). Run threshold-gated in the background, same pattern as reconcile.
  - **Note:** The 30-node context injection at extraction time is a partial mitigation but has coverage gaps — old nodes on tangential topics may not surface in a session's top 30, so the LLM re-extracts the same fact without knowing it already exists.

---

### v4 — Integration Surface
*Expands Engram's reach into the broader stack*

**Goal:** More ways to plug Engram into any tool or workflow.

- [ ] REST API — expose Engram as a standalone service
- [ ] WebSocket support — real-time context streaming to agents
- [ ] Broader MCP server coverage — more tools, more agents
- [ ] OpenAI-compatible API wrapper — drop-in for any tool expecting OpenAI format
- [ ] Claude Code native integration
- [ ] LM Studio native integration
- [ ] VS Code / Cursor plugin
- [ ] CLI enhancements — `engram inspect`, `engram graph`, `engram export`

---

### v5 — Agent Intelligence
*Deeper context routing and agent awareness*

**Goal:** Engram understands agents as first-class entities, not just storage buckets.

- [ ] Agent relationship mapping — which agents communicate with which
- [ ] Context routing rules — agent A receives fact types X and Y, agent B receives Z
- [ ] Broadcast vs. targeted context delivery
- [ ] Agent specialization profiles — Engram learns what each agent cares about
- [ ] Cross-session continuity — seamless pickup across sessions per agent
- [ ] Multi-user support — context scoped to users, not just agents
- [ ] Feedback loops — agents signal which context was useful, Engram adapts

---

### v6 — Observability
*Transparency into what Engram is doing and why*

**Goal:** Full visibility into context flow, retrieval decisions, and system health.

- [ ] Context audit trail — why was this fact surfaced for this request
- [ ] Retrieval explanations — what query returned what results and why
- [ ] DAG visualization — interactive knowledge graph explorer
- [ ] Fact provenance — which conversation produced which fact
- [ ] Performance dashboard — latency, hit rate, storage growth, agent activity
- [ ] Anomaly detection — unusual patterns in context worth flagging

---

### v7 — Privacy and Security
*Required before any enterprise or multi-user deployment*

**Goal:** Engram is safe to run in regulated or sensitive environments.

- [ ] Fact-level encryption at rest
- [ ] PII detection and automatic redaction
- [ ] Configurable retention policies — facts expire after N days
- [ ] Audit logging for compliance
- [ ] Role-based access control for context
- [ ] Air-gapped operation — fully local, zero external calls, zero telemetry
- [ ] License: review all dependencies against GPL/AGPL exposure before this phase

---

### v8 — Platform and SaaS
*The commercial layer*

**Goal:** Engram scales beyond a single developer to teams and organizations.

- [ ] Multi-tenant architecture
- [ ] Cloud sync — local Engram instance syncs to encrypted cloud backup
- [ ] Team shared knowledge — organizational context layer above agent layer
- [ ] Admin dashboard — manage agents, users, context policies
- [ ] Usage analytics — what context is being used, by whom, how often
- [ ] Billing and licensing infrastructure
- [ ] Enterprise SSO and access management

---

## Core Design Principles

These apply to every phase and every decision:

1. **Retrieval quality above all** — every enhancement should make context more accurate, relevant, and timely
2. **Embedded first** — prefer embedded dependencies (SQLite, sqlite-vec) over separate processes
3. **License hygiene** — MIT, Apache 2.0, BSD, and Public Domain only; avoid GPL and AGPL entirely
4. **Clean storage abstraction** — storage implementation is swappable; the schema and extraction logic are the IP
5. **Zero surprise dependencies** — every new dependency is a conscious decision, documented and licensed
6. **Local by default** — Engram runs fully offline; cloud features are opt-in, never required
7. **The proactive principle** — context should arrive unbidden; agents should never have to ask twice for the same thing

---

## Dependency Policy

| License | Status | Notes |
|---|---|---|
| Public Domain | ✅ Approved | SQLite |
| MIT | ✅ Approved | sqlite-vec, DuckDB |
| Apache 2.0 | ✅ Approved | Attribution required |
| BSD 2/3-clause | ✅ Approved | Attribution required |
| LGPL | ⚠️ Case by case | Dynamic linking only |
| GPL | ❌ Prohibited | Copyleft viral risk |
| AGPL | ❌ Prohibited | SaaS copyleft risk |
| Commons Clause | ❌ Prohibited | Prohibits commercial use |

**Tooling:** Run `pip-licenses` (Python) or `license-checker` (Node) on every dependency update. Maintain `THIRD_PARTY_LICENSES.md` from day one.

---

## Distribution Strategy

Distribution becomes relevant starting v3 when the integration surface expands. Each channel maps to a phase:

**v1-v2 — Foundation channels**
- **GitHub** — open core presence from day one; builds credibility, inbound developer interest, and a paper trail of serious engineering
- **PyPI / npm** — table stakes for developer adoption; `pip install engram` should work from v1

**v3-v4 — Ecosystem channels**
- **MCP marketplace** — as the MCP ecosystem matures, an Engram MCP server is a natural listing; high-intent audience of agent builders
- **LangChain / LlamaIndex integrations** — large developer audiences actively looking for memory and context solutions; an official integration drives inbound
- **Hugging Face** — growing as an agent tooling hub beyond just models; worth a presence as the model fine-tuning work matures
- **Claude Code ecosystem** — if Anthropic formalizes a plugin or extension marketplace, Engram is a natural fit given the deep Claude Code integration

**v5+ — Enterprise channels**
- **Direct enterprise sales** — context management at scale is a real enterprise problem; outbound to AI engineering teams
- **Consulting / implementation partners** — Unbidden as a company could build an implementation partner network around Engram deployments
- **Cloud marketplace listings** — AWS Marketplace, Azure Marketplace for enterprise procurement

**What Apify is not:**
Apify is a web scraping and data pipeline platform. Engram is an embedded agent memory layer. The audiences and use cases are orthogonal — Apify is not a distribution channel for Engram.

---

## Custom LLM Model Strategy

Engram currently uses general-purpose LLMs (Qwen3-32B-4bit, Claude API) for extraction, reflection, and guideline generation. A purpose-built small model is a realistic and strategically valuable future direction.

### Why it makes sense for Engram

Engram's LLM tasks are narrow, repeatable, and well-defined:
- Extract structured facts from raw conversation
- Identify typed relationships between entities
- Generate agent guidelines via reflection passes
- Detect contradictions between new and stored facts
- Score relevance and confidence

These are exactly the tasks small fine-tuned models excel at. A 1-3B model tuned specifically for these tasks can match or exceed a 32B general-purpose model on them — at a fraction of the compute cost and with zero API dependency.

### The data flywheel

Every Engram installation generates training data: real conversations → real extracted facts → agent feedback on what was useful. Over time this becomes a proprietary dataset that no general-purpose model provider can replicate. That flywheel is a genuine, defensible competitive moat.

### Phased model development plan

**Phase 1 — General-purpose models (v1-v2, now)**
- Qwen3-32B-4bit or Claude API for extraction
- No training overhead, fast to build, good enough to validate
- Collect and label outputs as future training data from day one

**Phase 2 — Fine-tune an existing small model (v4, parallel track)**

Target base models:
- **Qwen2.5-1.5B or 3B** — small, fast, strong instruction following
- **Phi-4-mini** — Microsoft, strong reasoning per parameter
- **Llama-3.2-1B or 3B** — Meta, widely supported, good tooling
- **Gemma-3-1B** — Google, surprisingly capable at small scale

Fine-tuning approach:
- **LoRA / QLoRA** — parameter-efficient, runs on M1 64GB without issue
- **MLX-LM** — Apple Silicon native, best performance on your hardware
- **Unsloth** — fast fine-tuning with memory optimization
- **Axolotl** — flexible training framework, good for structured output tasks
- Training data: conversation → structured fact extraction pairs, ~2,000-5,000 examples to start

#### Training data — positive examples (SFT)

All nodes already in the graph are positive examples. `engram feedback <project> --export training.jsonl` exports them as `(transcript chunk → extracted fact)` pairs. SFT requires only positives; the existing pipeline already collects these.

#### Training data — negative examples (DPO, future)

Three sources, ranked by signal quality:

**1. `--capture-rejected` flag** *(not yet implemented)*
Add a `rejected_extractions` table to `store.py`, keyed on `(transcript_hash, model_id)`. When running `engram extract` with a weaker model (e.g. Qwen 3.5 9B), pass `--capture-rejected` to save its raw JSON output without merging it into the graph. Pair with Gemini 2.5 Flash output on the same transcript:
```
chosen   = Gemini nodes JSON for transcript X  (good extraction)
rejected = Qwen raw JSON for transcript X       (weaker extraction)
```
This produces genuine DPO rows — same prompt, better vs. worse completion — with no labeling required.

Implementation needed:
- `store.py`: `rejected_extractions` table + `log_rejected(transcript_hash, model_id, raw_json)` method
- `extractor.py`: save raw LLM response before parse/merge when flag is set
- `cli.py`: `--capture-rejected` flag on `engram extract`
- `feedback.py`: `export_dpo_jsonl()` that joins `nodes` (chosen) with `rejected_extractions` (rejected) on `transcript_hash`

**2. Parse/schema failures** *(already collected)*
Any row in `extraction_failures` where `error_type IN ('json_parse', 'schema')` is an unambiguous rejected response — the model failed to produce valid output for a prompt that Gemini handles correctly. No labeling needed; use directly as DPO negatives.

**3. Thumbs-down from auto-label** *(infrastructure exists, unused)*
Run `engram feedback <project> --auto-label`. The LLM-as-judge down-rates vague, hallucinated, or over-broad nodes. These export as `label: -1` in the JSONL. Lower signal than option 1 but requires no additional extraction runs.

**Phase 2 output:** A 1-3B model running at ~100 tokens/second locally, zero cost per call, tuned specifically for Engram's extraction tasks. Bundled with Engram so users need no external API for core functionality.

**Phase 3 — Continuous improvement (v5+)**
- Feedback loops from real Engram usage feed back into training data
- Model versioning tied to Engram releases
- Optional: publish model to Hugging Face under Unbidden brand
- Full pretraining from scratch is likely overkill indefinitely — fine-tuning gives 90% of the benefit at 1% of the cost

### Licensing note
Base models vary in license:
- Llama 3.x — Meta Llama license (commercial use allowed above certain user thresholds, check current terms)
- Qwen2.5 — Apache 2.0 ✅
- Phi-4 — MIT ✅
- Gemma 3 — Gemma Terms of Use (commercial use allowed, attribution required)

Prefer Apache 2.0 or MIT base models for cleanest commercial licensing. Qwen2.5 is the safest starting point.

---

## Open Questions

- [ ] CLI command confirmed as `engram` — verify no pip/npm package name conflict
- [ ] Own licensing strategy for Engram itself — MIT/Apache open core vs. source available vs. proprietary
- [ ] v2 vector search: sqlite-vec vs. sqlite-vss — evaluate stability and feature set
- [ ] MCP vs. lifecycle hooks as primary integration path — or support both equally
- [ ] Begin labeling extraction outputs from v1 as future fine-tuning training data — establish format early
- [ ] Evaluate Qwen2.5-3B vs. Phi-4-mini as fine-tuning base model — run benchmark on extraction tasks
- [ ] Confirm Llama 3.x commercial license thresholds before using as base model

---

## Name and Brand Reference

| | |
|---|---|
| Company | Unbidden |
| Domain | unbidden.ai (purchased) |
| Product | Unbidden Engram |
| CLI | `engram` |
| Previous working title | Context Broker |
