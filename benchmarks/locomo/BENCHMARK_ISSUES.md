# Benchmark Issues

Cases where the LOCOMO ground truth answer appears to be incorrect, incomplete, or hallucinated — not attributable to extraction or retrieval failures.

Each entry includes the question index, what the ground truth says vs. what the context contains, and a diagnosis.

---

## Template

```
### Q<idx> — <short title>
- **Question:** ...
- **Ground truth answer:** ...
- **What context contains:** ...
- **Issue type:** hallucinated fact | incomplete | wrong answer | ambiguous
- **Diagnosis:** ...
- **Session source:** conv-26/session_N
- **Status:** open
```

---

## Open Issues

### Q15 — Hallucinated activity in ground truth
- **Question:** What activities does Melanie partake in?
- **Ground truth answer:** pottery, camping, painting, swimming
- **What context contains:** pottery ✓, camping ✓, painting ✓, running ✓, violin ✓, clarinet ✓, hiking ✓ — swimming appears in ZERO nodes across all 19 sessions (100 retrieved nodes checked)
- **Issue type:** hallucinated fact + incomplete
- **Diagnosis:** Ground truth lists "swimming" which is not present in any retrieved node across all sessions. Ground truth is also incomplete — it omits running, violin, clarinet, and hiking, which are clearly evidenced in multiple nodes. This appears to be a LOCOMO dataset quality issue: the benchmark authors either hallucinated "swimming" or sourced it from a session not present in conv-26. The ground truth is simultaneously fabricated on one item and missing several others.
- **Judge impact:** qwen3-8b=1.0 (too lenient — credited 3/4 items found), qwen3.5-9b=0.5 PARTIAL (correctly noted swimming was missing from context). qwen3.5-9b is the more accurate judge here.
- **Session source:** conv-26 (all 19 sessions)
- **Status:** open

### ~~Q19~~ — moved to RETRIEVAL_ISSUES.md
- "Dinosaurs" node exists in DB (`n_7464db14`: "Melanie's kids were stoked about the dinosaur exhibit at the museum.") but was not retrieved. This is a retrieval miss, not a benchmark error. See RETRIEVAL_ISSUES.md.

---

## Closed Issues

*(none yet)*

---

## Patterns

- **Incomplete ground truth**: LOCOMO ground truth answers are often partial enumerations. A question asking "what activities does X do" may list only 3-4 items when the conversation mentions 6-7. This is expected benchmark behavior but inflates false-negative rates for thorough retrieval systems.
- **Hallucinated ground truth facts**: At least one confirmed case (Q15 "swimming") where the ground truth contains a fact with zero evidence in any session or in the DB. Always verify DB before concluding ground truth is hallucinated — Q19 ("dinosaurs") looked like this but turned out to be a retrieval miss (node `n_7464db14` exists in DB).
- **Pattern**: Hallucinated + incomplete ground truth tends to produce max judge disagreement (qwen3-8b=1.0 vs qwen3.5-9b=0.0 or 0.5). Confirm with a DB-level search before logging here vs. RETRIEVAL_ISSUES.md.
