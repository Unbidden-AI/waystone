"""Tests for the CLI commands."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from waystone.cli import cli
from waystone.store import GraphStore


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
        (tmp_path / ".waystone").write_text("test-project\n")
        result = r.invoke(cli, ["--config", config, "doctor"], catch_exceptions=False)
        assert ".waystone marker found" in result.output

    def test_catchup_summarize_builds_chain(self, runner, tmp_path, monkeypatch):
        r, config, _ = runner
        # Two fake session transcripts in a temp transcripts dir.
        tdir = tmp_path / "transcripts"
        tdir.mkdir()
        (tdir / "20260101_100000_aaaa1111.md").write_text(
            "**User**: start project\n\n**Assistant**: ok, session one\n", encoding="utf-8")
        (tdir / "20260102_100000_bbbb2222.md").write_text(
            "**User**: continue\n\n**Assistant**: session two\n", encoding="utf-8")

        # Mock the LLM: return a distinct summary per call so chapters differ.
        import waystone.cli as climod
        calls = {"n": 0}

        async def fake_summary(new_text, prior, cfg):
            calls["n"] += 1
            return f"chapter summary {calls['n']}"

        monkeypatch.setattr(climod, "generate_session_summary", fake_summary)

        result = r.invoke(climod.cli, [
            "--config", config, "catchup-summarize", "proj",
            "--transcripts-dir", str(tdir),
        ], catch_exceptions=False)
        assert result.exit_code == 0
        assert "Summarizing 2 session(s)" in result.output

        from waystone.config import get_db_path, load_config
        cfg = load_config(config)
        store = GraphStore(get_db_path(cfg, "proj"))
        ss = [n for n in store.get_all_nodes() if n["type"] == "session_summary"]
        store.close()
        assert len(ss) == 2
        # Newest chapter supersedes (one node carries a supersedes edge)
        assert any(n.get("supersedes") for n in ss)
        # source tagged as history back-fill
        assert all(str(n.get("source_transcript", "")).startswith("history_summary:") for n in ss)

    def test_catchup_summarize_no_transcripts(self, runner, tmp_path):
        r, config, _ = runner
        result = r.invoke(cli, [
            "--config", config, "catchup-summarize", "proj",
            "--transcripts-dir", str(tmp_path / "nope"),
        ])
        assert result.exit_code == 0
        assert "No transcripts found" in result.output

    def test_doctor_reports_sqlite_vec_capability(self, runner, tmp_path):
        r, config, _ = runner
        # The sqlite-vec capability line must appear regardless of build (✓, ✗, or –).
        # Catches the NameError-class bug from testing extracted logic instead of the CLI.
        result = r.invoke(cli, ["--config", config, "doctor"])
        assert result.exit_code in (0, 1)
        assert "sqlite-vec" in result.output


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

        with patch("waystone.cli.extract", new=AsyncMock(return_value=mock_result)):
            result = r.invoke(cli, [
                "--config", config,
                "import-claude-sessions", "test-project", str(session_file),
            ])

        assert result.exit_code == 0
        assert "1 nodes" in result.output

        db_path = project_tmp / "projects" / "test-project" / "context.db"
        from waystone.store import GraphStore
        store = GraphStore(db_path)
        node = store.get_node("n_import001")
        store.close()
        assert node is not None


class TestJsonlToMarkdown:
    def test_nested_claude_code_format(self, tmp_path):
        """Real Claude Code session format nests the message under 'message'.

        This is the schema actual sessions use; an earlier parser read top-level
        role/content and produced empty output for every session, so onboard
        imported 0 nodes. Guard against that regression here.
        """
        import json

        from waystone.cli import _jsonl_to_markdown

        f = tmp_path / "session.jsonl"
        f.write_text(
            json.dumps({"type": "summary", "summary": "ignore me"}) + "\n"
            + json.dumps({"type": "user", "message": {"role": "user", "content": "Hello"}}) + "\n"
            + json.dumps({"type": "assistant", "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Hi there"}],
            }}) + "\n"
        )
        md = _jsonl_to_markdown(f)
        assert md.strip(), "nested Claude Code session must not convert to empty"
        assert "Hello" in md
        assert "Hi there" in md

    def test_plain_string_content(self, tmp_path):
        import json

        from waystone.cli import _jsonl_to_markdown

        f = tmp_path / "session.jsonl"
        f.write_text(
            json.dumps({"role": "user", "content": "Hello"}) + "\n"
            + json.dumps({"role": "assistant", "content": "Hi there"}) + "\n"
        )
        md = _jsonl_to_markdown(f)
        assert "Hello" in md
        assert "Hi there" in md

    def test_content_block_list(self, tmp_path):
        import json

        from waystone.cli import _jsonl_to_markdown

        f = tmp_path / "session.jsonl"
        f.write_text(
            json.dumps({
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Block content here"}],
                },
            }) + "\n"
        )
        md = _jsonl_to_markdown(f)
        assert "Block content here" in md

    def test_skips_invalid_json_lines(self, tmp_path):
        import json

        from waystone.cli import _jsonl_to_markdown

        f = tmp_path / "session.jsonl"
        f.write_text(
            "not json\n"
            + json.dumps({"role": "user", "content": "Valid line"}) + "\n"
        )
        md = _jsonl_to_markdown(f)
        assert "Valid line" in md

    def test_empty_file(self, tmp_path):
        from waystone.cli import _jsonl_to_markdown
        f = tmp_path / "empty.jsonl"
        f.write_text("")
        assert _jsonl_to_markdown(f) == ""


class TestReset:
    def _seed(self, env, runner):
        from waystone.config import get_db_path
        runner.invoke(cli, ["init", "proj"], env=env)
        cfg = {"projects_dir": env["HOME"] + "/.waystone/projects"}
        db = get_db_path(cfg, "proj")
        s = GraphStore(db, vec_enabled=False)
        s.add_node({"id": "n1", "fact": "hello", "type": "decision", "confidence": 1.0, "tags": []})
        s.close()
        return db

    def test_reset_clears_graph_keeps_transcripts(self, tmp_path):
        runner = CliRunner()
        home = tmp_path / "home"; home.mkdir()
        env = {"HOME": str(home), "USERPROFILE": str(home)}
        db = self._seed(env, runner)
        # A saved transcript that must survive the default reset.
        transcript = db.parent / "transcripts" / "old_session.md"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text("Human: hi\n", encoding="utf-8")

        assert GraphStore(db, vec_enabled=False).get_stats()["node_count"] == 1
        r = runner.invoke(cli, ["reset", "proj", "--yes"], env=env)
        assert r.exit_code == 0
        # graph emptied but re-initialized (db still present)
        assert db.exists()
        assert GraphStore(db, vec_enabled=False).get_stats()["node_count"] == 0
        # default keeps transcripts
        assert transcript.exists()

    def test_reset_purge_removes_transcripts(self, tmp_path):
        runner = CliRunner()
        home = tmp_path / "home"; home.mkdir()
        env = {"HOME": str(home), "USERPROFILE": str(home)}
        db = self._seed(env, runner)
        transcript = db.parent / "transcripts" / "old_session.md"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text("Human: hi\n", encoding="utf-8")

        r = runner.invoke(cli, ["reset", "proj", "--yes", "--purge"], env=env)
        assert r.exit_code == 0
        assert not transcript.exists()
        assert GraphStore(db, vec_enabled=False).get_stats()["node_count"] == 0

    def test_reset_abort_keeps_graph(self, tmp_path):
        runner = CliRunner()
        home = tmp_path / "home"; home.mkdir()
        env = {"HOME": str(home), "USERPROFILE": str(home)}
        db = self._seed(env, runner)

        r = runner.invoke(cli, ["reset", "proj"], input="n\n", env=env)
        assert r.exit_code == 0
        assert "Aborted" in r.output
        assert GraphStore(db, vec_enabled=False).get_stats()["node_count"] == 1

    def test_reset_missing_project(self, tmp_path):
        runner = CliRunner()
        home = tmp_path / "home"; home.mkdir()
        env = {"HOME": str(home), "USERPROFILE": str(home)}
        r = runner.invoke(cli, ["reset", "ghost", "--yes"], env=env)
        assert r.exit_code == 0
        assert "nothing to reset" in r.output


class TestVerify:
    """`waystone verify` runs a real extraction round-trip (mocked here)."""

    def test_verify_success(self, tmp_path):
        from unittest.mock import AsyncMock, patch
        runner = CliRunner()
        env = {"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}
        with patch("waystone.extractor.extract",
                   new=AsyncMock(return_value={"nodes": [{"fact": "PostgreSQL is the DB"}], "edges": []})):
            r = runner.invoke(cli, ["verify"], env=env)
        assert r.exit_code == 0
        assert "Extraction works" in r.output

    def test_verify_json_shape(self, tmp_path):
        import json
        from unittest.mock import AsyncMock, patch
        runner = CliRunner()
        env = {"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}
        with patch("waystone.extractor.extract",
                   new=AsyncMock(return_value={"nodes": [{"fact": "x"}], "edges": []})):
            r = runner.invoke(cli, ["verify", "--json"], env=env)
        d = json.loads(r.output.strip().splitlines()[-1])
        assert d["ok"] is True and d["nodes"] == 1
        assert {"backend", "model", "key_source", "elapsed_ms", "category"} <= set(d)

    def test_verify_auth_failure_exit1(self, tmp_path):
        from unittest.mock import AsyncMock, patch
        runner = CliRunner()
        env = {"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}
        with patch("waystone.extractor.extract",
                   new=AsyncMock(side_effect=Exception("API error 401 invalid api key"))):
            r = runner.invoke(cli, ["verify"], env=env)
        assert r.exit_code == 1
        assert "[auth]" in r.output

    def test_verify_zero_nodes_exit1(self, tmp_path):
        import json
        from unittest.mock import AsyncMock, patch
        runner = CliRunner()
        env = {"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}
        with patch("waystone.extractor.extract",
                   new=AsyncMock(return_value={"nodes": [], "edges": []})):
            r = runner.invoke(cli, ["verify", "--json"], env=env)
        d = json.loads(r.output.strip().splitlines()[-1])
        assert r.exit_code == 1 and d["ok"] is False and d["category"] == "model"


class TestSelfcheck:
    """`waystone selfcheck` — fast offline 'does it install & run' check."""

    def test_selfcheck_passes(self, tmp_path):
        runner = CliRunner()
        env = {"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}
        r = runner.invoke(cli, ["selfcheck"], env=env)
        assert r.exit_code == 0
        assert "installs and runs" in r.output

    def test_selfcheck_json_shape(self, tmp_path):
        import json
        runner = CliRunner()
        env = {"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}
        r = runner.invoke(cli, ["selfcheck", "--json"], env=env)
        d = json.loads(r.output.strip().splitlines()[-1])
        assert d["ok"] is True
        names = {c["name"] for c in d["checks"]}
        assert {"import waystone", "config loads", "api key resolves"} <= names

    def test_selfcheck_no_key_still_ok(self, tmp_path, monkeypatch):
        """Missing API key is non-fatal — a fresh install with no key still 'runs'."""
        import json
        for v in ("GEMINI_API_KEY", "CTX_API_KEY", "WAYSTONE_API_KEY", "OPENAI_API_KEY"):
            monkeypatch.delenv(v, raising=False)
        runner = CliRunner()
        env = {"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}
        r = runner.invoke(cli, ["selfcheck", "--json"], env=env)
        d = json.loads(r.output.strip().splitlines()[-1])
        assert r.exit_code == 0 and d["ok"] is True
        keycheck = next(c for c in d["checks"] if c["name"] == "api key resolves")
        assert keycheck["fatal"] is False


class TestVersionFlag:
    def test_version_flag_works(self):
        """`waystone --version` must work — the post-publish smoke test runs it."""
        r = CliRunner().invoke(cli, ["--version"])
        assert r.exit_code == 0
        assert "version" in r.output.lower()


class TestSelfcheckDeep:
    """`waystone selfcheck --deep` exercises hooks + MCP without a Claude session."""

    def test_deep_runs_all_hooks_and_mcp(self, tmp_path):
        import json
        runner = CliRunner()
        env = {"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}
        r = runner.invoke(cli, ["selfcheck", "--deep", "--json"], env=env)
        d = json.loads(r.output.strip().splitlines()[-1])
        names = {c["name"] for c in d["checks"]}
        for h in ("hook: submit", "hook: stop", "hook: posttool",
                  "hook: import_memory", "hook: statusline"):
            assert h in names, f"missing {h}"
        assert "MCP tools registered" in names
        # No fatal check may fail (hooks + MCP are fatal; entry-points L1 is not).
        fatal_failures = [c["name"] for c in d["checks"] if c["fatal"] and not c["ok"]]
        assert not fatal_failures, fatal_failures
        assert r.exit_code == 0


class TestSessionStartHook:
    """SessionStart hook: silent on healthy, exits 0, warns only on breakage."""

    def test_runs_silent_and_exits_zero(self, tmp_path):
        import subprocess, sys, json
        env = {**__import__("os").environ, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}
        p = subprocess.run(
            [sys.executable, "-m", "waystone._hooks.sessionstart"],
            input=json.dumps({"source": "startup", "cwd": str(tmp_path), "session_id": "s"}),
            text=True, capture_output=True, env=env, timeout=30,
        )
        assert p.returncode == 0
        # healthy env should not emit a failure warning
        assert "selfcheck failed" not in p.stdout

    def test_run_quick_shape(self):
        from waystone._selfcheck import run_quick
        ok, checks, ver = run_quick()
        names = {c["name"] for c in checks}
        assert {"import waystone", "config loads", "api key resolves", "sqlite-vec available"} <= names
        assert isinstance(ok, bool) and isinstance(ver, str)


class TestSessionPreview:
    def test_preview_returns_first_user_prompt(self, tmp_path):
        import json
        from waystone.cli import _session_preview
        f = tmp_path / "s.jsonl"
        f.write_text(
            json.dumps({"type": "summary", "summary": "x"}) + "\n"
            + json.dumps({"type": "user", "message": {"role": "user", "content": "Build the ASVAB test screen"}}) + "\n"
            + json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "ok"}}) + "\n",
            encoding="utf-8",
        )
        assert _session_preview(f) == "Build the ASVAB test screen"

    def test_preview_truncates_long_prompts(self, tmp_path):
        import json
        from waystone.cli import _session_preview
        f = tmp_path / "s.jsonl"
        long = "x" * 200
        f.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": long}}) + "\n",
                     encoding="utf-8")
        out = _session_preview(f, max_len=70)
        assert out.endswith("…") and len(out) <= 71

    def test_preview_empty_when_no_user_turn(self, tmp_path):
        import json
        from waystone.cli import _session_preview
        f = tmp_path / "s.jsonl"
        f.write_text(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "hi"}}) + "\n",
                     encoding="utf-8")
        assert _session_preview(f) == ""


class TestPrune:
    def _seed(self, env, runner):
        from waystone.config import get_db_path
        runner.invoke(cli, ["init", "proj"], env=env)
        db = get_db_path({"projects_dir": env["HOME"] + "/.waystone/projects"}, "proj")
        s = GraphStore(db, vec_enabled=False)
        s.add_node({"id": "junk1", "fact": "The Waystone database is currently empty.", "type": "implementation", "confidence": 1.0, "tags": []})
        s.add_node({"id": "junk2", "fact": "The 'proj' graph has 0 nodes and 0 edges.", "type": "implementation", "confidence": 1.0, "tags": []})
        s.add_node({"id": "real1", "fact": "Decided NOT to use Redis for caching.", "type": "decision", "confidence": 1.0, "tags": []})
        s.add_node({"id": "real2", "fact": "PostgreSQL is the primary database.", "type": "decision", "confidence": 1.0, "tags": []})
        s.close()
        return db

    def test_meta_noise_dry_run_deletes_nothing(self, tmp_path):
        runner = CliRunner()
        env = {"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}
        db = self._seed(env, runner)
        r = runner.invoke(cli, ["prune", "proj", "--meta-noise"], env=env)
        assert r.exit_code == 0
        assert "Would remove 2" in r.output and "--execute" in r.output
        assert GraphStore(db, vec_enabled=False).get_stats()["node_count"] == 4  # nothing deleted

    def test_meta_noise_execute_removes_only_junk(self, tmp_path):
        runner = CliRunner()
        env = {"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}
        db = self._seed(env, runner)
        r = runner.invoke(cli, ["prune", "proj", "--meta-noise", "--execute"], env=env)
        assert r.exit_code == 0
        assert "Deleted 2" in r.output
        s = GraphStore(db, vec_enabled=False)
        facts = {n["fact"] for n in s.get_all_nodes()}
        s.close()
        assert facts == {"Decided NOT to use Redis for caching.", "PostgreSQL is the primary database."}

    def test_prune_requires_a_filter(self, tmp_path):
        runner = CliRunner()
        env = {"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}
        self._seed(env, runner)
        r = runner.invoke(cli, ["prune", "proj"], env=env)
        assert r.exit_code != 0
        assert "at least one filter" in r.output


class TestSessionSummaries:
    def test_no_llm_returns_empty_map(self, tmp_path, monkeypatch):
        from waystone.cli import _summarize_sessions
        for v in ("GEMINI_API_KEY", "CTX_API_KEY", "WAYSTONE_API_KEY", "OPENAI_API_KEY"):
            monkeypatch.delenv(v, raising=False)
        sessions = [{"path": tmp_path / "x.jsonl"}]
        # no key + remote base_url => no LLM => {} (callers fall back to preview)
        out = _summarize_sessions(sessions, {"llm": {"base_url": "https://api.example.com/v1", "model": "m"}})
        assert out == {}

    def test_summaries_used_when_available(self, tmp_path, monkeypatch):
        import json
        from unittest.mock import AsyncMock, patch
        # make a session file with a misleading throwaway opener
        f = tmp_path / "s.jsonl"
        f.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "is waystone configured?"}}) + "\n",
                     encoding="utf-8")
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        with patch("waystone.cli.summarize_session", new=AsyncMock(return_value="Built extraction pipeline; fixed chunking")):
            from waystone.cli import _summarize_sessions
            out = _summarize_sessions([{"path": f}], {"llm": {"base_url": "https://x/v1", "model": "m", "api_key_env": "GEMINI_API_KEY"}})
        assert out[str(f)] == "Built extraction pipeline; fixed chunking"

    def test_summarize_session_no_baseurl_returns_empty(self):
        import asyncio
        from waystone.extractor import summarize_session
        assert asyncio.run(summarize_session("some text", {"llm": {}})) == ""


class TestAwaySummary:
    """Capture Claude Code's native session recaps (away_summary) as session_summary nodes."""

    def _session(self, tmp_path, *, aways=(), title=None, user="hello"):
        import json
        f = tmp_path / "s.jsonl"
        lines = [json.dumps({"type": "user", "message": {"role": "user", "content": user}})]
        for a in aways:
            lines.append(json.dumps({"type": "system", "subtype": "away_summary", "content": a}))
        if title:
            lines.append(json.dumps({"type": "ai-title", "aiTitle": title}))
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return f

    def test_extract_away_summaries_chronological(self, tmp_path):
        from waystone.transcript import extract_away_summaries
        f = self._session(tmp_path, aways=["first recap", "second recap", "LAST recap"])
        out = extract_away_summaries(f)
        assert out == ["first recap", "second recap", "LAST recap"]
        assert out[-1] == "LAST recap"

    def test_extract_ai_title_variants(self, tmp_path):
        import json
        from waystone.transcript import extract_ai_title
        f = tmp_path / "t.jsonl"
        f.write_text(
            json.dumps({"type": "ai-title", "aiTitle": "First Title"}) + "\n"
            + json.dumps({"type": "ai-title", "ai_title": "Final Title"}) + "\n",
            encoding="utf-8",
        )
        assert extract_ai_title(f) == "Final Title"  # last wins, alt key honored
        assert extract_ai_title(tmp_path / "missing.jsonl") == ""

    def test_build_node_uses_last_summary(self, tmp_path):
        from waystone.cli import _build_session_summary_node
        f = self._session(tmp_path, aways=["early", "Goal was X; shipped Y; Next Z"])
        node = _build_session_summary_node(f, "claude_session:x", "2026-06-09T00:00:00Z")
        assert node and node["type"] == "session_summary" and node["confidence"] == 1.0
        assert node["fact"] == "Goal was X; shipped Y; Next Z"
        assert "session" in node["tags"] and "summary" in node["tags"]

    def test_build_node_none_without_summary(self, tmp_path):
        from waystone.cli import _build_session_summary_node
        f = self._session(tmp_path, aways=())  # no away_summary entries
        assert _build_session_summary_node(f, "claude_session:x", "2026-06-09T00:00:00Z") is None

    def test_session_summary_node_is_retrievable(self, tmp_path):
        from waystone.store import GraphStore
        db = tmp_path / "g.db"
        s = GraphStore(db, vec_enabled=False)
        s.add_node({"id": "n_s1", "fact": "We hardened onboarding and shipped fixes to PyPI.",
                    "type": "session_summary", "confidence": 1.0,
                    "tags": ["session", "summary", "onboarding", "pypi"]})
        got = [n for n in s.get_all_nodes() if n["type"] == "session_summary"]
        s.close()
        assert len(got) == 1 and "onboarding" in got[0]["fact"].lower()


class TestSummarizeSession:
    """Periodic session summarization → timeline of superseding session_summary nodes."""

    def _jsonl(self, tmp_path, n=9):
        import json
        f = tmp_path / "s.jsonl"
        lines = []
        for i in range(n):
            role = "user" if i % 2 == 0 else "assistant"
            lines.append(json.dumps({"type": role, "message": {"role": role, "content": f"turn {i} about feature X"}}))
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return f

    def test_timeline_supersede_keeps_history(self, tmp_path, monkeypatch):
        from unittest.mock import AsyncMock, patch
        from waystone.config import get_db_path
        from waystone.store import GraphStore
        # skip embedding for speed/determinism
        monkeypatch.setattr("waystone.embedder.is_available", lambda: False, raising=False)
        env = {"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}
        runner = CliRunner()
        runner.invoke(cli, ["init", "proj"], env=env)
        f = self._jsonl(tmp_path, n=9)

        calls = {"n": 0}
        async def fake(new_text, prior, config):
            calls["n"] += 1
            return f"Summary v{calls['n']}"

        with patch("waystone.cli.generate_session_summary", new=fake):
            r = runner.invoke(cli, ["summarize-session", "proj", str(f), "--every", "4"], env=env)
        assert r.exit_code == 0, r.output

        db = get_db_path({"projects_dir": str(tmp_path / ".waystone" / "projects")}, "proj")
        s = GraphStore(db, vec_enabled=False)
        total = s.conn.execute("SELECT COUNT(*) FROM nodes WHERE type='session_summary'").fetchone()[0]
        active = s.conn.execute("SELECT COUNT(*) FROM nodes WHERE type='session_summary' AND is_active=1").fetchone()[0]
        s.close()
        assert total == 3, f"expected 3 timeline nodes, got {total}"
        assert active == 1, f"expected exactly 1 active (latest), got {active}"

    def test_dry_run_stores_nothing(self, tmp_path, monkeypatch):
        from unittest.mock import patch
        from waystone.config import get_db_path
        from waystone.store import GraphStore
        monkeypatch.setattr("waystone.embedder.is_available", lambda: False, raising=False)
        env = {"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}
        runner = CliRunner()
        runner.invoke(cli, ["init", "proj"], env=env)
        f = self._jsonl(tmp_path, n=9)

        async def fake(new_text, prior, config):
            return "Summary"

        with patch("waystone.cli.generate_session_summary", new=fake):
            r = runner.invoke(cli, ["summarize-session", "proj", str(f), "--every", "4", "--dry-run"], env=env)
        assert r.exit_code == 0 and "dry-run" in r.output.lower()
        db = get_db_path({"projects_dir": str(tmp_path / ".waystone" / "projects")}, "proj")
        s = GraphStore(db, vec_enabled=False)
        total = s.conn.execute("SELECT COUNT(*) FROM nodes WHERE type='session_summary'").fetchone()[0]
        s.close()
        assert total == 0
