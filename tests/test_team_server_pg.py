"""M2: the Team Server running on the Postgres backend (store_backend=postgres).

Proves the existing FastAPI server + retriever work end-to-end against a shared
multi-writer PostgresGraphStore — the heart of the Team product. Skips unless
fastapi + psycopg/pgvector + WAYSTONE_TEST_PG_DSN are all available (CI provides them).
"""

import os
import uuid

import pytest

DSN = os.environ.get("WAYSTONE_TEST_PG_DSN")
pytest.importorskip("fastapi", reason="fastapi not installed")
try:
    import pgvector  # noqa: F401
    import psycopg  # noqa: F401
    _HAVE_PG = True
except Exception:
    _HAVE_PG = False

pytestmark = pytest.mark.skipif(not (_HAVE_PG and DSN),
                                reason="need fastapi + psycopg + WAYSTONE_TEST_PG_DSN")

from unittest.mock import patch  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from waystone.api_server import app  # noqa: E402
from waystone.pg_store import PostgresGraphStore  # noqa: E402


def _config():
    return {
        "llm": {"base_url": "http://localhost:1234/v1", "model": "test", "timeout": 30.0},
        "defaults": {"hops": 3, "top_k": 25},
        "strategies": {"superseded_pruning": True, "semantic": False},
        "store_backend": "postgres",
        "database_url": DSN,
    }


@pytest.fixture
def tenant():
    """A unique project/tenant, cleaned up after."""
    name = f"team_{uuid.uuid4().hex[:8]}"
    yield name
    s = PostgresGraphStore(DSN, tenant_id=name)
    with s.conn.cursor() as cur:
        for t in ("edges", "worlds", "hyperedge_members", "edge_query_rules",
                  "extraction_failures", "kv", "nodes"):
            cur.execute(f"DELETE FROM {t} WHERE tenant_id = %s", (name,))
    s.conn.commit()
    s.close()


def test_stats_endpoint_on_postgres(tenant):
    # seed the shared graph directly (as another writer would)
    s = PostgresGraphStore(DSN, tenant_id=tenant)
    s.add_node({"id": "n1", "fact": "Team uses PostgreSQL for the shared graph",
                "type": "decision", "confidence": 0.9,
                "tags": ["database", "postgresql"], "supersedes": [],
                "created_at": "2026-03-07T00:00:00+00:00"})
    s.close()
    with patch("waystone.api_server._cfg", return_value=_config()):
        with TestClient(app) as c:
            r = c.get(f"/v1/projects/{tenant}/stats")
            assert r.status_code == 200
            assert r.json()["node_count"] == 1


def test_query_endpoint_retrieves_on_postgres(tenant):
    s = PostgresGraphStore(DSN, tenant_id=tenant)
    s.add_node({"id": "n1", "fact": "Auth uses JWT tokens, stateless",
                "type": "decision", "confidence": 0.9,
                "tags": ["auth", "jwt", "stateless"], "supersedes": [],
                "created_at": "2026-03-07T00:00:00+00:00"})
    s.close()
    with patch("waystone.api_server._cfg", return_value=_config()):
        with TestClient(app) as c:
            r = c.post(f"/v1/projects/{tenant}/query", json={"task": "how does auth work"})
            assert r.status_code == 200
            body = r.json()
            assert body["total_nodes"] == 1
            assert "JWT" in body["markdown"]


def test_empty_tenant_returns_empty(tenant):
    # a fresh tenant has no nodes — server returns empty, no 404 (schema auto-exists)
    with patch("waystone.api_server._cfg", return_value=_config()):
        with TestClient(app) as c:
            r = c.post(f"/v1/projects/{tenant}/query", json={"task": "anything"})
            assert r.status_code == 200
            assert r.json()["nodes_returned"] == 0
