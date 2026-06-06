"""Tests for the `waystone remember` command (the /btw backend)."""

import yaml
from click.testing import CliRunner

from waystone.cli import _detect_marker_project, cli
from waystone.store import GraphStore


def _write_config(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({"projects_dir": str(tmp_path / "projects")}))
    return cfg


def test_remember_adds_pinned_node(tmp_path):
    cfg = _write_config(tmp_path)
    result = CliRunner().invoke(
        cli,
        ["--config", str(cfg), "remember", "We chose Postgres over MySQL for JSONB", "--project", "p", "--pin"],
    )
    assert result.exit_code == 0, result.output

    db_path = tmp_path / "projects" / "p" / "context.db"
    store = GraphStore(db_path, vec_enabled=False)
    try:
        facts = [n["fact"] for n in store.get_all_nodes()]
        assert any("Postgres" in f for f in facts)
        pinned = [n["fact"] for n in store.get_pinned_nodes()]
        assert any("Postgres" in f for f in pinned)
        # high-confidence, manual source, keyword-tagged
        node = next(n for n in store.get_all_nodes() if "Postgres" in n["fact"])
        assert node["confidence"] == 1.0
        assert node["source_transcript"] == "manual"
        assert node["tags"]
    finally:
        store.close()


def test_remember_unpinned_by_default(tmp_path):
    cfg = _write_config(tmp_path)
    result = CliRunner().invoke(
        cli, ["--config", str(cfg), "remember", "Rate limiting is enforced at the gateway", "--project", "p"]
    )
    assert result.exit_code == 0, result.output

    store = GraphStore(tmp_path / "projects" / "p" / "context.db", vec_enabled=False)
    try:
        assert store.get_pinned_nodes() == []
        assert any("Rate limiting" in n["fact"] for n in store.get_all_nodes())
    finally:
        store.close()


def test_remember_errors_without_project(tmp_path, monkeypatch):
    cfg = _write_config(tmp_path)
    # cwd has no .waystone marker → no auto-detect, no --project → error
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, ["--config", str(cfg), "remember", "orphan fact"])
    assert result.exit_code != 0
    assert "no project" in result.output.lower()


def test_detect_marker_project(tmp_path):
    (tmp_path / ".waystone").write_text("from-marker\n")
    assert _detect_marker_project(tmp_path) == "from-marker"
