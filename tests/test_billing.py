"""Unit tests for waystone.billing."""

from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from waystone.billing import (
    TIERS,
    KEY_PREFIX,
    _hash_key,
    check_node_limit,
    check_project_limit,
    create_key,
    generate_api_key,
    init_admin_db,
    log_usage,
    open_admin_db,
    revoke_key,
    revoke_key_by_email,
    send_key_email,
    tier_from_variant,
    validate_key,
    verify_ls_signature,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mem_db():
    """In-memory SQLite admin DB for testing."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_admin_db(conn)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# generate_api_key
# ---------------------------------------------------------------------------

class TestGenerateApiKey:
    def test_has_prefix(self):
        raw, _ = generate_api_key()
        assert raw.startswith(KEY_PREFIX)

    def test_hash_is_sha256_hex(self):
        raw, key_hash = generate_api_key()
        assert key_hash == hashlib.sha256(raw.encode()).hexdigest()
        assert len(key_hash) == 64

    def test_unique_each_call(self):
        keys = {generate_api_key()[0] for _ in range(20)}
        assert len(keys) == 20


# ---------------------------------------------------------------------------
# create_key / validate_key / revoke_key
# ---------------------------------------------------------------------------

class TestCreateKey:
    def test_returns_raw_key(self, mem_db):
        raw = create_key(mem_db, email="user@example.com", tier="pro")
        assert raw.startswith(KEY_PREFIX)

    def test_key_stored_as_hash(self, mem_db):
        raw = create_key(mem_db, email="user@example.com", tier="pro")
        expected_hash = _hash_key(raw)
        row = mem_db.execute("SELECT * FROM api_keys WHERE key_hash = ?", (expected_hash,)).fetchone()
        assert row is not None
        assert row["tier"] == "pro"
        assert row["email"] == "user@example.com"

    def test_invalid_tier_raises(self, mem_db):
        with pytest.raises(ValueError, match="Unknown tier"):
            create_key(mem_db, email="x@y.com", tier="enterprise")

    def test_default_tier_is_free(self, mem_db):
        raw = create_key(mem_db, email="x@y.com")
        row = mem_db.execute(
            "SELECT tier FROM api_keys WHERE key_hash = ?", (_hash_key(raw),)
        ).fetchone()
        assert row["tier"] == "free"


class TestValidateKey:
    def test_valid_key_returns_row(self, mem_db):
        raw = create_key(mem_db, email="user@example.com", tier="pro")
        result = validate_key(mem_db, raw)
        assert result is not None
        assert result["email"] == "user@example.com"
        assert result["tier"] == "pro"

    def test_wrong_key_returns_none(self, mem_db):
        create_key(mem_db, email="user@example.com")
        result = validate_key(mem_db, "engram_wrongkey")
        assert result is None

    def test_updates_last_used(self, mem_db):
        raw = create_key(mem_db, email="user@example.com")
        row_before = mem_db.execute(
            "SELECT last_used FROM api_keys WHERE key_hash = ?", (_hash_key(raw),)
        ).fetchone()
        assert row_before["last_used"] is None

        validate_key(mem_db, raw)

        row_after = mem_db.execute(
            "SELECT last_used FROM api_keys WHERE key_hash = ?", (_hash_key(raw),)
        ).fetchone()
        assert row_after["last_used"] is not None

    def test_revoked_key_returns_none(self, mem_db):
        raw = create_key(mem_db, email="user@example.com")
        revoke_key(mem_db, raw)
        assert validate_key(mem_db, raw) is None


class TestRevokeKey:
    def test_revoke_returns_true(self, mem_db):
        raw = create_key(mem_db, email="user@example.com")
        assert revoke_key(mem_db, raw) is True

    def test_already_revoked_returns_false(self, mem_db):
        raw = create_key(mem_db, email="user@example.com")
        revoke_key(mem_db, raw)
        assert revoke_key(mem_db, raw) is False

    def test_unknown_key_returns_false(self, mem_db):
        assert revoke_key(mem_db, "engram_notakey") is False


class TestRevokeKeyByEmail:
    def test_revokes_all_active_keys(self, mem_db):
        raw1 = create_key(mem_db, email="user@example.com")
        raw2 = create_key(mem_db, email="user@example.com")
        count = revoke_key_by_email(mem_db, "user@example.com")
        assert count == 2
        assert validate_key(mem_db, raw1) is None
        assert validate_key(mem_db, raw2) is None

    def test_only_revokes_matching_email(self, mem_db):
        raw_other = create_key(mem_db, email="other@example.com")
        raw_target = create_key(mem_db, email="target@example.com")
        revoke_key_by_email(mem_db, "target@example.com")
        assert validate_key(mem_db, raw_other) is not None
        assert validate_key(mem_db, raw_target) is None

    def test_no_match_returns_zero(self, mem_db):
        assert revoke_key_by_email(mem_db, "nobody@example.com") == 0


# ---------------------------------------------------------------------------
# Tier enforcement
# ---------------------------------------------------------------------------

class TestCheckNodeLimit:
    def test_free_tier_allows_under_limit(self):
        check_node_limit("free", 499)  # should not raise

    def test_free_tier_blocks_at_limit(self):
        with pytest.raises(ValueError, match="Node limit reached"):
            check_node_limit("free", 500)

    def test_pro_tier_allows_under_limit(self):
        check_node_limit("pro", 24_999)  # should not raise

    def test_pro_tier_blocks_at_limit(self):
        with pytest.raises(ValueError, match="Node limit reached"):
            check_node_limit("pro", 25_000)

    def test_team_tier_blocks_at_limit(self):
        with pytest.raises(ValueError, match="Node limit reached"):
            check_node_limit("team", 250_000)

    def test_unknown_tier_falls_back_to_free(self):
        with pytest.raises(ValueError):
            check_node_limit("unknown_tier", 500)


class TestCheckProjectLimit:
    def test_free_tier_blocks_at_one(self, mem_db):
        with pytest.raises(ValueError, match="Project limit reached"):
            check_project_limit(mem_db, "somehash", "free", 1)

    def test_free_tier_allows_zero(self, mem_db):
        check_project_limit(mem_db, "somehash", "free", 0)  # should not raise

    def test_pro_tier_has_no_limit(self, mem_db):
        check_project_limit(mem_db, "somehash", "pro", 9999)  # should not raise

    def test_team_tier_has_no_limit(self, mem_db):
        check_project_limit(mem_db, "somehash", "team", 9999)  # should not raise


# ---------------------------------------------------------------------------
# Usage logging
# ---------------------------------------------------------------------------

class TestLogUsage:
    def test_inserts_row(self, mem_db):
        log_usage(mem_db, key_hash="abc123", project="myproject", action="extract", input_chars=500)
        row = mem_db.execute("SELECT * FROM usage_log").fetchone()
        assert row["key_hash"] == "abc123"
        assert row["project"] == "myproject"
        assert row["action"] == "extract"
        assert row["input_chars"] == 500
        assert row["timestamp"]


# ---------------------------------------------------------------------------
# send_key_email
# ---------------------------------------------------------------------------

class TestSendKeyEmail:
    def test_dev_mode_prints(self, capsys, monkeypatch):
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        send_key_email("dev@example.com", "engram_testkey123", "pro")
        out = capsys.readouterr().out
        assert "engram_testkey123" in out
        assert "dev@example.com" in out

    def test_resend_called_with_key(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_testkey")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            send_key_email("customer@example.com", "engram_abc123", "pro")

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs[1]["json"]["to"] == ["customer@example.com"]
        assert "engram_abc123" in call_kwargs[1]["json"]["text"]

    def test_resend_failure_is_non_fatal(self, monkeypatch, capsys):
        monkeypatch.setenv("RESEND_API_KEY", "re_testkey")
        with patch("httpx.post", side_effect=Exception("network error")):
            # should not raise
            send_key_email("user@example.com", "engram_key", "pro")
        out = capsys.readouterr().out
        assert "network error" in out


# ---------------------------------------------------------------------------
# LemonSqueezy helpers
# ---------------------------------------------------------------------------

class TestVerifyLsSignature:
    def _make_sig(self, body: bytes, secret: str) -> str:
        return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    def test_valid_signature(self, monkeypatch):
        monkeypatch.setenv("LS_WEBHOOK_SECRET", "testsecret")
        body = b'{"test": "payload"}'
        sig = self._make_sig(body, "testsecret")
        assert verify_ls_signature(body, sig) is True

    def test_invalid_signature(self, monkeypatch):
        monkeypatch.setenv("LS_WEBHOOK_SECRET", "testsecret")
        body = b'{"test": "payload"}'
        assert verify_ls_signature(body, "wrongsig") is False

    def test_no_secret_always_false(self, monkeypatch):
        monkeypatch.delenv("LS_WEBHOOK_SECRET", raising=False)
        assert verify_ls_signature(b"anything", "anysig") is False


class TestTierFromVariant:
    def test_pro_variant(self, monkeypatch):
        monkeypatch.setenv("LS_PRO_VARIANT_ID", "var_pro_123")
        assert tier_from_variant("var_pro_123") == "pro"

    def test_team_variant(self, monkeypatch):
        monkeypatch.setenv("LS_TEAM_VARIANT_ID", "var_team_456")
        assert tier_from_variant("var_team_456") == "team"

    def test_unknown_variant_defaults_to_free(self, monkeypatch):
        monkeypatch.delenv("LS_PRO_VARIANT_ID", raising=False)
        monkeypatch.delenv("LS_TEAM_VARIANT_ID", raising=False)
        assert tier_from_variant("var_unknown") == "free"
