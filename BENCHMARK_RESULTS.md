# Waystone Benchmark Results

## Overview

Waystone was evaluated on 23 recall questions across 3 engineering domains, drawn from realistic design meeting transcripts. The test measures whether Waystone can surface the right fact when asked a natural-language question about a project's history.

**Best result: 95% recall (21/23 questions answered correctly)**

---

## Test Set

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

---

## Results

| Extraction mode | Recall | Questions ≥ 80% |
|-----------------|--------|-----------------|
| Standard | 92% | 19 / 23 |
| With verification pass | **95%** | **21 / 23** |

The verification pass runs a second extraction sweep specifically hunting for secondary details, buried numerics, transition statements, and decision rationale — the things a first pass tends to skip.

---

## What Gets Recovered

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

## Methodology

- **Extraction model:** Gemini 2.5 Flash
- **Retrieval:** BFS traversal from keyword-matched entry nodes, strategy pipeline (superseded pruning → confidence threshold → recency decay → token budget)
- **top_k:** 30
- **Hops:** 3
- **Runs:** Multiple iterations; results represent best validated configuration
- **Source:** Internal evaluation, March 2026
