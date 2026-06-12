"""Remote-mode wiring for the UserPromptSubmit hook + extraction worker.

In remote (Team Server) mode the submit hook injects shared context via /query and
routes extraction to a detached `--remote` worker that POSTs to /extract — with no
local graph DB. These drive the isolated `_run_remote` functions directly with the
remote client + spawn patched out.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from waystone._hooks import submit as S
from waystone._hooks import worker as W


def _client(**methods):
    c = MagicMock()
    for name, ret in methods.items():
        setattr(c, name, AsyncMock(return_value=ret))
    return c


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(S, "STATE_DIR", tmp_path / ".waystone")
    monkeypatch.setattr(S, "PAUSE_FILE", tmp_path / ".waystone" / "paused")
    monkeypatch.setattr(S, "_write_state", lambda *a, **k: None)


# --------------------------------------------------------------- submit read path

def test_run_remote_injects_context(tmp_path, monkeypatch, capsys):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(S, "_spawn_extraction", lambda *a, **k: None)
    client = _client(query={
        "markdown": "## JWT decision", "nodes_returned": 2,
        "total_nodes": 5, "tokens_estimated": 10,
    })
    config = {"backend": "remote", "api_url": "http://x", "projects_dir": str(tmp_path / "projects"),
              "defaults": {"hops": 3, "top_k": 25}, "incremental": {}}
    with patch("waystone.config.make_remote_client", return_value=client):
        with pytest.raises(SystemExit) as ei:
            S._run_remote(config, "proj", "how does auth work?", "", "s1")
    assert ei.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert "JWT decision" in payload["hookSpecificOutput"]["additionalContext"]
    assert "remote" in payload["hookSpecificOutput"]["additionalContext"]
    client.query.assert_awaited_once()


def test_run_remote_empty_graph_no_injection(tmp_path, monkeypatch, capsys):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(S, "_spawn_extraction", lambda *a, **k: None)
    client = _client(query={"markdown": "", "nodes_returned": 0,
                            "total_nodes": 0, "tokens_estimated": 0})
    config = {"backend": "remote", "api_url": "http://x", "projects_dir": str(tmp_path / "projects"), "defaults": {}, "incremental": {}}
    with patch("waystone.config.make_remote_client", return_value=client):
        with pytest.raises(SystemExit):
            S._run_remote(config, "proj", "hi", "", "")
    assert capsys.readouterr().out.strip() == ""  # nothing injected


def test_run_remote_query_failure_is_fail_open(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(S, "_spawn_extraction", lambda *a, **k: None)
    client = MagicMock()
    client.query = AsyncMock(side_effect=RuntimeError("server down"))
    config = {"backend": "remote", "api_url": "http://x", "projects_dir": str(tmp_path / "projects"), "defaults": {}, "incremental": {}}
    with patch("waystone.config.make_remote_client", return_value=client):
        with pytest.raises(SystemExit) as ei:
            S._run_remote(config, "proj", "hi", "", "")
    assert ei.value.code == 0  # a server hiccup must never block the prompt


# --------------------------------------------------------------- submit write path

def test_run_remote_flush_spawns_remote_worker(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    spawned = []
    monkeypatch.setattr(S, "_spawn_extraction", lambda *a, **k: spawned.append(k))
    client = _client(query={"markdown": "", "nodes_returned": 0,
                            "total_nodes": 0, "tokens_estimated": 0})
    # max_turns=1 → the first prompt flushes immediately
    config = {"backend": "remote", "api_url": "http://x", "projects_dir": str(tmp_path / "projects"), "defaults": {},
              "incremental": {"min_turns": 1, "min_words": 1, "max_turns": 1, "short_turn_words": 0}}
    with patch("waystone.config.make_remote_client", return_value=client):
        with pytest.raises(SystemExit):
            S._run_remote(config, "proj", "We chose Postgres for the team graph.", "", "")
    assert spawned and all(k.get("remote") for k in spawned), \
        "flush must spawn a --remote worker"


def test_run_remote_paused_skips_extraction(tmp_path, monkeypatch):
    sd = tmp_path / ".waystone"
    sd.mkdir()
    (sd / "paused").write_text("")
    monkeypatch.setattr(S, "STATE_DIR", sd)
    monkeypatch.setattr(S, "PAUSE_FILE", sd / "paused")
    monkeypatch.setattr(S, "_write_state", lambda *a, **k: None)
    spawned = []
    monkeypatch.setattr(S, "_spawn_extraction", lambda *a, **k: spawned.append(k))
    client = _client(query={"markdown": "x", "nodes_returned": 1,
                            "total_nodes": 1, "tokens_estimated": 1})
    config = {"backend": "remote", "api_url": "http://x", "projects_dir": str(tmp_path / "projects"), "defaults": {}, "incremental": {}}
    with patch("waystone.config.make_remote_client", return_value=client):
        with pytest.raises(SystemExit):
            S._run_remote(config, "proj", "hi", "", "")
    assert spawned == []  # paused → no extraction, but context still injected


def test_spawn_extraction_appends_remote_flag(tmp_path, monkeypatch):
    captured = {}

    class _Proc:
        def __init__(self, cmd, **kw):
            captured["cmd"] = cmd
            self.stdin = MagicMock()

    monkeypatch.setattr(S.subprocess, "Popen", _Proc)
    S._spawn_extraction("text", "proj", tmp_path / "context.db",
                        source="live", remote=True)
    assert "--remote" in captured["cmd"]


# --------------------------------------------------------------- worker remote path

def test_worker_run_remote_posts_to_server(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "STATE_DIR", tmp_path / ".waystone")
    states = []
    monkeypatch.setattr(W, "_merge_state", lambda u, session_id="": states.append(u))
    client = _client(extract={"nodes_extracted": 4, "total_nodes": 10})
    args = MagicMock(project="proj", source="assistant", session_id="",
                     db_path=str(tmp_path / "proj" / "context.db"))
    with patch("waystone.config.make_remote_client", return_value=client):
        W._run_remote(args, "an assistant turn that records a decision")
    client.extract.assert_awaited_once()
    assert any(s.get("last_extract_nodes") == 4 for s in states)
    # no local graph DB created
    assert not (tmp_path / "proj" / "context.db").exists()
