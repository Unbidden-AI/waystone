"""Stripe purchase → self-hosted Team Server license issuance.

A checkout for the Team-license price mints a signed license token (NOT an API key)
and emails it. The token must verify against the bundled public key, so the test sets
a test signing key in the env and points the verifier's public key at its pair.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import time
from unittest.mock import patch

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi.testclient import TestClient  # noqa: E402

from waystone.billing import claim_event, init_admin_db, is_team_license_price  # noqa: E402
from waystone.licensing import (  # noqa: E402
    LicenseError,
    issue_license_from_env,
    verify_license,
)

WEBHOOK_SECRET = "whsec_test_secret"
LICENSE_PRICE = "price_team_license_seat10"


def _keypair():
    from cryptography.hazmat.primitives import serialization as ser
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    k = Ed25519PrivateKey.generate()
    priv = k.private_bytes(ser.Encoding.PEM, ser.PrivateFormat.PKCS8,
                           ser.NoEncryption()).decode()
    pub = k.public_key().public_bytes(ser.Encoding.PEM,
                                      ser.PublicFormat.SubjectPublicKeyInfo).decode()
    return priv, pub


def _sign(body: bytes) -> str:
    t = int(time.time())
    sig = hmac.new(WEBHOOK_SECRET.encode(), f"{t}".encode() + b"." + body,
                   hashlib.sha256).hexdigest()
    return f"t={t},v1={sig}"


def _payload(email="buyer@example.com", price_id=LICENSE_PRICE, seats="10"):
    md = {"price_id": price_id}
    if seats is not None:
        md["seats"] = seats
    return {"type": "checkout.session.completed",
            "data": {"object": {"customer_email": email, "customer": "cus_x",
                                "mode": "payment", "metadata": md}}}


def _invoice_payload(reason="subscription_cycle", price_id=LICENSE_PRICE, qty=10,
                     email="renew@example.com"):
    return {"type": "invoice.paid", "data": {"object": {
        "customer_email": email, "customer": "cus_r", "billing_reason": reason,
        "lines": {"data": [{"price": {"id": price_id}, "quantity": qty}]}}}}


@pytest.fixture
def signing(monkeypatch):
    priv, pub = _keypair()
    monkeypatch.setenv("WAYSTONE_LICENSE_PRIVKEY", priv)
    monkeypatch.setattr("waystone.licensing.LICENSE_PUBLIC_KEY", pub)
    return priv, pub


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("STRIPE_TEAM_LICENSE_PRICE_ID", LICENSE_PRICE)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    db = tmp_path / "admin.db"
    monkeypatch.setenv("CB_ADMIN_DB", str(db))
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    init_admin_db(conn)
    conn.close()
    cfg = {"llm": {"base_url": "http://x", "model": "t"}, "defaults": {},
           "strategies": {}, "projects_dir": str(tmp_path / "p")}
    from waystone.api_server import app
    with patch("waystone.api_server._cfg", return_value=cfg):
        with TestClient(app) as c:
            yield c


# --------------------------------------------------------------------------- units

def test_is_team_license_price(monkeypatch):
    monkeypatch.setenv("STRIPE_TEAM_LICENSE_PRICE_ID", "p_a, p_b")
    assert is_team_license_price("p_a")
    assert is_team_license_price("p_b")
    assert not is_team_license_price("p_c")
    assert not is_team_license_price("")


def test_issue_license_from_env_none_without_key(monkeypatch):
    monkeypatch.delenv("WAYSTONE_LICENSE_PRIVKEY", raising=False)
    monkeypatch.delenv("WAYSTONE_LICENSE_PRIVKEY_FILE", raising=False)
    assert issue_license_from_env(seats=5) is None


def test_issue_license_from_env_mints(signing):
    token = issue_license_from_env(seats=12, org="acme")
    lic = verify_license(token)
    assert lic.seats == 12 and lic.org == "acme" and not lic.is_expired


# --------------------------------------------------------------------------- webhook

def test_purchase_mints_and_emails_license(client, signing):
    captured = {}
    def _capture(email, token, seats, expires_at=None, admin_conn=None):
        captured.update(email=email, token=token, seats=seats)
    with patch("waystone.api_server.send_license_email", _capture):
        body = json.dumps(_payload(seats="10")).encode()
        r = client.post("/webhooks/stripe", content=body,
                        headers={"Content-Type": "application/json",
                                 "Stripe-Signature": _sign(body)})
    assert r.status_code == 200, r.text
    assert r.json()["license_seats"] == 10
    # the emailed token is a valid 10-seat license
    lic = verify_license(captured["token"])
    assert lic.seats == 10
    assert captured["email"] == "buyer@example.com"


def test_purchase_defaults_seats_when_unset(client, signing):
    from waystone.api_server import _DEFAULT_LICENSE_SEATS
    with patch("waystone.api_server.send_license_email", lambda *a, **k: None):
        body = json.dumps(_payload(seats=None)).encode()
        r = client.post("/webhooks/stripe", content=body,
                        headers={"Content-Type": "application/json",
                                 "Stripe-Signature": _sign(body)})
    assert r.json()["license_seats"] == _DEFAULT_LICENSE_SEATS


def test_seats_from_checkout_quantity(client, signing, monkeypatch):
    """Adjustable-quantity links: no `seats` metadata → seats = purchased quantity."""
    import sys
    import types
    fake = types.ModuleType("stripe")

    class _Price:
        id = LICENSE_PRICE

    class _Item:
        quantity = 15
        price = _Price()

    class _Items:
        data = [_Item()]

    fake.checkout = types.SimpleNamespace(
        Session=types.SimpleNamespace(list_line_items=lambda sid, limit=1: _Items()))
    monkeypatch.setitem(sys.modules, "stripe", fake)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")

    payload = {"type": "checkout.session.completed", "data": {"object": {
        "customer_email": "q@example.com", "customer": "cus_q",
        "id": "cs_test", "metadata": {}}}}  # no price_id, no seats → triggers fetch
    with patch("waystone.api_server.send_license_email", lambda *a, **k: None):
        body = json.dumps(payload).encode()
        r = client.post("/webhooks/stripe", content=body,
                        headers={"Content-Type": "application/json",
                                 "Stripe-Signature": _sign(body)})
    assert r.status_code == 200, r.text
    assert r.json()["license_seats"] == 15


def test_purchase_without_signing_key_is_deferred(client, monkeypatch):
    monkeypatch.delenv("WAYSTONE_LICENSE_PRIVKEY", raising=False)
    monkeypatch.delenv("WAYSTONE_LICENSE_PRIVKEY_FILE", raising=False)
    body = json.dumps(_payload()).encode()
    r = client.post("/webhooks/stripe", content=body,
                    headers={"Content-Type": "application/json",
                             "Stripe-Signature": _sign(body)})
    assert r.status_code == 200
    assert r.json().get("license") == "deferred"


def test_non_license_price_still_creates_api_key(client, signing):
    """A normal hosted-tier purchase must NOT be hijacked by the license path."""
    captured = {}
    with patch("waystone.api_server.send_key_email",
               lambda *a, **k: captured.update(sent=True)):
        body = json.dumps(_payload(price_id="price_pro_normal")).encode()
        r = client.post("/webhooks/stripe", content=body,
                        headers={"Content-Type": "application/json",
                                 "Stripe-Signature": _sign(body)})
    assert r.status_code == 200
    assert "tier" in r.json()        # API-key path, not license path
    assert captured.get("sent")


def test_license_auto_enables_per_seat_mode(monkeypatch):
    """A buyer who pastes WAYSTONE_LICENSE gets admin-DB (per-seat) auth without
    also having to flip CB_USE_ADMIN_DB — but an explicit setting always wins."""
    from waystone.api_server import _use_admin_db

    for var in ("CB_USE_ADMIN_DB", "WAYSTONE_LICENSE", "WAYSTONE_LICENSE_FILE"):
        monkeypatch.delenv(var, raising=False)
    assert _use_admin_db() is False  # nothing set → shared-key/local mode

    monkeypatch.setenv("WAYSTONE_LICENSE", "tok")
    assert _use_admin_db() is True  # license alone flips per-seat on

    monkeypatch.setenv("CB_USE_ADMIN_DB", "0")  # explicit opt-out wins
    assert _use_admin_db() is False
    monkeypatch.setenv("CB_USE_ADMIN_DB", "1")
    assert _use_admin_db() is True


def test_self_hosted_team_shares_projects_across_members(monkeypatch):
    """The marquee Team Server feature: members of a self-hosted server SHARE a
    project graph. Per-key isolation must apply ONLY on the hosted SaaS (signalled
    by STRIPE_WEBHOOK_SECRET), never on a self-hosted licensed server — otherwise
    Alice and Bob silently get separate tenants and never see each other's work."""
    from waystone.api_server import _isolate_by_key

    monkeypatch.setenv("CB_USE_ADMIN_DB", "1")  # per-seat mode
    alice = {"key_hash": "a" * 64}
    bob = {"key_hash": "b" * 64}

    # Self-hosted (no Stripe secret) → members are NOT isolated → they share.
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    assert _isolate_by_key(alice) is False
    assert _isolate_by_key(bob) is False

    # Hosted SaaS (Stripe secret set) → per-customer isolation stays on.
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_x")
    assert _isolate_by_key(alice) is True
    assert _isolate_by_key(bob) is True


def test_claim_event_dedupes(tmp_path):
    db = tmp_path / "admin.db"
    conn = sqlite3.connect(str(db))
    init_admin_db(conn)
    assert claim_event(conn, "evt_1") is True      # first time → process
    assert claim_event(conn, "evt_1") is False     # duplicate → skip
    assert claim_event(conn, "evt_2") is True       # a different event proceeds
    assert claim_event(conn, "") is True            # blank id can't be deduped
    assert claim_event(conn, "") is True
    conn.close()


def test_duplicate_webhook_does_not_double_issue(client, signing):
    """A replayed Stripe event (same id) must mint+email exactly once."""
    calls = {"n": 0}

    def _count(*a, **k):
        calls["n"] += 1

    payload = _payload(seats="10")
    payload["id"] = "evt_dup_123"
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", "Stripe-Signature": _sign(body)}
    with patch("waystone.api_server.send_license_email", _count):
        r1 = client.post("/webhooks/stripe", content=body, headers=headers)
        r2 = client.post("/webhooks/stripe", content=body, headers=headers)

    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json().get("license_seats") == 10
    assert r2.json().get("duplicate") is True
    assert calls["n"] == 1  # issued+emailed only once despite the replay


def test_tampered_license_rejected(signing):
    token = issue_license_from_env(seats=3)
    with pytest.raises(LicenseError):
        verify_license(token[:-4] + "zzzz")


# --------------------------------------------------------------------------- renewals

def test_subscription_renewal_reissues_license(client, signing):
    captured = {}

    def _cap(email, token, seats, expires_at=None, admin_conn=None):
        captured.update(email=email, token=token, seats=seats)

    with patch("waystone.api_server.send_license_email", _cap):
        body = json.dumps(_invoice_payload(reason="subscription_cycle", qty=12)).encode()
        r = client.post("/webhooks/stripe", content=body,
                        headers={"Content-Type": "application/json",
                                 "Stripe-Signature": _sign(body)})
    assert r.status_code == 200, r.text
    assert r.json()["license_seats"] == 12
    assert verify_license(captured["token"]).seats == 12  # fresh token for the new cycle


def test_first_invoice_does_not_double_issue(client, signing):
    """The initial subscription invoice (subscription_create) must NOT re-mint —
    checkout.session.completed already did."""
    sent = {"called": False}
    with patch("waystone.api_server.send_license_email",
               lambda *a, **k: sent.__setitem__("called", True)):
        body = json.dumps(_invoice_payload(reason="subscription_create")).encode()
        r = client.post("/webhooks/stripe", content=body,
                        headers={"Content-Type": "application/json",
                                 "Stripe-Signature": _sign(body)})
    assert r.status_code == 200
    assert r.json().get("skipped")
    assert sent["called"] is False


def test_renewal_of_non_license_subscription_skipped(client, signing):
    body = json.dumps(_invoice_payload(price_id="price_some_api_tier")).encode()
    r = client.post("/webhooks/stripe", content=body,
                    headers={"Content-Type": "application/json",
                             "Stripe-Signature": _sign(body)})
    assert r.status_code == 200
    assert r.json().get("skipped")
