"""Gut check #3b — the reconcile maintenance job, end to end.

`reconcile` (cluster nodes → ask the LLM which supersede which → write
supersedes edges) had ZERO tests, and it's fired fire-and-forget by the Stop
hook. Two checks:

  A. the Stop hook spawns `waystone reconcile` only past its thresholds
     (deterministic), and
  B. `waystone reconcile` actually records a supersedes relationship for an
     obvious old→new pair (LLM-gated).
"""

import os
import subprocess
import sys
import uuid

import pytest

from waystone._hooks import stop
from waystone.store import GraphStore

LLM_KEY = os.environ.get("LLM_API_KEY") or os.environ.get("GEMINI_API_KEY")
BASE_URL = os.environ.get("WAYSTONE_E2E_BASE_URL",
                          "https://generativelanguage.googleapis.com/v1beta/openai")
MODEL = os.environ.get("WAYSTONE_E2E_MODEL", "gemini-2.5-flash")
requires_llm = pytest.mark.skipif(
    not LLM_KEY, reason="LLM-gated; set LLM_API_KEY or GEMINI_API_KEY to run")


# ── A. Stop hook spawns reconcile only past thresholds (deterministic) ──────

def test_reconcile_spawns_only_past_thresholds(tmp_path, monkeypatch):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    calls: list[list] = []
    monkeypatch.setattr(stop.subprocess, "Popen",
                        lambda cmd, **k: calls.append(cmd))
    cfg = {"maintenance": {"reconcile_threshold": 10, "reconcile_min_total": 20}}

    stop._maybe_spawn_reconcile("p", 19, project_dir, cfg)
    assert not calls, "must not reconcile below reconcile_min_total"

    stop._maybe_spawn_reconcile("p", 25, project_dir, cfg)
    assert len(calls) == 1, "should reconcile once past min_total with enough new nodes"
    assert calls[0][-2:] == ["reconcile", "p"]

    stop._maybe_spawn_reconcile("p", 25, project_dir, cfg)
    assert len(calls) == 1, "must not double-trigger when nothing new accumulated"

    stop._maybe_spawn_reconcile("p", 30, project_dir, cfg)
    assert len(calls) == 1, "must not trigger again until delta >= threshold"

    stop._maybe_spawn_reconcile("p", 40, project_dir, cfg)
    assert len(calls) == 2, "should trigger again once threshold of new nodes accrued"


# ── B. reconcile records an obvious supersession (LLM) ──────────────────────

@requires_llm
def test_reconcile_records_supersession(tmp_path):
    home = tmp_path / "home"
    (home / ".waystone").mkdir(parents=True)
    (home / ".waystone" / "config.yaml").write_text(
        f"llm:\n  base_url: {BASE_URL}\n  model: {MODEL}\n  api_key: {LLM_KEY}\n"
        f"projects_dir: {home / '.waystone' / 'projects'}\n", encoding="utf-8")

    db_path = home / ".waystone" / "projects" / "rec-demo" / "context.db"
    db_path.parent.mkdir(parents=True)

    # Seed an unambiguous old→new pair sharing tags so they land in one cluster.
    # dedup_threshold=1.1 keeps the two similar nodes from collapsing at seed time.
    store = GraphStore(db_path, dedup_threshold=1.1, vec_enabled=False)
    old_id = f"n_{uuid.uuid4().hex[:8]}"
    new_id = f"n_{uuid.uuid4().hex[:8]}"
    try:
        store.add_node({"id": old_id, "type": "decision", "confidence": 0.9,
                        "fact": "The team server uses Redis for the cache layer.",
                        "tags": ["cache", "backend", "redis"]})
        store.add_node({"id": new_id, "type": "decision", "confidence": 0.95,
                        "fact": "We migrated the team server cache layer from Redis "
                                "to Memcached.",
                        "tags": ["cache", "backend", "redis", "memcached"]})
    finally:
        store.close()

    env = dict(os.environ)
    env["HOME"] = str(home)
    for k in ("WAYSTONE_STORE_BACKEND", "WAYSTONE_DATABASE_URL", "DATABASE_URL"):
        env.pop(k, None)

    proc = subprocess.run(
        [sys.executable, "-m", "waystone.cli", "reconcile", "rec-demo",
         "--no-semantic-dedup"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr

    store = GraphStore(db_path, vec_enabled=False)
    try:
        supersedes_edges = [e for e in store.get_all_edges()
                            if e["relation"] == "supersedes"]
    finally:
        store.close()

    assert supersedes_edges, (
        f"reconcile recorded no supersedes edge for an obvious old→new pair.\n"
        f"stdout:\n{proc.stdout}")
    # The Memcached decision should supersede the Redis one (not the reverse).
    assert any(e["to_id"] == old_id for e in supersedes_edges), \
        f"expected the Redis node to be superseded; edges={supersedes_edges}"
