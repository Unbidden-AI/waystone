# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Discord responses

When a message arrives via Discord (`<channel source="discord">`), respond as Flint Lockwood — eccentric inventor from Cloudy with a Chance of Meatballs. Full persona is in Waystone (pinned nodes: flint_persona_voice, flint_persona_principles).

## Commands

```bash
# Install (editable with dev deps)
pip install -e ".[dev]"

# Run all tests
pytest tests/

# Run a single test file
pytest tests/test_store.py

# CLI entry point
waystone --help
waystone init <project>
waystone extract <project> <transcript_file> [--verify] [--lessons] [--decisions] [--questions] [--constraints]
waystone query <project> "<task description>" [--hops N] [--top-k N] [--stats] [--at-time ISO8601]
waystone show <project>
waystone export <project> [-o output.md]

# Session summarization (high-altitude narrative — see "Session summarization" below)
waystone story <project> [--session ID] [--limit N]              # replay the session_summary timeline (incl. superseded)
waystone catchup-summarize <project> [--window N] [--sessions N]  # back-fill a project's story from saved transcripts
                          [--replace/--append] [--dry-run]
waystone summarize-session <project> <transcript> [--every N] [--max-windows N] [--dry-run]

# Maintenance
waystone doctor [--fix]              # preflight: config, LLM, marker, MCP, sqlite-vec capability
waystone dedup <project> [--execute] # merge semantically duplicate nodes
waystone prune <project> [--meta-noise] [--execute]
```

## Architecture

Waystone is a DAG-based context intelligence layer for LLM workflows. It extracts facts from conversation transcripts, stores them as a graph, and retrieves relevant subgraphs given a task description.

**Data flow:**

1. **Extract** — `waystone extract` sends a transcript to an OpenAI-compatible LLM (`extractor.py`). The LLM returns JSON with nodes (facts) and edges (relations). Short LLM-assigned IDs (`n1`, `n2`) are replaced with `n_<uuid8>` via `assign_ids()`. Nodes and edges are merged into SQLite via `GraphStore.merge_extraction()`.
   - `--verify`: runs a second LLM pass hunting for missed secondary details, buried numerics, transition statements, and rationale with time estimates
   - `--lessons`, `--decisions`, `--questions`, `--constraints`: run targeted extraction passes focused on a single category (implemented in `extract_targeted()` in `extractor.py`). Each pass sees existing nodes to avoid re-extracting them. Useful for improving recall on specific node types without touching the main prompt.

2. **Store** — `store.py` wraps SQLite with two tables: `nodes` (id, fact, type, confidence, tags JSON, supersedes JSON, source info, occurred_at, valid_to, is_active) and `edges` (from_id, to_id, relation). The `supersedes` relationship is tracked both as an edge and as a field on the superseding node.

3. **Retrieve** — `waystone query` calls `retriever.py`:
   - Keywords are extracted from the task description (stop-word filtered)
   - Entry nodes are found via tag matching (`get_nodes_by_tags` uses JSON LIKE queries)
   - BFS traversal up to `hops` depth collects the neighborhood (both outgoing and incoming edges)
   - A strategy pipeline is applied in order: `superseded_pruning` → `confidence_threshold` → `recency_decay` → `top_k` sort → `token_budget`
   - Results are assembled into grouped markdown by node type (decision > transition > constraint > implementation > resolved > preference > question)

**Configuration** (`config.yaml` or `~/.waystone/config.yaml`):
- `llm`: OpenAI-compatible endpoint (default: `http://localhost:1234/v1`)
- `defaults`: `hops`, `top_k`, `format`
- `strategies`: toggles for each reduction strategy (all overridable per-query via `--enable`/`--disable`)
- `projects_dir`: where project subdirectories are created (default: `./projects`)
- `session_summary`: live rolling-summary controls — `enabled` (true), `cadence_turns` (5; summarize every N turns), `context_turns` (30; recent turns fed each fire), `retries` (2; on empty/transient LLM responses), `inject` (true; lead per-prompt context with the latest summary)

Config is deep-merged with hardcoded defaults in `config.py`; missing keys fall back to defaults.

**Strategy pipeline** (all in `retriever.py`):
- `superseded_pruning`: drops nodes that have a `supersedes` edge pointing at them
- `confidence_threshold`: filters nodes below a float threshold
- `recency_decay`: multiplies confidence by `2^(-age_days / half_life_days)`, stored as `_score`
- `temporal_valid_at`: filters to nodes valid at a specific past timestamp (`--at-time`); auto-enabled for temporal queries
- `token_budget`: greedy packing by estimated tokens (~4 chars/token)
- `relevance_scoring`: ranks BFS entry nodes by tag overlap count before traversal

**Benchmarks** (`benchmarks/`): synthetic transcripts for three projects (api_design, auth_system, data_pipeline) with ground-truth eval questions in `eval_questions.yaml` for measuring precision/recall across strategy configurations.

**Node types** (extractor palette): `decision`, `transition`, `constraint`, `implementation`, `question`, `resolved`, `lesson_learned`, `preference`

**`session_summary`** is a SYSTEM-GENERATED type — NOT in the extractor's palette (removed in 0.4.31 to stop the per-turn extractor minting thin one-liners). It is created only by the live rolling-summary worker, `catchup-summarize`/`summarize-session`, and host-recap (away_summary) ingestion, all via `store.add_node` directly. It is first in the retriever's `type_order`.

**Edge relations**: `depends_on`, `flows_to`, `relates_to`, `supersedes`

## Session summarization

A model-agnostic layer that captures session-level NARRATIVE (goal · arc · current state · next) — the altitude atomic fact-extraction misses. Stored as `session_summary` nodes; each new summary **supersedes** the prior for that session, so retrieval surfaces the latest while the full timeline is KEPT (bi-temporal: `is_active=0`, never deleted).

- **Live (passive):** the Stop hook counts turns per session and every `cadence_turns` (default 5) spawns a detached worker (`waystone/_hooks/summarize.py`, console script `waystone-hook-summarize`) that folds the prior summary + the last `context_turns` (default 30) into an updated narrative. `generate_session_summary()` in `extractor.py` is the bounded incremental call (prior summary + new window → ~3k input / ≤512 output, O(1) regardless of session length; retries on empty/transient responses). Fail-open; respects `~/.waystone/paused`.
- **Injection:** the UserPromptSubmit hook leads injected context with a "Where we are (session narrative)" block — the latest active `session_summary`, fetched directly since its generic tags won't surface via keyword BFS (`_latest_session_narrative` in `submit.py`; toggle `session_summary.inject`).
- **Back-fill:** `waystone catchup-summarize <project>` walks a project's saved transcripts (`~/.waystone/transcripts/<project>/`) chronologically and builds the rolling chain after the fact (a chapter per session). `waystone summarize-session` does the same for one transcript file.
- **Read:** `waystone story <project>` replays the WHOLE timeline including superseded chapters (supersession keeps history; `story` presents it).
