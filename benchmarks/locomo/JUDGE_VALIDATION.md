# LLM Judge Validation Guide

How to validate and compare LLM judge models on LOCOMO QA pairs.

---

## The Problem

LLM judge accuracy numbers are ambiguous — a higher score could mean:
- **Better retrieval** (the config actually returns more relevant context), OR
- **More lenient judge** (the model accepts partial or loosely-related answers)

You cannot distinguish these from a single number. Validation requires comparing
judges against each other or against human ground truth.

---

## Observed Results (April 2026)

Both judges run on `waystone_default_topk100`, `conv-26`, 199 questions:

| Model | LLM Accuracy | n | Notes |
|-------|-------------|---|-------|
| `local:qwen/qwen3-8b` (MLX) | 89.2% | ~199 | Single-threaded in LM Studio (MLX) |
| `local:qwen/qwen3.5-9b` (GGUF) | 71.4% | 199 | 20 concurrent slots; zero timeouts after disabling Unified KV Cache |

18pp gap. Unresolved whether 3-8B is genuinely better or just more lenient.

---

## Spot-Check Tool

`benchmarks/locomo/spot_check.py` — compares two judge verdicts on the same questions
and walks you through disagreements interactively.

### Usage

```bash
python3.13 -m benchmarks.locomo.spot_check \
  --dataset benchmarks/locomo/data/locomo10.json \
  --config waystone_default_topk100 \
  --conv conv-26 \
  --model-a "local:qwen/qwen3-8b" \
  --model-b "local:qwen/qwen3.5-9b" \
  --max 30
```

### What it does

1. Replays retrieval for all QA pairs in the conversation (DB reads, no extraction)
2. Scores each pair with both judge models (uses `~/.cache/waystone_judge_cache.json` — zero new API calls if already scored)
3. Prints a summary: agreement rate, disagreement count, which direction each disagreement goes
4. Walks you through each disagreement one at a time, showing:
   - Question
   - Ground truth answer
   - Retrieved context (first 600 chars, configurable via `--context-chars`)
   - Both scores
5. You type `YES` / `NO` / `SKIP` for each case

### Interpreting results

- **Model A=YES / Model B=NO**: A is the more lenient judge — check whether the context actually supports the answer
- **Model A=NO / Model B=YES**: B is the more lenient judge
- 20–30 spot-checked disagreements is enough for a directional read

---

## Judge Cache

All judge verdicts are cached to `~/.cache/waystone_judge_cache.json`.

- Key: `sha256(model + "\x00" + question + "\x00" + answer + "\x00" + context)[:24]`
- Value: float score (0.0, 0.5, or 1.0)
- Cache is per-model — changing the model string busts the cache for that model
- To force re-evaluation: delete the cache file or specific keys

Because the cache is keyed on `(model, question, answer, context)`, re-runs with
the same config/conversation are free.

---

## LM Studio Gotchas for Concurrent Judging

### MLX models cannot run concurrent requests
MLX models in LM Studio are single-slot. All judge calls queue serially.
Use GGUF models for concurrent judging.

### Unified KV Cache breaks concurrent GGUF judging
When "Unified KV Cache" is enabled in LM Studio, all concurrent slots share
a single context budget. With a ~5100-token judge prompt and 8192 total tokens,
each slot exceeds its per-slot budget → `400: Context size has been exceeded`.

**Fix**: Disable Unified KV Cache in LM Studio → each slot gets its own independent
8192-token context.

### Evaluation batch size (n_batch)
Controls prefill speed (tokens processed per forward pass). 
- Default 512: ~1 token/batch at 3s intervals
- 2048: ~4-5x faster prefill, ~500MB extra RAM
- Diminishing returns past 4096 (memory bandwidth ceiling on Apple Silicon)

### CPU thread pool
Metal handles 99% of compute on Apple Silicon. CPU threads cover tokenization/bookkeeping only.
- M1: 8 physical cores (4P+4E). Setting above 8 hurts. 7 is fine.

---

## Recommended Judge Models (April 2026)

For GGUF concurrent judging with quality close to Qwen3-8B MLX:

| Model | Size | Why |
|-------|------|-----|
| `Qwen3-8B-Q8_0.gguf` | ~8.6GB | Same weights as MLX version; Q8 = near-lossless |
| `Qwen3-8B-Q6_K.gguf` | ~6.6GB | Slightly smaller, negligible quality loss |
| `Llama-3.1-8B-Instruct-Q8_0` | ~8.5GB | Strong instruction-following, good for binary judgment |

Download Qwen3-8B GGUF from Hugging Face: `bartowski/Qwen3-8B-GGUF`

---

## Arbiter Approach (no human needed)

To validate judges without human review, run a third "arbiter" judge (e.g. Gemini 2.5 Flash)
on the disagreement cases only. Whichever of A/B agrees with the arbiter more often is the
better-calibrated judge.

```bash
# After running spot_check.py, note the disagreement question indices
# Then re-score just those with Gemini:
python3.13 -m benchmarks.locomo.spot_check \
  --dataset benchmarks/locomo/data/locomo10.json \
  --config waystone_default_topk100 \
  --conv conv-26 \
  --model-a "local:qwen/qwen3-8b" \
  --model-b "gemini-2.5-flash-lite" \
  --max 30
```
