"""The extraction worker's status-bar helpers: the float-None elapsed bug and the
error classifier that turned a raw 403 dump into an actionable status."""

from __future__ import annotations

from waystone._hooks.worker import _elapsed_ms_since, _short_error


def test_elapsed_ms_handles_none_started():
    # The regression: completion writes extract_started_at=None, so the NEXT read
    # was float - None. Must not raise; None means "now" → ~0ms.
    assert _elapsed_ms_since(None) == 0


def test_elapsed_ms_normal():
    import time
    assert 500 <= _elapsed_ms_since(time.time() - 1.0) <= 2000


def test_short_error_classifies_403_as_auth():
    e = Exception("Client error '403 Forbidden' for url 'https://...'\nFor more info")
    msg = _short_error(e)
    assert "auth" in msg and "403" in msg
    assert "\n" not in msg  # single, short status line


def test_short_error_falls_back_to_first_line():
    e = ValueError("something specific broke\nsecond line ignored")
    assert _short_error(e) == "something specific broke"
