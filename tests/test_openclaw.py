"""Tests for the OpenClaw skill — Phase 1A + 1B + 2A + 2B."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from engram.openclaw.config import (
    OPENCLAW_DEFAULTS,
    get_db_path,
    get_memory_md_path,
    get_project,
    load_openclaw_config,
)
from engram.openclaw.errors import (
    ConfigError,
    DBInitError,
    EngramOpenClawError,
    LLMExtractionError,
    MemoryMDCorruptError,
)
from engram.openclaw.memory_sync import (
    _END,
    _START,
    _atomic_write,
    _inject_section,
    _render_nodes,
    _strip_engram_block,
    archive_overflow,
    bootstrap,
    write_back,
)
from engram.store import GraphStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_store(tmp_path):
    db_path = tmp_path / "context.db"
    store = GraphStore(db_path)
    yield store
    store.close()


@pytest.fixture
def tmp_cfg(tmp_path, tmp_store):
    db_path = tmp_path / "context.db"
    memory_path = tmp_path / "MEMORY.md"
    return {
        "project": "test_project",
        "top_k": 10,
        "context_top_k": 10,
        "hops": 2,
        "auto_extract": True,
        "extract_on_session_end_only": True,
        "memory_md_path": str(memory_path),
        "memory_md_section": "## Engram Context",
        "memory_md_max_bytes": 4096,
        "dry_run": False,
        "_engram": {
            "projects_dir": str(tmp_path),
            "llm": {"base_url": "http://localhost:1234/v1", "model": "test"},
            "strategies": {},
        },
    }


@pytest.fixture
def populated_store(tmp_store):
    nodes = [
        {"id": "n_a1", "fact": "Use PostgreSQL for production", "type": "decision",
         "confidence": 0.9, "tags": ["postgresql", "database"], "supersedes": [],
         "source_transcript": "test", "is_active": True},
        {"id": "n_b2", "fact": "Redis for caching layer", "type": "decision",
         "confidence": 0.85, "tags": ["redis", "caching"], "supersedes": [],
         "source_transcript": "test", "is_active": True},
        {"id": "n_c3", "fact": "Max latency 200ms", "type": "constraint",
         "confidence": 0.95, "tags": ["latency", "performance"], "supersedes": [],
         "source_transcript": "test", "is_active": True},
    ]
    edges = []
    tmp_store.merge_extraction(nodes, edges)
    return tmp_store


# ---------------------------------------------------------------------------
# errors.py
# ---------------------------------------------------------------------------


def test_error_hierarchy():
    assert issubclass(DBInitError, EngramOpenClawError)
    assert issubclass(LLMExtractionError, EngramOpenClawError)
    assert issubclass(MemoryMDCorruptError, EngramOpenClawError)
    assert issubclass(ConfigError, EngramOpenClawError)


def test_errors_are_catchable_as_base():
    with pytest.raises(EngramOpenClawError):
        raise DBInitError("test")


# ---------------------------------------------------------------------------
# config.py
# ---------------------------------------------------------------------------


def test_get_project_raises_if_empty():
    cfg = dict(OPENCLAW_DEFAULTS)
    cfg["project"] = ""
    with pytest.raises(ConfigError, match="ENGRAM_PROJECT"):
        get_project(cfg)


def test_get_project_returns_name():
    cfg = dict(OPENCLAW_DEFAULTS)
    cfg["project"] = "myproject"
    assert get_project(cfg) == "myproject"


def test_get_memory_md_path_default():
    cfg = dict(OPENCLAW_DEFAULTS)
    p = get_memory_md_path(cfg)
    assert p.name == "MEMORY.md"
    assert p.is_absolute()


def test_get_memory_md_path_custom(tmp_path):
    cfg = dict(OPENCLAW_DEFAULTS)
    custom = str(tmp_path / "custom_memory.md")
    cfg["memory_md_path"] = custom
    assert get_memory_md_path(cfg) == Path(custom)


def test_load_openclaw_config_returns_dict():
    with patch.dict(os.environ, {"ENGRAM_PROJECT": "test_proj"}):
        cfg = load_openclaw_config()
    assert isinstance(cfg, dict)
    assert cfg["project"] == "test_proj"
    assert "_engram" in cfg


def test_load_openclaw_config_env_overrides():
    with patch.dict(os.environ, {
        "ENGRAM_PROJECT": "envproject",
        "ENGRAM_TOP_K": "20",
        "ENGRAM_HOPS": "5",
    }):
        cfg = load_openclaw_config()
    assert cfg["project"] == "envproject"
    assert cfg["top_k"] == 20
    assert cfg["hops"] == 5


def test_load_openclaw_config_dry_run_env():
    with patch.dict(os.environ, {"ENGRAM_DRY_RUN": "1", "ENGRAM_PROJECT": "p"}):
        cfg = load_openclaw_config()
    assert cfg["dry_run"] is True


def test_load_openclaw_config_file(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("project: fileproject\ntop_k: 25\n")

    with patch("engram.openclaw.config._CONFIG_PATHS", [config_file]):
        cfg = load_openclaw_config()
    assert cfg["project"] == "fileproject"
    assert cfg["top_k"] == 25


# ---------------------------------------------------------------------------
# memory_sync.py — rendering
# ---------------------------------------------------------------------------


def test_render_nodes_empty():
    result = _render_nodes([], "## Test")
    assert "No context yet" in result


def test_render_nodes_groups_by_type():
    nodes = [
        {"fact": "Use PostgreSQL", "type": "decision"},
        {"fact": "Max 200ms latency", "type": "constraint"},
    ]
    result = _render_nodes(nodes, "## Context")
    assert "Decisions" in result
    assert "Constraints" in result
    assert "Use PostgreSQL" in result
    assert "Max 200ms latency" in result


def test_render_nodes_unknown_type_skipped():
    nodes = [{"fact": "something", "type": "totally_unknown_type"}]
    # Should not raise; unknown types simply don't appear under a section header
    result = _render_nodes(nodes, "## Context")
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# memory_sync.py — atomic write
# ---------------------------------------------------------------------------


def test_atomic_write_creates_file(tmp_path):
    target = tmp_path / "out.md"
    _atomic_write(target, "hello world")
    assert target.read_text() == "hello world"


def test_atomic_write_overwrites(tmp_path):
    target = tmp_path / "out.md"
    target.write_text("old content")
    _atomic_write(target, "new content")
    assert target.read_text() == "new content"


# ---------------------------------------------------------------------------
# memory_sync.py — inject_section
# ---------------------------------------------------------------------------


def test_inject_section_creates_block(tmp_path, tmp_cfg):
    memory_path = tmp_path / "MEMORY.md"
    tmp_cfg["memory_md_path"] = str(memory_path)

    nodes = [{"fact": "Use Redis", "type": "decision"}]
    content = _render_nodes(nodes, "## Engram Context")
    _inject_section(memory_path, content, tmp_cfg)

    text = memory_path.read_text()
    assert _START in text
    assert _END in text
    assert "Use Redis" in text


def test_inject_section_replaces_existing_block(tmp_path, tmp_cfg):
    memory_path = tmp_path / "MEMORY.md"
    memory_path.write_text(f"# User Notes\n\n{_START}\n## Engram Context\n- Old fact\n{_END}\n")
    tmp_cfg["memory_md_path"] = str(memory_path)

    nodes = [{"fact": "New fact", "type": "decision"}]
    content = _render_nodes(nodes, "## Engram Context")
    _inject_section(memory_path, content, tmp_cfg)

    text = memory_path.read_text()
    assert "New fact" in text
    assert "Old fact" not in text
    assert "# User Notes" in text  # User content preserved


def test_inject_section_preserves_user_content(tmp_path, tmp_cfg):
    memory_path = tmp_path / "MEMORY.md"
    memory_path.write_text("# My Notes\n\nI like pineapple pizza.\n")
    tmp_cfg["memory_md_path"] = str(memory_path)

    _inject_section(memory_path, "## Engram Context\n- some fact", tmp_cfg)

    text = memory_path.read_text()
    assert "I like pineapple pizza" in text
    assert "some fact" in text


def test_strip_engram_block_removes_block():
    text = f"User content\n{_START}\n## Engram\n- fact\n{_END}\nMore user content"
    stripped = _strip_engram_block(text)
    assert _START not in stripped
    assert _END not in stripped
    assert "User content" in stripped
    assert "More user content" in stripped


def test_strip_engram_block_noop_if_no_block():
    text = "Just user content, no block here"
    assert _strip_engram_block(text) == text


# ---------------------------------------------------------------------------
# memory_sync.py — bootstrap / write_back
# ---------------------------------------------------------------------------


def test_bootstrap_empty_graph_does_nothing(tmp_path, tmp_store, tmp_cfg):
    result = bootstrap(tmp_store, tmp_cfg)
    assert result is False
    memory_path = Path(tmp_cfg["memory_md_path"])
    assert not memory_path.exists()


def test_bootstrap_with_nodes_writes_memory_md(tmp_path, populated_store, tmp_cfg):
    result = bootstrap(populated_store, tmp_cfg)
    assert result is True
    memory_path = Path(tmp_cfg["memory_md_path"])
    assert memory_path.exists()
    text = memory_path.read_text()
    assert "PostgreSQL" in text or "Redis" in text


def test_write_back_refreshes_content(tmp_path, populated_store, tmp_cfg):
    bootstrap(populated_store, tmp_cfg)
    # Add a new node
    new_nodes = [{"id": "n_new1", "fact": "Use Kafka for messaging", "type": "decision",
                  "confidence": 0.9, "tags": ["kafka"], "supersedes": [],
                  "source_transcript": "test", "is_active": True}]
    populated_store.merge_extraction(new_nodes, [])
    write_back(populated_store, tmp_cfg)
    text = Path(tmp_cfg["memory_md_path"]).read_text()
    assert "Kafka" in text


# ---------------------------------------------------------------------------
# skill.py — command handlers
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_ctx():
    import uuid
    ctx = MagicMock()
    ctx.session_id = f"test_{uuid.uuid4().hex[:8]}"
    ctx.system_prompt = ""
    return ctx


def test_cmd_remember_no_text(mock_ctx, tmp_cfg):
    from engram.openclaw.skill import cmd_remember, _sessions

    with patch("engram.openclaw.skill.load_openclaw_config", return_value=tmp_cfg):
        result = asyncio.run(cmd_remember(mock_ctx, args=""))
    assert "Usage" in result


def test_cmd_recall_no_store_opens_gracefully(mock_ctx, tmp_path, tmp_cfg):
    from engram.openclaw.skill import cmd_recall, _sessions

    # DB doesn't exist yet — should return error message, not crash
    tmp_cfg["_engram"]["projects_dir"] = str(tmp_path / "nonexistent")

    with patch("engram.openclaw.skill.load_openclaw_config", return_value=tmp_cfg):
        result = asyncio.run(cmd_recall(mock_ctx, args="database choices"))
    # Should return an error message string, not raise
    assert isinstance(result, str)


def test_cmd_forget_no_topic(mock_ctx, tmp_cfg):
    from engram.openclaw.skill import cmd_forget

    with patch("engram.openclaw.skill.load_openclaw_config", return_value=tmp_cfg):
        result = asyncio.run(cmd_forget(mock_ctx, args=""))
    assert "Usage" in result


def test_cmd_status_returns_string(mock_ctx, tmp_path, tmp_cfg):
    from engram.openclaw.skill import cmd_status, _sessions

    db_path = tmp_path / "context.db"
    store = GraphStore(db_path)
    store.close()

    with patch("engram.openclaw.skill.load_openclaw_config", return_value=tmp_cfg):
        result = asyncio.run(cmd_status(mock_ctx, args=""))
    assert "test_project" in result
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# dreaming.py — Phase 2A
# ---------------------------------------------------------------------------


@pytest.fixture
def graph_store_with_edges(tmp_path):
    """A store with nodes connected by edges (for hub detection tests)."""
    db_path = tmp_path / "dream_test.db"
    store = GraphStore(db_path)
    nodes = [
        {"id": "n_hub1", "fact": "Use PostgreSQL", "type": "decision",
         "confidence": 0.7, "tags": ["postgresql", "database"], "supersedes": [],
         "source_transcript": "test", "is_active": True},
        {"id": "n_hub2", "fact": "Caching with Redis", "type": "decision",
         "confidence": 0.7, "tags": ["redis", "caching"], "supersedes": [],
         "source_transcript": "test", "is_active": True},
        {"id": "n_spoke1", "fact": "ORM uses SQLAlchemy", "type": "implementation",
         "confidence": 0.6, "tags": ["sqlalchemy", "postgresql"], "supersedes": [],
         "source_transcript": "test", "is_active": True},
        {"id": "n_spoke2", "fact": "Session store in Redis", "type": "implementation",
         "confidence": 0.6, "tags": ["redis", "sessions"], "supersedes": [],
         "source_transcript": "test", "is_active": True},
        {"id": "n_spoke3", "fact": "Read replica for reports", "type": "constraint",
         "confidence": 0.6, "tags": ["postgresql", "reporting"], "supersedes": [],
         "source_transcript": "test", "is_active": True},
    ]
    edges = [
        {"from_id": "n_spoke1", "to_id": "n_hub1", "relation": "depends_on"},
        {"from_id": "n_spoke2", "to_id": "n_hub2", "relation": "depends_on"},
        {"from_id": "n_spoke3", "to_id": "n_hub1", "relation": "depends_on"},
        {"from_id": "n_hub1", "to_id": "n_hub2", "relation": "relates_to"},
    ]
    store.merge_extraction(nodes, edges)
    yield store
    store.close()


def test_dream_result_dataclass():
    from engram.openclaw.dreaming import DreamResult
    r = DreamResult()
    assert r.nodes_promoted == 0
    assert r.nodes_pruned == 0
    assert r.conflicts_detected == 0
    assert r.duration_ms == 0.0


def test_run_dream_empty_store(tmp_path):
    from engram.openclaw.dreaming import run_dream
    db_path = tmp_path / "empty.db"
    store = GraphStore(db_path)
    cfg = {}
    try:
        result = run_dream(store, cfg)
        # Should not raise; pruned=0, promoted=0
        assert result.nodes_pruned == 0
    finally:
        store.close()


def test_run_dream_promotes_hub_nodes(graph_store_with_edges):
    from engram.openclaw.dreaming import run_dream
    # Record confidence before
    before = {n["id"]: n.get("confidence", 0.5)
               for n in graph_store_with_edges.get_all_nodes()}
    cfg = {}
    result = run_dream(graph_store_with_edges, cfg)
    after = {n["id"]: n.get("confidence", 0.5)
              for n in graph_store_with_edges.get_all_nodes()}
    # n_hub1 has degree 3 (spoke1→hub1, spoke3→hub1, hub1→hub2) → should be boosted
    assert after["n_hub1"] > before["n_hub1"]
    assert result.nodes_promoted > 0


def test_run_dream_prunes_superseded(tmp_path):
    from engram.openclaw.dreaming import run_dream
    db_path = tmp_path / "superseded.db"
    store = GraphStore(db_path)
    try:
        nodes = [
            {"id": "n_old", "fact": "Use MySQL", "type": "decision",
             "confidence": 0.8, "tags": ["mysql"], "supersedes": [],
             "source_transcript": "test", "is_active": True},
            {"id": "n_new", "fact": "Use PostgreSQL instead", "type": "decision",
             "confidence": 0.9, "tags": ["postgresql"], "supersedes": ["n_old"],
             "source_transcript": "test", "is_active": True},
        ]
        store.merge_extraction(nodes, [])
        # Manually reactivate the old node to simulate a stale state
        store.conn.execute("UPDATE nodes SET is_active=1 WHERE id='n_old'")
        store.conn.commit()

        result = run_dream(store, {})
        assert result.nodes_pruned >= 1
        # n_old should now be inactive
        active = {n["id"] for n in store.get_active_nodes()}
        assert "n_old" not in active
    finally:
        store.close()


def test_looks_conflicting_negation():
    from engram.openclaw.dreaming import _looks_conflicting
    assert _looks_conflicting("Use Redis for caching", "Never use Redis in production")
    assert not _looks_conflicting("Use Redis for caching", "Use Redis for session storage")


def test_looks_conflicting_numeric():
    from engram.openclaw.dreaming import _looks_conflicting
    assert _looks_conflicting("Max latency 200ms", "Max latency 500ms")
    assert not _looks_conflicting("Max latency 200ms", "Max latency 200ms")


def test_looks_conflicting_no_false_positives():
    from engram.openclaw.dreaming import _looks_conflicting
    # Two facts with no numbers and no negation words — should not conflict
    assert not _looks_conflicting(
        "Use PostgreSQL for the main datastore",
        "Use PostgreSQL for the reporting database",
    )


def test_conflict_record_detected_at():
    from engram.openclaw.dreaming import ConflictRecord
    c = ConflictRecord(
        node_a_id="a", node_b_id="b",
        node_a_fact="fact a", node_b_fact="fact b",
        shared_tags=["tag1"],
    )
    assert c.detected_at  # auto-populated
    assert "T" in c.detected_at  # ISO format


def test_format_dream_summary_no_conflicts():
    from engram.openclaw.dreaming import DreamResult, format_dream_summary
    result = DreamResult(nodes_promoted=3, nodes_pruned=1, conflicts_detected=0, duration_ms=42.5)
    summary = format_dream_summary(result)
    assert "Promoted 3" in summary
    assert "Pruned 1" in summary
    assert "No conflicts" in summary


def test_format_dream_summary_with_conflicts(tmp_path):
    from engram.openclaw.dreaming import DreamResult, format_dream_summary
    result = DreamResult(nodes_promoted=0, nodes_pruned=0, conflicts_detected=2, duration_ms=10.0)
    summary = format_dream_summary(result)
    assert "2 potential conflicts" in summary


# ---------------------------------------------------------------------------
# skill.py — cmd_dream + cmd_export (Phase 2B)
# ---------------------------------------------------------------------------


def _project_db_path(tmp_path, tmp_cfg):
    """Return the DB path that get_db_path(tmp_cfg) resolves to."""
    projects_dir = tmp_cfg["_engram"]["projects_dir"]
    project = tmp_cfg["project"]
    return Path(projects_dir) / project / "context.db"


def test_cmd_dream_returns_summary(mock_ctx, tmp_path, tmp_cfg):
    from engram.openclaw.skill import cmd_dream, _sessions

    # Seed the DB at the path that get_db_path resolves to
    db_path = _project_db_path(tmp_path, tmp_cfg)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = GraphStore(db_path)
    nodes = [
        {"id": "n_d1", "fact": "Use Docker", "type": "decision",
         "confidence": 0.8, "tags": ["docker"], "supersedes": [],
         "source_transcript": "test", "is_active": True},
    ]
    store.merge_extraction(nodes, [])
    store.close()

    with patch("engram.openclaw.skill.load_openclaw_config", return_value=tmp_cfg):
        result = asyncio.run(cmd_dream(mock_ctx, args=""))
    assert isinstance(result, str)
    # Should mention "Dream cycle" or similar
    assert "Dream" in result or "Promoted" in result


def test_cmd_export_default_path(mock_ctx, tmp_path, tmp_cfg):
    from engram.openclaw.skill import cmd_export, _sessions

    # Pre-create the DB at the path get_db_path resolves to
    db_path = _project_db_path(tmp_path, tmp_cfg)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = GraphStore(db_path)
    nodes = [
        {"id": "n_e1", "fact": "API uses REST", "type": "decision",
         "confidence": 0.9, "tags": ["rest", "api"], "supersedes": [],
         "source_transcript": "test", "is_active": True},
    ]
    store.merge_extraction(nodes, [])
    store.close()

    out_path = tmp_path / "export_test.md"

    with patch("engram.openclaw.skill.load_openclaw_config", return_value=tmp_cfg):
        result = asyncio.run(cmd_export(mock_ctx, args=str(out_path)))

    assert "Exported" in result
    assert out_path.exists()
    content = out_path.read_text()
    assert "REST" in content


def test_cmd_export_empty_graph(mock_ctx, tmp_path, tmp_cfg):
    from engram.openclaw.skill import cmd_export, _sessions

    # DB exists but empty — at the resolved path
    db_path = _project_db_path(tmp_path, tmp_cfg)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    GraphStore(db_path).close()

    out_path = tmp_path / "empty_export.md"

    with patch("engram.openclaw.skill.load_openclaw_config", return_value=tmp_cfg):
        result = asyncio.run(cmd_export(mock_ctx, args=str(out_path)))

    assert "Exported" in result or "export" in result.lower()
    assert out_path.exists()


# ---------------------------------------------------------------------------
# memory_sync.py — archive_overflow (Phase 2B)
# ---------------------------------------------------------------------------


def test_archive_overflow_noop_when_under_cap(tmp_path, tmp_cfg):
    """archive_overflow does nothing when MEMORY.md is within the size cap."""
    memory_path = tmp_path / "MEMORY.md"
    # Write a tiny block — well under the 4096 byte cap
    small_content = f"{_START}\n## Engram Context\n- Small fact\n{_END}\n"
    memory_path.write_text(small_content)
    tmp_cfg["memory_md_path"] = str(memory_path)

    count = archive_overflow(tmp_cfg)
    assert count == 0
    archive_path = tmp_path / "MEMORY_ARCHIVE.md"
    assert not archive_path.exists()


def test_archive_overflow_missing_memory_md(tmp_path, tmp_cfg):
    """archive_overflow returns 0 cleanly when MEMORY.md doesn't exist."""
    tmp_cfg["memory_md_path"] = str(tmp_path / "MEMORY.md")
    count = archive_overflow(tmp_cfg)
    assert count == 0
