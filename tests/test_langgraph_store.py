"""Tests for the LangGraph BaseStore adapter."""

import asyncio

import pytest

pytest.importorskip("langgraph")  # optional dep — skip this module if not installed

from waystone.langgraph_store import (
    WaystoneStore,
    _decode_source,
    _encode_source,
    make_waystone_store,
)
from waystone.store import GraphStore


@pytest.fixture
def tmp_store(tmp_path):
    db_path = tmp_path / "context.db"
    store = GraphStore(db_path)
    yield store
    store.close()


@pytest.fixture
def adapter(tmp_store):
    return WaystoneStore(tmp_store)


# ---------------------------------------------------------------------------
# Encoding helpers
# ---------------------------------------------------------------------------


def test_encode_decode_roundtrip():
    ns = ("user_alice", "decisions")
    key = "some-key"
    assert _decode_source(_encode_source(ns, key)) == (ns, key)


def test_encode_decode_single_ns():
    ns = ("project",)
    key = "k1"
    assert _decode_source(_encode_source(ns, key)) == (ns, key)


def test_decode_non_waystone_source():
    assert _decode_source("not-an-lg-source") is None
    assert _decode_source("") is None


# ---------------------------------------------------------------------------
# put / get
# ---------------------------------------------------------------------------


def test_put_and_get(adapter):
    adapter.put(("ns",), "key1", {"content": "Use PostgreSQL", "type": "decision"})
    item = adapter.get(("ns",), "key1")
    assert item is not None
    assert item.key == "key1"
    assert item.namespace == ("ns",)
    assert item.value["content"] == "Use PostgreSQL"


def test_get_missing_key(adapter):
    assert adapter.get(("ns",), "nonexistent") is None


def test_put_overwrite(adapter):
    adapter.put(("ns",), "key1", {"content": "original"})
    adapter.put(("ns",), "key1", {"content": "updated"})
    item = adapter.get(("ns",), "key1")
    assert item.value["content"] == "updated"


def test_put_none_deletes(adapter):
    adapter.put(("ns",), "key1", {"content": "fact"})
    adapter.put(("ns",), "key1", None)
    assert adapter.get(("ns",), "key1") is None


def test_put_preserves_namespace_as_tags(tmp_store):
    adapter = WaystoneStore(tmp_store)
    adapter.put(("user_alice", "decisions"), "d1", {"content": "fact"})
    item = adapter.get(("user_alice", "decisions"), "d1")
    assert "user_alice" in item.value["tags"]
    assert "decisions" in item.value["tags"]


# ---------------------------------------------------------------------------
# search (no query)
# ---------------------------------------------------------------------------


def test_search_no_query_returns_all_in_ns(adapter):
    adapter.put(("proj", "constraints"), "c1", {"content": "Fact A", "type": "constraint"})
    adapter.put(("proj", "constraints"), "c2", {"content": "Fact B", "type": "constraint"})
    adapter.put(("other",), "x1", {"content": "Should not appear"})

    results = adapter.search(("proj",))
    keys = {r.key for r in results}
    assert "c1" in keys
    assert "c2" in keys
    assert "x1" not in keys


def test_search_deeper_namespace(adapter):
    adapter.put(("proj", "constraints"), "c1", {"content": "Fact A"})
    results = adapter.search(("proj", "constraints"))
    assert len(results) == 1
    assert results[0].key == "c1"


def test_search_empty_namespace_prefix(adapter):
    adapter.put(("a",), "k1", {"content": "fact 1"})
    adapter.put(("b",), "k2", {"content": "fact 2"})
    results = adapter.search(())
    assert len(results) >= 2


# ---------------------------------------------------------------------------
# list_namespaces
# ---------------------------------------------------------------------------


def test_list_namespaces(adapter):
    adapter.put(("user_alice", "decisions"), "d1", {"content": "alice decided to use PostgreSQL"})
    adapter.put(("user_bob", "constraints"), "c1", {"content": "bob requires max latency of 200ms"})
    ns = adapter.list_namespaces()
    assert ("user_alice", "decisions") in ns
    assert ("user_bob", "constraints") in ns


def test_list_namespaces_max_depth(adapter):
    adapter.put(("a", "b", "c"), "k1", {"content": "fact"})
    ns = adapter.list_namespaces(max_depth=2)
    for n in ns:
        assert len(n) <= 2


# ---------------------------------------------------------------------------
# async variants
# ---------------------------------------------------------------------------


def test_aput_aget_roundtrip(adapter):
    async def _run():
        await adapter.aput(("async_ns",), "key1", {"content": "async fact"})
        item = await adapter.aget(("async_ns",), "key1")
        return item

    item = asyncio.run(_run())
    assert item is not None
    assert item.key == "key1"
    assert item.value["content"] == "async fact"


def test_asearch_returns_results(adapter):
    adapter.put(("as_ns",), "k1", {"content": "fact 1"})
    adapter.put(("as_ns",), "k2", {"content": "fact 2"})

    async def _run():
        return await adapter.asearch(("as_ns",))

    results = asyncio.run(_run())
    assert len(results) == 2


def test_alist_namespaces(adapter):
    adapter.put(("alice",), "k1", {"content": "alice's decision to use Redis"})
    adapter.put(("bob",), "k2", {"content": "bob's constraint on memory usage"})

    async def _run():
        return await adapter.alist_namespaces()

    ns = asyncio.run(_run())
    assert ("alice",) in ns
    assert ("bob",) in ns


# ---------------------------------------------------------------------------
# make_waystone_store factory
# ---------------------------------------------------------------------------


def test_make_waystone_store_by_path(tmp_path):
    db_path = tmp_path / "test.db"
    # Need to pre-create the store so db exists
    GraphStore(db_path).close()
    store = make_waystone_store(db_path=str(db_path))
    assert store is not None
    store._store.close()
