"""CLI remote-backend routing: `backend: remote` sends reads/writes to the Team
Server (RemoteContextBroker) over HTTP instead of the local SQLite GraphStore.

The remote client is patched, so these assert the CLI *routes* correctly (calls the
right client method, prints the result, touches no local DB) without a live server.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import yaml
from click.testing import CliRunner

from waystone.cli import cli
from waystone.config import is_remote


def _remote_cfg(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({
        "backend": "remote",
        "api_url": "https://team.example.com",
        "api_key": "k_test",
        "projects_dir": str(tmp_path / "projects"),
    }))
    return cfg


def _fake_client(**methods):
    c = MagicMock()
    for name, ret in methods.items():
        setattr(c, name, AsyncMock(return_value=ret))
    return c


# --------------------------------------------------------------------------- switch

def test_is_remote_backend_switch():
    assert is_remote({"backend": "remote"}) is True
    assert is_remote({"backend": "REMOTE"}) is True               # case-insensitive
    assert is_remote({"backend": "local", "api_url": "https://x"}) is False
    assert is_remote({"api_url": "https://x"}) is True            # infer from api_url
    assert is_remote({}) is False


# --------------------------------------------------------------------------- query

def test_query_routes_remote(tmp_path):
    cfg = _remote_cfg(tmp_path)
    client = _fake_client(query={
        "markdown": "## JWT decision", "nodes_returned": 1,
        "total_nodes": 1, "tokens_estimated": 42,
    })
    with patch("waystone.cli.make_remote_client", return_value=client):
        r = CliRunner().invoke(cli, ["--config", str(cfg), "query", "p", "how does auth work"])
    assert r.exit_code == 0, r.output
    assert "JWT decision" in r.output
    client.query.assert_awaited_once()
    # remote mode must not create a local graph
    assert not (tmp_path / "projects" / "p" / "context.db").exists()


def test_query_warns_on_ignored_strategy_flags(tmp_path):
    cfg = _remote_cfg(tmp_path)
    client = _fake_client(query={
        "markdown": "x", "nodes_returned": 0, "total_nodes": 0, "tokens_estimated": 0,
    })
    with patch("waystone.cli.make_remote_client", return_value=client):
        r = CliRunner().invoke(
            cli, ["--config", str(cfg), "query", "p", "task", "--confidence", "0.6"])
    assert r.exit_code == 0, r.output
    assert "ignored in remote mode" in r.output  # confidence is silently-ignored → must warn


# --------------------------------------------------------------------------- extract

def test_extract_routes_remote(tmp_path):
    cfg = _remote_cfg(tmp_path)
    src = tmp_path / "t.md"
    src.write_text("We chose Postgres for JSONB.")
    client = _fake_client(extract={"nodes_extracted": 3, "edges_extracted": 1})
    with patch("waystone.cli.make_remote_client", return_value=client):
        r = CliRunner().invoke(cli, ["--config", str(cfg), "extract", "p", str(src)])
    assert r.exit_code == 0, r.output
    assert "3 nodes" in r.output
    client.extract.assert_awaited_once()
    args, _ = client.extract.call_args
    assert args[0] == "p"
    assert "Postgres" in args[1]


# --------------------------------------------------------------------------- show

def test_show_routes_remote(tmp_path):
    cfg = _remote_cfg(tmp_path)
    client = _fake_client(get_stats={
        "node_count": 5, "edge_count": 2, "type_counts": {"decision": 3},
    })
    with patch("waystone.cli.make_remote_client", return_value=client):
        r = CliRunner().invoke(cli, ["--config", str(cfg), "show", "p"])
    assert r.exit_code == 0, r.output
    assert "Nodes: 5" in r.output
    assert "decision: 3" in r.output


# --------------------------------------------------------------------------- export

def test_export_routes_remote_to_file(tmp_path):
    cfg = _remote_cfg(tmp_path)
    out = tmp_path / "out.md"
    client = _fake_client(export={"markdown": "# Export\nstuff", "node_count": 7})
    with patch("waystone.cli.make_remote_client", return_value=client):
        r = CliRunner().invoke(cli, ["--config", str(cfg), "export", "p", "-o", str(out)])
    assert r.exit_code == 0, r.output
    assert out.read_text().startswith("# Export")
    assert "7 nodes" in r.output


def test_export_routes_remote_stdout(tmp_path):
    cfg = _remote_cfg(tmp_path)
    client = _fake_client(export={"markdown": "# Export body", "node_count": 1})
    with patch("waystone.cli.make_remote_client", return_value=client):
        r = CliRunner().invoke(cli, ["--config", str(cfg), "export", "p"])
    assert r.exit_code == 0, r.output
    assert "# Export body" in r.output


# --------------------------------------------------------------------------- init

def test_init_routes_remote(tmp_path):
    cfg = _remote_cfg(tmp_path)
    client = _fake_client(init_project={"project": "p", "created": True})
    with patch("waystone.cli.make_remote_client", return_value=client):
        r = CliRunner().invoke(cli, ["--config", str(cfg), "init", "p"])
    assert r.exit_code == 0, r.output
    assert "Initialized" in r.output
    client.init_project.assert_awaited_once_with("p")
    assert not (tmp_path / "projects" / "p").exists()


# --------------------------------------------------------------------------- local override

def test_local_backend_overrides_api_url(tmp_path):
    """backend: local wins even when api_url is present → real local graph."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({
        "backend": "local",
        "api_url": "https://x",
        "projects_dir": str(tmp_path / "projects"),
    }))
    r = CliRunner().invoke(cli, ["--config", str(cfg), "init", "p"])
    assert r.exit_code == 0, r.output
    assert (tmp_path / "projects" / "p" / "context.db").exists()
