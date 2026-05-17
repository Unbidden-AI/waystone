"""Integration tests for the LemonSqueezy webhook endpoint."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed — skip webhook tests")

from fastapi.testclient import TestClient  # noqa: E402

from waystone.billing import _hash_key, init_admin_db, validate_key  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

WEBHOOK_SECRET = "test_webhook_secret"
LS_PRO_VARIANT = "var_pro_111"
LS_TEAM_VARIANT = "var_team_222"


def _sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _subscription_created_payload(email: str, variant_id: str = LS_PRO_VARIANT) -> dict:
    return {
        "meta": {"event_name": "subscription_created"},
        "data": {
            "attributes": {
                "user_email": email,
                "variant_id": variant_id,
                "status": "active",
            }
        },
    }


def _subscription_cancelled_payload(email: str) -> dict:
    return {
        "meta": {"event_name": "subscription_cancelled"},
        "data": {
            "attributes": {
                "user_email": email,
                "status": "cancelled",
            }
        },
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def env_vars(monkeypatch):
    monkeypatch.setenv("LS_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("LS_PRO_VARIANT_ID", LS_PRO_VARIANT)
    monkeypatch.setenv("LS_TEAM_VARIANT_ID", LS_TEAM_VARIANT)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)


@pytest.fixture
def admin_db_path(tmp_path, monkeypatch):
    """File-based admin DB; sets CB_ADMIN_DB env var so open_admin_db() finds it."""
    db_path = tmp_path / "admin.db"
    monkeypatch.setenv("CB_ADMIN_DB", str(db_path))
    # Pre-initialise the schema
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_admin_db(conn)
    conn.close()
    return db_path


def _open_db(db_path: Path):
    """Open a read connection to the file DB for assertions."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture
def client_with_db(tmp_path, admin_db_path):
    """TestClient with config patched; admin DB is file-based via env var."""
    config = {
        "llm": {"base_url": "http://localhost:1234/v1", "model": "test"},
        "defaults": {},
        "strategies": {},
        "projects_dir": str(tmp_path / "projects"),
    }
    from waystone.api_server import app
    with patch("waystone.api_server._cfg", return_value=config):
        with TestClient(app) as c:
            yield c, admin_db_path


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------

class TestWebhookSignature:
    def test_invalid_signature_returns_400(self, client_with_db):
        c, _ = client_with_db
        body = json.dumps(_subscription_created_payload("user@example.com")).encode()
        r = c.post(
            "/webhooks/lemonsqueezy",
            content=body,
            headers={"Content-Type": "application/json", "X-Signature": "badsig"},
        )
        assert r.status_code == 400
        assert "signature" in r.json()["detail"].lower()

    def test_missing_signature_returns_400(self, client_with_db):
        c, _ = client_with_db
        body = json.dumps(_subscription_created_payload("user@example.com")).encode()
        r = c.post(
            "/webhooks/lemonsqueezy",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 400

    def test_no_secret_configured_returns_400(self, client_with_db, monkeypatch):
        monkeypatch.delenv("LS_WEBHOOK_SECRET")
        c, _ = client_with_db
        body = json.dumps(_subscription_created_payload("user@example.com")).encode()
        # Even a "correctly" signed request fails with no secret configured
        sig = _sign(body, "anything")
        r = c.post(
            "/webhooks/lemonsqueezy",
            content=body,
            headers={"Content-Type": "application/json", "X-Signature": sig},
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# subscription_created
# ---------------------------------------------------------------------------

class TestSubscriptionCreated:
    def _post(self, client, payload: dict):
        body = json.dumps(payload).encode()
        sig = _sign(body)
        return client.post(
            "/webhooks/lemonsqueezy",
            content=body,
            headers={"Content-Type": "application/json", "X-Signature": sig},
        )

    def test_returns_200_ok(self, client_with_db):
        c, _ = client_with_db
        r = self._post(c, _subscription_created_payload("new@example.com"))
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_creates_key_in_db(self, client_with_db):
        c, db_path = client_with_db
        self._post(c, _subscription_created_payload("new@example.com", LS_PRO_VARIANT))
        db = _open_db(db_path)
        row = db.execute("SELECT * FROM api_keys WHERE email = ?", ("new@example.com",)).fetchone()
        db.close()
        assert row is not None
        assert row["tier"] == "pro"
        assert row["is_revoked"] == 0

    def test_team_variant_creates_team_key(self, client_with_db):
        c, db_path = client_with_db
        self._post(c, _subscription_created_payload("team@example.com", LS_TEAM_VARIANT))
        db = _open_db(db_path)
        row = db.execute("SELECT tier FROM api_keys WHERE email = ?", ("team@example.com",)).fetchone()
        db.close()
        assert row["tier"] == "team"

    def test_sends_email_with_key(self, client_with_db, capsys):
        c, _ = client_with_db
        self._post(c, _subscription_created_payload("emailtest@example.com"))
        # Dev mode (no RESEND_API_KEY) prints key to stdout
        out = capsys.readouterr().out
        assert "emailtest@example.com" in out
        assert "waystone_" in out  # key prefix in dev mode stdout

    def test_response_includes_tier(self, client_with_db):
        c, _ = client_with_db
        r = self._post(c, _subscription_created_payload("x@example.com", LS_TEAM_VARIANT))
        assert r.json()["tier"] == "team"

    def test_missing_email_returns_422(self, client_with_db):
        c, _ = client_with_db
        payload = {"meta": {"event_name": "subscription_created"}, "data": {"attributes": {}}}
        body = json.dumps(payload).encode()
        sig = _sign(body)
        r = c.post(
            "/webhooks/lemonsqueezy",
            content=body,
            headers={"Content-Type": "application/json", "X-Signature": sig},
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# subscription_cancelled
# ---------------------------------------------------------------------------

class TestSubscriptionCancelled:
    def _post(self, client, payload: dict):
        body = json.dumps(payload).encode()
        sig = _sign(body)
        return client.post(
            "/webhooks/lemonsqueezy",
            content=body,
            headers={"Content-Type": "application/json", "X-Signature": sig},
        )

    def test_returns_200_ok(self, client_with_db):
        c, db_path = client_with_db
        # Pre-create a key directly in the file DB
        from waystone.billing import create_key
        db = _open_db(db_path)
        create_key(db, email="sub@example.com", tier="pro")
        db.close()
        r = self._post(c, _subscription_cancelled_payload("sub@example.com"))
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_revokes_key(self, client_with_db):
        c, db_path = client_with_db
        from waystone.billing import create_key
        db = _open_db(db_path)
        raw = create_key(db, email="cancel@example.com", tier="pro")
        db.close()
        self._post(c, _subscription_cancelled_payload("cancel@example.com"))
        db = _open_db(db_path)
        assert validate_key(db, raw) is None
        db.close()

    def test_revoke_count_in_response(self, client_with_db):
        c, db_path = client_with_db
        from waystone.billing import create_key
        db = _open_db(db_path)
        create_key(db, email="multi@example.com", tier="pro")
        create_key(db, email="multi@example.com", tier="pro")
        db.close()
        r = self._post(c, _subscription_cancelled_payload("multi@example.com"))
        assert r.json()["revoked"] == 2

    def test_no_existing_key_returns_zero(self, client_with_db):
        c, _ = client_with_db
        r = self._post(c, _subscription_cancelled_payload("ghost@example.com"))
        assert r.status_code == 200
        assert r.json()["revoked"] == 0


# ---------------------------------------------------------------------------
# Unknown events
# ---------------------------------------------------------------------------

class TestUnknownEvents:
    def test_unknown_event_ignored(self, client_with_db):
        c, _ = client_with_db
        payload = {"meta": {"event_name": "order_refunded"}, "data": {"attributes": {}}}
        body = json.dumps(payload).encode()
        sig = _sign(body)
        r = c.post(
            "/webhooks/lemonsqueezy",
            content=body,
            headers={"Content-Type": "application/json", "X-Signature": sig},
        )
        assert r.status_code == 200
        assert r.json()["ignored"] is True
