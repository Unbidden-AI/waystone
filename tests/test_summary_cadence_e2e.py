"""Gut check #2 — the session-summary cadence, end to end.

We literally watched the injected "Where we are" narrative stay FROZEN all
session while the Stop hook's background path was dead. That path had only
helper-level unit coverage; nothing asserted the two things that actually
matter:

  A. the Stop hook, after `cadence_turns`, actually FIRES the summarize worker
     (deterministic, no LLM — catches "cadence never triggers" regressions), and
  B. the summarize worker, given turns, actually PRODUCES a `session_summary`
     node (LLM-gated — the real narrative generation).

Together: "Stop fires the worker at cadence" + "the worker writes a correct
summary node" = the whole chain that was silently broken.
"""

import os
import subprocess
import sys

import pytest

from waystone._hooks import stop, summarize
from waystone.store import GraphStore

LLM_KEY = os.environ.get("LLM_API_KEY") or os.environ.get("GEMINI_API_KEY")
BASE_URL = os.environ.get("WAYSTONE_E2E_BASE_URL",
                          "https://generativelanguage.googleapis.com/v1beta/openai")
MODEL = os.environ.get("WAYSTONE_E2E_MODEL", "gemini-2.5-flash")
requires_llm = pytest.mark.skipif(
    not LLM_KEY, reason="LLM-gated; set LLM_API_KEY or GEMINI_API_KEY to run")


# ── A. Deterministic cadence logic (no LLM, runs in CI) ─────────────────────

def test_should_trigger_only_at_cadence(tmp_path):
    """The counter must fire exactly at the cadence threshold, not before."""
    state_path = tmp_path / "sess.json"
    pause = tmp_path / "paused"  # absent
    config = {"session_summary": {"enabled": True, "cadence_turns": 5}}

    for i in range(1, 5):
        summarize._increment_summary_counter(state_path)
        assert not summarize.should_trigger_summary(state_path, config, pause), \
            f"fired early at turn {i}"
    summarize._increment_summary_counter(state_path)  # 5th turn
    assert summarize.should_trigger_summary(state_path, config, pause)


def test_disabled_or_paused_never_triggers(tmp_path):
    state_path = tmp_path / "sess.json"
    for _ in range(20):
        summarize._increment_summary_counter(state_path)
    # disabled
    assert not summarize.should_trigger_summary(
        state_path, {"session_summary": {"enabled": False, "cadence_turns": 5}},
        tmp_path / "nope")
    # paused
    pause = tmp_path / "paused"
    pause.write_text("", encoding="utf-8")
    assert not summarize.should_trigger_summary(
        state_path, {"session_summary": {"enabled": True, "cadence_turns": 5}},
        pause)


def test_stop_hook_spawns_summary_at_cadence(tmp_path, monkeypatch):
    """The Stop hook's `_maybe_spawn_session_summary` must actually invoke the
    worker spawn at each cadence boundary — the step that was never reached
    while the narrative was frozen."""
    monkeypatch.setattr(summarize, "STATE_DIR", tmp_path / "ws")
    pause = tmp_path / "ws" / "paused"
    if pause.exists():
        pytest.skip("a real pause file is present")

    spawned: list[tuple] = []
    monkeypatch.setattr(summarize, "spawn_background_summary",
                        lambda *a, **k: spawned.append(a))
    # should_trigger_summary's pause_file default is bound at import; redirect it
    # to our isolated (absent) path so a dev's real pause file can't interfere.
    monkeypatch.setattr(summarize.should_trigger_summary, "__defaults__", (pause,))

    config = {"session_summary": {"enabled": True, "cadence_turns": 3}}
    fired_at = []
    for turn in range(1, 7):
        stop._maybe_spawn_session_summary(
            "proj", tmp_path / "db.sqlite", "sess", tmp_path / "t.md", config)
        if len(spawned) > len(fired_at):
            fired_at.append(turn)
    assert fired_at == [3, 6], f"expected spawns at turns 3 and 6, got {fired_at}"


# ── B. The summarize worker really produces a session_summary node (LLM) ────

@requires_llm
def test_summarize_worker_writes_session_summary_node(tmp_path):
    """Run the real summarize worker against a saved transcript and confirm it
    generates and stores a `session_summary` node."""
    home = tmp_path / "home"
    (home / ".waystone").mkdir(parents=True)
    (home / ".waystone" / "config.yaml").write_text(
        f"llm:\n  base_url: {BASE_URL}\n  model: {MODEL}\n  api_key: {LLM_KEY}\n"
        f"projects_dir: {home / '.waystone' / 'projects'}\n",
        encoding="utf-8")

    db_path = home / ".waystone" / "projects" / "sum-demo" / "context.db"
    db_path.parent.mkdir(parents=True)

    # The Stop hook's saved-transcript markdown format.
    transcript = tmp_path / "saved.md"
    transcript.write_text(
        "**User**: We need to cut Postgres connection churn on the team server.\n"
        "**Assistant**: I added a PgBouncer-style pool in front of Postgres and "
        "set the store to reuse one connection per request. Next we'll load-test "
        "it and then wire the same pool into the worker processes.\n"
        "**User**: Great, let's make sure the pool size is configurable.\n"
        "**Assistant**: Done — pool size now reads from WAYSTONE_PG_POOL_SIZE, "
        "defaulting to 10. The remaining work is the load test and the worker wiring.\n",
        encoding="utf-8")

    env = dict(os.environ)
    env["HOME"] = str(home)
    for k in ("WAYSTONE_STORE_BACKEND", "WAYSTONE_DATABASE_URL", "DATABASE_URL"):
        env.pop(k, None)

    # Run the worker FOREGROUND (not detached) so we can wait on it directly.
    proc = subprocess.run(
        [sys.executable, "-m", "waystone._hooks.summarize",
         "--project", "sum-demo", "--db-path", str(db_path),
         "--session-id", "sess-sum", "--transcript-path", str(transcript)],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr

    store = GraphStore(db_path, vec_enabled=False)
    try:
        summaries = [n for n in store.get_all_nodes()
                     if n["type"] == "session_summary"]
    finally:
        store.close()

    assert summaries, "summarize worker did not write a session_summary node"
    text = " ".join(s["fact"].lower() for s in summaries)
    # The narrative should capture the session's actual subject.
    assert "pool" in text or "postgres" in text or "pgbouncer" in text, text
