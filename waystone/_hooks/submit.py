#!/usr/bin/env python3
"""Claude Code UserPromptSubmit hook for Waystone.

Buffers each user prompt as a conversation turn. When enough text has
accumulated, spawns a background extraction worker (non-blocking) so prompt
submission is never delayed. Then queries the existing graph for context
relevant to the current prompt and injects it via additionalContext.

Extraction is skipped if ~/.waystone/paused exists. Use:
  waystone pause    # disable extraction
  waystone resume   # re-enable extraction

Project detection:
  Looks for a .waystone file in the cwd (or any parent directory up
  to home). Falls back to the cwd basename.

Install:
  python hooks/install.py
"""

import html
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from .._logging import hook_entry

WORKER = Path(__file__).resolve().parent / "worker.py"


# Load project-local .env (e.g. GEMINI_API_KEY) before any Waystone imports.
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(dotenv_path=Path.home() / ".waystone" / ".env", override=True)
except ImportError:
    pass

STATE_DIR = Path.home() / ".waystone"
PAUSE_FILE = STATE_DIR / "paused"
SESSION_STATE_MAX_CHARS = 2400  # ~600 tokens
SESSION_STATE_TTL_SECONDS = 600  # fallback expiry: 10 minutes

REFLECT_INTERVAL = 20  # Fire reflect every N new user+assistant turns

_SESSION_STATE_TS_RE = re.compile(r'^\[[\d:]+\|ts=(\d+)\]')
_CHANNEL_TAG_RE = re.compile(r'<channel\b[^>]*>(.*?)</channel>', re.DOTALL)


def _strip_channel_wrapper(text: str) -> str:
    """Strip <channel source="..."> wrappers injected by Discord/Telegram plugins.

    Extracts the inner message text and HTML-unescapes it.
    """
    def _replace(m: re.Match) -> str:
        return html.unescape(m.group(1).strip())

    result = _CHANNEL_TAG_RE.sub(_replace, text)
    return html.unescape(result)


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
            last_extracted_at = float(last_extract_at_path.read_text(encoding="utf-8").strip())
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


def _latest_session_narrative(store, session_id: str = "") -> str:
    """Return the freshest active session_summary fact (the project's 'where we are').

    Rolling session summaries carry generic tags, so keyword-driven retrieval won't
    surface them — this fetches them directly. Prefers the CURRENT session's live
    summary (continuity within a session); otherwise falls back to the most recent
    active session_summary across the project (cross-session orientation). Returns ""
    if none exists. Best-effort: never raises.
    """
    try:
        nodes = [n for n in store.get_nodes_by_type("session_summary")
                 if n.get("is_active") == 1 and (n.get("fact") or "").strip()]
        if not nodes:
            return ""
        nodes.sort(key=lambda n: n.get("created_at", ""), reverse=True)
        if session_id:
            for n in nodes:
                if n.get("source_transcript") == f"live_session:{session_id}":
                    return n["fact"].strip()
        return nodes[0]["fact"].strip()
    except Exception:
        return ""


@hook_entry
def main():
    try:
        from waystone._io import force_utf8
        force_utf8()
    except Exception:
        pass
    try:
        hook_input = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    prompt = _strip_channel_wrapper(hook_input.get("prompt", "").strip())
    cwd = hook_input.get("cwd", ".")
    transcript_path = hook_input.get("transcript_path", "")
    session_id = hook_input.get("session_id", "")

    if not prompt:
        sys.exit(0)

    STATE_DIR.mkdir(parents=True, exist_ok=True)

    try:
        from waystone.config import get_db_path, load_config
        from waystone.extractor import ExtractionBuffer
        from waystone.retriever import retrieve_with_stats
        from waystone.store import GraphStore

        config = load_config()
        project = _detect_project(cwd)

        # Remote (Team Server) mode is a fully separate path — local users unaffected.
        from waystone.config import is_remote
        if is_remote(config):
            _run_remote(config, project, prompt, transcript_path, session_id)
            return

        db_path = get_db_path(config, project)
        paused = PAUSE_FILE.exists()

        # Auto-create the project on first use.
        # vec not needed for buffer/watermark/stats — skip the 49ms extension load.
        store = GraphStore(db_path, vec_enabled=False)
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
                        session_id=session_id,
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
                _spawn_extraction(flushed_text, project, db_path, source="live", session_id=session_id)
            else:
                store.save_buffer(buffer._turns)

            # --- Auto-extract any new chat-plugin attachment files (message.txt) ---
            _extract_new_inbox_attachments(cwd, project, db_path, session_id=session_id)

            # --- Check if reflect should be triggered ---
            _maybe_trigger_reflect(project, transcript_path)
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
        # Token estimate: use cached value from previous state when the DB hasn't changed
        # (avoids a 9ms SUM(LENGTH(fact)) scan on every prompt).
        # Falls back to a full scan if the state is missing or the DB is newer.
        _tokens_in_graph = _read_cached_tokens(session_id, db_path)
        if _tokens_in_graph is None:
            _tokens_in_graph = _estimate_graph_tokens_from_store(store)

        if total_nodes == 0:
            store.close()
            _write_state({
                "project": project,
                "status": "buffering" if not paused else "paused",
                "buffered_turns": len(buffer._turns),
            }, session_id=session_id)
            sys.exit(0)

        t0 = time.time()
        defaults = config.get("defaults", {})
        _strategies = config.get("strategies", {})
        _hops = defaults.get("hops", 3)
        _top_k = defaults.get("top_k", 25)

        # Retrieval runs inline (no thread) — semantic is always False in hook mode,
        # so there's no 3.5s cold-load risk. The existing store connection is reused
        # directly, avoiding a second GraphStore open and ~25ms thread overhead.
        retrieval = retrieve_with_stats(
            store, prompt,
            hops=_hops, top_k=_top_k, strategies=_strategies,
        )

        # P4: lead the injected context with the latest rolling session summary —
        # keyword retrieval won't surface it (generic tags), but it's the highest-
        # altitude orientation ("where we are"). Captured before the store closes.
        narrative = ""
        if (config.get("session_summary", {}) or {}).get("inject", True):
            narrative = _latest_session_narrative(store, session_id)

        store.close()

        elapsed_ms = int((time.time() - t0) * 1000)

        tokens_in_graph = _tokens_in_graph
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
        }, session_id=session_id)

        project_dir = get_db_path(config, project).parent
        last_context_path = project_dir / "last_context.md"

        narrative_block = (
            "## Where we are (session narrative)\n" + narrative + "\n\n"
        ) if narrative else ""

        # Inject if we have EITHER keyword hits or a session narrative.
        if retrieval.nodes_after_strategies == 0 and not narrative_block:
            sys.exit(0)

        preamble = (
            f"[Waystone: retrieved {retrieval.nodes_after_strategies} of {total_nodes} "
            f"graph nodes for project '{project}' (~{retrieval.tokens_estimated} tokens). "
            f"Full context: {last_context_path}]\n\n"
        )
        additional_context = preamble + narrative_block + retrieval.markdown

        prior_turns_window = inc_cfg.get("prior_turns_window", 0)
        if prior_turns_window and transcript_path:
            recent_turns = _read_recent_turns(transcript_path, prior_turns_window)
            if recent_turns:
                additional_context += "\n\n## Recent Conversation\n" + recent_turns

        session_state_path = db_path.parent / "session_state.md"
        session_state = _read_active_session_state(session_state_path)
        if session_state:
            additional_context += "\n\n## Recent session activity\n" + session_state

        last_context_path.write_text(additional_context, encoding="utf-8")

        output = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": additional_context,
            }
        }
        print(json.dumps(output))

    except Exception as e:
        _write_state({"status": "error", "error": str(e)}, session_id=session_id)
        sys.exit(0)


def _extract_new_inbox_attachments(
    cwd: str, project: str, db_path: Path, session_id: str = "",
) -> None:
    """Extract chat-plugin message.txt attachments that haven't been ingested yet.

    Long Discord/Telegram messages arrive as text-file attachments; their content
    never reaches the prompt (which shows only "(attachment)"). This scans the
    plugin inbox for new ``.txt`` files and spawns background extraction for each,
    so they land in the graph like any other turn. A per-project ledger prevents
    re-extracting the same file.

    Note: a file downloaded mid-turn is picked up on the next prompt (the hook
    runs before the agent downloads the attachment), which is the soonest a
    self-contained hook can see it.
    """
    try:
        inbox_dirs = [
            Path(cwd) / ".claude" / "discord" / "inbox",
            Path(cwd) / ".claude" / "telegram" / "inbox",
        ]
        candidates: list[Path] = []
        for d in inbox_dirs:
            if d.is_dir():
                candidates.extend(sorted(d.glob("*.txt")))
        if not candidates:
            return

        ledger_path = db_path.parent / "extracted_inbox.json"
        try:
            ledger = set(json.loads(ledger_path.read_text(encoding="utf-8"))) if ledger_path.exists() else set()
        except Exception:
            ledger = set()

        new_ledger = set(ledger)
        for f in candidates:
            if f.name in ledger:
                continue
            new_ledger.add(f.name)  # mark regardless so we never retry a bad file
            try:
                content = f.read_text(encoding="utf-8", errors="replace").strip()
            except Exception:
                content = ""
            if not content or len(content) > 200_000:
                continue
            _spawn_extraction(content, project, db_path, source=f.name, session_id=session_id)

        if new_ledger != ledger:
            try:
                ledger_path.write_text(json.dumps(sorted(new_ledger)), encoding="utf-8")
            except Exception:
                pass
    except Exception:
        pass


def _spawn_extraction(
    text: str, project: str, db_path: Path, source: str, hints_path: Path | None = None,
    session_id: str = "", remote: bool = False,
) -> None:
    """Fire-and-forget: spawn the extraction worker as a detached subprocess.

    remote=True makes the worker POST to the Team Server instead of extracting
    and merging into a local graph.
    """
    try:
        cmd = [sys.executable, str(WORKER),
               "--project", project,
               "--db-path", str(db_path),
               "--source", source]
        if remote:
            cmd += ["--remote"]
        if hints_path is not None and hints_path.exists():
            cmd += ["--hints-path", str(hints_path)]
        if session_id:
            cmd += ["--session-id", session_id]
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


def _read_recent_turns(transcript_path: str, n: int) -> str:
    """Return the last n user+assistant turns from the session JSONL as plain text."""
    path = Path(transcript_path).expanduser()
    if not path.exists() or n <= 0:
        return ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return ""

    turns: list[str] = []
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except Exception:
            continue
        message = entry.get("message", {})
        role = message.get("role")
        if role not in ("user", "assistant"):
            continue
        content = message.get("content", [])
        text = ""
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "").strip()
                    break
        elif isinstance(content, str):
            text = content.strip()
        if text:
            label = "User" if role == "user" else "Assistant"
            if role == "user":
                text = _strip_channel_wrapper(text)
            turns.append(f"{label}: {text}")

    tail = turns[-n:] if n < len(turns) else turns
    return "\n".join(tail)


def _remote_state_path(project: str) -> Path:
    """Client-side buffer/watermark for remote mode (no local graph DB)."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", project) or "project"
    return STATE_DIR / "remote" / f"{safe}.json"


def _load_remote_state(project: str) -> dict:
    try:
        p = _remote_state_path(project)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_remote_state(project: str, state: dict) -> None:
    try:
        p = _remote_state_path(project)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass


def _run_remote(config: dict, project: str, prompt: str,
                transcript_path: str, session_id: str) -> None:
    """Remote (Team Server) submit path — fully isolated from the local-store path.

    READ: inject shared context via the API (/query). WRITE: extract the assistant's
    new turn per-prompt and batch user prompts, both routed to the server via a
    detached `--remote` worker. Buffer/watermark live in a small JSON file, so no
    local graph DB is created. Always fail-open: never block the prompt.
    """
    import asyncio

    from waystone.config import get_db_path, make_remote_client
    from waystone.extractor import ExtractionBuffer

    paused = PAUSE_FILE.exists()
    db_path = get_db_path(config, project)  # local path for state files only (no graph)
    inc_cfg = config.get("incremental", {})

    if not paused:
        state = _load_remote_state(project)

        # Assistant's new turn → extract per-turn (mirrors the local path).
        if transcript_path:
            assistant_text, new_wm = _read_assistant_since(
                transcript_path, int(state.get("watermark", 0)))
            state["watermark"] = new_wm
            if assistant_text:
                _spawn_extraction(assistant_text, project, db_path,
                                  source="assistant", session_id=session_id, remote=True)

        # Buffer user prompts; flush to the server when the threshold is met.
        buffer = ExtractionBuffer(
            min_turns=inc_cfg.get("min_turns"),
            min_words=inc_cfg.get("min_words"),
            max_turns=inc_cfg.get("max_turns"),
            short_turn_words=inc_cfg.get("short_turn_words"),
        )
        buffer._turns = list(state.get("buffer", []))
        if buffer.add(prompt):
            flushed = buffer.flush()
            state["buffer"] = []
            _spawn_extraction(flushed, project, db_path,
                              source="live", session_id=session_id, remote=True)
        else:
            state["buffer"] = buffer._turns
        _save_remote_state(project, state)

    # Context injection from the shared graph. Cap the timeout HARD (~8s): this runs
    # synchronously before the prompt reaches Claude, so a slow/hung server must never
    # stall submission. The (backgrounded) extraction worker keeps the full timeout.
    defaults = config.get("defaults", {})
    _llm = config.get("llm", {})
    hook_cfg = {**config, "llm": {**_llm, "timeout": min(_llm.get("timeout", 120.0), 8.0)}}
    client = make_remote_client(hook_cfg)
    try:
        result = asyncio.run(client.query(
            project, prompt,
            hops=defaults.get("hops", 3), top_k=defaults.get("top_k", 25)))
    except Exception:
        sys.exit(0)  # fail-open — a server hiccup must never block the prompt

    markdown = result.get("markdown", "")
    nodes_returned = result.get("nodes_returned", 0)
    total_nodes = result.get("total_nodes", 0)
    tokens = result.get("tokens_estimated", 0)

    _write_state({
        "project": project,
        "status": "paused" if paused else "ok",
        "nodes_retrieved": nodes_returned,
        "nodes_total": total_nodes,
        "tokens_injected": tokens,
        "remote": True,
        "timestamp": time.time(),
    }, session_id=session_id)

    if nodes_returned == 0 or not markdown:
        sys.exit(0)

    preamble = (
        f"[Waystone: retrieved {nodes_returned} of {total_nodes} graph nodes "
        f"for project '{project}' (~{tokens} tokens, remote).]\n\n"
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": preamble + markdown,
        }
    }))
    sys.exit(0)


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
        marker = directory / ".waystone"
        if marker.exists():
            try:
                name = marker.read_text(encoding="utf-8").strip()
                if name:
                    return name
            except Exception:
                pass
        if directory == home:
            break
    return cwd_path.name


def _write_state(state: dict, session_id: str = "") -> None:
    try:
        # Preserve extracting flag written by worker
        existing = {}
        if session_id:
            p = STATE_DIR / "state" / f"{session_id}.json"
            p.parent.mkdir(parents=True, exist_ok=True)
        else:
            p = STATE_DIR / "state.json"
        if p.exists():
            try:
                existing = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        if existing.get("extracting"):
            state["extracting"] = True
            state["extract_started_at"] = existing.get("extract_started_at")
        p.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass


def _read_cached_tokens(session_id: str, db_path) -> int | None:
    """Return cached tokens_in_graph from the previous state file if still valid.

    Valid means: the state file is newer than context.db (no extraction ran since
    the last hook invocation). Returns None on any cache miss so the caller falls
    back to the full SUM(LENGTH(fact)) scan.
    """
    try:
        if session_id:
            p = STATE_DIR / "state" / f"{session_id}.json"
        else:
            p = STATE_DIR / "state.json"
        if not p.exists():
            return None
        state_mtime = p.stat().st_mtime
        db_mtime = db_path.stat().st_mtime
        if db_mtime > state_mtime:
            return None  # DB was modified (extraction ran) — must recompute
        cached = json.loads(p.read_text(encoding="utf-8")).get("tokens_in_graph")
        return int(cached) if cached else None
    except Exception:
        return None


def _estimate_graph_tokens_from_store(store) -> int:
    """Estimate total tokens using an already-open GraphStore connection."""
    try:
        total_chars = store.conn.execute("SELECT SUM(LENGTH(fact)) FROM nodes").fetchone()[0] or 0
        return max(1, total_chars // 4)
    except Exception:
        return 0


def _estimate_graph_tokens(db_path: Path) -> int:
    """Estimate total tokens in the graph without fetching all fact text."""
    import sqlite3
    try:
        conn = sqlite3.connect(str(db_path))
        total_chars = conn.execute("SELECT SUM(LENGTH(fact)) FROM nodes").fetchone()[0] or 0
        conn.close()
        return max(1, total_chars // 4)
    except Exception:
        return 0


def _count_utterances(transcript_path: str) -> int:
    """Count user + assistant turns in Claude JSONL session format.

    Returns the total number of utterances (role in ("user", "assistant")).
    """
    path = Path(transcript_path).expanduser()
    if not path.exists():
        return 0

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return 0

    count = 0
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except Exception:
            continue
        message = entry.get("message", {})
        if message.get("role") in ("user", "assistant"):
            count += 1
    return count


def _get_reflect_watermark_path(project: str) -> Path:
    """Get the path to the reflect watermark file for a project."""
    watermark_dir = STATE_DIR / "projects" / project
    watermark_dir.mkdir(parents=True, exist_ok=True)
    return watermark_dir / ".reflect_watermark"


def _read_reflect_watermark(project: str) -> int:
    """Read the reflect watermark (turn count) for a project. Default 0 if missing."""
    watermark_path = _get_reflect_watermark_path(project)
    if not watermark_path.exists():
        return 0
    try:
        return int(watermark_path.read_text(encoding="utf-8").strip())
    except Exception:
        return 0


def _write_reflect_watermark(project: str, watermark: int) -> None:
    """Write the reflect watermark (turn count) for a project."""
    watermark_path = _get_reflect_watermark_path(project)
    try:
        watermark_path.write_text(str(watermark), encoding="utf-8")
    except Exception:
        pass


def _spawn_reflect(project: str, transcript_path: str, since_turn: int) -> None:
    """Fire-and-forget: spawn reflect as a non-blocking background subprocess."""
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = STATE_DIR / f"reflect_{project}_{timestamp}.log"

        cmd = [
            sys.executable, "-m", "waystone.cli",
            "reflect",
            project,
            transcript_path,
            "--since-turn", str(since_turn),
            "--domain", "software_dev",
        ]

        with open(log_file, "w", encoding="utf-8") as log_f:
            subprocess.Popen(
                cmd,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                start_new_session=True,  # detach so it outlives the hook process
            )
    except Exception:
        pass


def _maybe_trigger_reflect(project: str, transcript_path: str) -> None:
    """Check if reflect should be triggered based on accumulated turns.

    Maintains a watermark of the turn count when reflect last ran.
    Triggers reflect if (current_count - watermark) >= REFLECT_INTERVAL.
    """
    if not transcript_path:
        return

    try:
        current_count = _count_utterances(transcript_path)
        watermark = _read_reflect_watermark(project)
        delta = current_count - watermark

        if delta >= REFLECT_INTERVAL:
            # Update watermark BEFORE spawning (to avoid double-fire)
            _write_reflect_watermark(project, current_count)
            # Reflect on turns since the old watermark
            _spawn_reflect(project, transcript_path, watermark)
    except Exception:
        pass


if __name__ == "__main__":
    main()
