"""Connection pooling for the Postgres Team backend.

The server opens one short-lived store per request; pooling reuses warm connections
instead of connect-and-discard. Needs a real Postgres + psycopg_pool + the DSN env.
"""

import os

import pytest

DSN = os.environ.get("WAYSTONE_TEST_PG_DSN")
try:
    import pgvector  # noqa: F401
    import psycopg  # noqa: F401
    import psycopg_pool  # noqa: F401
    _HAVE = True
except Exception:
    _HAVE = False

pytestmark = pytest.mark.skipif(
    not (_HAVE and DSN), reason="need postgres + psycopg_pool + WAYSTONE_TEST_PG_DSN")

from waystone.pg_store import PostgresGraphStore, _pooling_enabled  # noqa: E402


def _backend_pid(store) -> int:
    with store.conn.cursor() as cur:
        cur.execute("SELECT pg_backend_pid() AS p")
        return cur.fetchone()["p"]


def test_pooling_on_by_default():
    assert _pooling_enabled()
    s = PostgresGraphStore(DSN, tenant_id="pool_default")
    try:
        assert s._pool is not None  # borrowed from the pool, not a fresh connect
    finally:
        s.close()


def test_many_stores_reuse_a_bounded_set_of_connections():
    """20 open/close cycles must NOT spawn 20 backends — connections are recycled."""
    pids = set()
    for i in range(20):
        s = PostgresGraphStore(DSN, tenant_id=f"pool_reuse_{i}")
        pids.add(_backend_pid(s))
        s.close()
    assert len(pids) < 20, f"expected connection reuse, saw {len(pids)} distinct backends"


def test_returned_connection_is_clean():
    """A store that left an open read txn must return a usable connection: the next
    borrower (possibly the same physical conn) works without an 'aborted transaction'."""
    a = PostgresGraphStore(DSN, tenant_id="pool_clean_a")
    a.get_stats()  # opens a read transaction on the borrowed conn
    a.close()      # rollback + putconn
    b = PostgresGraphStore(DSN, tenant_id="pool_clean_b")
    try:
        assert b.get_stats()["node_count"] == 0  # conn is clean + usable
    finally:
        b.close()


def test_pool_opt_out(monkeypatch):
    monkeypatch.setenv("WAYSTONE_PG_POOL", "0")
    s = PostgresGraphStore(DSN, tenant_id="pool_off")
    try:
        assert s._pool is None        # direct connect, no pool
        assert s.get_stats()["node_count"] == 0
    finally:
        s.close()
