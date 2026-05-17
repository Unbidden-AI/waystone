"""Tests for the Waystone REST API server."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
