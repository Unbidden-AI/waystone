"""Dead-letter email retry: a paid customer whose key/license email failed once must
get it re-sent — and a LICENSE row must resend as a license email, not an API key."""

from __future__ import annotations

import sqlite3
import time
from unittest.mock import patch

from waystone.billing import (
    count_dead_letters,
    init_admin_db,
    retry_dead_letter_emails,
)


def _db(tmp_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "admin.db"))
    init_admin_db(conn)
    return conn


def _queue(conn, email, api_key, tier):
    conn.execute(
        "INSERT INTO dead_letter_emails (email, api_key, tier, created_at, retry_count, last_attempt)"
        " VALUES (?,?,?,?,0,?)",
        (email, api_key, tier, time.time(), time.time()),
    )
    conn.commit()


def test_retry_is_noop_without_resend_key(tmp_path, monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    conn = _db(tmp_path)
    _queue(conn, "a@x.com", "waystone_k", "pro")
    assert retry_dead_letter_emails(conn) == 0
    assert count_dead_letters(conn) == 1  # not dropped — can't send, so keep it


def test_retry_resends_api_key_row_and_clears_it(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_x")
    conn = _db(tmp_path)
    _queue(conn, "a@x.com", "waystone_thekey", "pro")
    sent = {}
    with patch("waystone.billing._post_email", lambda e, s, b: sent.update(body=b)):
        assert retry_dead_letter_emails(conn) == 1
    assert count_dead_letters(conn) == 0  # removed on success
    assert "WAYSTONE_API_KEY=waystone_thekey" in sent["body"]


def test_retry_resends_license_row_with_license_format(tmp_path, monkeypatch):
    """The bug this guards: license rows are stored as api_key='LICENSE:<token>' and
    must resend as a LICENSE email (WAYSTONE_LICENSE=…), not the API-key format."""
    monkeypatch.setenv("RESEND_API_KEY", "re_x")
    conn = _db(tmp_path)
    _queue(conn, "b@x.com", "LICENSE:tok_abc123", "team_license")

    class _Lic:
        seats = 7
        expires_at = None

    monkeypatch.setattr("waystone.licensing.verify_license", lambda t: _Lic())
    sent = {}
    with patch("waystone.billing._post_email", lambda e, s, b: sent.update(subject=s, body=b)):
        assert retry_dead_letter_emails(conn) == 1
    assert count_dead_letters(conn) == 0
    assert "WAYSTONE_LICENSE=tok_abc123" in sent["body"]
    assert "WAYSTONE_API_KEY" not in sent["body"]
    assert "7 seats" in sent["subject"]


def test_retry_bumps_count_and_keeps_row_on_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_x")
    conn = _db(tmp_path)
    _queue(conn, "c@x.com", "waystone_k", "pro")

    def _boom(*a):
        raise RuntimeError("resend down")

    with patch("waystone.billing._post_email", _boom):
        assert retry_dead_letter_emails(conn) == 0
    assert conn.execute("SELECT retry_count FROM dead_letter_emails").fetchone()[0] == 1
    assert count_dead_letters(conn) == 1  # kept for the next pass


def test_retry_gives_up_after_max_retries(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_x")
    conn = _db(tmp_path)
    conn.execute(
        "INSERT INTO dead_letter_emails (email, api_key, tier, created_at, retry_count, last_attempt)"
        " VALUES (?,?,?,?,?,?)",
        ("d@x.com", "waystone_k", "pro", time.time(), 3, time.time()),
    )
    conn.commit()
    with patch("waystone.billing._post_email", lambda *a: None):
        assert retry_dead_letter_emails(conn, max_retries=3) == 0  # already at the cap → skipped
    assert count_dead_letters(conn) == 1
