"""Tests for the status line: from-start visibility + configurable error alerts."""

import importlib.util
from pathlib import Path

_SL_PATH = Path(__file__).resolve().parent.parent / "hooks" / "statusline.py"
_spec = importlib.util.spec_from_file_location("waystone_statusline", _SL_PATH)
statusline = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(statusline)


def test_ready_segment_shows_from_start(tmp_path):
    (tmp_path / ".waystone").write_text("proj")
    s = statusline._format_cb({}, 0, cwd=str(tmp_path), cfg={"enabled": True, "alert_on_error": True})
    assert s == "WS(proj): ready"


def test_no_segment_without_project_marker(tmp_path):
    s = statusline._format_cb({}, 0, cwd=str(tmp_path), cfg={"enabled": True})
    assert s == ""


def test_disabled_returns_empty(tmp_path):
    (tmp_path / ".waystone").write_text("proj")
    s = statusline._format_cb({}, 0, cwd=str(tmp_path), cfg={"enabled": False})
    assert s == ""


def test_error_status_alerts_when_enabled():
    s = statusline._format_cb(
        {"status": "error", "project": "p", "error": "boom"}, 0,
        cfg={"enabled": True, "alert_on_error": True},
    )
    assert s.startswith("⚠")
    assert "error" in s


def test_error_status_suppressed_when_alert_off():
    s = statusline._format_cb(
        {"status": "error", "project": "p", "error": "boom"}, 0,
        cfg={"enabled": True, "alert_on_error": False},
    )
    assert "⚠" not in s


def test_extract_error_label_respects_alert_toggle():
    state = {"status": "ok", "project": "p", "nodes_retrieved": 1, "nodes_total": 2,
             "extract_error": "401 invalid api key"}
    on = statusline._format_cb(state, 0, cfg={"enabled": True, "alert_on_error": True})
    assert "⚠ auth" in on
    off = statusline._format_cb(state, 0, cfg={"enabled": True, "alert_on_error": False})
    assert "⚠" not in off


def test_error_label_classification():
    assert statusline._error_label("403 permission_denied") == "⚠ key"
    assert statusline._error_label("429 rate limit") == "⚠ rate"
    assert statusline._error_label("request timeout") == "⚠ timeout"
    assert statusline._error_label("weird boom") == "⚠ extract"
