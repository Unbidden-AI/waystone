#!/usr/bin/env python3
"""Claude Code UserPromptSubmit hook for Context Broker.

Buffers each user prompt as a conversation turn. When enough text has
accumulated, spawns a background extraction worker (non-blocking) so prompt
submission is never delayed. Then queries the existing graph for context
relevant to the current prompt and injects it via additionalContext.

Extraction is skipped if ~/.context-broker/paused exists. Use:
  ctx pause    # disable extraction
  ctx resume   # re-enable extraction

Project detection:
  Looks for a .context-broker file in the cwd (or any parent directory up
  to home). Falls back to the cwd basename.

Install:
  python hooks/install.py
"""

import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKER = Path(__file__).resolve().parent / "extraction_worker.py"
sys.path.insert(0, str(REPO_ROOT))

STATE_DIR = Path.home() / ".context-broker"
PAUSE_FILE = STATE_DIR / "paused"


def main():
    try:
        hook_input = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    prompt = hook_input.get("prompt", "").strip()
    cwd = hook_input.get("cwd", ".")
    transcript_path = hook_input.get("transcript_path", "")

    if not prompt:
        sys.exit(0)

    STATE_DIR.mkdir(parents=True, exist_ok=True)

    try:
        from context_broker.config import get_db_path, load_config
        from context_broker.extractor import ExtractionBuffer
        from context_broker.retriever import retrieve_with_stats
        from context_broker.store import GraphStore

        config = load_config()
        project = _detect_project(cwd)
        db_path = get_db_path(config, project)
        paused = PAUSE_FILE.exists()

        # Auto-create the project on first use
        store = GraphStore(db_path)
        inc_cfg = config.get("incremental", {})

        if not paused:
            # --- Queue assistant response for background extraction ---
            if transcript_path:
                watermark = store.load_watermark(transcript_path)
                assistant_text, new_watermark = _read_assistant_since(transcript_path, watermark)
                store.save_watermark(transcript_path, new_watermark)
                if assistant_text:
                    _spawn_extraction(assistant_text, project, db_path, source="assistant")

            # --- Buffer user prompt; spawn extraction when threshold met ---
            persisted_turns = store.load_buffer()
            buffer = ExtractionBuffer(
                min_turns=inc_cfg.get("min_turns"),
                min_words=inc_cfg.get("min_words"),
                max_turns=inc_cfg.get("max_turns"),
                short_turn_words=inc_cfg.get("short_turn_words"),
            )
            buffer._turns = list(persisted_turns)
            should_flush = buffer.add(prompt)

            if should_flush:
                flushed_text = buffer.flush()
                store.clear_buffer()
                _spawn_extraction(flushed_text, project, db_path, source="live")
            else:
                store.save_buffer(buffer._turns)
        else:
            # Still update buffer so turns accumulate while paused
            persisted_turns = store.load_buffer()
            buffer = ExtractionBuffer(
                min_turns=inc_cfg.get("min_turns"),
                min_words=inc_cfg.get("min_words"),
                max_turns=inc_cfg.get("max_turns"),
                short_turn_words=inc_cfg.get("short_turn_words"),
            )
            buffer._turns = list(persisted_turns)
            buffer.add(prompt)
            store.save_buffer(buffer._turns)

        # --- Context retrieval (always synchronous — must inject before Claude sees prompt) ---
        graph_stats = store.get_stats()
        total_nodes = graph_stats["node_count"]
        store.close()

        if total_nodes == 0:
            _write_state({
                "project": project,
                "status": "buffering" if not paused else "paused",
                "buffered_turns": len(buffer._turns),
            })
            sys.exit(0)

        store = GraphStore(db_path)
        t0 = time.time()
        defaults = config.get("defaults", {})
        retrieval = retrieve_with_stats(
            store,
            prompt,
            hops=defaults.get("hops", 3),
            top_k=defaults.get("top_k", 25),
            strategies=config.get("strategies", {}),
        )
        elapsed_ms = int((time.time() - t0) * 1000)
        store.close()

        tokens_in_graph = _estimate_graph_tokens(db_path)
        _write_state({
            "project": project,
            "status": "paused" if paused else "ok",
            "nodes_retrieved": retrieval.nodes_after_strategies,
            "nodes_total": total_nodes,
            "tokens_injected": retrieval.tokens_estimated,
            "tokens_in_graph": tokens_in_graph,
            "tokens_filtered": max(0, tokens_in_graph - retrieval.tokens_estimated),
            "elapsed_ms": elapsed_ms,
            "timestamp": time.time(),
        })

        project_dir = get_db_path(config, project).parent
        last_context_path = project_dir / "last_context.md"
        last_context_path.write_text(retrieval.markdown)

        if retrieval.nodes_after_strategies == 0:
            sys.exit(0)

        preamble = (
            f"[Context Broker: retrieved {retrieval.nodes_after_strategies} of {total_nodes} "
            f"graph nodes for project '{project}' (~{retrieval.tokens_estimated} tokens). "
            f"Full context: {last_context_path}]\n\n"
        )
        output = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": preamble + retrieval.markdown,
            }
        }
        print(json.dumps(output))

    except Exception as e:
        _write_state({"status": "error", "error": str(e)})
        sys.exit(0)


def _spawn_extraction(text: str, project: str, db_path: Path, source: str) -> None:
    """Fire-and-forget: spawn the extraction worker as a detached subprocess."""
    try:
        proc = subprocess.Popen(
            [sys.executable, str(WORKER),
             "--project", project,
             "--db-path", str(db_path),
             "--source", source],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # detach so it outlives the hook process
        )
        proc.stdin.write(text.encode("utf-8"))
        proc.stdin.close()
    except Exception:
        pass


MIN_ASSISTANT_WORDS = 40


def _read_assistant_since(transcript_path: str, watermark: int) -> tuple[str, int]:
    """Read assistant text from JSONL lines after watermark."""
    path = Path(transcript_path).expanduser()
    if not path.exists():
        return "", watermark
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return "", watermark

    new_watermark = len(lines)
    text_parts = []
    for raw in lines[watermark:]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except Exception:
            continue
        message = entry.get("message", {})
        if message.get("role") != "assistant":
            continue
        content = message.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "").strip()
                    if text:
                        text_parts.append(text)
        elif isinstance(content, str) and content.strip():
            text_parts.append(content.strip())

    full_text = "\n\n".join(text_parts)
    if len(full_text.split()) < MIN_ASSISTANT_WORDS:
        return "", new_watermark
    return full_text, new_watermark


def _detect_project(cwd: str) -> str:
    cwd_path = Path(cwd).resolve()
    home = Path.home()
    for directory in [cwd_path, *cwd_path.parents]:
        marker = directory / ".context-broker"
        if marker.exists():
            try:
                name = marker.read_text().strip()
                if name:
                    return name
            except Exception:
                pass
        if directory == home:
            break
    return cwd_path.name


def _write_state(state: dict) -> None:
    try:
        # Preserve extracting flag written by worker
        existing = {}
        p = STATE_DIR / "state.json"
        if p.exists():
            try:
                existing = json.loads(p.read_text())
            except Exception:
                pass
        if existing.get("extracting"):
            state["extracting"] = True
            state["extract_started_at"] = existing.get("extract_started_at")
        p.write_text(json.dumps(state))
    except Exception:
        pass


def _estimate_graph_tokens(db_path: Path) -> int:
    import sqlite3
    try:
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT fact FROM nodes").fetchall()
        conn.close()
        return max(1, sum(len(r[0]) for r in rows) // 4)
    except Exception:
        return 0


if __name__ == "__main__":
    main()
