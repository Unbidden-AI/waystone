"""Hermes Agent memory-provider integration (hermes_plugin/).

Hermes isn't installed in CI, so the provider's base class falls back to a stub
(`_get_base()`); these drive the WaystoneMemoryProvider directly against a real
local graph to prove the contract Hermes relies on: name, initialize, is_available,
prefetch, the query/recall tools, tool schemas, and the register() convention.
"""

import json

import pytest

from hermes_plugin import WaystoneMemoryProvider, register
from waystone.store import GraphStore


def _seed(projects_dir, project="demo"):
    db = projects_dir / project / "context.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    s = GraphStore(db)
    s.add_node({
        "id": "n_ch", "fact": "We use ClickHouse for analytics", "type": "decision",
        "confidence": 1.0, "tags": ["clickhouse", "analytics", "database"],
        "created_at": "2026-06-01T00:00:00Z", "supersedes": [],
    })
    s.close()
    return db


@pytest.fixture
def provider(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    _seed(projects)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"projects_dir: {projects}\n")
    monkeypatch.setenv("WAYSTONE_CONFIG", str(cfg))
    monkeypatch.setenv("WAYSTONE_PROJECT", "demo")
    p = WaystoneMemoryProvider()
    p.initialize("s1", hermes_home=str(tmp_path / "hermes"))
    return p


def test_provider_name(provider):
    assert provider.name == "waystone"


def test_is_available_true_when_project_db_exists(provider):
    assert provider.is_available() is True


def test_prefetch_injects_context(provider):
    out = provider.prefetch("what database do we use for analytics")
    assert "ClickHouse" in out


def test_query_tool(provider):
    res = json.loads(provider._handle_query({"query": "analytics database"}))
    assert "ClickHouse" in res["context"]
    assert res["project"] == "demo"


def test_recall_tool(provider):
    res = json.loads(provider._handle_recall({"tags": ["clickhouse"]}))
    assert res["count"] >= 1
    assert any("ClickHouse" in n["fact"] for n in res["nodes"])


def test_recall_requires_tags(provider):
    res = json.loads(provider._handle_recall({"tags": []}))
    assert "error" in res


def test_tool_schemas_and_system_prompt(provider):
    schemas = provider.get_tool_schemas()
    assert isinstance(schemas, list) and schemas
    names = json.dumps(schemas)
    assert "waystone_query" in names or "waystone_recall" in names
    assert isinstance(provider.system_prompt_block(), str)


def test_register_convention():
    class _Ctx:
        registered = None

        def register_memory_provider(self, p):
            self.registered = p

    ctx = _Ctx()
    register(ctx)
    assert isinstance(ctx.registered, WaystoneMemoryProvider)


def test_real_hermes_base_class_compatibility():
    """When hermes-agent is actually installed, our provider must bind to and satisfy
    its real MemoryProvider ABC — not just the test stub. Skips in CI (hermes absent);
    runs on a hermes-runner. Verified live against hermes-agent 0.16.0."""
    pytest.importorskip("agent", reason="hermes-agent not installed")
    from agent.memory_provider import MemoryProvider

    import hermes_plugin
    assert hermes_plugin._get_base() is MemoryProvider
    assert issubclass(hermes_plugin.WaystoneMemoryProvider, MemoryProvider)
    hermes_plugin.WaystoneMemoryProvider()  # instantiates → no missing abstract methods


def test_disabled_without_project(tmp_path, monkeypatch):
    monkeypatch.delenv("WAYSTONE_PROJECT", raising=False)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"projects_dir: {tmp_path / 'p'}\n")
    monkeypatch.setenv("WAYSTONE_CONFIG", str(cfg))
    p = WaystoneMemoryProvider()
    p.initialize("s2", hermes_home=str(tmp_path / "hermes"))
    assert p.is_available() is False
    assert p.prefetch("anything") == ""  # no store → empty, never raises
