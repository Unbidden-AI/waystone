# Waystone Architecture

## Overview

Waystone is a DAG-based context intelligence layer for LLM workflows. It extracts facts from
conversation transcripts, stores them as a typed knowledge graph, and retrieves relevant
subgraphs given a task description. See `CLAUDE.md` for the command reference and data flow.

---

## Temporal Decay Model

### Background

Memory systems face a core challenge: facts have different lifespans. A preference ("I like
Thai food") may remain valid for years. A transition fact ("switched from S3 to GCS") becomes
irrelevant once the migration is complete. A question node ("still deciding on auth provider")
may be resolved within days. A single global decay rate treats all of these the same.

Waystone's `recency_decay` strategy originally applied one global half-life (`recency_half_life_days`)
to all nodes using exponential decay:

```
score = confidence × 2^(−age / half_life)
```

This was adequate for short-term sessions but underweights fresh information in long-running
knowledge graphs — and provides no principled way to distinguish volatile from stable facts.

### RoMem: Per-Relation Volatility (arxiv:2604.11544)

**Time is Not a Label: RoMem — Continuous Phase Rotation for Temporal KGs**
(Wang et al., 2026)

RoMem introduces two key ideas for temporal knowledge graphs:

**1. Per-relation volatility scores (αᵣ ∈ (0,1))**

Rather than one global decay rate, each relation type (or in Waystone's case, node type) gets
its own volatility score. High-volatility relations decay quickly; low-volatility relations
remain stable for months or years. RoMem learns these scores from data; Waystone assigns them
by reasoning about node-type semantics:

| Node type        | `half_life_by_type` (days) | Rationale |
|------------------|---------------------------|-----------|
| `transition`     | 14                        | "Switched from X to Y" — fact is absorbed into the new state quickly |
| `question`       | 30                        | Open questions get resolved or abandoned within weeks |
| `implementation` | 60                        | Implementation details evolve sprint-to-sprint |
| `preference`     | 90                        | Preferences shift over months |
| `resolved`       | 90                        | Resolution context fades once acted on |
| `decision`       | 180                       | Decisions stand for months but get revisited semi-annually |
| `lesson_learned` | 365                       | Lessons are slow to acquire and slow to expire |
| `constraint`     | 365                       | Hard constraints rarely change |

The global `recency_half_life_days` remains as a fallback for any unlisted type.

**2. Continuous phase rotation**

RoMem replaces the exponential decay curve with a cosine-based formula:

```
score = confidence × cos(age / half_life × π/2), clamped to [0, 1]
```

The key difference:

| Age relative to half_life | Exponential score | Phase rotation score |
|---------------------------|-------------------|----------------------|
| 0 (new)                   | 1.0               | 1.0                  |
| 0.5× half_life            | 0.71              | 0.71                 |
| 1× half_life              | 0.50              | **0.0**              |
| 2× half_life              | 0.25              | **0.0** (clamped)    |

Exponential decay means outdated facts linger indefinitely near zero, still competing
for `top_k` slots. Phase rotation means a fact that has exceeded its half-life is fully
rotated out — it contributes zero weight to retrieval and is naturally excluded by any
`top_k` or `token_budget` cut.

RoMem reported 2–3× MRR improvement on MultiTQ and 85.7% recall on LoCoMo, benchmarked
against HippoRAG and Mem0 as baselines.

### Waystone Implementation

Three RoMem components are implemented as independent ablation flags:

**`half_life_by_type`** (Step 1) — dict mapping node type → half-life days. Wired into
`apply_recency_decay()` in `waystone/retriever.py`. Falls back to `recency_half_life_days`
for unlisted types. Zero code-path changes for configs that don't set it.

**`phase_rotation`** (Step 2) — bool flag. When `True`, `apply_recency_decay()` uses
`cos(age/hl × π/2)` instead of `2^(-age/hl)`. Orthogonal to per-type half-lives; can
be combined or tested alone.

**`soft_supersede`** (Step 3) — replaces `superseded_pruning`'s hard removal with a
score-zeroing pass. Superseded nodes remain in the graph (supporting `--at-time` temporal
queries) but rank at the bottom of every result set, so `top_k` and `token_budget` cuts
naturally exclude them from normal responses. Implemented as `apply_soft_supersede()` in
`waystone/retriever.py`.

### Ablation Configs

Three named configs in `benchmarks/locomo/ablation_configs.py` isolate each component:

- `waystone_romem_typedecay` — per-type half-lives only (Step 1)
- `waystone_romem_phase` — phase rotation only (Step 2)
- `waystone_romem_full` — all three: per-type half-lives + phase rotation + soft supersede

All reuse the `waystone_dedup95` extraction checkpoint. No re-extraction is needed.

To run the ablation:

```bash
python -m benchmarks.locomo.harness \
  --dataset benchmarks/locomo/data/locomo10.json \
  --configs waystone_dedup95 waystone_romem_typedecay waystone_romem_phase waystone_romem_full \
  --split dev \
  --llm-judge \
  --output benchmarks/locomo/results/romem_ablation_$(date +%Y%m%d).json
```

### Memory as Metabolism (arxiv:2604.12034)

**Memory as Metabolism: A Design for Companion Knowledge Systems**
(forthcoming, 2026)

A complementary vision paper proposing five operations for long-term memory systems:
TRIAGE, DECAY, CONTEXTUALIZE, CONSOLIDATE, and AUDIT. The paper's key concern — the
"centrality-protected dominant interpretation" problem, where popular nodes crowd out
minority hypotheses — maps directly to Waystone's planned `minority_protected` flag (not
yet implemented).

Planned additions based on this paper:
- `last_accessed_at` + `access_count` columns on `nodes` (foundation for gravity scoring)
- `gravity` float: blend of access frequency, recency, and in-degree
- `minority_protected` flag: reserves `top_k` slots for low-confidence nodes with
  diverging facts on the same tags
- `waystone audit` command: exposes metabolism state for diagnostics
