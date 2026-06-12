"""Integration tests for the Stripe webhook endpoint."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed — skip webhook tests")

from fastapi.testclient import TestClient  # noqa: E402

from waystone.billing import init_admin_db, validate_key  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

WEBHOOK_SECRET = "whsec_test_secret"
STRIPE_PRO_PRICE = "price_pro_111"
STRIPE_TEAM_PRICE = "price_team_222"


def _sign(body: bytes, secret: str = WEBHOOK_SECRET, timestamp: int | None = None) -> str:
    """Return a valid Stripe-Signature header value for the given body."""
    t = timestamp or int(time.time())
    signed_payload = f"{t}".encode() + b"." + body
    sig = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={t},v1={sig}"


def _checkout_completed_payload(
    email: str,
    customer_id: str = "cus_test123",
    price_id: str = STRIPE_PRO_PRICE,
) -> dict:
    return {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "customer_email": email,
                "customer": customer_id,
                "mode": "subscription",
                "metadata": {"price_id": price_id},
            }
        },
    }


def _subscription_deleted_payload(customer_id: str = "cus_test123") -> dict:
    return {
        "type": "customer.subscription.deleted",
        "data": {
            "object": {
                "customer": customer_id,
                "status": "canceled",
            }
        },
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def env_vars(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("STRIPE_PRO_PRICE_ID", STRIPE_PRO_PRICE)
    monkeypatch.setenv("STRIPE_TEAM_PRICE_ID", STRIPE_TEAM_PRICE)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)


@pytest.fixture
def admin_db_path(tmp_path, monkeypatch):
    """File-based admin DB; sets CB_ADMIN_DB env var so open_admin_db() finds it."""
    db_path = tmp_path / "admin.db"
    monkeypatch.setenv("CB_ADMIN_DB", str(db_path))
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_admin_db(conn)
    conn.close()
    return db_path


def _open_db(db_path: Path):
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
        body = json.dumps(_checkout_completed_payload("user@example.com")).encode()
        r = c.post(
            "/webhooks/stripe",
            content=body,
            headers={"Content-Type": "application/json", "Stripe-Signature": "t=1,v1=badhex"},
        )
        assert r.status_code == 400
        assert "signature" in r.json()["detail"].lower()

    def test_missing_signature_returns_400(self, client_with_db):
        c, _ = client_with_db
        body = json.dumps(_checkout_completed_payload("user@example.com")).encode()
        r = c.post(
            "/webhooks/stripe",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 400

    def test_no_secret_configured_returns_503(self, client_with_db, monkeypatch):
        monkeypatch.delenv("STRIPE_WEBHOOK_SECRET")
        c, _ = client_with_db
        body = json.dumps(_checkout_completed_payload("user@example.com")).encode()
        sig = _sign(body, "anything")
        r = c.post(
            "/webhooks/stripe",
            content=body,
            headers={"Content-Type": "application/json", "Stripe-Signature": sig},
        )
        assert r.status_code == 503


# ---------------------------------------------------------------------------
# checkout.session.completed
# ---------------------------------------------------------------------------

class TestCheckoutSessionCompleted:
    def _post(self, client, payload: dict):
        body = json.dumps(payload).encode()
        sig = _sign(body)
        return client.post(
            "/webhooks/stripe",
            content=body,
            headers={"Content-Type": "application/json", "Stripe-Signature": sig},
        )

    def test_returns_200_ok(self, client_with_db):
        c, _ = client_with_db
        r = self._post(c, _checkout_completed_payload("new@example.com"))
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_creates_key_in_db(self, client_with_db):
        c, db_path = client_with_db
        self._post(c, _checkout_completed_payload("new@example.com", price_id=STRIPE_PRO_PRICE))
        db = _open_db(db_path)
        row = db.execute("SELECT * FROM api_keys WHERE email = ?", ("new@example.com",)).fetchone()
        db.close()
        assert row is not None
        assert row["tier"] == "pro"
        assert row["is_revoked"] == 0

    def test_team_price_creates_team_key(self, client_with_db):
        c, db_path = client_with_db
        self._post(c, _checkout_completed_payload("team@example.com", price_id=STRIPE_TEAM_PRICE))
        db = _open_db(db_path)
        row = db.execute("SELECT tier FROM api_keys WHERE email = ?", ("team@example.com",)).fetchone()
        db.close()
        assert row["tier"] == "team"

    def test_stores_stripe_customer_id(self, client_with_db):
        c, db_path = client_with_db
        self._post(c, _checkout_completed_payload("cust@example.com", customer_id="cus_abc123"))
        db = _open_db(db_path)
        row = db.execute("SELECT stripe_customer_id FROM api_keys WHERE email = ?", ("cust@example.com",)).fetchone()
        db.close()
        assert row["stripe_customer_id"] == "cus_abc123"

    def test_sends_email_with_key(self, client_with_db, capsys):
        c, _ = client_with_db
        self._post(c, _checkout_completed_payload("emailtest@example.com"))
        out = capsys.readouterr().out
        assert "emailtest@example.com" in out
        assert "waystone_" in out  # key prefix in dev mode stdout

    def test_response_includes_tier(self, client_with_db):
        c, _ = client_with_db
        r = self._post(c, _checkout_completed_payload("x@example.com", price_id=STRIPE_TEAM_PRICE))
        assert r.json()["tier"] == "team"

    def test_missing_email_returns_422(self, client_with_db):
        c, _ = client_with_db
        payload = {
            "type": "checkout.session.completed",
            "data": {"object": {"customer": "cus_xxx", "metadata": {}}},
        }
        body = json.dumps(payload).encode()
        sig = _sign(body)
        r = c.post(
            "/webhooks/stripe",
            content=body,
            headers={"Content-Type": "application/json", "Stripe-Signature": sig},
        )
        assert r.status_code == 422

    def test_no_price_id_defaults_to_pro(self, client_with_db):
        c, db_path = client_with_db
        payload = {
            "type": "checkout.session.completed",
            "data": {"object": {"customer_email": "noprice@example.com", "customer": "cus_yyy", "metadata": {}}},
        }
        body = json.dumps(payload).encode()
        sig = _sign(body)
        c.post(
            "/webhooks/stripe",
            content=body,
            headers={"Content-Type": "application/json", "Stripe-Signature": sig},
        )
        db = _open_db(db_path)
        row = db.execute("SELECT tier FROM api_keys WHERE email = ?", ("noprice@example.com",)).fetchone()
        db.close()
        assert row["tier"] == "pro"


# ---------------------------------------------------------------------------
# customer.subscription.deleted
# ---------------------------------------------------------------------------

class TestSubscriptionDeleted:
    def _post(self, client, payload: dict):
        body = json.dumps(payload).encode()
        sig = _sign(body)
        return client.post(
            "/webhooks/stripe",
            content=body,
            headers={"Content-Type": "application/json", "Stripe-Signature": sig},
        )

    def test_returns_200_ok(self, client_with_db):
        c, db_path = client_with_db
        from waystone.billing import create_key
        db = _open_db(db_path)
        create_key(db, email="sub@example.com", tier="pro", stripe_customer_id="cus_del123")
        db.close()
        r = self._post(c, _subscription_deleted_payload("cus_del123"))
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_revokes_key_by_customer_id(self, client_with_db):
        c, db_path = client_with_db
        from waystone.billing import create_key
        db = _open_db(db_path)
        raw = create_key(db, email="cancel@example.com", tier="pro", stripe_customer_id="cus_cancel")
        db.close()
        self._post(c, _subscription_deleted_payload("cus_cancel"))
        db = _open_db(db_path)
        assert validate_key(db, raw) is None
        db.close()

    def test_revoke_count_in_response(self, client_with_db):
        c, db_path = client_with_db
        from waystone.billing import create_key
        db = _open_db(db_path)
        create_key(db, email="multi1@example.com", tier="pro", stripe_customer_id="cus_multi")
        create_key(db, email="multi2@example.com", tier="pro", stripe_customer_id="cus_multi")
        db.close()
        r = self._post(c, _subscription_deleted_payload("cus_multi"))
        assert r.json()["revoked"] == 2

    def test_no_existing_key_returns_zero(self, client_with_db):
        c, _ = client_with_db
        r = self._post(c, _subscription_deleted_payload("cus_ghost"))
        assert r.status_code == 200
        assert r.json()["revoked"] == 0


# ---------------------------------------------------------------------------
# Unknown events
# ---------------------------------------------------------------------------

class TestUnknownEvents:
    def test_unknown_event_ignored(self, client_with_db):
        c, _ = client_with_db
        payload = {"type": "payment_intent.succeeded", "data": {"object": {}}}
        body = json.dumps(payload).encode()
        sig = _sign(body)
        r = c.post(
            "/webhooks/stripe",
            content=body,
            headers={"Content-Type": "application/json", "Stripe-Signature": sig},
        )
        assert r.status_code == 200
        assert r.json()["ignored"] is True
