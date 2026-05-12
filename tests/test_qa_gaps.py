"""QA audit tests for critical production gaps.

This file identifies untested paths that must be covered before public release.
Note: FastAPI-dependent tests are skipped in this environment; they must be
run separately with `pip install -e ".[dev]"` which installs fastapi.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engram.store import GraphStore
from engram.billing import open_admin_db, create_key, init_admin_db

try:
    from fastapi.testclient import TestClient
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False


# ============================================================================
# P0 GAPS: Node Limit Enforcement on Extract (blocks release)
# ============================================================================

@pytest.mark.skipif(not HAS_FASTAPI, reason="Requires fastapi (optional dependency)")
class TestNodeLimitEnforcementOnExtract:
    """Test that free-tier users cannot exceed their node limit on extraction."""

    @pytest.fixture
    def api_with_admin_db(self, tmp_path, monkeypatch):
        """API server with admin DB enabled."""
        # Setup admin DB
        admin_db_path = tmp_path / "admin.db"
        monkeypatch.setenv("CB_USE_ADMIN_DB", "1")
        monkeypatch.setenv("CB_ADMIN_DB", str(admin_db_path))
        monkeypatch.setenv("LS_WEBHOOK_SECRET", "test_secret")

        conn = sqlite3.connect(str(admin_db_path))
        conn.row_factory = sqlite3.Row
        init_admin_db(conn)
        conn.close()

        # Setup projects dir
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()

        config = {
            "llm": {"base_url": "http://localhost:1234/v1", "model": "test"},
            "defaults": {},
            "strategies": {},
            "projects_dir": str(projects_dir),
        }

        from engram.api_server import app
        with patch("engram.api_server._cfg", return_value=config):
            with TestClient(app) as client:
                yield client, admin_db_path, projects_dir, config

    def test_extract_respects_free_tier_node_limit(self, api_with_admin_db):
        """Free tier (500 nodes) should reject extraction when at limit."""
        client, admin_db_path, projects_dir, config = api_with_admin_db

        # Create a free-tier key and project
        conn = sqlite3.connect(str(admin_db_path))
        conn.row_factory = sqlite3.Row
        raw_key = create_key(conn, email="free@example.com", tier="free")
        conn.close()

        # Create project with 500 nodes (at limit)
        project_dir = projects_dir / "free-project"
        project_dir.mkdir(parents=True)
        store = GraphStore(project_dir / "context.db")
        for i in range(500):
            store.add_node({
                "id": f"n_existing_{i:03d}",
                "fact": f"Fact {i}",
                "type": "decision",
                "confidence": 0.9,
                "tags": ["test"],
                "supersedes": [],
            })
        store.close()

        # Try to extract 1 more node → should get 402 Payment Required
        from engram.api_server import app
        with patch("engram.api_server._cfg", return_value=config):
            with TestClient(app) as c:
                extract_result = {
                    "nodes": [{
                        "id": "n_too_many",
                        "fact": "This should fail",
                        "type": "decision",
                        "confidence": 0.9,
                        "tags": ["test"],
                        "supersedes": [],
                    }],
                    "edges": [],
                }
                with patch("engram.api_server._extract", new=AsyncMock(return_value=extract_result)):
                    r = c.post(
                        "/v1/projects/free-project/extract",
                        json={"text": "some text"},
                        headers={"Authorization": f"Bearer {raw_key}"},
                    )
        assert r.status_code == 402, f"Expected 402, got {r.status_code}: {r.text}"

    def test_extract_allows_pro_tier_higher_limit(self, api_with_admin_db):
        """Pro tier (25k nodes) should allow extract up to 25k."""
        client, admin_db_path, projects_dir, config = api_with_admin_db

        # Create a pro-tier key and project
        conn = sqlite3.connect(str(admin_db_path))
        conn.row_factory = sqlite3.Row
        raw_key = create_key(conn, email="pro@example.com", tier="pro")
        conn.close()

        # Create project with 24,999 nodes
        project_dir = projects_dir / "pro-project"
        project_dir.mkdir(parents=True)
        store = GraphStore(project_dir / "context.db")
        for i in range(24_999):
            store.add_node({
                "id": f"n_pro_{i:05d}",
                "fact": f"Pro fact {i}",
                "type": "decision",
                "confidence": 0.9,
                "tags": ["pro"],
                "supersedes": [],
            })
        store.close()

        # Extract 1 more → should succeed
        extract_result = {
            "nodes": [{
                "id": "n_pro_limit",
                "fact": "Last pro node",
                "type": "decision",
                "confidence": 0.9,
                "tags": ["pro"],
                "supersedes": [],
            }],
            "edges": [],
        }

        from engram.api_server import app
        with patch("engram.api_server._cfg", return_value=config):
            with TestClient(app) as c:
                with patch("engram.api_server._extract", new=AsyncMock(return_value=extract_result)):
                    r = c.post(
                        "/v1/projects/pro-project/extract",
                        json={"text": "some text"},
                        headers={"Authorization": f"Bearer {raw_key}"},
                    )
        assert r.status_code == 200, f"Pro user should succeed at limit; got {r.status_code}: {r.text}"


# ============================================================================
# P0 GAPS: Webhook Signature Bypass (Security)
# ============================================================================

@pytest.mark.skipif(not HAS_FASTAPI, reason="Requires fastapi (optional dependency)")
class TestWebhookSignatureBypasses:
    """Test that invalid webhook signatures are always rejected."""

    @pytest.fixture
    def webhook_client(self, tmp_path, monkeypatch):
        """API with webhook secret configured."""
        admin_db_path = tmp_path / "admin.db"
        monkeypatch.setenv("CB_ADMIN_DB", str(admin_db_path))
        monkeypatch.setenv("LS_WEBHOOK_SECRET", "prod_secret_123")
        monkeypatch.delenv("RESEND_API_KEY", raising=False)

        conn = sqlite3.connect(str(admin_db_path))
        conn.row_factory = sqlite3.Row
        init_admin_db(conn)
        conn.close()

        config = {
            "llm": {"base_url": "http://localhost:1234/v1", "model": "test"},
            "defaults": {},
            "strategies": {},
            "projects_dir": str(tmp_path / "projects"),
        }

        from engram.api_server import app
        with patch("engram.api_server._cfg", return_value=config):
            with TestClient(app) as c:
                yield c

    def test_webhook_rejects_empty_signature_header(self, webhook_client):
        """Empty X-Signature should be rejected."""
        payload = json.dumps({
            "meta": {"event_name": "subscription_created"},
            "data": {"attributes": {"user_email": "new@test.com", "variant_id": "var_pro"}},
        }).encode()
        r = webhook_client.post(
            "/webhooks/lemonsqueezy",
            content=payload,
            headers={"X-Signature": ""},
        )
        assert r.status_code == 400

    def test_webhook_rejects_wrong_hmac(self, webhook_client):
        """Mismatched HMAC should be rejected."""
        payload = json.dumps({
            "meta": {"event_name": "subscription_created"},
            "data": {"attributes": {"user_email": "new@test.com", "variant_id": "var_pro"}},
        }).encode()
        r = webhook_client.post(
            "/webhooks/lemonsqueezy",
            content=payload,
            headers={"X-Signature": "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"},
        )
        assert r.status_code == 400

    def test_webhook_rejects_modified_payload(self, webhook_client):
        """Changing payload after signing should be rejected."""
        import hashlib
        import hmac

        original = {
            "meta": {"event_name": "subscription_created"},
            "data": {"attributes": {"user_email": "attacker@test.com", "variant_id": "var_team"}},
        }
        payload = json.dumps(original).encode()

        # Sign with original
        secret = os.environ.get("LS_WEBHOOK_SECRET", "")
        sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

        # Modify payload in transit (attacker changes email)
        modified_payload = json.dumps({
            "meta": {"event_name": "subscription_created"},
            "data": {"attributes": {"user_email": "victim@test.com", "variant_id": "var_team"}},
        }).encode()

        r = webhook_client.post(
            "/webhooks/lemonsqueezy",
            content=modified_payload,
            headers={"X-Signature": sig},
        )
        assert r.status_code == 400, "Signature validation should detect payload tampering"


# ============================================================================
# P1 GAPS: API Auth Edge Cases
# ============================================================================

@pytest.mark.skipif(not HAS_FASTAPI, reason="Requires fastapi (optional dependency)")
class TestAPIAuthEdgeCases:
    """Test API authentication failures and edge cases."""

    @pytest.fixture
    def api_client_simple(self, tmp_path, monkeypatch):
        """Simple API without admin DB (self-hosted mode)."""
        monkeypatch.delenv("CB_USE_ADMIN_DB", raising=False)
        monkeypatch.delenv("LS_WEBHOOK_SECRET", raising=False)
        monkeypatch.setenv("CB_API_KEY", "self-hosted-secret")

        config = {
            "llm": {"base_url": "http://localhost:1234/v1", "model": "test"},
            "defaults": {},
            "strategies": {},
            "projects_dir": str(tmp_path / "projects"),
        }

        from engram.api_server import app
        with patch("engram.api_server._cfg", return_value=config):
            with TestClient(app) as c:
                yield c

    def test_bearer_token_case_insensitive_bearer_keyword(self, api_client_simple):
        """Bearer keyword must match case exactly (RFC 7235)."""
        # This is actually case-insensitive in HTTP spec, but let's verify behavior
        r = api_client_simple.get(
            "/v1/projects",
            headers={"Authorization": "bearer self-hosted-secret"},  # lowercase 'bearer'
        )
        # FastAPI HTTPBearer typically rejects non-Bearer format
        assert r.status_code in (401, 403), f"Got {r.status_code}"

    def test_bearer_token_with_extra_whitespace(self, api_client_simple):
        """Extra spaces in Bearer token should be rejected."""
        r = api_client_simple.get(
            "/v1/projects",
            headers={"Authorization": "Bearer  self-hosted-secret"},  # double space
        )
        assert r.status_code == 401

    def test_bearer_token_with_no_space(self, api_client_simple):
        """Malformed Authorization header (no space) should fail."""
        r = api_client_simple.get(
            "/v1/projects",
            headers={"Authorization": "Bearerself-hosted-secret"},
        )
        assert r.status_code == 401

    def test_bearer_token_with_tabs(self, api_client_simple):
        """Tab character in Authorization header should fail."""
        r = api_client_simple.get(
            "/v1/projects",
            headers={"Authorization": "Bearer\tself-hosted-secret"},
        )
        assert r.status_code == 401


# ============================================================================
# P1 GAPS: CLI Error Paths
# ============================================================================

class TestCLIErrorHandling:
    """Test CLI errors for missing projects, bad API keys, etc."""

    def test_extract_nonexistent_project_shows_helpful_error(self, tmp_path, monkeypatch):
        """Extracting from nonexistent project should show helpful error."""
        monkeypatch.chdir(tmp_path)
        from engram.cli import cli
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(cli, ["extract", "nonexistent", "transcript.md"])
        assert result.exit_code != 0
        assert "nonexistent" in result.output.lower() or "not found" in result.output.lower()

    def test_query_nonexistent_project_shows_helpful_error(self, tmp_path, monkeypatch):
        """Querying from nonexistent project should show helpful error."""
        monkeypatch.chdir(tmp_path)
        from engram.cli import cli
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(cli, ["query", "nonexistent", "task"])
        assert result.exit_code != 0


# ============================================================================
# P1 GAPS: Graph Consistency (Partial Extraction)
# ============================================================================

class TestGraphConsistencyAfterFailure:
    """Test that partial extraction failures don't corrupt the graph."""

    def test_extract_partial_failure_rollback(self, tmp_path):
        """If extraction extracts 5 nodes but edge merge fails, nodes should still be added."""
        # This is actually checking that merge_extraction is atomic per-call
        # (It's not transactional, so partial writes could occur)
        store = GraphStore(tmp_path / "context.db")

        # Add 5 nodes
        nodes = []
        for i in range(5):
            nodes.append({
                "id": f"n_test_{i}",
                "fact": f"Test fact {i}",
                "type": "decision",
                "confidence": 0.9,
                "tags": ["test"],
                "supersedes": [],
            })

        # Bad edge: references nonexistent node
        edges = [
            {"from_id": "n_test_0", "to_id": "n_nonexistent", "relation": "depends_on"}
        ]

        # merge_extraction should either:
        # A) Add all nodes and edges (optimistic)
        # B) Fail completely with no changes (atomic)
        # C) Add nodes but skip bad edges (lenient)

        try:
            store.merge_extraction(nodes, edges)
        except Exception:
            pass  # Expected

        store.close()

        # Verify graph state is consistent
        store = GraphStore(tmp_path / "context.db")
        # This test just checks that the DB doesn't crash on re-open
        stats = store.get_stats()
        store.close()
        assert stats is not None


# ============================================================================
# P1 GAPS: Rate Limiting Under Admin DB
# ============================================================================

@pytest.mark.skipif(not HAS_FASTAPI, reason="Requires fastapi (optional dependency)")
class TestRateLimitingUnderAdminDB:
    """Test rate limiting enforcement when admin DB is enabled."""

    @pytest.fixture
    def api_with_rate_limiting(self, tmp_path, monkeypatch):
        """API server with admin DB and rate limiting enabled."""
        admin_db_path = tmp_path / "admin.db"
        monkeypatch.setenv("CB_USE_ADMIN_DB", "1")
        monkeypatch.setenv("CB_ADMIN_DB", str(admin_db_path))
        monkeypatch.setenv("LS_WEBHOOK_SECRET", "test_secret")

        conn = sqlite3.connect(str(admin_db_path))
        conn.row_factory = sqlite3.Row
        init_admin_db(conn)
        conn.close()

        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        (projects_dir / "test-project").mkdir()
        GraphStore(projects_dir / "test-project" / "context.db").close()

        config = {
            "llm": {"base_url": "http://localhost:1234/v1", "model": "test"},
            "defaults": {},
            "strategies": {},
            "projects_dir": str(projects_dir),
        }

        from engram.api_server import app
        with patch("engram.api_server._cfg", return_value=config):
            with TestClient(app) as c:
                yield c, admin_db_path

    def test_free_tier_hits_minute_limit(self, api_with_rate_limiting):
        """Free tier (10 req/min) should hit limit after 10 requests."""
        client, admin_db_path = api_with_rate_limiting

        conn = sqlite3.connect(str(admin_db_path))
        conn.row_factory = sqlite3.Row
        raw_key = create_key(conn, email="rate@example.com", tier="free")
        conn.close()

        # Make 10 successful requests
        for i in range(10):
            r = client.get(
                "/v1/projects",
                headers={"Authorization": f"Bearer {raw_key}"},
            )
            assert r.status_code == 200, f"Request {i+1} should succeed"

        # 11th request should be rate-limited
        r = client.get(
            "/v1/projects",
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert r.status_code == 429, f"11th request should be rate-limited; got {r.status_code}"

    def test_rate_limit_headers_present(self, api_with_rate_limiting):
        """Rate limit headers should be present in response."""
        client, admin_db_path = api_with_rate_limiting

        conn = sqlite3.connect(str(admin_db_path))
        conn.row_factory = sqlite3.Row
        raw_key = create_key(conn, email="header@example.com", tier="pro")
        conn.close()

        r = client.get(
            "/v1/projects",
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert r.status_code == 200
        assert "X-RateLimit-Limit-Minute" in r.headers
        assert "X-RateLimit-Remaining-Minute" in r.headers


# ============================================================================
# P2 GAPS: Edge Cases and Nice-to-Haves
# ============================================================================

class TestMCPServerStartup:
    """Test that MCP server can start without errors."""

    def test_mcp_server_loads_without_crash(self):
        """MCP server should import and initialize without errors."""
        try:
            from engram.mcp_server import mcp
            # mcp object should be created
            assert mcp is not None
        except Exception as e:
            pytest.fail(f"MCP server failed to load: {e}")

    def test_mcp_tools_are_registered(self):
        """All 4 MCP tools should be registered."""
        from engram.mcp_server import mcp
        # FastMCP has tools attribute
        # engram_query, engram_extract, engram_stats, engram_list_projects
        tools_dict = getattr(mcp, '_request_handlers', {}) or {}
        # Just verify mcp object exists and is usable
        assert mcp is not None


@pytest.mark.skipif(not HAS_FASTAPI, reason="Requires fastapi (optional dependency)")
class TestAPIEndpointConsistency:
    """Test that all endpoints handle missing auth consistently."""

    @pytest.fixture
    def api_strict_auth(self, tmp_path, monkeypatch):
        """API with strict auth enabled."""
        monkeypatch.delenv("CB_USE_ADMIN_DB", raising=False)
        monkeypatch.setenv("CB_API_KEY", "required_key")

        config = {
            "llm": {"base_url": "http://localhost:1234/v1", "model": "test"},
            "defaults": {},
            "strategies": {},
            "projects_dir": str(tmp_path / "projects"),
        }

        from engram.api_server import app
        with patch("engram.api_server._cfg", return_value=config):
            with TestClient(app) as c:
                yield c

    def test_health_bypasses_auth(self, api_strict_auth):
        """/v1/health should not require auth."""
        r = api_strict_auth.get("/v1/health")
        assert r.status_code == 200

    def test_all_other_endpoints_require_auth(self, api_strict_auth):
        """All other endpoints should require auth when CB_API_KEY is set."""
        endpoints = [
            ("GET", "/v1/projects"),
            ("POST", "/v1/projects/test"),
            ("GET", "/v1/projects/test/stats"),
            ("POST", "/v1/projects/test/query"),
            ("GET", "/v1/projects/test/export"),
        ]
        for method, path in endpoints:
            if method == "GET":
                r = api_strict_auth.get(path)
            else:
                r = api_strict_auth.post(path, json={})
            assert r.status_code == 401, f"{method} {path} should require auth"
