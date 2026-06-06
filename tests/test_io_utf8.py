"""Regression tests for Windows cp1252 UnicodeEncodeError hardening."""

import io
import sys

from waystone import _io


def test_force_utf8_handles_stream_without_reconfigure(monkeypatch):
    # A plain object lacking .reconfigure must not raise.
    monkeypatch.setattr("sys.stdout", object())
    monkeypatch.setattr("sys.stderr", object())
    _io.force_utf8()  # no exception


def test_force_utf8_reconfigures_both_streams(monkeypatch):
    calls = []

    class Stream:
        def reconfigure(self, **kw):
            calls.append(kw)

    monkeypatch.setattr("sys.stdout", Stream())
    monkeypatch.setattr("sys.stderr", Stream())
    _io.force_utf8()
    assert len(calls) == 2
    assert all(c == {"encoding": "utf-8", "errors": "replace"} for c in calls)


def test_unicode_print_survives_after_force_utf8_on_cp1252(monkeypatch):
    # Reproduce the Windows bug: a cp1252-backed stdout would raise on "✓".
    raw = io.BytesIO()
    wrapper = io.TextIOWrapper(raw, encoding="cp1252")
    monkeypatch.setattr("sys.stdout", wrapper)

    _io.force_utf8()  # flips stdout to utf-8
    print("✓ Config loaded ⟁")  # would have raised UnicodeEncodeError on cp1252
    sys.stdout.flush()

    assert "✓".encode("utf-8") in raw.getvalue()
