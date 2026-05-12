# Engram Memory Provider for Hermes Agent

Plugs Engram's DAG-based knowledge graph into [Hermes Agent](https://github.com/nousresearch/hermes-agent) as a first-class memory provider.

- **`prefetch()`** — BFS retrieval injects structured context (decisions, constraints, implementations) before each LLM call
- **`sync_turn()`** — incremental fact extraction runs in the background after each turn; the graph grows automatically
- **Tools** — exposes `engram_query` (semantic BFS search) and `engram_recall` (tag-based lookup) to the Hermes LLM

## Why Engram vs other Hermes memory providers

| | Engram | Mem0 | Hindsight | Honcho |
|---|---|---|---|---|
| Infrastructure | None (SQLite file) | Cloud API | Cloud API | Cloud API |
| Graph structure | DAG + typed edges | Flat cards | Entity graph | Flat |
| Supersession | Yes (structural) | No | Partial | No |
| Temporal validity | Yes (`valid_to`, `occurred_at`) | No | No | No |
| Air-gapped / offline | Yes | No | No | No |
| Open source | Yes | Partial | No | No |

## Installation

### Option A — copy into a Hermes installation

```bash
cp -r hermes_plugin/ /path/to/hermes-agent/plugins/memory/engram/
pip install engram
```

### Option B — install as a package (coming soon)

```bash
pip install engram-hermes
```

## Configuration

### Via `hermes memory setup`

Run `hermes memory setup` and select `engram`. You'll be prompted for:

| Field | Description | Required |
|---|---|---|
| `project` | Engram project name (must already exist: `engram init <project>`) | Yes |
| `db_path` | Explicit path to `context.db` (overrides project lookup) | No |
| `config_path` | Path to Engram `config.yaml` | No |
| `top_k` | Max nodes per retrieval (default: 10) | No |
| `hops` | BFS traversal depth (default: 3) | No |
| `auto_extract` | Extract facts from turns automatically (default: true) | No |

### Via environment variables

```bash
export ENGRAM_PROJECT=my-project
export ENGRAM_TOP_K=15
export ENGRAM_HOPS=3
export ENGRAM_EXTRACT=1   # set to "0" to disable auto-extraction
```

## Setup

1. **Create an Engram project** (if you don't have one):
   ```bash
   engram init my-project
   ```

2. **Optionally seed it** with existing transcripts:
   ```bash
   engram extract my-project transcript.md --verify
   ```

3. **Start Hermes** with the Engram provider selected.

The graph grows automatically as Hermes has conversations — each session's turns are buffered and extracted in the background.

## How it works

```
Hermes LLM call
     │
     ▼ prefetch(query)                     ← BFS retrieval, sub-second
EngramMemoryProvider
     │ returns structured markdown context
     ▼
LLM call with context injected

     │ after response
     ▼ sync_turn(user, assistant)          ← buffer turn content (non-blocking)
ExtractionBuffer
     │ when buffer threshold reached (3 turns or 200 words)
     ▼ background thread
LLM extraction → new nodes merged into graph
```

The extraction LLM call happens in the background; Hermes never blocks waiting for it.

## Contributing

To submit this provider to the upstream Hermes repo:
1. Fork `nousresearch/hermes-agent`
2. Copy `hermes_plugin/` to `plugins/memory/engram/`
3. Submit a PR
