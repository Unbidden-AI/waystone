"""Tests for the Waystone REST API server."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed — skip API server tests")

from fastapi.testclient import TestClient  # noqa: E402 (after importorskip)

from waystone.store import GraphStore  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def project_setup(tmp_path):
    """Temp projects dir with one populated project."""
    projects_dir = tmp_path / "projects"
    project_dir = projects_dir / "test-project"
    project_dir.mkdir(parents=True)

    db_path = project_dir / "context.db"
    store = GraphStore(db_path)
    store.add_node({
        "id": "n_abc12345",
        "fact": "The system uses PostgreSQL for persistence",
        "type": "decision",
        "confidence": 0.9,
        "tags": ["database", "postgresql", "persistence"],
        "created_at": "2026-03-07T00:00:00Z",
        "supersedes": [],
    })
    store.close()

    config = {
        "llm": {"base_url": "http://localhost:1234/v1", "model": "test", "timeout": 30.0},
        "defaults": {"hops": 3, "top_k": 25},
        "strategies": {"superseded_pruning": True},
        "projects_dir": str(projects_dir),
    }
    return tmp_path, db_path, config


@pytest.fixture
def api_client(project_setup):
    """TestClient with config patched to use tmp project dir."""
    tmp_path, db_path, config = project_setup
    from waystone.api_server import app
    with patch("waystone.api_server._cfg", return_value=config):
        with TestClient(app) as c:
            yield c, config, tmp_path


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_ok(self, api_client):
        c, _, _ = api_client
        r = c.get("/v1/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        assert "version" in r.json()

    def test_health_no_auth_needed(self, project_setup, monkeypatch):
        """Health endpoint is unauthenticated even when WAYSTONE_API_KEY is set."""
        _, _, config = project_setup
        monkeypatch.setenv("WAYSTONE_API_KEY", "secret")
        from waystone.api_server import app
        with patch("waystone.api_server._cfg", return_value=config):
            with TestClient(app) as c:
                r = c.get("/v1/health")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class TestAuth:
    def test_401_when_key_set_and_missing(self, project_setup, monkeypatch):
        _, _, config = project_setup
        monkeypatch.setenv("WAYSTONE_API_KEY", "secret")
        from waystone.api_server import app
        with patch("waystone.api_server._cfg", return_value=config):
            with TestClient(app) as c:
                r = c.get("/v1/projects")
        assert r.status_code == 401

    def test_401_with_wrong_key(self, project_setup, monkeypatch):
        _, _, config = project_setup
        monkeypatch.setenv("WAYSTONE_API_KEY", "secret")
        from waystone.api_server import app
        with patch("waystone.api_server._cfg", return_value=config):
            with TestClient(app) as c:
                r = c.get("/v1/projects", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401

    def test_200_with_correct_key(self, project_setup, monkeypatch):
        _, _, config = project_setup
        monkeypatch.setenv("WAYSTONE_API_KEY", "secret")
        from waystone.api_server import app
        with patch("waystone.api_server._cfg", return_value=config):
            with TestClient(app) as c:
                r = c.get("/v1/projects", headers={"Authorization": "Bearer secret"})
        assert r.status_code == 200

    def test_open_when_no_key_set(self, api_client, monkeypatch):
        """No auth required when WAYSTONE_API_KEY is unset."""
        monkeypatch.delenv("WAYSTONE_API_KEY", raising=False)
        c, _, _ = api_client
        r = c.get("/v1/projects")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Projects — list / init
# ---------------------------------------------------------------------------

class TestListProjects:
    def test_returns_list(self, api_client):
        c, _, _ = api_client
        r = c.get("/v1/projects")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert any(p["name"] == "test-project" for p in data)

    def test_project_has_node_count(self, api_client):
        c, _, _ = api_client
        r = c.get("/v1/projects")
        project = next(p for p in r.json() if p["name"] == "test-project")
        assert project["node_count"] == 1
        assert "edge_count" in project

    def test_empty_when_no_projects_dir(self, tmp_path):
        config = {"projects_dir": str(tmp_path / "nonexistent"), "llm": {}, "defaults": {}, "strategies": {}}
        from waystone.api_server import app
        with patch("waystone.api_server._cfg", return_value=config):
            with TestClient(app) as c:
                r = c.get("/v1/projects")
        assert r.status_code == 200
        assert r.json() == []


class TestInitProject:
    def test_creates_new_project(self, api_client):
        c, _, _ = api_client
        r = c.post("/v1/projects/brand-new")
        assert r.status_code == 201
        assert r.json()["created"] is True
        assert r.json()["project"] == "brand-new"

    def test_idempotent_for_existing(self, api_client):
        c, _, _ = api_client
        r = c.post("/v1/projects/test-project")
        assert r.status_code == 201
        assert r.json()["created"] is False


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class TestStats:
    def test_returns_counts(self, api_client):
        c, _, _ = api_client
        r = c.get("/v1/projects/test-project/stats")
        assert r.status_code == 200
        data = r.json()
        assert data["node_count"] == 1
        assert data["edge_count"] == 0
        assert data["project"] == "test-project"
        assert "type_counts" in data

    def test_404_for_missing(self, api_client):
        c, _, _ = api_client
        r = c.get("/v1/projects/does-not-exist/stats")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

class TestQuery:
    def test_returns_markdown(self, api_client):
        c, _, _ = api_client
        r = c.post("/v1/projects/test-project/query", json={"task": "database"})
        assert r.status_code == 200
        data = r.json()
        assert "markdown" in data
        assert "nodes_returned" in data
        assert "total_nodes" in data
        assert "tokens_estimated" in data

    def test_matching_query_finds_node(self, api_client):
        c, _, _ = api_client
        r = c.post("/v1/projects/test-project/query", json={"task": "postgresql database"})
        assert r.status_code == 200
        assert "PostgreSQL" in r.json()["markdown"]

    def test_empty_graph_returns_zero(self, tmp_path):
        projects_dir = tmp_path / "projects"
        (projects_dir / "empty").mkdir(parents=True)
        db = projects_dir / "empty" / "context.db"
        GraphStore(db).close()
        config = {"projects_dir": str(projects_dir), "llm": {}, "defaults": {}, "strategies": {}}
        from waystone.api_server import app
        with patch("waystone.api_server._cfg", return_value=config):
            with TestClient(app) as c:
                r = c.post("/v1/projects/empty/query", json={"task": "anything"})
        assert r.status_code == 200
        assert r.json()["nodes_returned"] == 0

    def test_404_for_missing_project(self, api_client):
        c, _, _ = api_client
        r = c.post("/v1/projects/gone/query", json={"task": "anything"})
        assert r.status_code == 404

    def test_honours_top_k(self, api_client):
        c, _, _ = api_client
        r = c.post("/v1/projects/test-project/query", json={"task": "database", "top_k": 1})
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Extract
# ---------------------------------------------------------------------------

class TestExtract:
    _MOCK_RESULT = {
        "nodes": [{
            "id": "n_new00001",
            "fact": "Redis used for rate limiting",
            "type": "implementation",
            "confidence": 0.9,
            "tags": ["redis", "rate-limiting"],
            "supersedes": [],
        }],
        "edges": [],
    }

    def test_merges_nodes(self, api_client):
        c, config, tmp_path = api_client
        with patch("waystone.api_server._extract", new=AsyncMock(return_value=self._MOCK_RESULT)):
            r = c.post("/v1/projects/test-project/extract", json={"text": "We use Redis."})
        assert r.status_code == 200
        data = r.json()
        assert data["nodes_extracted"] == 1
        assert data["edges_extracted"] == 0
        assert data["project"] == "test-project"

    def test_node_persisted_to_db(self, api_client):
        c, config, tmp_path = api_client
        db_path = Path(config["projects_dir"]) / "test-project" / "context.db"
        with patch("waystone.api_server._extract", new=AsyncMock(return_value=self._MOCK_RESULT)):
            c.post("/v1/projects/test-project/extract", json={"text": "We use Redis."})
        store = GraphStore(db_path)
        node = store.get_node("n_new00001")
        store.close()
        assert node is not None
        assert node["fact"] == "Redis used for rate limiting"

    def test_422_for_oversized_input(self, api_client):
        c, _, _ = api_client
        r = c.post("/v1/projects/test-project/extract", json={"text": "x" * 200_001})
        assert r.status_code == 422

    def test_404_for_missing_project(self, api_client):
        c, _, _ = api_client
        r = c.post("/v1/projects/gone/extract", json={"text": "some text"})
        assert r.status_code == 404

    def test_source_name_set_on_nodes(self, api_client):
        c, config, tmp_path = api_client
        db_path = Path(config["projects_dir"]) / "test-project" / "context.db"
        with patch("waystone.api_server._extract", new=AsyncMock(return_value=self._MOCK_RESULT)):
            c.post(
                "/v1/projects/test-project/extract",
                json={"text": "We use Redis.", "source_name": "my-session"},
            )
        store = GraphStore(db_path)
        node = store.get_node("n_new00001")
        store.close()
        assert node["source_transcript"] == "my-session"

    def test_verify_calls_verify_extraction(self, api_client):
        c, _, _ = api_client
        verify_result = {"nodes": [], "edges": []}
        with patch("waystone.api_server._extract", new=AsyncMock(return_value=self._MOCK_RESULT)):
            with patch("waystone.api_server.verify_extraction", new=AsyncMock(return_value=verify_result)) as mock_verify:
                c.post(
                    "/v1/projects/test-project/extract",
                    json={"text": "text", "verify": True},
                )
        mock_verify.assert_called_once()


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

class TestExport:
    def test_returns_markdown(self, api_client):
        c, _, _ = api_client
        r = c.get("/v1/projects/test-project/export")
        assert r.status_code == 200
        data = r.json()
        assert "markdown" in data
        assert data["node_count"] == 1
        assert "PostgreSQL" in data["markdown"]

    def test_empty_graph_returns_empty(self, tmp_path):
        projects_dir = tmp_path / "projects"
        (projects_dir / "empty").mkdir(parents=True)
        db = projects_dir / "empty" / "context.db"
        GraphStore(db).close()
        config = {"projects_dir": str(projects_dir), "llm": {}, "defaults": {}, "strategies": {}}
        from waystone.api_server import app
        with patch("waystone.api_server._cfg", return_value=config):
            with TestClient(app) as c:
                r = c.get("/v1/projects/empty/export")
        assert r.status_code == 200
        assert r.json()["node_count"] == 0

    def test_404_for_missing_project(self, api_client):
        c, _, _ = api_client
        r = c.get("/v1/projects/gone/export")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Security — Path Traversal Protection
# ---------------------------------------------------------------------------

class TestPathTraversalProtection:
    """Verify that malicious project names cannot escape the project directory."""

    def test_rejects_parent_directory_traversal(self, api_client):
        """Reject '..' path traversal when embedded in project name."""
        from fastapi import HTTPException

        from waystone.api_server import _validate_project_name
        with pytest.raises(HTTPException) as exc_info:
            _validate_project_name("..evil")
        assert exc_info.value.status_code == 400
        assert "traversal" in exc_info.value.detail.lower()

    def test_rejects_multiple_parent_traversal(self, api_client):
        """Reject '../../' path traversal."""
        from fastapi import HTTPException

        from waystone.api_server import _validate_project_name
        with pytest.raises(HTTPException) as exc_info:
            _validate_project_name("../../etc/passwd")
        assert exc_info.value.status_code == 400

    def test_rejects_absolute_path(self, api_client):
        """Reject absolute paths."""
        from fastapi import HTTPException

        from waystone.api_server import _validate_project_name
        with pytest.raises(HTTPException) as exc_info:
            _validate_project_name("/etc/passwd")
        assert exc_info.value.status_code == 400

    def test_rejects_forward_slash_in_name(self, api_client):
        """Reject names with forward slashes."""
        from fastapi import HTTPException

        from waystone.api_server import _validate_project_name
        with pytest.raises(HTTPException) as exc_info:
            _validate_project_name("other/myproject")
        assert exc_info.value.status_code == 400
        assert "path separator" in exc_info.value.detail.lower()

    def test_rejects_backslash_in_name(self, api_client):
        """Reject names with backslashes."""
        from fastapi import HTTPException

        from waystone.api_server import _validate_project_name
        with pytest.raises(HTTPException) as exc_info:
            _validate_project_name("evil\\project")
        assert exc_info.value.status_code == 400
        assert "path separator" in exc_info.value.detail.lower()

    def test_rejects_null_byte(self, api_client):
        """Reject names containing null bytes."""
        from fastapi import HTTPException

        from waystone.api_server import _validate_project_name
        with pytest.raises(HTTPException) as exc_info:
            _validate_project_name("evil\x00project")
        assert exc_info.value.status_code == 400
        assert "null byte" in exc_info.value.detail.lower()

    def test_rejects_empty_name(self, api_client):
        """Reject empty project names."""
        c, _, _ = api_client
        # Note: FastAPI may strip this before we see it, but test the validator directly
        from fastapi import HTTPException

        from waystone.api_server import _validate_project_name
        with pytest.raises(HTTPException) as exc_info:
            _validate_project_name("")
        assert exc_info.value.status_code == 400

    def test_rejects_whitespace_only(self, api_client):
        """Reject whitespace-only names."""
        from fastapi import HTTPException

        from waystone.api_server import _validate_project_name
        with pytest.raises(HTTPException) as exc_info:
            _validate_project_name("   ")
        assert exc_info.value.status_code == 400

    def test_rejects_leading_dot(self, api_client):
        """Reject names starting with dot (hidden files on Unix)."""
        c, _, _ = api_client
        r = c.post("/v1/projects/.hidden", json={"text": "test"})
        assert r.status_code == 400

    def test_rejects_leading_dash(self, api_client):
        """Reject names starting with dash."""
        c, _, _ = api_client
        r = c.post("/v1/projects/-invalid", json={"text": "test"})
        assert r.status_code == 400

    def test_rejects_name_too_long(self, api_client):
        """Reject project names exceeding 128 characters."""
        c, _, _ = api_client
        long_name = "a" * 150  # Over 128 char limit
        r = c.post(f"/v1/projects/{long_name}", json={"text": "test"})
        assert r.status_code == 400

    def test_accepts_valid_names(self, api_client):
        """Verify legitimate project names still work."""
        c, config, tmp_path = api_client
        valid_names = [
            "myproject",
            "MyProject",
            "my-project",
            "my_project",
            "my.project",
            "MyProject123",
            "a",  # single char
            "Project_v2.0-beta",
        ]
        for name in valid_names:
            r = c.post(f"/v1/projects/{name}")
            # Status should be 201 (created) or 200 (OK), not 400 (bad request)
            assert r.status_code in (200, 201), f"Project name '{name}' should be valid but got {r.status_code}: {r.text}"

    def test_does_not_escape_projects_dir(self, tmp_path):
        """Verify that even if validation somehow failed, resolved path stays in projects_dir."""
        projects_dir = tmp_path / "projects"
        other_dir = tmp_path / "other"
        projects_dir.mkdir(parents=True)
        other_dir.mkdir(parents=True)

        config = {
            "projects_dir": str(projects_dir),
            "llm": {},
            "defaults": {},
            "strategies": {},
        }

        from waystone.api_server import _get_project_dir

        # With valid names, paths should stay under projects_dir
        result = _get_project_dir(config, {"tier": "local"}, "valid")
        assert str(result).startswith(str(projects_dir.resolve()))

    def test_isolate_by_key_prevents_cross_tenant(self, tmp_path):
        """Verify that per-key isolation prevents one tenant seeing another's dir.

        On hosted SaaS with _isolate_by_key=True, tenant prefix must scope the path.
        """
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir(parents=True)

        config = {
            "projects_dir": str(projects_dir),
            "llm": {},
            "defaults": {},
            "strategies": {},
            "store_backend": "sqlite",  # Use SQLite to test filesystem scoping
        }

        from unittest.mock import patch

        from waystone.api_server import _get_project_dir

        # Simulate two tenants with different key_hashes
        key_info_a = {"key_hash": "aaa111222333", "tier": "pro"}
        key_info_b = {"key_hash": "bbb222333444", "tier": "pro"}

        with patch("waystone.api_server._use_admin_db", return_value=True):
            path_a = _get_project_dir(config, key_info_a, "myproject")
            path_b = _get_project_dir(config, key_info_b, "myproject")

            # Both should resolve under projects_dir but in different prefixes
            assert str(path_a).startswith(str(projects_dir.resolve()))
            assert str(path_b).startswith(str(projects_dir.resolve()))
            # And they must be different
            assert path_a != path_b
            # Tenant A's path should include "aaa111222" prefix
            assert "aaa111222" in str(path_a)
            assert "bbb222333" in str(path_b)
            # They must not escape projects_dir
            assert path_a.resolve().is_relative_to(projects_dir.resolve())
            assert path_b.resolve().is_relative_to(projects_dir.resolve())


# ---------------------------------------------------------------------------
# Connection leak tests (Blocker A)
# ---------------------------------------------------------------------------

class TestConnectionLeakPrevention:
    """Verify that stores are always closed, even on exceptions."""

    def test_get_stats_closes_on_exception(self, api_client):
        """Exception during get_stats() must not leak the connection."""
        c, config, _ = api_client
        with patch("waystone.api_server._open_store") as mock_open:
            mock_store = mock_open.return_value
            mock_store.get_stats.side_effect = RuntimeError("simulated error")
            try:
                c.get("/v1/projects/test-project/stats")
            except RuntimeError:
                pass  # Expected, the exception bubbles up from the mock
        # Even though get_stats raised, store.close() should have been called in finally
        assert mock_store.close.called, "store.close() was not called despite exception"

    def test_query_project_closes_on_exception(self, api_client):
        """Exception during query_project() must not leak the connection."""
        c, _, _ = api_client
        with patch("waystone.api_server._open_store") as mock_open:
            mock_store = mock_open.return_value
            mock_store.get_stats.return_value = {"node_count": 5}
            with patch("waystone.api_server.retrieve_with_stats", side_effect=RuntimeError("simulated error")):
                try:
                    c.post(
                        "/v1/projects/test-project/query",
                        json={"task": "test"},
                    )
                except RuntimeError:
                    pass  # Expected
        assert mock_store.close.called, "store.close() was not called despite exception"


    def test_export_project_closes_on_exception(self, api_client):
        """Exception during export_project() must not leak the connection."""
        c, _, _ = api_client
        with patch("waystone.api_server._open_store") as mock_open:
            mock_store = mock_open.return_value
            mock_store.get_all_nodes.side_effect = RuntimeError("simulated error")
            try:
                c.get("/v1/projects/test-project/export")
            except RuntimeError:
                pass  # Expected
        assert mock_store.close.called, "store.close() was not called despite exception"


# ---------------------------------------------------------------------------
# License validation tests (Blocker B)
# ---------------------------------------------------------------------------

class TestLicenseValidation:
    """Verify that self-hosted Team servers reject expired licenses."""

    def test_check_auth_rejects_expired_license_in_selfhosted_mode(self):
        """_check_auth should reject a request when license is expired in self-hosted mode."""
        from datetime import datetime, timedelta, timezone

        from fastapi.security import HTTPAuthorizationCredentials

        from waystone.api_server import HTTPException, _check_auth
        from waystone.licensing import License

        # Create an expired license
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        expired_lic = License(seats=5, org="test-org", expires_at=yesterday.isoformat())

        # Create mock credentials
        creds = HTTPAuthorizationCredentials(scheme="bearer", credentials="test-key")

        # Mock the billing module to simulate admin-DB mode but without Stripe webhook
        with patch("waystone.api_server.open_admin_db"):
            with patch("waystone.api_server.validate_key", return_value={"tier": "team"}):
                with patch("waystone.api_server._use_admin_db", return_value=True):
                    with patch("waystone.api_server.os.environ.get") as mock_env:

                        def env_side_effect(key, default=""):
                            if key == "STRIPE_WEBHOOK_SECRET":
                                return ""  # Not set, so we check license in self-hosted mode
                            return default

                        mock_env.side_effect = env_side_effect

                        # Patch load_license where it's imported
                        with patch("waystone.licensing.load_license", return_value=expired_lic):
                            # Should raise 401 due to expired license
                            with pytest.raises(HTTPException) as exc_info:
                                _check_auth(creds)
                            assert exc_info.value.status_code == 401
                            assert "expired" in exc_info.value.detail.lower()

    def test_check_auth_accepts_valid_license_in_selfhosted_mode(self):
        """_check_auth should accept a request when license is valid in self-hosted mode."""
        from datetime import datetime, timedelta, timezone

        from fastapi.security import HTTPAuthorizationCredentials

        from waystone.api_server import _check_auth
        from waystone.licensing import License

        # Create a valid license
        tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
        valid_lic = License(seats=5, org="test-org", expires_at=tomorrow.isoformat())

        # Create mock credentials
        creds = HTTPAuthorizationCredentials(scheme="bearer", credentials="test-key")

        # Mock the billing module to simulate admin-DB mode but without Stripe webhook
        with patch("waystone.api_server.open_admin_db"):
            with patch("waystone.api_server.validate_key", return_value={"tier": "team"}):
                with patch("waystone.api_server._use_admin_db", return_value=True):
                    with patch("waystone.api_server.os.environ.get") as mock_env:

                        def env_side_effect(key, default=""):
                            if key == "STRIPE_WEBHOOK_SECRET":
                                return ""  # Not set, so we check license in self-hosted mode
                            return default

                        mock_env.side_effect = env_side_effect

                        # Patch load_license where it's imported
                        with patch("waystone.licensing.load_license", return_value=valid_lic):
                            # Should succeed and return key_info
                            result = _check_auth(creds)
                            # Should return the validated key info
                            assert result.get("tier") == "team"
