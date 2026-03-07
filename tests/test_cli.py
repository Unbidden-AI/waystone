"""Tests for the CLI commands."""

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from context_broker.cli import cli
from context_broker.store import GraphStore


@pytest.fixture
def runner(tmp_path, monkeypatch):
    """CLI runner with a temp projects dir."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"""
llm:
  base_url: "http://localhost:1234/v1"
  model: "test-model"
  temperature: 0.1
  max_tokens: 4096
defaults:
  hops: 3
  top_k: 10
  format: "markdown"
projects_dir: "{tmp_path / 'projects'}"
""")
    r = CliRunner()
    return r, str(config_path), tmp_path


class TestInit:
    def test_init_creates_project(self, runner):
        r, config, tmp_path = runner
        result = r.invoke(cli, ["--config", config, "init", "test-project"])
        assert result.exit_code == 0
        assert "Initialized" in result.output

        project_dir = tmp_path / "projects" / "test-project"
        assert project_dir.exists()
        assert (project_dir / "transcripts").exists()
        assert (project_dir / "exports").exists()
        assert (project_dir / "context.db").exists()

    def test_init_existing_project(self, runner):
        r, config, tmp_path = runner
        r.invoke(cli, ["--config", config, "init", "test-project"])
        result = r.invoke(cli, ["--config", config, "init", "test-project"])
        assert "already exists" in result.output


class TestShow:
    def test_show_empty(self, runner):
        r, config, tmp_path = runner
        r.invoke(cli, ["--config", config, "init", "test-project"])
        result = r.invoke(cli, ["--config", config, "show", "test-project"])
        assert result.exit_code == 0
        assert "Nodes: 0" in result.output

    def test_show_with_data(self, runner):
        r, config, tmp_path = runner
        r.invoke(cli, ["--config", config, "init", "test-project"])

        # Manually add some data
        db_path = tmp_path / "projects" / "test-project" / "context.db"
        store = GraphStore(db_path)
        store.add_node({
            "id": "n_test1", "fact": "Test fact", "type": "decision",
            "confidence": 0.9, "tags": ["test"], "created_at": "2026-03-07T00:00:00Z",
            "supersedes": [],
        })
        store.close()

        result = r.invoke(cli, ["--config", config, "show", "test-project"])
        assert "Nodes: 1" in result.output
        assert "decision: 1" in result.output

    def test_show_nonexistent(self, runner):
        r, config, _ = runner
        result = r.invoke(cli, ["--config", config, "show", "nonexistent"])
        assert result.exit_code != 0


class TestQuery:
    def test_query_with_data(self, runner):
        r, config, tmp_path = runner
        r.invoke(cli, ["--config", config, "init", "test-project"])

        db_path = tmp_path / "projects" / "test-project" / "context.db"
        store = GraphStore(db_path)
        store.add_node({
            "id": "n_1", "fact": "Uses PostgreSQL for storage", "type": "decision",
            "confidence": 0.9, "tags": ["database", "postgresql"],
            "created_at": "2026-03-07T00:00:00Z", "supersedes": [],
        })
        store.close()

        result = r.invoke(cli, ["--config", config, "query", "test-project", "database storage"])
        assert result.exit_code == 0
        assert "PostgreSQL" in result.output

    def test_query_no_matches(self, runner):
        r, config, _ = runner
        r.invoke(cli, ["--config", config, "init", "test-project"])
        result = r.invoke(cli, ["--config", config, "query", "test-project", "kubernetes"])
        assert "No relevant context" in result.output

    def test_query_with_strategy_flags(self, runner):
        r, config, tmp_path = runner
        r.invoke(cli, ["--config", config, "init", "test-project"])

        db_path = tmp_path / "projects" / "test-project" / "context.db"
        store = GraphStore(db_path)
        store.add_node({
            "id": "n_1", "fact": "Uses PostgreSQL", "type": "decision",
            "confidence": 0.5, "tags": ["database"],
            "created_at": "2026-03-07T00:00:00Z", "supersedes": [],
        })
        store.close()

        # With high confidence threshold, this node should be filtered
        result = r.invoke(cli, [
            "--config", config, "query", "test-project", "database",
            "--confidence", "0.8"
        ])
        assert result.exit_code == 0
        assert "No relevant context" in result.output

    def test_query_with_stats_flag(self, runner):
        r, config, tmp_path = runner
        r.invoke(cli, ["--config", config, "init", "test-project"])

        db_path = tmp_path / "projects" / "test-project" / "context.db"
        store = GraphStore(db_path)
        store.add_node({
            "id": "n_1", "fact": "Uses PostgreSQL", "type": "decision",
            "confidence": 0.9, "tags": ["database"],
            "created_at": "2026-03-07T00:00:00Z", "supersedes": [],
        })
        store.close()

        result = r.invoke(cli, [
            "--config", config, "query", "test-project", "database", "--stats"
        ])
        assert result.exit_code == 0
        assert "Retrieval Stats" in result.output
        assert "Nodes before strategies" in result.output

    def test_query_enable_disable_flags(self, runner):
        r, config, _ = runner
        r.invoke(cli, ["--config", config, "init", "test-project"])
        result = r.invoke(cli, [
            "--config", config, "query", "test-project", "anything",
            "-e", "superseded_pruning", "-d", "relevance_scoring"
        ])
        assert result.exit_code == 0


class TestExport:
    def test_export_creates_file(self, runner):
        r, config, tmp_path = runner
        r.invoke(cli, ["--config", config, "init", "test-project"])

        db_path = tmp_path / "projects" / "test-project" / "context.db"
        store = GraphStore(db_path)
        store.add_node({
            "id": "n_1", "fact": "Test fact for export", "type": "implementation",
            "confidence": 0.9, "tags": ["test"], "created_at": "2026-03-07T00:00:00Z",
            "supersedes": [],
        })
        store.close()

        result = r.invoke(cli, ["--config", config, "export", "test-project"])
        assert result.exit_code == 0

        export_path = tmp_path / "projects" / "test-project" / "exports" / "current.md"
        assert export_path.exists()
        content = export_path.read_text()
        assert "Test fact for export" in content

    def test_export_empty_graph(self, runner):
        r, config, _ = runner
        r.invoke(cli, ["--config", config, "init", "test-project"])
        result = r.invoke(cli, ["--config", config, "export", "test-project"])
        assert "empty" in result.output.lower()
