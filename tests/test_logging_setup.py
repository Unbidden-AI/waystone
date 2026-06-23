"""The central logging sinks + the hook fail-open decorator."""

from __future__ import annotations

import logging

import pytest

from waystone._logging import hook_entry, setup_file_logging, setup_stream_logging


@pytest.fixture(autouse=True)
def _isolate_home_and_logger(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows Path.home()
    monkeypatch.delenv("WAYSTONE_LOG_LEVEL", raising=False)
    # Start each test from a clean waystone logger (handlers are module-global).
    wl = logging.getLogger("waystone")
    saved = wl.handlers[:]
    wl.handlers.clear()
    yield
    wl.handlers[:] = saved


def test_hook_entry_fails_open_and_logs(tmp_path, capsys):
    @hook_entry
    def boom():
        raise RuntimeError("kaboom")

    with pytest.raises(SystemExit) as exc:
        boom()
    assert exc.value.code == 0  # fail-open

    log_file = tmp_path / ".waystone" / "logs" / "hooks.log"
    assert log_file.exists()
    contents = log_file.read_text(encoding="utf-8")
    assert "kaboom" in contents and "failing open" in contents
    # The crash must NOT leak to stdout (that is the hook protocol channel).
    assert capsys.readouterr().out == ""


def test_hook_entry_preserves_explicit_exit():
    @hook_entry
    def clean():
        raise SystemExit(3)

    with pytest.raises(SystemExit) as exc:
        clean()
    assert exc.value.code == 3  # explicit exits pass through unchanged


def test_hook_entry_returns_value_on_success():
    @hook_entry
    def ok():
        return "done"

    assert ok() == "done"


def test_setup_file_logging_is_idempotent():
    setup_file_logging("hooks")
    setup_file_logging("hooks")
    handlers = logging.getLogger("waystone").handlers
    file_handlers = [h for h in handlers if getattr(h, "_waystone_tag", "") == "file:hooks"]
    assert len(file_handlers) == 1


def test_setup_stream_logging_targets_waystone_logger():
    setup_stream_logging()
    wl = logging.getLogger("waystone")
    assert any(getattr(h, "_waystone_tag", "") == "stream" for h in wl.handlers)
    assert wl.propagate is False  # don't double-emit through root
