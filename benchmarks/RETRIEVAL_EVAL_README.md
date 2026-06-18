# Retrieval Quality Benchmark Harness

This is the durable, standard eval gate for Waystone retrieval quality. Every change to retrieval strategies, graph extraction, or scoring logic should be validated against this benchmark before merging.

## Purpose

The harness measures recall, node counts, and token efficiency for retrieval against 23 ground-truth eval questions spanning three projects (api_design, auth_system, data_pipeline). It provides:

- **Reproducible baseline:** cached graphs + fixed questions ensure consistent scoring
- **Trustworthiness:** explicit import-path verification, loud failure on missing data, ballpark validation
- **Strategy comparison:** built-in variants (baseline, default, filtered, tight) + extensible for new strategies
- **Audit trail:** JSON + Markdown reports saved to `benchmarks/results/`

## Quick Start

### First run (extracts graphs, requires LLM)
```bash
python benchmarks/run_retrieval_eval.py
```
This will:
1. Load 23 eval questions from `eval_questions.yaml`
2. Extract missing graphs (one LLM call per transcript)
3. Run baseline retrieval strategy
4. Grade recall via ground-truth elements
5. Write results to `benchmarks/results/retrieval_eval_TIMESTAMP/`
6. Validate baseline recall is in ballpark (80–96%)

### Reuse cached graphs (no LLM calls)
```bash
python benchmarks/run_retrieval_eval.py --skip-extract
```
Fails loudly if any project graphs are missing.

### Compare multiple strategies
```bash
python benchmarks/run_retrieval_eval.py --strategies baseline default filtered tight
```

### Tune retrieval params
```bash
python benchmarks/run_retrieval_eval.py --top-k 20 --hops 2
```

### Validation only (no retrieval eval)
```bash
python benchmarks/run_retrieval_eval.py --self-check-only
```
Verifies:
- waystone module imports from correct path (worktree)
- 23 questions load successfully
- All 3 projects have non-empty cached graphs

## Adding a New Strategy Variant

### Option 1: Preset variant (no code change)

Add a dict entry to `STRATEGY_PRESETS` in `run_retrieval_eval.py`:

```python
STRATEGY_PRESETS = {
    # ... existing ...
    "my_variant": {
        "semantic": True,
        "superseded_pruning": True,
        "confidence_threshold": 0.5,  # <-- adjust
        "recency_decay": False,
        "token_budget": 500,          # <-- adjust
        "relevance_scoring": True,
    },
}
```

Then run:
```bash
python benchmarks/run_retrieval_eval.py --strategies baseline my_variant
```

### Option 2: Config-level override

Create a model config YAML that overrides strategy defaults:

```yaml
# benchmarks/model_configs/my_config.yaml
llm:
  model: "models/gemini-2.5-flash"
  api_key_env: "GEMINI_API_KEY"

strategies:
  baseline:
    confidence_threshold: 0.5
  default:
    token_budget: 500
```

The harness loads config overrides and merges them with presets per-strategy.

## Output Format

Results are written to `benchmarks/results/retrieval_eval_TIMESTAMP/`:

```
results.json          # Raw data: graphs used, per-question stats, aggregates
report.md             # Human-readable summary + per-strategy tables
```

### results.json structure
```json
{
  "timestamp": "20260618_141505",
  "hops": 3,
  "top_k": 30,
  "graphs": {
    "project_api_design": 77,
    "project_auth_system": 133,
    "project_data_pipeline": 109
  },
  "query": {
    "baseline": {
      "questions": [
        {
          "question_id": "q_api_01",
          "transcript": "project_api_design",
          "recall": 0.75,
          "matched": ["JWT tokens", "stateless"],
          "missed": ["scales horizontally"],
          "nodes_before_strategies": 47,
          "nodes_after_strategies": 30,
          "tokens_estimated": 848
        }
      ],
      "summary": {
        "mean_recall": 0.88,
        "mean_nodes": 25.3,
        "mean_tokens": 756,
        "questions_80pct_plus": 18,
        "total_questions": 23
      }
    }
  }
}
```

## Trustworthiness Features

### 1. Import Path Verification
On startup, prints the absolute path to the imported `waystone` module:
```
[IMPORT] waystone module: /Users/justinwalton/Apps/ContextBroker/.claude/worktrees/.../waystone/__init__.py
```
This ensures you're testing code from the worktree, not a stale system install.

### 2. Loud Failures
- Missing graph DB → exception with path
- Empty graph (0 nodes) → exception  
- Question references unknown project → exception
- Extraction LLM error → exception with context
- Graph extraction returns 0 nodes → exception

No silent scoring of 0% or skips.

### 3. Ballpark Validation
Baseline recall must land in documented range (80–96%, from MEMORY.md Gemini 2.5 Flash + verify):
- Below 80% → "Possible causes: graph extraction issue, retriever regression, config mismatch"
- Above 96% → "Possible causes: eval questions changed, extraction improved, or grader artifact"

Exit code 0 only if ballpark check passes.

### 4. Coverage Reporting
At end of eval, prints:
- Which questions were graded (count)
- Any skipped + why
- Graphs used + node counts

## Recall Scoring Algorithm

Matching is two-tier:

1. **Full phrase match** — check if ground-truth element appears verbatim in retrieved markdown
2. **Keyword overlap fallback** — if not found, extract non-stop-words, check 70% coverage with morphological variants

Morphological variants handle:
- Hyphenated compounds: "hot-path" also checks "hot" and "path"
- Singular/plural: "minutes" ↔ "minute"

Example:
- GT: "JWT tokens" → check if present, else check keywords ["jwt", "tokens"]
- Retrieved: "The system uses JWT" → keywords ["system", "uses", "jwt"] → 1/2 = 50% match, FAIL
- Retrieved: "JWT and refresh tokens" → keywords ["jwt", "refresh", "tokens"] → 3/3 = 100% match, PASS

## Extension Points (Future)

### Large/Production-Scale Graph Eval
```python
# Set before import
import os
os.environ["WAYSTONE_PROJECTS_DIR"] = "/var/waystone/prod-graphs"

# Harness will use that dir instead of ~/.waystone/projects
```

### Extractor-Quality Eval Mode
```python
# (Not yet implemented)
# Run with a known-good extraction, measure retrieval deltas
# Traces coupling: extractor quality → retrieval recall
# python benchmarks/run_retrieval_eval.py --frozen-extraction <tag>
```

## Known Limitations

- **sqlite-vec not available:** Semantic search disabled on non-POSIX systems (Windows, some WSL). `semantic: true` is ignored; BFS still works via tag matching.
- **LLM extraction:** First run requires GEMINI_API_KEY and network access. Subsequent runs reuse cached graphs.
- **Grader artifacts:** Some ground-truth elements are linguistically similar but not identical (e.g., "moved away from JSON" vs "switched from JSON"). The ballpark accounts for this noise.

## See Also

- `benchmarks/run_benchmark.py` — older full extraction + eval harness (extraction, incremental, buffered modes)
- `benchmarks/compare_baseline.py` — compares Waystone vs raw-transcript retrieval
- `benchmarks/eval_questions.yaml` — ground truth: 23 questions, 3 projects, elements + tags
- `benchmarks/transcripts/` — source transcripts (project_*.md)
