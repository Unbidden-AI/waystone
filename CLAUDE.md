# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (editable with dev deps)
pip install -e ".[dev]"

# Run all tests
pytest tests/

# Run a single test file
pytest tests/test_store.py

# CLI entry point
ctx --help
ctx init <project>
ctx extract <project> <transcript_file> [--verify] [--lessons] [--decisions] [--questions] [--constraints]
ctx query <project> "<task description>" [--hops N] [--top-k N] [--stats]
ctx show <project>
ctx export <project> [-o output.md]
```

## Architecture

Context Broker is a DAG-based context intelligence layer for LLM workflows. It extracts facts from conversation transcripts, stores them as a graph, and retrieves relevant subgraphs given a task description.

**Data flow:**

1. **Extract** — `ctx extract` sends a transcript to an OpenAI-compatible LLM (`extractor.py`). The LLM returns JSON with nodes (facts) and edges (relations). Short LLM-assigned IDs (`n1`, `n2`) are replaced with `n_<uuid8>` via `assign_ids()`. Nodes and edges are merged into SQLite via `GraphStore.merge_extraction()`.
   - `--verify`: runs a second LLM pass hunting for missed secondary details, buried numerics, transition statements, and rationale with time estimates
   - `--lessons`, `--decisions`, `--questions`, `--constraints`: run targeted extraction passes focused on a single category (implemented in `extract_targeted()` in `extractor.py`). Each pass sees existing nodes to avoid re-extracting them. Useful for improving recall on specific node types without touching the main prompt.

2. **Store** — `store.py` wraps SQLite with two tables: `nodes` (id, fact, type, confidence, tags JSON, supersedes JSON, source info) and `edges` (from_id, to_id, relation). The `supersedes` relationship is tracked both as an edge and as a field on the superseding node.

3. **Retrieve** — `ctx query` calls `retriever.py`:
   - Keywords are extracted from the task description (stop-word filtered)
   - Entry nodes are found via tag matching (`get_nodes_by_tags` uses JSON LIKE queries)
   - BFS traversal up to `hops` depth collects the neighborhood (both outgoing and incoming edges)
   - A strategy pipeline is applied in order: `superseded_pruning` → `confidence_threshold` → `recency_decay` → `top_k` sort → `token_budget`
   - Results are assembled into grouped markdown by node type (decision > constraint > implementation > resolved > preference > question)

**Configuration** (`config.yaml` or `~/.context-broker/config.yaml`):
- `llm`: OpenAI-compatible endpoint (default: `http://localhost:1234/v1`)
- `defaults`: `hops`, `top_k`, `format`
- `strategies`: toggles for each reduction strategy (all overridable per-query via `--enable`/`--disable`)
- `projects_dir`: where project subdirectories are created (default: `./projects`)

Config is deep-merged with hardcoded defaults in `config.py`; missing keys fall back to defaults.

**Strategy pipeline** (all in `retriever.py`):
- `superseded_pruning`: drops nodes that have a `supersedes` edge pointing at them
- `confidence_threshold`: filters nodes below a float threshold
- `recency_decay`: multiplies confidence by `2^(-age_days / half_life_days)`, stored as `_score`
- `token_budget`: greedy packing by estimated tokens (~4 chars/token)
- `relevance_scoring`: ranks BFS entry nodes by tag overlap count before traversal

**Benchmarks** (`benchmarks/`): synthetic transcripts for three projects (api_design, auth_system, data_pipeline) with ground-truth eval questions in `eval_questions.yaml` for measuring precision/recall across strategy configurations.

**Node types**: `decision`, `constraint`, `implementation`, `question`, `resolved`, `lesson_learned`, `preference`

**Edge relations**: `depends_on`, `flows_to`, `relates_to`, `supersedes`
