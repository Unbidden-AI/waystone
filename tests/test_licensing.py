"""Offline license verification + per-seat enforcement (Team Server)."""

from datetime import datetime, timedelta, timezone

import pytest
from click.testing import CliRunner

from waystone.cli import cli
from waystone.licensing import (
    TRIAL_SEATS,
    License,
    LicenseError,
    issue_license,
    load_license,
    seats_for,
    verify_license,
)

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _keypair():
    from cryptography.hazmat.primitives import serialization as ser
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    priv = Ed25519PrivateKey.generate()
    priv_pem = priv.private_bytes(
        ser.Encoding.PEM, ser.PrivateFormat.PKCS8, ser.NoEncryption()).decode()
    pub_pem = priv.public_key().public_bytes(
        ser.Encoding.PEM, ser.PublicFormat.SubjectPublicKeyInfo).decode()
    return priv_pem, pub_pem


@pytest.fixture
def keypair():
    return _keypair()


# --------------------------------------------------------------------- verify

def test_issue_verify_roundtrip(keypair):
    priv, pub = keypair
    token = issue_license(priv, seats=10, org="acme", issued_at=_T0,
                          expires_at=_T0 + timedelta(days=365))
    lic = verify_license(token, pub)
    assert lic.seats == 10
    assert lic.org == "acme"
    assert lic.plan == "team"
    assert not lic.is_expired  # expiry is in the test's relative future? -> see below


def test_expired_license_rejected(keypair):
    priv, pub = keypair
    past = datetime(2020, 1, 1, tzinfo=timezone.utc)
    token = issue_license(priv, seats=5, issued_at=past,
                          expires_at=past + timedelta(days=1))
    with pytest.raises(LicenseError):
        verify_license(token, pub)


def test_tampered_token_rejected(keypair):
    priv, pub = keypair
    token = issue_license(priv, seats=5, issued_at=_T0)
    tampered = token[:-4] + ("zzzz" if not token.endswith("zzzz") else "aaaa")
    with pytest.raises(LicenseError):
        verify_license(tampered, pub)


def test_wrong_public_key_rejected(keypair):
    priv, _ = keypair
    _, other_pub = _keypair()
    token = issue_license(priv, seats=5, issued_at=_T0)
    with pytest.raises(LicenseError):
        verify_license(token, other_pub)


# --------------------------------------------------------------------- load

def test_load_license_from_env(keypair, monkeypatch):
    priv, pub = keypair
    monkeypatch.setattr("waystone.licensing.LICENSE_PUBLIC_KEY", pub)
    token = issue_license(priv, seats=7, issued_at=_T0)
    monkeypatch.setenv("WAYSTONE_LICENSE", token)
    lic = load_license()
    assert lic is not None and lic.seats == 7


def test_load_license_none_when_unset(monkeypatch):
    monkeypatch.delenv("WAYSTONE_LICENSE", raising=False)
    monkeypatch.delenv("WAYSTONE_LICENSE_FILE", raising=False)
    assert load_license() is None


def test_load_license_fail_closed_on_tamper(keypair, monkeypatch):
    priv, pub = keypair
    monkeypatch.setattr("waystone.licensing.LICENSE_PUBLIC_KEY", pub)
    token = issue_license(priv, seats=7, issued_at=_T0)
    monkeypatch.setenv("WAYSTONE_LICENSE", token[:-4] + "zzzz")
    with pytest.raises(LicenseError):
        load_license()  # a tampered license must NOT silently grant seats


def test_seats_for():
    assert seats_for(None) == TRIAL_SEATS
    assert seats_for(License(seats=42)) == 42


# --------------------------------------------------------------------- CLI / seats

def _setup(tmp_path, monkeypatch, pub, token=None):
    monkeypatch.setattr("waystone.licensing.LICENSE_PUBLIC_KEY", pub)
    monkeypatch.setenv("CB_ADMIN_DB", str(tmp_path / "admin.db"))
    if token:
        monkeypatch.setenv("WAYSTONE_LICENSE", token)
    else:
        monkeypatch.delenv("WAYSTONE_LICENSE", raising=False)


def test_team_issue_enforces_seat_limit(tmp_path, monkeypatch, keypair):
    priv, pub = keypair
    token = issue_license(priv, seats=2, issued_at=_T0)
    _setup(tmp_path, monkeypatch, pub, token)
    run = CliRunner()
    assert run.invoke(cli, ["team", "issue", "a@x.com"]).exit_code == 0
    assert run.invoke(cli, ["team", "issue", "b@x.com"]).exit_code == 0
    r3 = run.invoke(cli, ["team", "issue", "c@x.com"])
    assert r3.exit_code != 0
    assert "seat limit" in r3.output
    # re-issuing for an existing member does NOT consume another seat
    r4 = run.invoke(cli, ["team", "issue", "a@x.com"])
    assert r4.exit_code == 0, r4.output


def test_team_revoke_frees_seat(tmp_path, monkeypatch, keypair):
    priv, pub = keypair
    token = issue_license(priv, seats=1, issued_at=_T0)
    _setup(tmp_path, monkeypatch, pub, token)
    run = CliRunner()
    assert run.invoke(cli, ["team", "issue", "a@x.com"]).exit_code == 0
    assert run.invoke(cli, ["team", "issue", "b@x.com"]).exit_code != 0  # full
    assert run.invoke(cli, ["team", "revoke", "a@x.com"]).exit_code == 0
    assert run.invoke(cli, ["team", "issue", "b@x.com"]).exit_code == 0  # seat freed


def test_team_trial_seats_when_unlicensed(tmp_path, monkeypatch, keypair):
    _, pub = keypair
    _setup(tmp_path, monkeypatch, pub, token=None)  # no license → TRIAL_SEATS
    run = CliRunner()
    for i in range(TRIAL_SEATS):
        assert run.invoke(cli, ["team", "issue", f"m{i}@x.com"]).exit_code == 0
    over = run.invoke(cli, ["team", "issue", "extra@x.com"])
    assert over.exit_code != 0
    assert "seat limit" in over.output


def test_team_license_and_members_report(tmp_path, monkeypatch, keypair):
    priv, pub = keypair
    token = issue_license(priv, seats=5, org="acme", issued_at=_T0)
    _setup(tmp_path, monkeypatch, pub, token)
    run = CliRunner()
    run.invoke(cli, ["team", "issue", "a@x.com"])
    lic_out = run.invoke(cli, ["team", "license"])
    assert lic_out.exit_code == 0
    assert "1/5" in lic_out.output
    assert "acme" in lic_out.output
    mem = run.invoke(cli, ["team", "members"])
    assert mem.exit_code == 0
    assert "a@x.com" in mem.output


def test_team_issue_rejects_bad_license(tmp_path, monkeypatch, keypair):
    priv, pub = keypair
    monkeypatch.setattr("waystone.licensing.LICENSE_PUBLIC_KEY", pub)
    monkeypatch.setenv("CB_ADMIN_DB", str(tmp_path / "admin.db"))
    monkeypatch.setenv("WAYSTONE_LICENSE", issue_license(priv, seats=3, issued_at=_T0)[:-4] + "zzzz")
    r = CliRunner().invoke(cli, ["team", "issue", "a@x.com"])
    assert r.exit_code != 0
    assert "Invalid license" in r.output or "license" in r.output.lower()
