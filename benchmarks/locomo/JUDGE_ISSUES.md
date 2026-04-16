# Judge Calibration Issues

Cases where one or both LLM judges gave a clearly wrong verdict — not attributable to extraction or retrieval failures.

Each entry includes the question index, what the context contained, and a diagnosis of which judge failed and why.

Judge labels: **A** = qwen3-8b (tends lenient), **B** = qwen3.5-9b (tends strict)

**Note on thinking mode:** Both models were tested with "Enable Thinking" **off** (`/no_think` system message for Qwen3 models). Enabling reasoning may improve calibration — particularly for qwen3.5-9b's temporal inference and semantic equivalence failures — but at the cost of higher latency and token usage. Not evaluated.

---

## Template

```
### Q<idx> — <short title>
- **Question:** ...
- **Ground truth answer:** ...
- **What context contained:** ...
- **Verdicts:** A=<score>  B=<score>
- **Correct verdict:** YES / NO / PARTIAL
- **Which judge failed:** A / B / both
- **Failure mode:** too lenient | too strict | paraphrase rejection | counting failure | framing mismatch
- **Diagnosis:** ...
- **Config:** ...
- **Status:** open
```

---

## Open Issues

### Q0 — LGBTQ support group date (temporal inference)
- **Question:** When did Caroline go to the LGBTQ support group?
- **Ground truth answer:** 7 May 2023
- **What context contained:** "Caroline attended an LGBTQ support group the day before May 8, 2023." — the date is fully recoverable by simple arithmetic (May 8 − 1 = May 7).
- **Verdicts:** A=1.0  B=0.0
- **Correct verdict:** YES
- **Which judge failed:** B (qwen3.5-9b)
- **Failure mode:** temporal inference rejection
- **Diagnosis:** qwen3.5-9b required the literal string "7 May 2023" (or equivalent) to appear in the context. "The day before May 8" encodes the same date but requires a one-step arithmetic inference. The model refused to perform this inference and returned 0.0. qwen3-8b correctly derived May 7 from the relative date expression.
- **Config:** engram_semantic_rerank_topk100
- **Status:** open

---

### Q34 — "to help children" framing mismatch
- **Question:** What events has Caroline participated in to help children?
- **Ground truth answer:** Mentoring program, school speech
- **What context contained:** Both the mentoring program and school speech were clearly evidenced in retrieved nodes — Caroline spoke at a school and ran a mentoring program.
- **Verdicts:** A=1.0  B=0.0
- **Correct verdict:** YES
- **Which judge failed:** B (qwen3.5-9b)
- **Failure mode:** framing mismatch — too strict
- **Diagnosis:** qwen3.5-9b penalized because the context didn't explicitly frame the events as "to help children" — the nodes described the events themselves (school speech, mentoring), not their purpose. The judge demanded purpose-language that matched the question framing, even though the answer was fully supported. qwen3-8b correctly credited the factual match.
- **Config:** engram_semantic_rerank_topk100
- **Status:** open

---

### Q38 — Melanie's family activities (compound failure)
- **Question:** What activities has Melanie done with her family?
- **Ground truth answer:** Pottery, painting, camping, museum, swimming, hiking
- **What context contained:**
  - Painting ✓ — retrieved
  - Camping ✓ — retrieved
  - Hiking ✓ — retrieved
  - Museum ✗ — node `n_7464db14` exists in DB but was not retrieved (see RETRIEVAL_ISSUES.md)
  - Swimming ✗ — not in DB or raw conversation (see BENCHMARK_ISSUES.md — hallucinated ground truth)
  - Pottery — ambiguous; retrieved as individual activity but not clearly framed as "with family"
- **Verdicts:** A=1.0  B=0.0
- **Correct verdict:** PARTIAL (0.5) — 3 of 4 real items present, 1 retrieval miss, 1 benchmark error
- **Which judge failed:** both
- **Failure mode:** A too lenient (1.0 despite museum/swimming absent); B too strict (0.0 despite 3 items present)
- **Diagnosis:** Compound failure. Ground truth has 6 items but only 4 are real (swimming hallucinated). Of the 4 real ones, 3 were in context and 1 was a retrieval miss (museum). Correct score is ~0.5 PARTIAL. qwen3-8b overcredited — gave full YES despite two items absent. qwen3.5-9b overcorrected — gave full NO despite three items clearly present. Neither judge handled partial enumeration correctly.
- **Note:** This case also appears in BENCHMARK_ISSUES.md (swimming) and RETRIEVAL_ISSUES.md (museum).
- **Config:** engram_semantic_rerank_topk100
- **Status:** open

---

### Q40 — Beach trip count (counting failure)
- **Question:** How many times has Melanie gone to the beach in 2023?
- **Ground truth answer:** 2
- **What context contained:**
  - Camping trip at the beach the week before June 27, 2023 (session_4) ✓
  - Beach trip in July 2023 — "Melanie's kids had a blast at the beach" (session_10, dated July 20) ✓
  - Corroborating: "Melanie and her kids usually go to the beach once or twice a year" (session_10)
- **Verdicts:** A=1.0  B=0.0
- **Correct verdict:** YES — 2 specific beach trips are evidenced in the context
- **Which judge failed:** B (qwen3.5-9b)
- **Failure mode:** counting failure — too strict
- **Diagnosis:** qwen3.5-9b latched onto "once or twice a year" as imprecise and concluded the context didn't definitively confirm "2". But the context contains two *specific* beach trip events in 2023 (June camping trip + July beach day). The judge failed to count distinct event mentions and instead treated the frequency statement as the only evidence. qwen3-8b gave the correct verdict.
- **Config:** engram_semantic_rerank_topk100
- **Status:** open

---

### Q42 — National park preference (inferential reasoning)
- **Question:** Would Melanie be more interested in going to a national park or a theme park?
- **Answer:** National park; she likes the outdoors
- **What context contained:** Camping trips, beach trips, park visits ("Melanie found the park experience to be very fun"), "Melanie's two younger kids love nature", "Melanie's kids love learning about animals" — strong circumstantial evidence for outdoor preference across multiple sessions.
- **Verdicts:** A=1.0  B=0.0
- **Correct verdict:** YES
- **Which judge failed:** B (qwen3.5-9b)
- **Failure mode:** inferential reasoning rejection
- **Diagnosis:** No single node says "Melanie likes the outdoors" — the evidence is circumstantial (she camps, goes to the beach, takes kids to parks, finds parks fun). qwen3.5-9b demanded an explicit preference statement and gave 0.0. qwen3-8b correctly synthesized the behavioral evidence. This is a preference-inference question: the correct evaluation requires reasoning from activity patterns, not matching a single fact.
- **Config:** engram_semantic_rerank_topk100
- **Status:** open

---

### Q69 — Caroline's personality traits (semantic equivalence)
- **Question:** What personality traits might Melanie say Caroline has?
- **Answer:** Thoughtful, authentic, driven
- **What context contained:** "Melanie believes Caroline has a caring heart" (→ thoughtful/caring), "Caroline wants to live authentically" + "Melanie is in awe of Caroline's courage" (→ authentic), "Melanie finds Caroline's passion for helping kids awesome" + pursuit of adoption/mentoring (→ driven). All three traits are evidenced, just not in those exact words.
- **Verdicts:** A=1.0  B=0.0
- **Correct verdict:** YES
- **Which judge failed:** B (qwen3.5-9b)
- **Failure mode:** semantic equivalence rejection
- **Diagnosis:** The ground truth uses adjectives ("thoughtful", "authentic", "driven") but the context expresses the same traits through behavioral observations and Melanie's own statements. qwen3.5-9b required verbatim trait labels; qwen3-8b correctly mapped caring→thoughtful, authentic-living→authentic, passion+pursuit→driven. Semantic synonym matching is the core skill being tested here.
- **Config:** engram_semantic_rerank_topk100
- **Status:** open

---

## Closed Issues

*(none yet)*

---

## Patterns

### Known judge failure modes (qwen3.5-9b)
- **Framing mismatch**: Penalizes correct factual answers when the context doesn't use the same purpose/framing language as the question. Example: "to help children" not in node text even though node describes the event.
- **Counting failure**: For numeric answers, gets distracted by approximate frequency language ("once or twice") and misses specific event instances that sum to the correct count.
- **Enumeration strictness**: For multi-item answers, returns 0.0 if any item is missing — even if the majority are present. Should return PARTIAL but instead returns full NO.
- **Inferential reasoning rejection**: For preference/prediction questions ("would X prefer Y or Z?"), requires explicit preference statements instead of reasoning from behavioral evidence. Fails on questions that are designed to test episodic inference.
- **Semantic equivalence rejection**: Requires verbatim ground truth terms rather than accepting semantically equivalent paraphrases (e.g., "caring heart" is not credited as evidence for "thoughtful").
- **Temporal inference rejection**: For dates expressed as relative offsets ("the day before May 8"), refuses to compute the implied date and returns 0.0. Requires the literal date string to appear in context.

### Known judge failure modes (qwen3-8b)
- **Enumeration leniency**: For multi-item answers, returns 1.0 if most items are present — even when significant items are absent. Should return PARTIAL but returns full YES.

### Calibration summary (conv-26, engram_semantic_rerank_topk100, thinking=off)
- qwen3-8b: **91.0%** (n=199)
- qwen3.5-9b: **56.8%** (n=199)
- Disagreements: **74/199 (37%)**
  - qwen3-8b=YES / qwen3.5-9b=NO: 71 cases
  - qwen3-8b=NO  / qwen3.5-9b=YES: 3 cases
- **Verdict**: qwen3.5-9b is not suitable as an authoritative judge at default settings (thinking off). The 34-point gap is systematic, not random noise. qwen3-8b is more reasonable but still too lenient for rigorous benchmarking. Use GPT-4o-mini for production evaluation.
- **Thinking caveat**: Both models ran with thinking disabled. Enabling reasoning might close the gap for qwen3.5-9b (particularly temporal inference and semantic equivalence failures) but this was not evaluated.

### Impact
- Judge disagreements involving partial enumeration questions are the highest-frequency failure mode.
- Temporal inference rejection (Q0) is a newly identified systematic failure for qwen3.5-9b.
- Compound cases (retrieval miss + benchmark error + judge miscalibration) are difficult to attribute — log all three issue files and cross-reference.
