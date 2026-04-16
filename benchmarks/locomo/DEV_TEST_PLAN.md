# LOCOMO Dev Test Plan

First full run against the dev split (5 conversations). Documents execution
parameters, cost estimates, and the rationale behind each decision.

**Status: ALL 3 CONFIGS COMPLETE ✓**

Spot check complete (task bolahkpt2): qwen3-8b=91.0% vs qwen3.5-9b=56.8%, 74/199 disagreements.
Neither local judge is suitable as the authoritative judge. GPT-4o-mini confirmed as correct choice.
See `JUDGE_ISSUES.md` for full calibration summary.

**Final results (2026-04-06, run6 + batch API):**
| Config | Keyword | LLM (n) |
|--------|---------|---------|
| `engram_dedup95` | 63.5% | 64.9% (999/999) ✓ |
| `engram_default_topk100` | 71.6% | 70.8% (999/999) ✓ |
| `engram_semantic_rerank_topk100` | **73.9%** | **75.6% (999/999)** ✓ |

semantic_rerank judged via OpenAI Batch API (separate RPD quota, 50% cheaper).
504 requests submitted (495 cache hits), completed in ~8 min, 998/999 scored (1 empty-context QA).
Results written to `benchmarks/locomo/results/dev_test_20260406.json`.

---

## Scope

- **Split**: DEV — `["conv-26", "conv-30", "conv-41", "conv-42", "conv-43"]`
  (128 sessions, 999 QA pairs total)
- **Configs**: 3 (see below)
- **Extraction model**: `gemini-2.5-flash-lite`
- **Judge model**: `gpt-4o-mini`

---

## Configs to Run

| Config | checkpoint_source | Notes |
|--------|-------------------|-------|
| `engram_dedup95` | *(none — runs extraction)* | Base extraction; dedup threshold 0.95 |
| `engram_default_topk100` | `engram_dedup95` | Copies DB; no re-extraction |
| `engram_semantic_rerank_topk100` | `engram_dedup95` | Copies DB; no re-extraction |

`full_context` is **excluded** — it is 8× more expensive per QA pair
(~31K tokens vs ~3,850) and doesn't exercise the retrieval pipeline.

---

## Execution Parameters

### Extraction
- Model: `gemini-2.5-flash-lite`
- `engram_dedup95` runs extraction once; the other two configs copy its DB
  via `checkpoint_source` — zero additional extraction cost

### Judging
- Model: `gpt-4o-mini`
- **`_judge_workers = 5`** — 5 workers × 5 conv_workers = 25 peak concurrent requests,
  each ~3,850 tokens ≈ 96K tokens/min peak. Within gpt-4o-mini paid-tier TPM limits.
- Note: 10 workers was tried but RPD exhaustion (10K/day cap) caused 953/2,997 failures.
  Judge cache (`~/.cache/engram_judge_cache.json`) means ~2,044 already-scored QAs
  are free — the re-run only bills for the 953 that failed.

### Concurrency
- `conv_workers=5` — all 5 conversations in parallel
- Sessions within each conversation: sequential (context carry-forward
  required for ingestion pipeline)
- Configs: **sequential** — run `engram_dedup95` first (extraction), then
  the two checkpoint consumers

---

## Cost Estimate

| Item | Tokens | Cost |
|------|--------|------|
| Extraction (Gemini Flash Lite, 5 convs) | ~5M input, ~500K output | ~$0.05 |
| Judge — `engram_dedup95` (999 QA × ~3,850 tok) | ~3.85M input | ~$0.58 |
| Judge — `engram_default_topk100` | ~3.85M input | ~$0.58 |
| Judge — `engram_semantic_rerank_topk100` | ~3.85M input | ~$0.58 |
| **Total** | | **~$1.78** |

GPT-4o-mini pricing: $0.15/1M input, $0.60/1M output (output tokens negligible
for judge — single YES/PARTIAL/NO response).

---

## Time Estimate

| Phase | Wall-clock |
|-------|-----------|
| Extraction (`engram_dedup95`, 5 convs parallel) | ~3–4 min |
| Judge scoring × 3 configs (sequential, 10 workers, 5 convs parallel) | ~4–6 min |
| **Total** | **~7–10 min** |

---

## Pre-Run Checklist

- [x] Spot check (task `bolahkpt2`) complete — qwen3-8b vs qwen3.5-9b
      disagreements reviewed, no systemic judge issues found
- [x] `harness.py:288`: `_judge_workers = 5 if llm_model.startswith("gpt-") else 20`
- [x] Extraction model is `gemini-2.5-flash-lite` in harness config
- [x] Extraction complete — `engram_dedup95` DBs built for all 5 convs
      (conv-26: existing, conv-30: 1,339 nodes, conv-41: 2,463 nodes,
       conv-42: 2,164 nodes, conv-43: ~2,200 nodes)
- [x] Preflight check passed (harness prints model + max_tokens at start)

## Re-run Checklist — COMPLETE ✓

- [x] Batch API implemented in `scoring.py` (`submit_judge_batch`) and `harness.py` (`--use-batch`)
- [x] semantic_rerank judged via batch — no RPD consumed from sync quota
- [x] Result JSON written with `llm_score` populated for all 999 QA pairs × 3 configs

**Command used:**
```bash
PYTHONUNBUFFERED=1 python3.13 -m benchmarks.locomo.harness \
  --dataset benchmarks/locomo/data/locomo10.json \
  --split dev \
  --configs engram_semantic_rerank_topk100 \
  --llm-judge \
  --llm-model gpt-4o-mini \
  --conv-workers 5 \
  --use-batch \
  --output benchmarks/locomo/results/dev_test_20260406.json
```

---

## Run Command (draft)

```bash
python -m benchmarks.locomo.harness \
  --dataset benchmarks/locomo/data/locomo10.json \
  --split dev \
  --configs engram_dedup95 engram_default_topk100 engram_semantic_rerank_topk100 \
  --extraction-model gemini-2.5-flash-lite \
  --judge-model gpt-4o-mini \
  --conv-workers 5
```

*(Verify exact CLI flags against harness.py before running.)*
