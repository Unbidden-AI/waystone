"""Integration tests for node limit enforcement in the API server.

Tests verify that billing tier limits (free=500, pro=25000, team=250000) are
properly enforced during extraction, with proper transaction handling to prevent
race conditions.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed — skip API server tests")

from fastapi.testclient import TestClient  # noqa: E402 (after importorskip)

from waystone.billing import _hash_key, create_key, open_admin_db  # noqa: E402
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
        "fact": "Initial fact",
        "type": "decision",
        "confidence": 0.9,
        "tags": ["initial"],
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
# Mock extraction result
# ---------------------------------------------------------------------------

_MOCK_EXTRACT_1_NODE = {
    "nodes": [{
        "id": "n_new00001",
        "fact": "Test fact",
        "type": "decision",
        "confidence": 0.9,
        "tags": ["test"],
        "supersedes": [],
    }],
    "edges": [],
}

_MOCK_EXTRACT_100_NODES = {
    "nodes": [
        {
            "id": f"n_new{i:05d}",
            "fact": f"Fact {i}",
            "type": "decision",
            "confidence": 0.9,
            "tags": [f"tag{i}"],
            "supersedes": [],
        }
        for i in range(100)
    ],
    "edges": [],
}


# ---------------------------------------------------------------------------
# Tests: Free tier (500 node limit)
# ---------------------------------------------------------------------------

class TestNodeLimitFreeTier:
    """Free tier limited to 500 nodes."""

    def test_free_tier_at_499_nodes_succeeds(self, tmp_path, monkeypatch):
        """Extraction succeeds when at 499 nodes (below limit)."""
        # Setup: create admin DB with free-tier key
        admin_db_path = tmp_path / "admin.db"
        admin_conn = open_admin_db(admin_db_path)
        raw_key = create_key(admin_conn, email="user@test.com", tier="free")
        admin_conn.close()

        # Setup: project with 499 nodes
        projects_dir = tmp_path / "projects"
        project_dir = projects_dir / _hash_key(raw_key)[:12] / "test-project"
        project_dir.mkdir(parents=True)
        db_path = project_dir / "context.db"

        store = GraphStore(db_path, dedup_threshold=1.1)  # disable semantic dedup so the seeded count is exact (CI has no embedder; dev machines do)
        for i in range(499):
            store.add_node({
                "id": f"n_existing_{i}",
                "fact": f"Existing fact {i}",
                "type": "decision",
                "confidence": 0.9,
                "tags": [f"tag{i}"],
                "created_at": "2026-03-07T00:00:00Z",
                "supersedes": [],
            })
        store.close()

        config = {
            "llm": {"base_url": "http://localhost:1234/v1", "model": "test"},
            "defaults": {"hops": 3, "top_k": 25},
            "projects_dir": str(projects_dir),
        }

        monkeypatch.setenv("CB_USE_ADMIN_DB", "1")
        monkeypatch.setenv("CB_ADMIN_DB", str(admin_db_path))

        from waystone.api_server import app
        with patch("waystone.api_server._cfg", return_value=config):
            with TestClient(app) as c:
                with patch("waystone.api_server._extract", new=AsyncMock(return_value=_MOCK_EXTRACT_1_NODE)):
                    r = c.post(
                        "/v1/projects/test-project/extract",
                        json={"text": "Add one node"},
                        headers={"Authorization": f"Bearer {raw_key}"},
                    )

        assert r.status_code == 200
        assert r.json()["nodes_extracted"] == 1

    def test_free_tier_at_500_nodes_fails(self, tmp_path, monkeypatch):
        """Extraction fails when at exactly 500 nodes (at limit)."""
        # Setup: create admin DB with free-tier key
        admin_db_path = tmp_path / "admin.db"
        admin_conn = open_admin_db(admin_db_path)
        raw_key = create_key(admin_conn, email="user@test.com", tier="free")
        admin_conn.close()

        # Setup: project with exactly 500 nodes
        projects_dir = tmp_path / "projects"
        project_dir = projects_dir / _hash_key(raw_key)[:12] / "test-project"
        project_dir.mkdir(parents=True)
        db_path = project_dir / "context.db"

        store = GraphStore(db_path, dedup_threshold=1.1)  # disable semantic dedup so the seeded count is exact (CI has no embedder; dev machines do)
        for i in range(500):
            store.add_node({
                "id": f"n_existing_{i}",
                "fact": f"Existing fact {i}",
                "type": "decision",
                "confidence": 0.9,
                "tags": [f"tag{i}"],
                "created_at": "2026-03-07T00:00:00Z",
                "supersedes": [],
            })
        store.close()

        config = {
            "llm": {"base_url": "http://localhost:1234/v1", "model": "test"},
            "defaults": {"hops": 3, "top_k": 25},
            "projects_dir": str(projects_dir),
        }

        monkeypatch.setenv("CB_USE_ADMIN_DB", "1")
        monkeypatch.setenv("CB_ADMIN_DB", str(admin_db_path))

        from waystone.api_server import app
        with patch("waystone.api_server._cfg", return_value=config):
            with TestClient(app) as c:
                with patch("waystone.api_server._extract", new=AsyncMock(return_value=_MOCK_EXTRACT_1_NODE)):
                    r = c.post(
                        "/v1/projects/test-project/extract",
                        json={"text": "Try to add one node"},
                        headers={"Authorization": f"Bearer {raw_key}"},
                    )

        assert r.status_code == 402  # Payment Required
        data = r.json()
        assert "Node limit reached" in data["detail"]["message"]
        assert "free" in data["detail"]["message"].lower()

    def test_free_tier_multiple_nodes_fails_at_limit(self, tmp_path, monkeypatch):
        """Extraction with multiple new nodes fails when it would exceed limit."""
        # Setup: create admin DB with free-tier key
        admin_db_path = tmp_path / "admin.db"
        admin_conn = open_admin_db(admin_db_path)
        raw_key = create_key(admin_conn, email="user@test.com", tier="free")
        admin_conn.close()

        # Setup: project already at the 500-node limit — a multi-node extract is
        # blocked the same as a single-node one (enforcement is on the current
        # count, so any extract is rejected once you're at the limit).
        projects_dir = tmp_path / "projects"
        project_dir = projects_dir / _hash_key(raw_key)[:12] / "test-project"
        project_dir.mkdir(parents=True)
        db_path = project_dir / "context.db"

        store = GraphStore(db_path, dedup_threshold=1.1)  # disable semantic dedup so the seeded count is exact (CI has no embedder; dev machines do)
        for i in range(500):
            store.add_node({
                "id": f"n_existing_{i}",
                "fact": f"Existing fact {i}",
                "type": "decision",
                "confidence": 0.9,
                "tags": [f"tag{i}"],
                "created_at": "2026-03-07T00:00:00Z",
                "supersedes": [],
            })
        store.close()

        config = {
            "llm": {"base_url": "http://localhost:1234/v1", "model": "test"},
            "defaults": {"hops": 3, "top_k": 25},
            "projects_dir": str(projects_dir),
        }

        monkeypatch.setenv("CB_USE_ADMIN_DB", "1")
        monkeypatch.setenv("CB_ADMIN_DB", str(admin_db_path))

        from waystone.api_server import app
        with patch("waystone.api_server._cfg", return_value=config):
            with TestClient(app) as c:
                with patch("waystone.api_server._extract", new=AsyncMock(return_value=_MOCK_EXTRACT_100_NODES)):
                    r = c.post(
                        "/v1/projects/test-project/extract",
                        json={"text": "Try to add 100 nodes"},
                        headers={"Authorization": f"Bearer {raw_key}"},
                    )

        assert r.status_code == 402  # Payment Required


# ---------------------------------------------------------------------------
# Tests: Pro tier (25,000 node limit)
# ---------------------------------------------------------------------------

class TestNodeLimitProTier:
    """Pro tier limited to 25,000 nodes."""

    def test_pro_tier_at_24999_nodes_succeeds(self, tmp_path, monkeypatch):
        """Extraction succeeds when at 24,999 nodes (below limit)."""
        admin_db_path = tmp_path / "admin.db"
        admin_conn = open_admin_db(admin_db_path)
        raw_key = create_key(admin_conn, email="user@test.com", tier="pro")
        admin_conn.close()

        projects_dir = tmp_path / "projects"
        project_dir = projects_dir / _hash_key(raw_key)[:12] / "test-project"
        project_dir.mkdir(parents=True)
        db_path = project_dir / "context.db"

        store = GraphStore(db_path, dedup_threshold=1.1)  # disable semantic dedup so the seeded count is exact (CI has no embedder; dev machines do)
        for i in range(24999):
            store.add_node({
                "id": f"n_existing_{i}",
                "fact": f"Existing fact {i}",
                "type": "decision",
                "confidence": 0.9,
                "tags": [f"tag{i}"],
                "created_at": "2026-03-07T00:00:00Z",
                "supersedes": [],
            })
        store.close()

        config = {
            "llm": {"base_url": "http://localhost:1234/v1", "model": "test"},
            "defaults": {"hops": 3, "top_k": 25},
            "projects_dir": str(projects_dir),
        }

        monkeypatch.setenv("CB_USE_ADMIN_DB", "1")
        monkeypatch.setenv("CB_ADMIN_DB", str(admin_db_path))

        from waystone.api_server import app
        with patch("waystone.api_server._cfg", return_value=config):
            with TestClient(app) as c:
                with patch("waystone.api_server._extract", new=AsyncMock(return_value=_MOCK_EXTRACT_1_NODE)):
                    r = c.post(
                        "/v1/projects/test-project/extract",
                        json={"text": "Add one node"},
                        headers={"Authorization": f"Bearer {raw_key}"},
                    )

        assert r.status_code == 200
        assert r.json()["nodes_extracted"] == 1

    def test_pro_tier_at_25000_nodes_fails(self, tmp_path, monkeypatch):
        """Extraction fails when at exactly 25,000 nodes (at limit)."""
        admin_db_path = tmp_path / "admin.db"
        admin_conn = open_admin_db(admin_db_path)
        raw_key = create_key(admin_conn, email="user@test.com", tier="pro")
        admin_conn.close()

        projects_dir = tmp_path / "projects"
        project_dir = projects_dir / _hash_key(raw_key)[:12] / "test-project"
        project_dir.mkdir(parents=True)
        db_path = project_dir / "context.db"

        store = GraphStore(db_path, dedup_threshold=1.1)  # disable semantic dedup so the seeded count is exact (CI has no embedder; dev machines do)
        for i in range(25000):
            store.add_node({
                "id": f"n_existing_{i}",
                "fact": f"Existing fact {i}",
                "type": "decision",
                "confidence": 0.9,
                "tags": [f"tag{i}"],
                "created_at": "2026-03-07T00:00:00Z",
                "supersedes": [],
            })
        store.close()

        config = {
            "llm": {"base_url": "http://localhost:1234/v1", "model": "test"},
            "defaults": {"hops": 3, "top_k": 25},
            "projects_dir": str(projects_dir),
        }

        monkeypatch.setenv("CB_USE_ADMIN_DB", "1")
        monkeypatch.setenv("CB_ADMIN_DB", str(admin_db_path))

        from waystone.api_server import app
        with patch("waystone.api_server._cfg", return_value=config):
            with TestClient(app) as c:
                with patch("waystone.api_server._extract", new=AsyncMock(return_value=_MOCK_EXTRACT_1_NODE)):
                    r = c.post(
                        "/v1/projects/test-project/extract",
                        json={"text": "Try to add one node"},
                        headers={"Authorization": f"Bearer {raw_key}"},
                    )

        assert r.status_code == 402  # Payment Required
        data = r.json()
        assert "Node limit reached" in data["detail"]["message"]
        assert "pro" in data["detail"]["message"].lower()


# ---------------------------------------------------------------------------
# Tests: Authentication
# ---------------------------------------------------------------------------

class TestNodeLimitAuthentication:
    """Node limit checks only apply to authenticated users."""

    def test_unauthenticated_request_returns_401(self, tmp_path):
        """Unauthenticated request without CB_USE_ADMIN_DB returns 401."""
        projects_dir = tmp_path / "projects"
        project_dir = projects_dir / "test-project"
        project_dir.mkdir(parents=True)
        db_path = project_dir / "context.db"

        store = GraphStore(db_path)
        store.add_node({
            "id": "n_initial",
            "fact": "Initial fact",
            "type": "decision",
            "confidence": 0.9,
            "tags": ["initial"],
            "created_at": "2026-03-07T00:00:00Z",
            "supersedes": [],
        })
        store.close()

        config = {
            "llm": {"base_url": "http://localhost:1234/v1", "model": "test"},
            "defaults": {"hops": 3, "top_k": 25},
            "projects_dir": str(projects_dir),
        }

        import os
        # Ensure CB_USE_ADMIN_DB is set so auth is required
        os.environ["CB_USE_ADMIN_DB"] = "1"
        try:
            from waystone.api_server import app
            with patch("waystone.api_server._cfg", return_value=config):
                with TestClient(app) as c:
                    r = c.post(
                        "/v1/projects/test-project/extract",
                        json={"text": "Add a node"},
                        # No Authorization header
                    )
            assert r.status_code == 401
        finally:
            os.environ.pop("CB_USE_ADMIN_DB", None)

    def test_local_tier_bypasses_limit_check(self, api_client):
        """Local tier (dev mode) bypasses node limit checks."""
        c, config, tmp_path = api_client
        with patch("waystone.api_server._extract", new=AsyncMock(return_value=_MOCK_EXTRACT_1_NODE)):
            r = c.post(
                "/v1/projects/test-project/extract",
                json={"text": "Add one node"},
            )
        # Should succeed even without auth, since dev mode allows all
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Tests: Transaction atomicity
# ---------------------------------------------------------------------------

class TestNodeLimitTransactionality:
    """Verify that the check-then-merge is atomic."""

    def test_failed_limit_check_does_not_commit_partial_merge(self, tmp_path, monkeypatch):
        """When limit check fails, no nodes are persisted (transaction rolled back)."""
        admin_db_path = tmp_path / "admin.db"
        admin_conn = open_admin_db(admin_db_path)
        raw_key = create_key(admin_conn, email="user@test.com", tier="free")
        admin_conn.close()

        projects_dir = tmp_path / "projects"
        project_dir = projects_dir / _hash_key(raw_key)[:12] / "test-project"
        project_dir.mkdir(parents=True)
        db_path = project_dir / "context.db"

        store = GraphStore(db_path, dedup_threshold=1.1)  # disable semantic dedup so the seeded count is exact (CI has no embedder; dev machines do)
        for i in range(500):  # Exactly at limit
            store.add_node({
                "id": f"n_existing_{i}",
                "fact": f"Existing fact {i}",
                "type": "decision",
                "confidence": 0.9,
                "tags": [f"tag{i}"],
                "created_at": "2026-03-07T00:00:00Z",
                "supersedes": [],
            })
        store.close()

        config = {
            "llm": {"base_url": "http://localhost:1234/v1", "model": "test"},
            "defaults": {"hops": 3, "top_k": 25},
            "projects_dir": str(projects_dir),
        }

        monkeypatch.setenv("CB_USE_ADMIN_DB", "1")
        monkeypatch.setenv("CB_ADMIN_DB", str(admin_db_path))

        from waystone.api_server import app
        with patch("waystone.api_server._cfg", return_value=config):
            with TestClient(app) as c:
                with patch("waystone.api_server._extract", new=AsyncMock(return_value=_MOCK_EXTRACT_1_NODE)):
                    r = c.post(
                        "/v1/projects/test-project/extract",
                        json={"text": "Try to add one node"},
                        headers={"Authorization": f"Bearer {raw_key}"},
                    )

        assert r.status_code == 402

        # Verify no new nodes were added (DB is still at 500)
        store = GraphStore(db_path)
        stats = store.get_stats()
        store.close()
        assert stats["node_count"] == 500  # Still at limit, not 501
