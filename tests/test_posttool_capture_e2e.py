"""Gut check #3a — PostToolUse autonomous-run capture, end to end.

The PostToolUse hook is how Waystone captures what an agent did during an
autonomous run (Write/Edit/Bash/...). It's a headline feature that had ZERO
test coverage, and it spawns the same detached extraction worker that silently
broke in 0.4.42–0.4.46. Two checks:

  A. the hook buffers tool events and actually FLUSHES to extraction at the
     configured threshold (deterministic, no LLM), and
  B. firing the hook the production way (subprocess) really lands nodes in the
     graph (LLM-gated).
"""

import io
import json
import os
import subprocess
import sys

import pytest

from waystone._hooks import posttool
from waystone.store import GraphStore

LLM_KEY = os.environ.get("LLM_API_KEY") or os.environ.get("GEMINI_API_KEY")
BASE_URL = os.environ.get("WAYSTONE_E2E_BASE_URL",
                          "https://generativelanguage.googleapis.com/v1beta/openai")
MODEL = os.environ.get("WAYSTONE_E2E_MODEL", "gemini-2.5-flash")
requires_llm = pytest.mark.skipif(
    not LLM_KEY, reason="LLM-gated; set LLM_API_KEY or GEMINI_API_KEY to run")


def _write_config(home, min_events, with_key=False):
    cfg = home / ".waystone"
    cfg.mkdir(parents=True, exist_ok=True)
    llm = (f"llm:\n  base_url: {BASE_URL}\n  model: {MODEL}\n  api_key: {LLM_KEY}\n"
           if with_key else
           "llm:\n  base_url: http://127.0.0.1:1/v1\n  model: m\n  api_key: k\n")
    (cfg / "config.yaml").write_text(
        llm + f"posttool:\n  enabled: true\n  min_events: {min_events}\n"
        f"projects_dir: {cfg / 'projects'}\n", encoding="utf-8")


def _work(home, project):
    w = home / "work"
    w.mkdir(exist_ok=True)
    (w / ".waystone").write_text(project, encoding="utf-8")
    return w


def _events(cwd, session="s"):
    mk = lambda tn, ti: {"tool_name": tn, "cwd": str(cwd),  # noqa: E731
                         "session_id": session, "tool_input": ti}
    return [
        mk("Bash", {"command": "pytest -q", "description": "run the suite"}),
        mk("Write", {"file_path": "cache.py", "content": "class Cache: ..."}),
        mk("Edit", {"file_path": "cache.py",
                    "new_string": "Migrated the session cache from Redis to "
                                  "Memcached for the team server."}),
        mk("Bash", {"command": "docker compose up -d", "description": "boot stack"}),
    ]


# ── A. Buffer flushes to extraction at threshold (deterministic) ────────────

def test_posttool_flushes_to_extraction_at_threshold(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    # Patch load_config directly — config.py's CONFIG_SEARCH_PATHS is frozen at
    # import time, so monkeypatching $HOME would NOT redirect config loading
    # (and CI, lacking a real ~/.waystone/config.yaml, would fall back to the
    # default min_events). Returning the config explicitly is deterministic.
    test_config = {
        "posttool": {"enabled": True, "min_events": 3},
        "projects_dir": str(home / ".waystone" / "projects"),
    }
    monkeypatch.setattr("waystone.config.load_config", lambda *a, **k: test_config)
    monkeypatch.setattr(posttool, "PAUSE_FILE", home / ".waystone" / "paused")
    work = _work(home, "pt-demo")

    captured: list[str] = []
    monkeypatch.setattr(posttool, "_spawn_extraction",
                        lambda text, *a, **k: captured.append(text))

    events = _events(work)[:3]  # exactly the threshold
    for i, payload in enumerate(events, 1):
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        with pytest.raises(SystemExit):  # hooks end with sys.exit(0)
            posttool.main()
        # Should flush ONLY on the 3rd event, not before.
        assert len(captured) == (1 if i == 3 else 0), f"flush timing wrong at event {i}"

    # The flushed text must contain all buffered tool events.
    assert "pytest" in captured[0]
    assert "cache.py" in captured[0]
    assert "Memcached" in captured[0]


# ── B. Production-path capture really lands nodes (LLM) ─────────────────────

@requires_llm
def test_posttool_capture_extracts_nodes(tmp_path):
    home = tmp_path / "home"
    _write_config(home, min_events=4, with_key=True)
    work = _work(home, "pt-live")
    db_path = home / ".waystone" / "projects" / "pt-live" / "context.db"

    env = dict(os.environ)
    env["HOME"] = str(home)
    for k in ("WAYSTONE_STORE_BACKEND", "WAYSTONE_DATABASE_URL", "DATABASE_URL"):
        env.pop(k, None)

    # Fire the hook the way Claude Code does — once per tool event.
    for payload in _events(work):
        subprocess.run(
            [sys.executable, "-m", "waystone._hooks.posttool"],
            input=json.dumps(payload), capture_output=True, text=True,
            cwd=str(work), env=env, timeout=60,
        )

    # The 4th event flushes → detached worker extracts. Poll for nodes.
    import time
    nodes = []
    deadline = time.time() + 150
    while time.time() < deadline:
        if db_path.exists():
            store = GraphStore(db_path, vec_enabled=False)
            try:
                if store.get_stats()["node_count"] > 0:
                    nodes = store.get_all_nodes()
            finally:
                store.close()
            if nodes:
                break
        time.sleep(2)

    assert nodes, "autonomous PostToolUse capture extracted no nodes"
    facts = " ".join(n["fact"].lower() for n in nodes)
    assert "memcached" in facts or "redis" in facts or "cache" in facts, facts
