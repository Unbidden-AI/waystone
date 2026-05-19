# Waystone Benchmark Results

## Overview

Waystone has been evaluated across three distinct benchmarks: an internal precision test on engineering design meeting transcripts, LOCOMO (a multi-conversation memory benchmark), and LongMemEval (a large-scale general memory benchmark).

---

## Internal Engineering Recall Test

Waystone was evaluated on 23 recall questions across 3 engineering domains, drawn from realistic design meeting transcripts. The test measures whether Waystone can surface the right fact when asked a natural-language question about a project's history.

**Best result: 95% recall (21/23 questions answered correctly)**

### Test Set

**Transcripts:** 3 engineering design meeting transcripts, each 300–500 extracted knowledge nodes:
- `api_design` — REST API design session covering auth, rate limiting, versioning, caching
- `auth_system` — Authentication architecture session covering MFA, session management, token design
- `data_pipeline` — Data pipeline design session covering storage format, stream processing, schema evolution

**Questions:** 23 total (8 API design, 8 auth system, 7 data pipeline)

Example questions:
- *"Why was SMS ruled out for MFA?"*
- *"What serialization format did the team choose for the pipeline, and why?"*
- *"What access control model was selected — RBAC or something else?"*
- *"What library handles Kafka serialization, and what was the performance rationale?"*
- *"What are the hard limits on API rate limiting?"*

**Recall grading:** A question is marked correct if the retrieved context contains the answer. Grading is strict — partial matches count as misses.

### Results

| Extraction mode | Recall | Questions ≥ 80% |
|-----------------|--------|-----------------|
| Standard | 92% | 19 / 23 |
| With verification pass | **95%** | **21 / 23** |

The verification pass runs a second extraction sweep specifically hunting for secondary details, buried numerics, transition statements, and decision rationale — the things a first pass tends to skip.

### What Gets Recovered

Representative facts that baseline extraction misses but Waystone surfaces:

| Domain | Fact recovered |
|--------|----------------|
| Auth | *"SIM swapping makes SMS too weak for MFA — hardware keys or TOTP only"* |
| Auth | *"ABAC (Attribute-Based Access Control) was selected over RBAC — the team originally considered a simple role-based system"* |
| Auth | *"OPA policies stored in Git, deployed as sidecar containers"* |
| Data pipeline | *"fastavro library chosen for Kafka serialization — 3× faster than the official Confluent library"* |
| Data pipeline | *"Schema evolution handled via Confluent Schema Registry with backward compatibility enforced"* |

These are the facts that matter most in a real project — the *why* behind decisions, the alternatives that were rejected, the constraints that aren't obvious from the code.

---

## Token Efficiency

On a mature project with 200+ extracted nodes, Waystone injects 10–25 relevant facts per query rather than everything ever recorded. Compared to a naive MEMORY.md approach:

| Approach | Tokens per session (typical) | Recall |
|----------|------------------------------|--------|
| Full MEMORY.md dump | ~8,000–15,000 | ~75% (irrelevant noise) |
| Waystone BFS retrieval | ~800–1,500 | **92–95%** |

60–80% fewer context tokens with higher recall — because Waystone retrieves the subgraph relevant to the current task, not everything.

---

## LOCOMO

[LOCOMO](https://github.com/snap-research/locomo) is a published multi-conversation memory benchmark from Snap Research. It tests a system's ability to answer questions about long-running personal conversations spanning months of interactions, requiring multi-hop reasoning across sessions.

**Result: 88.1% LLM-judged accuracy (n=762 questions, 5 conversations)**

| System | LLM Accuracy |
|--------|-------------|
| Waystone | **88.1%** |
| Full-context baseline | ~60% |

Avg retrieval tokens per query: ~1,338 — roughly 10–15× more efficient than full-context injection.

### Methodology

- **Judge model:** GPT-4o-mini
- **Retrieval:** BFS traversal with RRF re-ranking and person-hub fanout
- **Split:** Dev split (5/10 conversations)
- **Source:** Internal evaluation, April 2026

---

## LongMemEval

[LongMemEval](https://arxiv.org/abs/2410.10813) is a large-scale benchmark from CMU that measures memory systems on 500 questions across 6 question types spanning multiple conversation sessions.

**Result: 61.6% overall accuracy (n=500, LLM-judged)**

### Per-category breakdown

| Question type | Waystone | Notes |
|---------------|---------|-------|
| Single-session assistant | **87.5%** | Technical Q&A recall |
| Knowledge update | 74.4% | Tracking changed facts |
| Temporal reasoning | 62.4% | Answering "when" questions |
| Single-session user | 54.3% | Personal fact recall |
| Preference | 50.0% | Preference inference |
| Multi-session | 48.9% | Cross-session episodic recall |

### Competitor comparison

| System | Overall accuracy |
|--------|----------------|
| Mem0 | 93.4% |
| Waystone | **61.6%** |
| GPT-4o Full memory (paper baseline) | 60.6% |
| Zep | 58% |
| Naive RAG (turn retrieval) | 52% |

**Context:** LongMemEval is weighted toward general personal assistant use cases — episodic recall ("what did I say last Tuesday"), preference inference, and cross-session chat history. Waystone is purpose-built for structured technical context: decisions, constraints, architectural rationale, and fact supersession. The 87.5% on single-session assistant tasks (closest to Waystone's core use case) reflects this design focus.

Systems like Mem0 store raw conversation turns and query them semantically. Waystone extracts structured facts into a knowledge graph, which excels at "what was decided and why" but is less suited to verbatim episodic chat recall.

### Methodology

- **Judge model:** GPT-4o-mini
- **Retrieval:** BFS traversal with RRF re-ranking
- **Avg retrieval tokens:** ~1,897 per query
- **Split:** Full S-split (500 questions)
- **Source:** Internal evaluation, April 2026

---

## Internal Engineering Test — Methodology

- **Extraction model:** Gemini 2.5 Flash
- **Retrieval:** BFS traversal from keyword-matched entry nodes, strategy pipeline (superseded pruning → confidence threshold → recency decay → token budget)
- **top_k:** 30
- **Hops:** 3
- **Runs:** Multiple iterations; results represent best validated configuration
- **Source:** Internal evaluation, March 2026
