# Extraction Issues

Failures and gaps identified via spot-check on conv-26 (`waystone_default_topk100`).

Each entry includes the question index, observed vs. expected extracted node, and a diagnosis.

---

## Template

```
### Q<idx> — <short title>
- **Question:** ...
- **Ground truth answer:** ...
- **Extracted node:** ...
- **Expected node:** ...
- **Root cause:** ...
- **Session source:** conv-26/session_N
- **Status:** open
```

---

## Open Issues

### Q5 — Off-by-one day on relative date
- **Question:** When did Melanie run a charity race?
- **Ground truth answer:** The Sunday before 25 May 2023 (= May 21)
- **Extracted node:** "Melanie ran a charity race for mental health on 20 May 2023."
- **Expected node:** "Melanie ran a charity race for mental health on 21 May 2023."
- **Root cause:** The conversation used relative language ("last Sunday" or similar). The extractor resolved it to May 20 (Saturday) instead of May 21 (Sunday). Off-by-one day in relative date resolution.
- **Session source:** conv-26/session_2
- **Status:** open

### Q11 — Proper noun abstracted to generic ("Sweden" → "home country")
- **Question:** Where did Caroline move from 4 years ago?
- **Ground truth answer:** Sweden
- **Extracted node:** "Caroline moved from her home country 4 years ago." / "Caroline has known her friends for 4 years, since she moved from her home country."
- **Expected node:** "Caroline moved from Sweden 4 years ago." (with tags: ["sweden", "moved", "home country"])
- **Root cause:** Extractor paraphrased "Sweden" to "home country" — a specific proper noun (country name) was generalized away. This is a precision loss in abstraction: the extractor should preserve named entities verbatim in the fact text.
- **Session source:** conv-26/session_3
- **Status:** open

---

## Closed Issues

*(none yet)*

---

## Patterns

- **Relative date resolution errors**: Extractor attempts to anchor relative expressions ("last Sunday", "the day before") to absolute dates using the session date. Off-by-one errors occur when the session date itself is approximate or when the relative anchor is not the session date.
- **Proper noun generalization**: Extractor occasionally paraphrases specific named entities (countries, places, names) to generic descriptions. Fix: add an extraction rule requiring named entities to be preserved verbatim in fact text and in tags.
