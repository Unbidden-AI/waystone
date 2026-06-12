"""Tests for the MCP server tools."""

import asyncio
import json
import subprocess
import sys
from unittest.mock import AsyncMock, patch

import pytest

from waystone.store import GraphStore


@pytest.fixture
def project_setup(tmp_path):
    """Create a temp project dir with a populated graph and a mock config."""
    projects_dir = tmp_path / "projects"
    project_dir = projects_dir / "test-project"
    project_dir.mkdir(parents=True)
    (project_dir / "transcripts").mkdir()
    (project_dir / "exports").mkdir()

    db_path = project_dir / "context.db"
    store = GraphStore(db_path)
    store.add_node({
        "id": "n_abc12345",
        "fact": "The system uses PostgreSQL for persistence",
        "type": "decision",
        "confidence": 0.9,
        "tags": ["database", "postgresql", "persistence", "storage"],
        "created_at": "2026-03-07T00:00:00Z",
        "supersedes": [],
    })
    store.add_node({
        "id": "n_def67890",
        "fact": "Authentication uses JWT tokens with 24-hour expiry",
        "type": "constraint",
        "confidence": 0.85,
        "tags": ["authentication", "jwt", "security", "tokens"],
        "created_at": "2026-03-08T00:00:00Z",
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


class TestContextBrokerQuery:
    def test_returns_markdown_for_matching_query(self, project_setup, monkeypatch):
        tmp_path, db_path, config = project_setup

        monkeypatch.chdir(tmp_path)
        (tmp_path / ".waystone").write_text("test-project\n")

        from waystone.mcp_server import waystone_query
        with patch("waystone.mcp_server._load_config", return_value=config):
            result = waystone_query(task="database storage", project="test-project")

        assert "PostgreSQL" in result

    def test_raises_on_missing_project(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = {"llm": {}, "defaults": {}, "strategies": {}, "projects_dir": str(tmp_path / "projects")}

        from waystone.mcp_server import waystone_query
        with patch("waystone.mcp_server._load_config", return_value=config):
            with pytest.raises(FileNotFoundError):
                waystone_query(task="anything", project="nonexistent-project")

    def test_raises_when_no_project_detected(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = {"llm": {}, "defaults": {}, "strategies": {}, "projects_dir": str(tmp_path / "projects")}

        from waystone.mcp_server import waystone_query
        with patch("waystone.mcp_server._load_config", return_value=config):
            with pytest.raises(ValueError, match="No project specified"):
                waystone_query(task="anything")


class TestContextBrokerStats:
    def test_returns_stats(self, project_setup):
        tmp_path, db_path, config = project_setup

        from waystone.mcp_server import waystone_stats
        with patch("waystone.mcp_server._load_config", return_value=config):
            result = waystone_stats(project="test-project")

        assert "Nodes: 2" in result
        assert "test-project" in result

    def test_raises_on_missing_project(self, tmp_path):
        config = {"llm": {}, "defaults": {}, "strategies": {}, "projects_dir": str(tmp_path / "projects")}

        from waystone.mcp_server import waystone_stats
        with patch("waystone.mcp_server._load_config", return_value=config):
            with pytest.raises(FileNotFoundError):
                waystone_stats(project="nonexistent")


class TestContextBrokerListProjects:
    def test_lists_existing_projects(self, project_setup):
        tmp_path, db_path, config = project_setup

        from waystone.mcp_server import waystone_list_projects
        with patch("waystone.mcp_server._load_config", return_value=config):
            result = waystone_list_projects()

        assert "test-project" in result
        assert "2 nodes" in result

    def test_returns_message_when_no_projects(self, tmp_path):
        config = {"llm": {}, "defaults": {}, "strategies": {}, "projects_dir": str(tmp_path / "nonexistent")}

        from waystone.mcp_server import waystone_list_projects
        with patch("waystone.mcp_server._load_config", return_value=config):
            result = waystone_list_projects()

        assert "No projects found" in result


class TestContextBrokerExtract:
    def test_raises_on_oversized_input(self, project_setup):
        tmp_path, db_path, config = project_setup

        from waystone.mcp_server import _MAX_EXTRACT_CHARS, waystone_extract
        with patch("waystone.mcp_server._load_config", return_value=config):
            with pytest.raises(ValueError, match="exceeds the"):
                waystone_extract(
                    text="x" * (_MAX_EXTRACT_CHARS + 1),
                    project="test-project",
                )

    def test_raises_on_missing_project(self, tmp_path):
        config = {"llm": {}, "defaults": {}, "strategies": {}, "projects_dir": str(tmp_path / "projects")}

        from waystone.mcp_server import waystone_extract
        with patch("waystone.mcp_server._load_config", return_value=config):
            with pytest.raises(FileNotFoundError):
                waystone_extract(text="some text", project="nonexistent")

    def test_extract_merges_into_graph(self, project_setup):
        tmp_path, db_path, config = project_setup

        mock_result = {
            "nodes": [{
                "id": "n_new00001",
                "fact": "Rate limiting uses Redis",
                "type": "implementation",
                "confidence": 0.9,
                "tags": ["redis", "rate-limiting", "cache"],
                "supersedes": [],
            }],
            "edges": [],
        }

        from waystone.mcp_server import waystone_extract
        with patch("waystone.mcp_server._load_config", return_value=config):
            with patch("waystone.mcp_server.extract", new=AsyncMock(return_value=mock_result)):
                result = waystone_extract(
                    text="We decided to use Redis for rate limiting.",
                    project="test-project",
                )

        assert "1 nodes" in result
        # Verify node was written to the DB
        store = GraphStore(db_path)
        node = store.get_node("n_new00001")
        store.close()
        assert node is not None
        assert node["fact"] == "Rate limiting uses Redis"


class TestMcpToolsAllLoad:
    def test_all_four_tools_registered(self):
        """Smoke test: all four tools load without decorator errors."""
        from waystone.mcp_server import mcp

        tool_names = asyncio.run(mcp.list_tools())
        names = {t.name for t in tool_names}
        assert "waystone_query" in names
        assert "waystone_extract" in names
        assert "waystone_stats" in names
        assert "waystone_list_projects" in names


class TestMcpJsonRpcHandshake:
    """End-to-end integration test: spawn waystone-mcp, send JSON-RPC initialize, verify response."""

    def test_initialize_handshake(self):
        """Spawn the MCP server subprocess and verify the JSON-RPC initialize handshake."""
        # Build the initialize request per MCP/JSON-RPC 2.0 spec
        init_request = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "0.0.1"},
            },
        }) + "\n"

        # Use the installed console script; fall back to invoking run_server via -c
        proc = subprocess.Popen(
            [sys.executable, "-c", "from waystone.mcp_server import run_server; run_server()"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            stdout, _ = proc.communicate(input=init_request.encode(), timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise AssertionError("MCP server did not respond within 10 seconds")

        # Parse the first line of output as JSON-RPC
        lines = [ln for ln in stdout.decode().splitlines() if ln.strip()]
        assert lines, "MCP server produced no stdout output"
        response = json.loads(lines[0])

        assert response.get("jsonrpc") == "2.0"
        assert response.get("id") == 1
        assert "result" in response, f"Expected 'result', got: {response}"
        result = response["result"]
        assert "serverInfo" in result or "protocolVersion" in result, (
            f"Unexpected initialize result shape: {result}"
        )
