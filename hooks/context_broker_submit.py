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
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKER = Path(__file__).resolve().parent / "extraction_worker.py"
sys.path.insert(0, str(REPO_ROOT))

STATE_DIR = Path.home() / ".context-broker"
PAUSE_FILE = STATE_DIR / "paused"
SESSION_STATE_MAX_CHARS = 2400  # ~600 tokens
SESSION_STATE_TTL_SECONDS = 600  # fallback expiry: 10 minutes

_SESSION_STATE_TS_RE = re.compile(r'^\[[\d:]+\|ts=(\d+)\]')


_MEASUREMENT_RE = re.compile(
    r'\b\d[\d,\.]*\s*(?:ms|s|%|tokens?|nodes?|edges?|KB|MB|GB|[KM])\b'
)
_FILE_PATH_RE = re.compile(r'`[^`\s]{3,60}\.[a-zA-Z]{1,6}`')
_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z])')


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences on './?/!' followed by a capital letter."""
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]


def _is_fragment(s: str) -> bool:
    """True if the string looks like it starts mid-sentence."""
    return bool(s) and s[0].islower()


def _is_duplicate(candidate: str, seen: list[str], threshold: float = 0.6) -> bool:
    """True if candidate shares >threshold of its words with any seen string."""
    cwords = set(candidate.lower().split())
    if not cwords:
        return False
    for s in seen:
        swords = set(s.lower().split())
        overlap = len(cwords & swords) / len(cwords)
        if overlap > threshold:
            return True
    return False


def _heuristic_extract(text: str) -> str:
    """Extract key facts heuristically (~5ms, no LLM). Returns bullet list string.

    Extracts sentence-complete units in priority order:
      1. Sentences containing measurements (numbers + units)
      2. Markdown headings
      3. Sentences containing backtick file paths
      4. First sentence of long (3+) paragraphs
    Deduplicates by word overlap and enforces a ~400-token output budget.
    """
    # Bucket by priority; each entry is (priority, text)
    candidates: list[tuple[int, str]] = []

    all_sentences = _split_sentences(text)

    # Priority 1: sentences with measurements
    for sentence in all_sentences:
        if _MEASUREMENT_RE.search(sentence) and 15 < len(sentence) < 200:
            if not _is_fragment(sentence):
                candidates.append((1, sentence))

    # Priority 2: markdown headings (already clean, always kept)
    for m in re.finditer(r'^#{1,3} .+', text, re.MULTILINE):
        candidates.append((2, m.group(0).strip()))

    # Priority 3: sentences containing file paths
    for sentence in all_sentences:
        if _FILE_PATH_RE.search(sentence) and 10 < len(sentence) < 200:
            if not _is_fragment(sentence):
                candidates.append((3, sentence))

    # Priority 4: first sentence of long paragraphs
    for para in re.split(r'\n{2,}', text):
        para = para.strip()
        if not para or para[0] in ('#', '|', '`', '-', '*'):
            continue
        sentences = _split_sentences(para)
        if len(sentences) >= 3 and not _is_fragment(sentences[0]):
            candidates.append((4, sentences[0]))

    if not candidates:
        return ""

    # Sort by priority, then select with dedup and token budget
    candidates.sort(key=lambda x: x[0])
    selected: list[str] = []
    total_chars = 0
    for _, text_item in candidates:
        if total_chars + len(text_item) > 1600:
            break
        if not _is_duplicate(text_item, selected):
            selected.append(text_item)
            total_chars += len(text_item)

    return "\n".join(f"- {s}" for s in selected)


def _update_session_state(session_state_path: Path, new_extract: str) -> None:
    """Prepend timestamped heuristic extract to rolling session state file."""
    existing = session_state_path.read_text(encoding="utf-8") if session_state_path.exists() else ""

    clock = datetime.now().strftime("%H:%M")
    ts = int(time.time())
    entry = f"[{clock}|ts={ts}]\n{new_extract}"
    combined = f"{entry}\n\n{existing}".strip() if existing else entry

    # Trim to max, keeping most recent (top)
    if len(combined) > SESSION_STATE_MAX_CHARS:
        trimmed = combined[:SESSION_STATE_MAX_CHARS]
        last_nl = trimmed.rfind("\n")
        if last_nl > SESSION_STATE_MAX_CHARS // 2:
            trimmed = trimmed[:last_nl]
        combined = trimmed

    session_state_path.write_text(combined, encoding="utf-8")


def _read_active_session_state(session_state_path: Path) -> str:
    """Read session state, filtering out entries already processed by Tier 2 extraction.

    An entry is expired when either:
    - Its timestamp predates the last successful extraction for this project
      (i.e. the LLM has already ingested that content into the graph), OR
    - It is older than SESSION_STATE_TTL_SECONDS (fallback for extraction failures).
    """
    if not session_state_path.exists():
        return ""
    raw = session_state_path.read_text(encoding="utf-8").strip()
    if not raw:
        return ""

    # Load the per-project last-extraction timestamp
    last_extracted_at = 0.0
    last_extract_at_path = session_state_path.parent / "last_extract_at"
    try:
        if last_extract_at_path.exists():
            last_extracted_at = float(last_extract_at_path.read_text().strip())
    except Exception:
        pass

    cutoff = max(last_extracted_at, time.time() - SESSION_STATE_TTL_SECONDS)

    active_blocks = []
    for block in re.split(r'\n{2,}', raw):
        block = block.strip()
        if not block:
            continue
        m = _SESSION_STATE_TS_RE.match(block)
        if m:
            if float(m.group(1)) > cutoff:
                active_blocks.append(block)
            # else: expired — silently drop
        else:
            # Legacy format without timestamp — keep conservatively
            active_blocks.append(block)

    return "\n\n".join(active_blocks)


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
                    # Tier 1: heuristic extract for immediate session state (no LLM, ~5ms)
                    heuristic = _heuristic_extract(assistant_text)
                    session_state_path = db_path.parent / "session_state.md"
                    if heuristic:
                        _update_session_state(session_state_path, heuristic)
                    # Tier 2: spawn background LLM extraction, guided by Tier 1 sentences
                    _spawn_extraction(
                        assistant_text, project, db_path, source="assistant",
                        hints_path=session_state_path if heuristic else None,
                    )

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
        additional_context = preamble + retrieval.markdown

        session_state_path = db_path.parent / "session_state.md"
        session_state = _read_active_session_state(session_state_path)
        if session_state:
            additional_context += "\n\n## Recent session activity\n" + session_state

        output = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": additional_context,
            }
        }
        print(json.dumps(output))

    except Exception as e:
        _write_state({"status": "error", "error": str(e)})
        sys.exit(0)


def _spawn_extraction(
    text: str, project: str, db_path: Path, source: str, hints_path: Path | None = None
) -> None:
    """Fire-and-forget: spawn the extraction worker as a detached subprocess."""
    try:
        cmd = [sys.executable, str(WORKER),
               "--project", project,
               "--db-path", str(db_path),
               "--source", source]
        if hints_path is not None and hints_path.exists():
            cmd += ["--hints-path", str(hints_path)]
        proc = subprocess.Popen(
            cmd,
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
