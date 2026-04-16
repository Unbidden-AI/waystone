# Local LLM Judge Research — April 2026

> Task: Find the best local models for YES/PARTIAL/NO classification (LLM judge) on Apple Silicon.
> Max output: 16 tokens. Need speed + accuracy. Used in LOCOMO benchmark evaluation.

---

## Top Recommendation: Qwen 3 8B + MLX

**Qwen 3 8B with MLX backend** is the best overall pick for LOCOMO benchmark judging:
- 60+ tok/s on M3 Max (~270ms per call, well under 1s target)
- 8.3 GB RAM — fits M3 Max comfortably
- 81.9% accuracy on official benchmarks
- Excellent multilingual support

**For maximum format reliability**: Use **Gemma 3 12B** — IFEval-optimized, 95%+ compliance.

---

## Comparison Table

| Model | Size (Q4) | Speed (M3 Max) | MMLU | Format Reliability | Verdict |
|-------|-----------|----------------|------|-------------------|---------|
| **Qwen 3 8B** | ~5 GB | 60+ tok/s | 81.9% | Good (90%) | **Best overall** |
| **Gemma 3 12B** | ~7 GB | 40–60 tok/s | 80–85% | **Excellent (95%+)** | Best format |
| Phi-4 14B | ~8 GB | 25–40 tok/s | 85.6% | Moderate (~80%) | ❌ IFEval weakness |
| Llama 3.2 3B | ~2 GB | 60–80 tok/s | 63.4% | Good (95%+) | Good if RAM-constrained |
| Phi-4 mini 3.8B | ~2.3 GB | 80–100 tok/s | — | Excellent | Speed + quality balance |
| Llama 3.2 1B | ~0.6 GB | 200–300 tok/s | — | Good (93%) | 8 GB Macs only |

**Do NOT use Phi-4 14B as primary judge** — documented IFEval weakness causes ~20% format failures ("Based on the context..."). Smaller Phi-4 mini is fine.

---

## vs. Gemini 2.5 Flash Lite (Current Cloud Judge)

| Factor | Gemini 2.5 Flash Lite | Qwen 3 8B (local) |
|--------|----------------------|-------------------|
| Latency per call | ~600–800ms wall-clock | ~270ms |
| Speed | ~4–12× slower | — |
| Cost | ~$0.10–0.20 per 200 calls | $0 |
| Format compliance | 99%+ | ~90% |
| Privacy | No (transcript uploaded) | Yes |

**Rule of thumb**: Use local for iteration/development. Reserve Gemini for final paper numbers.

---

## Inference Backend: MLX vs Ollama

**Use MLX, not Ollama, for speed-critical benchmark runs.**

- MLX is 30–50% faster than Ollama (llama.cpp Metal backend) on Apple Silicon
- Some models show 2–3× speedup with MLX
- Ollama v0.19+ (March 31, 2026) now uses MLX backend by default — closes the gap

**MLX setup (recommended):**
```bash
pip install mlx-lm openai
python -m mlx_lm.server --model Qwen/Qwen3-8B-Instruct
# OpenAI-compatible API at http://localhost:8000/v1
export LOCAL_LLM_BASE_URL=http://localhost:8000/v1
```

**Ollama setup (easiest):**
```bash
ollama pull qwen3:8b
ollama serve
# API at http://localhost:11434/v1
export LOCAL_LLM_BASE_URL=http://localhost:11434/v1
```

**Use in harness:**
```bash
python -m benchmarks.locomo.harness --llm-model local:qwen3:8b --limit 1
```

For concurrent requests (parallel judge calls), use **vLLM-MLX** — adds continuous batching, 3.4× throughput with 5 concurrent requests.

---

## Quantization: Always Q4_K_M

- Q4_K_M = best balance of quality/speed/memory for classification
- ~4× smaller than FP16 with negligible accuracy loss for YES/PARTIAL/NO
- Q6_K only if you need max accuracy and have headroom
- Never Q2/Q3 — significant accuracy degradation

**Official GGUF sources:**
- `Qwen/Qwen3-8B-GGUF` (official)
- `google/gemma-3-12b-it-qat-q4_0-gguf` (Google's official QAT quantization)
- `microsoft/phi-4` (base) + `unsloth/` or `TheBloke/` for GGUF

---

## Prompt Tweak for Format Reliability

Current `_judge_prompt()` in `scoring.py` is fine. For Phi-4 or any model with format issues, add:

```
Reply with ONLY one word: YES, PARTIAL, or NO.
Do not include any explanation, preamble, or punctuation.
Output exactly one of these three words and nothing else.
```

Also: keep `temperature=0.0` (already set in scoring.py for local path).

---

## PHUDGE: Fine-Tuned Judge Option

If you have 50+ labeled YES/PARTIAL/NO examples:
- **PHUDGE** (arxiv 2405.08029): Phi-3 fine-tuned as scalable judge
- Claims SOTA on 4 judge benchmarks, 10× smaller than Prometheus-2
- Strong GPT-4 correlation on absolute/relative grading
- Repo: `vicgalle/Phudge-3` on Hugging Face
- Could fine-tune on Phi-4 base with LoRA (rank=128) for +5–10% over base models

---

## Hardware-Specific Recommendations

| Hardware | Recommended Model | Why |
|----------|-------------------|-----|
| M3 Max, 16 GB | Qwen 3 8B (Q4) | 60+ tok/s, fits in 8.3 GB |
| M3 Max, 32 GB | Gemma 3 12B (Q4) | Better format reliability, still fast |
| M4 Ultra, 48 GB+ | Phi-4 14B or Mistral Nemo 12B | Higher accuracy ceiling |
| MacBook Air M3, 8 GB | Llama 3.2 3B or Phi-4 mini | Fits in memory |

---

## Judge Cache Note

The judge cache at `~/.cache/engram_judge_cache.json` is keyed on `(model, question, answer, context)`.
Switching from `gemini-2.5-flash-lite` to `local:qwen3:8b` will NOT reuse cached scores — new model = new cache key. Clear the cache only if you change judge model or prompt.
