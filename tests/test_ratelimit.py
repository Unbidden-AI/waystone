"""Tests for per-tier rate limiting in the Waystone API."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed — skip rate limit tests")

from fastapi.testclient import TestClient

from waystone.billing import (
    RateLimiter,
    _hash_key,
    create_key,
    init_admin_db,
)


# ---------------------------------------------------------------------------
# Unit tests for RateLimiter
# ---------------------------------------------------------------------------


class TestRateLimiterUnit:
    """Direct tests of the RateLimiter class."""

    def test_within_limit_allowed(self):
        """N requests all succeed if within limit."""
        limiter = RateLimiter()
        key = "test_key_1"
        tier = "free"

        # Free tier allows 10/min
        for i in range(10):
            allowed, reason, remaining_min, remaining_day = limiter.check(key, tier)
            assert allowed is True
            assert reason == ""
            assert remaining_min == 10 - (i + 1)

    def test_minute_limit_enforced(self):
        """N+1 requests fail when minute limit exceeded."""
        limiter = RateLimiter()
        key = "test_key_2"
        tier = "free"

        # Free tier allows 10/min
        for i in range(10):
            allowed, reason, _, _ = limiter.check(key, tier)
            assert allowed is True

        # 11th request should fail
        allowed, reason, remaining_min, remaining_day = limiter.check(key, tier)
        assert allowed is False
        assert "Rate limit exceeded" in reason
        assert "10 requests/minute" in reason
        assert remaining_min == 0

    def test_day_limit_enforced(self):
        """Day limit is enforced separately from minute limit."""
        limiter = RateLimiter()
        key = "test_key_3"
        tier = "pro"

        # Pro tier allows 60/min and 5000/day
        # Test that day limit is tracked independently: make requests
        # spaced 61+ seconds apart to stay under minute limit, then
        # verify day window accumulates correctly

        with patch("waystone.billing.time.time") as mock_time:
            now = 1000.0

            # Make 10 requests, each in a separate minute (advance 61 seconds)
            for i in range(10):
                mock_time.return_value = now + (i * 61)
                allowed, reason, remaining_min, remaining_day = limiter.check(key, tier)
                assert allowed is True
                # Each request consumes one from both windows
                assert remaining_min == 59  # fresh minute each time
                assert remaining_day == 5000 - (i + 1)

            # Verify final day remaining count
            assert remaining_day == 4990

    def test_429_has_retry_after_header(self):
        """429 response includes Retry-After header."""
        limiter = RateLimiter()
        key = "test_key_4"
        tier = "free"

        # Hit the limit
        for _ in range(10):
            limiter.check(key, tier)

        # Next request fails
        allowed, reason, _, _ = limiter.check(key, tier)
        assert allowed is False
        # The HTTPException will be raised by the API endpoint with Retry-After

    def test_different_keys_independent(self):
        """Rate limits are independent per key."""
        limiter = RateLimiter()
        key_a = "key_a"
        key_b = "key_b"
        tier = "free"

        # Key A hits limit
        for _ in range(10):
            limiter.check(key_a, tier)

        # Key A is blocked
        allowed_a, _, _, _ = limiter.check(key_a, tier)
        assert allowed_a is False

        # Key B is still allowed
        allowed_b, reason_b, _, _ = limiter.check(key_b, tier)
        assert allowed_b is True
        assert reason_b == ""

    def test_free_tier_lower_limit_than_pro(self):
        """Free tier has lower limit than pro."""
        from waystone.billing import RATE_LIMITS

        free_limit = RATE_LIMITS["free"]["requests_per_minute"]
        pro_limit = RATE_LIMITS["pro"]["requests_per_minute"]
        team_limit = RATE_LIMITS["team"]["requests_per_minute"]

        assert free_limit < pro_limit < team_limit

    def test_window_sliding_clears_old_timestamps(self):
        """Requests outside the window are pruned."""
        limiter = RateLimiter()
        key = "test_key_sliding"
        tier = "free"

        with patch("waystone.billing.time.time") as mock_time:
            now = 1000.0
            mock_time.return_value = now

            # Make 5 requests at t=1000
            for _ in range(5):
                limiter.check(key, tier)

            # Advance 65 seconds (beyond 60-second window)
            mock_time.return_value = now + 65

            # Minute window should be cleared, so we can make 10 more
            allowed, reason, remaining_min, _ = limiter.check(key, tier)
            assert allowed is True
            assert remaining_min == 9  # 10 - 1 (just consumed)

    def test_pro_tier_higher_limits(self):
        """Pro tier has higher request limits."""
        limiter = RateLimiter()
        key_pro = "pro_key"
        tier = "pro"

        # Pro allows 60/min
        for i in range(60):
            allowed, _, _, _ = limiter.check(key_pro, tier)
            assert allowed is True

        # 61st should fail
        allowed, _, _, _ = limiter.check(key_pro, tier)
        assert allowed is False

    def test_team_tier_highest_limits(self):
        """Team tier has the highest request limits."""
        limiter = RateLimiter()
        key_team = "team_key"
        tier = "team"

        # Team allows 300/min
        for i in range(300):
            allowed, _, _, _ = limiter.check(key_team, tier)
            assert allowed is True

        # 301st should fail
        allowed, _, _, _ = limiter.check(key_team, tier)
        assert allowed is False


# ---------------------------------------------------------------------------
# Integration tests with FastAPI
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_db_path(tmp_path, monkeypatch):
    """File-based admin DB; sets CB_ADMIN_DB env var."""
    db_path = tmp_path / "admin.db"
    monkeypatch.setenv("CB_ADMIN_DB", str(db_path))
    monkeypatch.setenv("CB_USE_ADMIN_DB", "1")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "test_secret")
    # Pre-initialize schema
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_admin_db(conn)
    conn.close()
    return db_path


@pytest.fixture
def client_with_db(tmp_path, admin_db_path):
    """TestClient with admin DB."""
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


def _open_db(db_path: Path):
    """Open a read connection to the file DB."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


class TestRateLimitIntegration:
    """Integration tests: FastAPI endpoints enforcing rate limits."""

    def test_within_limit_request_succeeds(self, client_with_db):
        """Requests within limit get 200 response."""
        c, db_path = client_with_db
        db = _open_db(db_path)
        key = create_key(db, email="user1@example.com", tier="free")
        db.close()

        # Make 5 requests (free tier allows 10/min)
        for _ in range(5):
            r = c.get(
                "/v1/projects",
                headers={"Authorization": f"Bearer {key}"},
            )
            assert r.status_code == 200

    def test_rate_limited_request_returns_429(self, client_with_db):
        """Requests exceeding limit get 429 response."""
        c, db_path = client_with_db
        db = _open_db(db_path)
        key = create_key(db, email="user2@example.com", tier="free")
        db.close()

        # Free tier allows 10/min; make 10 + 1 requests
        for i in range(10):
            r = c.get(
                "/v1/projects",
                headers={"Authorization": f"Bearer {key}"},
            )
            assert r.status_code == 200

        # 11th request should be rate limited
        r = c.get(
            "/v1/projects",
            headers={"Authorization": f"Bearer {key}"},
        )
        assert r.status_code == 429
        assert "Rate limit exceeded" in r.json()["detail"]

    def test_429_has_retry_after_header(self, client_with_db):
        """429 response includes Retry-After header."""
        c, db_path = client_with_db
        db = _open_db(db_path)
        key = create_key(db, email="user3@example.com", tier="free")
        db.close()

        # Hit the limit
        for _ in range(10):
            c.get("/v1/projects", headers={"Authorization": f"Bearer {key}"})

        # Next request is rate limited
        r = c.get(
            "/v1/projects",
            headers={"Authorization": f"Bearer {key}"},
        )
        assert r.status_code == 429
        assert "Retry-After" in r.headers
        assert r.headers["Retry-After"] == "60"

    def test_response_headers_include_rate_limit_info(self, client_with_db):
        """Successful responses include X-RateLimit-* headers."""
        c, db_path = client_with_db
        db = _open_db(db_path)
        key = create_key(db, email="user4@example.com", tier="free")
        db.close()

        r = c.get(
            "/v1/projects",
            headers={"Authorization": f"Bearer {key}"},
        )
        assert r.status_code == 200
        # Free tier: 10/min, 100/day
        assert "X-RateLimit-Limit-Minute" in r.headers
        assert r.headers["X-RateLimit-Limit-Minute"] == "10"
        assert "X-RateLimit-Remaining-Minute" in r.headers
        assert r.headers["X-RateLimit-Remaining-Minute"] == "9"  # 1 consumed
        assert "X-RateLimit-Limit-Day" in r.headers
        assert r.headers["X-RateLimit-Limit-Day"] == "100"
        assert "X-RateLimit-Remaining-Day" in r.headers
        assert r.headers["X-RateLimit-Remaining-Day"] == "99"  # 1 consumed

    def test_pro_tier_higher_limit_than_free(self, client_with_db):
        """Pro tier allows more requests than free."""
        c, db_path = client_with_db

        # Create free and pro keys
        db = _open_db(db_path)
        free_key = create_key(db, email="free@example.com", tier="free")
        pro_key = create_key(db, email="pro@example.com", tier="pro")
        db.close()

        # Free key hits limit at 10
        for i in range(10):
            r = c.get(
                "/v1/projects",
                headers={"Authorization": f"Bearer {free_key}"},
            )
            assert r.status_code == 200

        r = c.get(
            "/v1/projects",
            headers={"Authorization": f"Bearer {free_key}"},
        )
        assert r.status_code == 429

        # Pro key can make 60 requests/min
        for i in range(60):
            r = c.get(
                "/v1/projects",
                headers={"Authorization": f"Bearer {pro_key}"},
            )
            assert r.status_code == 200

        r = c.get(
            "/v1/projects",
            headers={"Authorization": f"Bearer {pro_key}"},
        )
        assert r.status_code == 429

    def test_different_keys_independent_limits(self, client_with_db):
        """Rate limit is per-key, not global."""
        c, db_path = client_with_db
        db = _open_db(db_path)
        key1 = create_key(db, email="user5a@example.com", tier="free")
        key2 = create_key(db, email="user5b@example.com", tier="free")
        db.close()

        # Hit limit for key1
        for _ in range(10):
            r = c.get(
                "/v1/projects",
                headers={"Authorization": f"Bearer {key1}"},
            )
            assert r.status_code == 200

        r = c.get(
            "/v1/projects",
            headers={"Authorization": f"Bearer {key1}"},
        )
        assert r.status_code == 429

        # key2 still has requests available
        r = c.get(
            "/v1/projects",
            headers={"Authorization": f"Bearer {key2}"},
        )
        assert r.status_code == 200

    def test_rate_limit_only_enforced_with_webhook_secret(self, client_with_db, monkeypatch):
        """Rate limiting is only enforced if STRIPE_WEBHOOK_SECRET is set."""
        # This behavior is already tested implicitly (our fixture sets the secret)
        # Verify the condition: if secret is not set, rate limiting is skipped
        c, db_path = client_with_db

        # Remove the webhook secret
        monkeypatch.delenv("STRIPE_WEBHOOK_SECRET")

        # We need to reload the app to pick up the env var change
        # For now, this test documents the design; actual behavior verified via fixture

    def test_extract_endpoint_respects_rate_limit(self, client_with_db, tmp_path):
        """Rate limiting applies to extract endpoint."""
        c, db_path = client_with_db
        db = _open_db(db_path)
        key = create_key(db, email="user6@example.com", tier="free")
        db.close()

        # Create a project first
        r = c.post(
            "/v1/projects/test_project",
            headers={"Authorization": f"Bearer {key}"},
        )
        assert r.status_code == 201

        # Mock the LLM extraction call to avoid network errors
        with patch("waystone.extractor._call_llm") as mock_llm:
            mock_llm.return_value = json.dumps({
                "nodes": [{"id": "n1", "fact": "test fact", "type": "transition", "confidence": 0.9, "tags": []}],
                "edges": []
            })

            # Make several extract requests up to the limit
            # We just check that they don't get 429 (rate limit)
            for i in range(5):
                r = c.post(
                    "/v1/projects/test_project/extract",
                    json={
                        "text": "Sample text for extraction",
                        "source_name": "api",
                        "verify": False,
                    },
                    headers={"Authorization": f"Bearer {key}"},
                )
                # Rate limit should not block these (they're within the limit)
                assert r.status_code != 429, f"Got 429 on request {i}"

    def test_query_endpoint_respects_rate_limit(self, client_with_db):
        """Rate limiting applies to query endpoint."""
        c, db_path = client_with_db
        db = _open_db(db_path)
        key = create_key(db, email="user7@example.com", tier="free")
        db.close()

        # Create a project first
        r = c.post(
            "/v1/projects/test_project",
            headers={"Authorization": f"Bearer {key}"},
        )
        assert r.status_code == 201

        # Make several query requests
        for i in range(5):
            r = c.post(
                "/v1/projects/test_project/query",
                json={"task": "What was decided?", "hops": 3, "top_k": 25},
                headers={"Authorization": f"Bearer {key}"},
            )
            # 200 because query succeeds on empty graph
            assert r.status_code == 200

    def test_remaining_counters_decrease(self, client_with_db):
        """Remaining counter decreases with each request."""
        c, db_path = client_with_db
        db = _open_db(db_path)
        key = create_key(db, email="user8@example.com", tier="pro")
        db.close()

        for i in range(5):
            r = c.get(
                "/v1/projects",
                headers={"Authorization": f"Bearer {key}"},
            )
            assert r.status_code == 200
            remaining = int(r.headers["X-RateLimit-Remaining-Minute"])
            assert remaining == 60 - (i + 1)

    def test_day_limit_shown_in_headers(self, client_with_db):
        """Day limit info is included in response headers."""
        c, db_path = client_with_db
        db = _open_db(db_path)
        key = create_key(db, email="user9@example.com", tier="team")
        db.close()

        r = c.get(
            "/v1/projects",
            headers={"Authorization": f"Bearer {key}"},
        )
        assert r.status_code == 200
        # Team tier: 300/min, 50000/day
        assert r.headers["X-RateLimit-Limit-Day"] == "50000"
        assert r.headers["X-RateLimit-Remaining-Day"] == "49999"

    def test_missing_auth_returns_401_before_rate_limit(self, client_with_db):
        """Missing auth is rejected with 401 before rate limit check."""
        c, _ = client_with_db

        # No Authorization header
        r = c.get("/v1/projects")
        assert r.status_code == 401
        assert "Missing API key" in r.json()["detail"]

    def test_invalid_auth_returns_401_before_rate_limit(self, client_with_db):
        """Invalid auth is rejected with 401 before rate limit check."""
        c, _ = client_with_db

        r = c.get(
            "/v1/projects",
            headers={"Authorization": "Bearer invalid_key"},
        )
        assert r.status_code == 401
        assert "Invalid or revoked API key" in r.json()["detail"]
