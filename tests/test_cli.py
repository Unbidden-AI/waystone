"""Tests for the CLI commands."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from engram.cli import cli
from engram.store import GraphStore


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
        r, config, tmp_path = runner
        r.invoke(cli, ["--config", config, "init", "test-project"])
        # Populate graph with unrelated node so empty-graph guard doesn't fire
        db_path = tmp_path / "projects" / "test-project" / "context.db"
        store = GraphStore(db_path)
        store.add_node({
            "id": "n_1", "fact": "Uses PostgreSQL", "type": "decision",
            "confidence": 0.9, "tags": ["database", "postgresql", "storage", "sql"],
            "created_at": "2026-03-07T00:00:00Z", "supersedes": [],
        })
        store.close()
        result = r.invoke(cli, ["--config", config, "query", "test-project", "kubernetes", "--disable", "semantic"])
        assert result.exit_code == 0
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
        r, config, tmp_path = runner
        r.invoke(cli, ["--config", config, "init", "test-project"])
        db_path = tmp_path / "projects" / "test-project" / "context.db"
        store = GraphStore(db_path)
        store.add_node({
            "id": "n_1", "fact": "Uses PostgreSQL", "type": "decision",
            "confidence": 0.9, "tags": ["database", "postgresql", "storage", "sql"],
            "created_at": "2026-03-07T00:00:00Z", "supersedes": [],
        })
        store.close()
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


class TestDoctor:
    def test_doctor_runs_without_crash(self, runner, tmp_path):
        r, config, _ = runner
        # Doctor may exit 1 if LLM is unreachable; that's fine — just no unhandled exception
        result = r.invoke(cli, ["--config", config, "doctor"])
        assert result.exit_code in (0, 1)
        assert "Config file loaded" in result.output

    def test_doctor_detects_marker(self, runner, tmp_path):
        r, config, _ = runner
        # Write a marker in the tmp_path so doctor can detect it
        (tmp_path / ".context-broker").write_text("test-project\n")
        result = r.invoke(cli, ["--config", config, "doctor"], catch_exceptions=False)
        assert ".context-broker marker found" in result.output


class TestImportClaudeSessions:
    def _make_jsonl(self, path: "Path", messages: list[dict]) -> None:
        import json
        path.write_text("\n".join(json.dumps(m) for m in messages))

    def test_list_only(self, runner, tmp_path):
        r, config, project_tmp = runner
        r.invoke(cli, ["--config", config, "init", "test-project"])

        session_file = tmp_path / "session.jsonl"
        self._make_jsonl(session_file, [
            {"role": "user", "content": "How should we structure the API?"},
            {"role": "assistant", "content": "Use RESTful endpoints with versioning."},
        ])

        result = r.invoke(cli, [
            "--config", config,
            "import-claude-sessions", "test-project", str(session_file),
            "--list-only",
        ])
        assert result.exit_code == 0
        assert "session.jsonl" in result.output

    def test_import_with_mocked_llm(self, runner, tmp_path):
        r, config, project_tmp = runner
        r.invoke(cli, ["--config", config, "init", "test-project"])

        session_file = tmp_path / "session.jsonl"
        self._make_jsonl(session_file, [
            {"role": "user", "content": "We will use PostgreSQL."},
            {"role": "assistant", "content": "Good choice for relational data."},
        ])

        mock_result = {
            "nodes": [{
                "id": "n_import001",
                "fact": "PostgreSQL chosen for data storage",
                "type": "decision",
                "confidence": 0.9,
                "tags": ["postgresql", "database", "storage"],
                "supersedes": [],
            }],
            "edges": [],
        }

        with patch("engram.cli.extract", new=AsyncMock(return_value=mock_result)):
            result = r.invoke(cli, [
                "--config", config,
                "import-claude-sessions", "test-project", str(session_file),
            ])

        assert result.exit_code == 0
        assert "1 nodes" in result.output

        db_path = project_tmp / "projects" / "test-project" / "context.db"
        from engram.store import GraphStore
        store = GraphStore(db_path)
        node = store.get_node("n_import001")
        store.close()
        assert node is not None


class TestJsonlToMarkdown:
    def test_plain_string_content(self, tmp_path):
        import json

        from engram.cli import _jsonl_to_markdown

        f = tmp_path / "session.jsonl"
        f.write_text(
            json.dumps({"role": "user", "content": "Hello"}) + "\n"
            + json.dumps({"role": "assistant", "content": "Hi there"}) + "\n"
        )
        md = _jsonl_to_markdown(f)
        assert "**Human:** Hello" in md
        assert "**Assistant:** Hi there" in md

    def test_content_block_list(self, tmp_path):
        import json

        from engram.cli import _jsonl_to_markdown

        f = tmp_path / "session.jsonl"
        f.write_text(
            json.dumps({
                "role": "assistant",
                "content": [{"type": "text", "text": "Block content here"}],
            }) + "\n"
        )
        md = _jsonl_to_markdown(f)
        assert "Block content here" in md

    def test_skips_invalid_json_lines(self, tmp_path):
        import json

        from engram.cli import _jsonl_to_markdown

        f = tmp_path / "session.jsonl"
        f.write_text(
            "not json\n"
            + json.dumps({"role": "user", "content": "Valid line"}) + "\n"
        )
        md = _jsonl_to_markdown(f)
        assert "Valid line" in md

    def test_empty_file(self, tmp_path):
        from engram.cli import _jsonl_to_markdown
        f = tmp_path / "empty.jsonl"
        f.write_text("")
        assert _jsonl_to_markdown(f) == ""
