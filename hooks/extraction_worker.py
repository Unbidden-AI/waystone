#!/usr/bin/env python3
"""Background extraction worker for Context Broker.

Spawned by context_broker_submit.py as a detached subprocess so extraction
never blocks prompt submission. Reads text from stdin, runs the LLM extraction,
merges results into the project graph, and updates state.json throughout.

State transitions written to state.json:
  extracting  — worker started, includes started_at timestamp
  ok / error  — written after completion (same fields as normal retrieval state)

Usage (internal — called by context_broker_submit.py):
  python extraction_worker.py --project <name> --source <live|assistant>
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

STATE_DIR = Path.home() / ".context-broker"
PAUSE_FILE = STATE_DIR / "paused"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--source", default="live")
    args = parser.parse_args()

    text = sys.stdin.read().strip()
    if not text:
        sys.exit(0)

    STATE_DIR.mkdir(parents=True, exist_ok=True)

    # Signal that extraction is running
    _merge_state({
        "extracting": True,
        "extract_started_at": time.time(),
        "extract_source": args.source,
    })

    try:
        from context_broker.config import load_config
        from context_broker.extractor import extract_turn
        from context_broker.retriever import bfs_collect, extract_keywords, score_by_relevance
        from context_broker.store import GraphStore

        config = load_config()
        db_path = Path(args.db_path)
        inc_cfg = config.get("incremental", {})
        ctx_k = inc_cfg.get("context_k", 30)
        ctx_hops = inc_cfg.get("context_hops", 2)

        store = GraphStore(db_path)
        keywords = extract_keywords(text)
        context_nodes = []
        if keywords:
            entry_nodes = store.get_nodes_by_tags(keywords)
            if entry_nodes:
                entry_nodes = score_by_relevance(entry_nodes, keywords)
                context_nodes = bfs_collect(store, entry_nodes, ctx_hops)
                context_nodes.sort(key=lambda n: n.get("_relevance", 0), reverse=True)
                context_nodes = context_nodes[:ctx_k]
        store.close()

        result = asyncio.run(extract_turn(text, context_nodes, config))
        nodes = result["nodes"]
        edges = result["edges"]
        for node in nodes:
            node.setdefault("source_transcript", args.source)
            node.setdefault("created_at", datetime.now(timezone.utc).isoformat())

        store = GraphStore(db_path)
        store.merge_extraction(nodes, edges)
        stats = store.get_stats()
        store.close()

        elapsed_ms = int((time.time() - _load_state().get("extract_started_at", time.time())) * 1000)
        _merge_state({
            "extracting": False,
            "extract_started_at": None,
            "last_extract_nodes": len(nodes),
            "last_extract_ms": elapsed_ms,
            "nodes_total": stats["node_count"],
        })

    except Exception as e:
        _merge_state({
            "extracting": False,
            "extract_started_at": None,
            "extract_error": str(e),
        })
        sys.exit(1)


def _load_state() -> dict:
    try:
        p = STATE_DIR / "state.json"
        if p.exists():
            return json.loads(p.read_text())
    except Exception:
        pass
    return {}


def _merge_state(updates: dict) -> None:
    """Merge updates into state.json without overwriting unrelated keys."""
    try:
        state = _load_state()
        state.update(updates)
        (STATE_DIR / "state.json").write_text(json.dumps(state))
    except Exception:
        pass


if __name__ == "__main__":
    main()
