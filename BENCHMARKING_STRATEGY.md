# Benchmarking Strategy

*How to quantify Engram's accuracy, and why it matters strategically.*

---

## The Core Question

Engram scores 93–96% recall on the software_dev benchmark (`eval_questions.yaml`, 23 questions across three transcripts). The question this doesn't answer: **is that a property of the graph approach, or a property of technical transcripts being easy to extract?**

Software dev conversations are unusually amenable to this kind of extraction. They're dense with named entities, specific values, and explicit decisions — "JWT, RS256, 15-minute expiry" leaves little ambiguity. Most memory systems would perform reasonably well on that content.

LOCOMO is the test that separates the approaches. Personal episodic conversation is ambiguous, temporally diffuse, and full of implicit context — exactly the conditions where architectural choices matter. If Engram scores well on LOCOMO, the graph approach is genuinely superior to vector-based alternatives. If it doesn't, Engram is a strong niche product with a narrow moat.

Both outcomes are useful to know. They imply different positioning, different target customers, and a different Phase 2 story.

---

## Why LOCOMO Specifically

LOCOMO (snap-research/locomo) is the closest thing to a widely accepted standard for conversational memory systems. It's cited by Zep, Mem0, and others. It's not dev-specific — it tests personal episodic memory across 10 long-term conversations, ~27 sessions each, ~199 QA pairs per conversation.

The `episodic_personal` domain profile is the prerequisite for running LOCOMO fairly. Using the `software_dev` profile on personal conversations would be an apples-to-oranges test that tells you nothing useful.

**Published competitor numbers (LLM judge, 10-conv subset):**

| System | Score | Architecture |
|---|---|---|
| Zep | ~72–75% | Graph + vector hybrid (Neo4j + pgvector) |
| Mem0 | ~87–90% | LLM-generated memory cards + vector search |
| Full context (oracle) | ~92–95% | No compression, full transcript in context |
| **Engram target** | ≥ 87% | Graph + BFS + SQLite |

See `LOCOMO_PLAN.md` for the specific gaps and phased improvement plan.

---

## Is There a Dev-Specific Benchmark?

No widely accepted one exists. The closest alternatives:

- **SWE-bench / HumanEval / MBPP** — test code generation quality, not memory or context retrieval. Not applicable.
- **RAGAS** — evaluates RAG pipelines generically (faithfulness, answer relevance, context precision/recall). Respected and automatable, but not dev-specific.
- **Nothing** that specifically measures whether a context management system improves AI coding assistant quality.

The 93–96% recall number from `eval_questions.yaml` is essentially a custom benchmark. It's rigorous but self-authored, which limits its credibility as an external selling point.

---

## Other Quantification Approaches

### 1. RAGAS Metrics (medium effort, recognized framework)

Run Engram's retrieval output through RAGAS to get standardized scores:
- **Context precision**: of retrieved nodes, how many were actually relevant?
- **Context recall**: of relevant facts, how many were retrieved?
- **Faithfulness**: does the retrieval accurately reflect what's in the graph?
- **Answer relevance**: does the retrieved context support correct answers?

RAGAS is framework-agnostic and the metrics are cited in ML literature. Applying it to the existing `eval_questions.yaml` questions requires minimal new work.

### 2. Ablation Study (high effort, highest buyer impact)

The most compelling metric for enterprise buyers isn't a benchmark score — it's measurable improvement in the thing they care about.

Design:
1. Take a real task: fix a bug, implement a feature, explain an architecture decision
2. Run Claude with and without Context Broker context injection (same model, same task, same codebase)
3. Measure: wrong assumptions made, clarifying questions asked, first-attempt correctness, time to correct solution

This is what nobody has published cleanly for a context management tool. A well-executed ablation study is more persuasive to a CTO than a LOCOMO score.

### 3. Token Efficiency (low effort, already measurable)

The benchmark reports already track average tokens per query:
- `baseline` strategy: ~1,007 avg tokens
- `tight` strategy: ~709 avg tokens
- Recall delta: 96% → 91%

Framing: "Equivalent recall at 30% fewer tokens than full-context injection" is a concrete, measurable claim that matters for hosted API cost modeling and for users who are token-budget-conscious.

### 4. ContextBench (speculative, high strategic value)

Publish the three software_dev transcripts, 23 eval questions, and scoring methodology as an open benchmark. Call it something like "DevMemBench."

Even a small, well-documented benchmark with publicly available transcripts and ground-truth answers is citable. If it's good, other systems adopt it for comparison. That gives Engram authority in the dev-specific niche that no competitor currently has.

---

## Recommended Priority

| Benchmark | Effort | Strategic value | When |
|---|---|---|---|
| LOCOMO (with `episodic_personal` profile) | Low — harness built, profile ready, need dataset | High — answers the general vs. niche question | Before Phase 2 (hosted API) |
| RAGAS on existing eval questions | Low | Medium — adds recognized framing to existing numbers | Anytime |
| Token efficiency framing | Minimal — data already in reports | Medium — useful for pricing/positioning | Immediately |
| Ablation study | High | Highest for sales | After MCP server ships (Phase 1) |
| ContextBench publication | Medium | High long-term | After LOCOMO results are known |

The LOCOMO run should happen before the hosted API is built. The answer to "is this a general-purpose memory tool" has direct implications for Phase 2 pricing tiers, target customers, and competitive positioning.
