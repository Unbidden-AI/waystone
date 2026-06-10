# Session-summary retrieval benchmark (P4b)

`benchmarks/bench_summary_retrieval.py` — measures whether `session_summary` nodes
help retrieval **without crowding atomic facts**. Reuses a populated 296-node graph
(all three synthetic projects); the only LLM calls are one rolling summary per
transcript. Scoring is `score_recall` + `retrieve_with_stats` (LLM-free).

## Result (2026-06-10, gemini-2.5-flash)

**1. Crowding — PASS.** Adding the three rolling `session_summary` nodes to the graph
and re-running the 23 atomic eval questions:

| | mean recall |
|---|---|
| atomic-only | 77.5% |
| + summaries in graph | 79.8% |
| **delta** | **+2.3%** (1 marginal regression, `q_pipe_03`) |

Summaries do **not** displace atomic facts from the top-k — recall is flat-to-slightly-up.
This is the defensive guarantee: turning on session summaries is safe for existing
fact retrieval.

**2. Narrative lift — +0% on this dataset, and that is EXPECTED.** The synthetic
transcripts are short (~4 KB) and exhaustively extracted, so the atomic graph already
contains the goal / next-step tokens as discrete facts (`50k events per second`,
`next step … Jordan … Kong gateway`, `Avro`). There is no narrative gap to fill, so
injecting the summary can't raise recall.

The retrieval value of summaries appears where atomic extraction **misses** the arc —
long, messy real sessions with reversed decisions and "why we changed" rationale. The
live-session demo (this project's own 531-turn transcript) showed exactly that: the
rolling summary captured the *rejected* two-tier-memory decision and its reasoning,
content that atomic extraction scatters or drops. A narrative-**lift** benchmark needs
a labeled long transcript; this benchmark's job — confirm summaries don't HURT — is met.

## Takeaway

- Crowding is empirically ruled out → `session_summary.inject` (P4a) and the graph
  nodes are safe to ship on by default.
- Narrative value is real but qualitative on clean data; to quantify it, build an eval
  on a long real session where the atomic graph is known to be incomplete.
