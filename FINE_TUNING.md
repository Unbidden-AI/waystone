# Fine-Tuning Plan

This document tracks goals, candidates, and steps for fine-tuning LLMs to improve Context Broker extraction quality.

## Why fine-tune?

The extraction model has two jobs where fine-tuning helps most:

1. **Fact writing quality** — richer, self-contained fact sentences produce better embeddings for semantic search.
   - Terse: `"JWT expiry set"`
   - Better: `"Authentication tokens use JWT with a 24-hour expiry enforced at the API gateway"`

2. **Existing-context usage** — the extraction prompt injects up to 30 existing nodes from the graph. The model should:
   - Emit `supersedes` pointers when a new fact replaces an existing one (at creation time, not via reconcile)
   - Avoid re-extracting facts already in the graph
   - Create cross-edges to existing nodes

Current models do this inconsistently. Supervised fine-tuning on labeled examples (transcript + injected existing nodes → correct nodes/edges with `supersedes` set) directly fixes this.

## Target models (ranked)

| Rank | Model | Params | Hardware | License | Notes |
|------|-------|--------|----------|---------|-------|
| 1 | Qwen 3.5 9B | 9B | M3 Max (comfortable) | Apache 2.0 | 76% baseline recall; native JSON schema; IFBench 76.5 |
| 2 | Llama 4 Scout 17B | 17B | M3 Max / M4 Max | Meta | Strong instruction following |
| 3 | Phi-4-mini | 3.8B | 8GB MacBook | MIT | Good baseline est. 70-80%; fits smallest hardware |

## Training approach

- **Method**: LoRA / QLoRA (parameter-efficient, fits Apple Silicon)
- **Tool**: MLX-LM (`mlx_lm.lora`) for on-device training on Apple Silicon
- **Format**: SFT (supervised fine-tuning) — no DPO needed initially
- **Starting point**: 750 base examples from existing benchmark extractions

### Data format

```json
{
  "messages": [
    {"role": "system", "content": "You are a context extraction engine. Return only valid JSON."},
    {"role": "user", "content": "<transcript excerpt>\n\nEXISTING CONTEXT:\n<injected nodes>"},
    {"role": "assistant", "content": "{\"nodes\": [...], \"edges\": [...]}"}
  ]
}
```

### Training signal for existing-context usage

For each extraction with injected existing nodes, the correct output:
- Has zero nodes that duplicate injected existing nodes
- Has correct `supersedes` references to existing node IDs when a fact replaces a prior one
- Has cross-edges to existing node IDs when new facts relate to prior ones

Synthetic hard cases: take a known-good extraction, re-run with those nodes injected — correct output should have zero new nodes (all already captured).

## Steps

- [ ] Generate SFT dataset from existing benchmark extractions (750+ examples)
- [ ] Add hard cases: re-extraction scenarios with existing-context injection
- [ ] Run baseline eval (Qwen 3.5 9B, no fine-tune) on benchmark suite
- [ ] Fine-tune with LoRA on MLX-LM
- [ ] Run benchmark eval on fine-tuned model
- [ ] Measure: recall delta, duplicate node rate, supersedes-at-creation rate

## Embedding model

`all-MiniLM-L6-v2` (general-purpose, 384-dim) is sufficient for technical English project notes. Domain-specific embedding fine-tuning is not needed unless semantic search recall is measurably poor after process improvements are in place.
