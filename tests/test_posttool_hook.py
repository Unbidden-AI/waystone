"""Tests for the PostToolUse capture hook."""

import io
import json

import pytest

from waystone._hooks import posttool


def test_summarize_bash():
    s = posttool._summarize("Bash", {"command": "pytest -q", "description": "run tests"})
    assert "Ran command: pytest -q" in s
    assert "run tests" in s


def test_summarize_write_edit_multiedit():
    assert "Wrote file /a.py" in posttool._summarize("Write", {"file_path": "/a.py", "content": "print(1)"})
    assert "Edited file /a.py" in posttool._summarize("Edit", {"file_path": "/a.py", "new_string": "x = 1"})
    assert "2 edits" in posttool._summarize("MultiEdit", {"file_path": "/a.py", "edits": [1, 2]})


def test_detect_project(tmp_path):
    (tmp_path / ".waystone").write_text("myproj\n")
    assert posttool._detect_project(str(tmp_path)) == "myproj"


def _invoke(monkeypatch, tmp_path, hook_input, cfg=None):
    config = {
        "projects_dir": str(tmp_path),
        "posttool": cfg or {"enabled": True, "min_events": 2, "max_chars": 100000,
                            "tools": ["Write", "Edit", "Bash"]},
    }
    monkeypatch.setattr("waystone.config.load_config", lambda *a, **k: config)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(hook_input)))
    with pytest.raises(SystemExit):
        posttool.main()


def test_buffer_flushes_at_threshold(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(posttool, "_spawn_extraction", lambda *a, **k: calls.append(a))
    (tmp_path / ".waystone").write_text("proj\n")
    hi = {"tool_name": "Write", "cwd": str(tmp_path), "session_id": "s",
          "tool_input": {"file_path": "/x.py", "content": "a"}}

    _invoke(monkeypatch, tmp_path, hi)   # event 1 → buffered
    assert calls == []
    _invoke(monkeypatch, tmp_path, hi)   # event 2 → flush (min_events=2)
    assert len(calls) == 1

    buf = tmp_path / "proj" / "tool_buffer.json"
    assert json.loads(buf.read_text()) == []  # cleared after flush


def test_non_state_changing_tool_is_skipped(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(posttool, "_spawn_extraction", lambda *a, **k: calls.append(a))
    (tmp_path / ".waystone").write_text("proj\n")
    hi = {"tool_name": "Read", "cwd": str(tmp_path), "session_id": "s",
          "tool_input": {"file_path": "/x"}}

    _invoke(monkeypatch, tmp_path, hi)
    assert calls == []
    assert not (tmp_path / "proj" / "tool_buffer.json").exists()


def test_disabled_via_config(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(posttool, "_spawn_extraction", lambda *a, **k: calls.append(a))
    (tmp_path / ".waystone").write_text("proj\n")
    hi = {"tool_name": "Write", "cwd": str(tmp_path), "session_id": "s",
          "tool_input": {"file_path": "/x.py", "content": "a"}}
    _invoke(monkeypatch, tmp_path, hi, cfg={"enabled": False})
    assert calls == []


def test_paused_skips(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(posttool, "_spawn_extraction", lambda *a, **k: calls.append(a))
    paused = tmp_path / "paused"
    paused.write_text("")
    monkeypatch.setattr(posttool, "PAUSE_FILE", paused)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"tool_name": "Write", "cwd": str(tmp_path)})))
    with pytest.raises(SystemExit):
        posttool.main()
    assert calls == []
