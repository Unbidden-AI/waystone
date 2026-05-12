name: engram
version: 0.1.0
description: >
  Persistent knowledge graph memory for OpenClaw. Extracts facts from
  conversations, stores them as a typed DAG, and surfaces relevant context
  automatically. Replaces MEMORY.md with a queryable, conflict-aware graph.
author: engram-ai
license: MIT
install: pip install engram[openclaw]
hooks:
  - on_session_start: engram.openclaw:on_session_start
  - on_turn_end: engram.openclaw:on_turn_end
  - on_session_end: engram.openclaw:on_session_end
  - on_dream: engram.openclaw:on_dream
commands:
  - name: remember
    handler: engram.openclaw:cmd_remember
    description: Extract text into the knowledge graph immediately
    usage: "@claw remember <text>"
  - name: recall
    handler: engram.openclaw:cmd_recall
    description: Search the knowledge graph and return relevant facts
    usage: "@claw recall <query>"
  - name: forget
    handler: engram.openclaw:cmd_forget
    description: Soft-delete graph nodes matching a topic
    usage: "@claw forget <topic>"
  - name: summarize
    handler: engram.openclaw:cmd_summarize
    description: Show all active knowledge graph facts grouped by type
    usage: "@claw summarize"
  - name: sync_now
    handler: engram.openclaw:cmd_sync_now
    description: Force MEMORY.md refresh from the live graph
    usage: "@claw sync_now"
  - name: status
    handler: engram.openclaw:cmd_status
    description: Show graph stats, last sync time, and any errors
    usage: "@claw status"
  - name: dream
    handler: engram.openclaw:cmd_dream
    description: Manually trigger a reflection + reconciliation cycle
    usage: "@claw dream"
  - name: export
    handler: engram.openclaw:cmd_export
    description: Dump full graph to a markdown file
    usage: "@claw export [path]"
requires:
  openclaw: ">=2.0"
  python: ">=3.13"
tags:
  - memory
  - knowledge-graph
  - context
  - local-first
  - privacy
config:
  - key: project
    description: Engram project name
    required: true
    env_var: ENGRAM_PROJECT
  - key: top_k
    description: Max nodes per retrieval query
    required: false
    default: 15
    env_var: ENGRAM_TOP_K
  - key: hops
    description: BFS traversal depth
    required: false
    default: 3
    env_var: ENGRAM_HOPS
  - key: auto_extract
    description: Extract facts from turns automatically
    required: false
    default: true
    env_var: ENGRAM_EXTRACT
  - key: extract_on_session_end_only
    description: "Cost safety: extract once per session instead of per turn"
    required: false
    default: true
  - key: memory_md_path
    description: Path to MEMORY.md file
    required: false
    default: "~/.openclaw/MEMORY.md"
  - key: dry_run
    description: Log what would be extracted without spending LLM tokens
    required: false
    default: false
    env_var: ENGRAM_DRY_RUN
