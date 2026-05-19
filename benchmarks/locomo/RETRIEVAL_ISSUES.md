# Retrieval Issues

Failures where the extracted nodes exist in the graph but the retrieval pipeline failed to surface them in time for scoring.

Each entry includes the question index, what was (and wasn't) retrieved, and a diagnosis.

---

## Template

```
### Q<idx> — <short title>
- **Question:** ...
- **Ground truth answer:** ...
- **Node that should have been retrieved:** ...
- **Why it was missed:** ...
  - tag mismatch / BFS depth / token budget cutoff / wrong entry node / ...
- **Config:** waystone_default_topk100 | waystone_semantic_rerank_topk100 | ...
- **Session source:** conv-26/session_N
- **Status:** open
```

---

## Open Issues

### Q19 — Dinosaur exhibit node not retrieved
- **Question:** What do Melanie's kids like?
- **Ground truth answer:** dinosaurs, nature
- **Node that should have been retrieved:** `n_7464db14` — "Melanie's kids were stoked about the dinosaur exhibit at the museum." (tags: `["melanie", "kids", "museum", "dinosaur exhibit", "excitement"]`)
- **Why it was missed:** Tag mismatch. The query keywords from "what do Melanie's kids like" produce terms like "melanie", "kids", "like" — none of which overlap with `"dinosaur exhibit"` or `"museum"`. The node is retrievable only if the query contains "dinosaur" or "museum". BFS never starts from this node.
- **Config:** waystone_semantic_rerank_topk100
- **Session source:** conv-26 (session unknown — museum visit)
- **Status:** open

---

## Closed Issues

*(none yet)*

---

## Patterns

*(fill in as patterns emerge)*

### Known causes of retrieval miss
- **Tag mismatch**: Query keywords don't overlap with node tags — entry node never found, BFS never starts.
- **BFS depth ceiling**: Correct node is more than `hops` edges away from any entry node.
- **Token budget cutoff**: Node is ranked below the token budget cut — present in BFS result set but trimmed.
- **Wrong entry node**: BFS starts at a plausible but unrelated node and the correct node is not in its neighborhood.
- **Semantic rerank displacement**: With `semantic_rerank`, a semantically similar but wrong node bumps the correct node below top_k.
