"""Tests for RemoteContextBroker async HTTP client."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("httpx", reason="httpx not installed — skip remote client tests")

import httpx

from waystone.remote_client import RemoteContextBroker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(status_code: int, json_data) -> MagicMock:
    """Build a mock httpx.Response."""
    r = MagicMock(spec=httpx.Response)
    r.status_code = status_code
    r.json.return_value = json_data
    if status_code >= 400:
        r.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"{status_code}", request=MagicMock(), response=r
        )
    else:
        r.raise_for_status.return_value = None
    return r


def _mock_client(response: MagicMock) -> MagicMock:
    """Async context manager that yields a client returning ``response`` for any call."""
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    client.post = AsyncMock(return_value=response)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, client


# ---------------------------------------------------------------------------
# __init__ / headers
# ---------------------------------------------------------------------------

class TestInit:
    def test_base_url_trailing_slash_stripped(self):
        c = RemoteContextBroker("https://cb.example.com/")
        assert c.base_url == "https://cb.example.com"

    def test_no_auth_header_when_no_key(self):
        c = RemoteContextBroker("https://cb.example.com")
        assert "Authorization" not in c._headers

    def test_bearer_header_set_when_key_given(self):
        c = RemoteContextBroker("https://cb.example.com", api_key="sk-secret")
        assert c._headers["Authorization"] == "Bearer sk-secret"

    def test_timeout_stored(self):
        c = RemoteContextBroker("https://cb.example.com", timeout=30.0)
        assert c._timeout == 30.0


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------

class TestHealth:
    @pytest.mark.asyncio
    async def test_returns_json(self):
        payload = {"status": "ok", "version": "0.1.0"}
        cm, client = _mock_client(_mock_response(200, payload))
        broker = RemoteContextBroker("https://cb.example.com")
        with patch("httpx.AsyncClient", return_value=cm):
            result = await broker.health()
        assert result == payload

    @pytest.mark.asyncio
    async def test_raises_on_server_error(self):
        cm, _ = _mock_client(_mock_response(500, {}))
        broker = RemoteContextBroker("https://cb.example.com")
        with patch("httpx.AsyncClient", return_value=cm):
            with pytest.raises(httpx.HTTPStatusError):
                await broker.health()


# ---------------------------------------------------------------------------
# list_projects
# ---------------------------------------------------------------------------

class TestListProjects:
    @pytest.mark.asyncio
    async def test_returns_list(self):
        payload = [{"name": "proj-a", "node_count": 5, "edge_count": 2}]
        cm, client = _mock_client(_mock_response(200, payload))
        broker = RemoteContextBroker("https://cb.example.com")
        broker._client = lambda: cm
        result = await broker.list_projects()
        assert result == payload
        client.get.assert_called_once_with("https://cb.example.com/v1/projects")

    @pytest.mark.asyncio
    async def test_raises_on_401(self):
        cm, _ = _mock_client(_mock_response(401, {"detail": "Unauthorized"}))
        broker = RemoteContextBroker("https://cb.example.com")
        broker._client = lambda: cm
        with pytest.raises(httpx.HTTPStatusError):
            await broker.list_projects()


# ---------------------------------------------------------------------------
# init_project
# ---------------------------------------------------------------------------

class TestInitProject:
    @pytest.mark.asyncio
    async def test_posts_to_correct_url(self):
        payload = {"project": "my-proj", "created": True}
        cm, client = _mock_client(_mock_response(201, payload))
        broker = RemoteContextBroker("https://cb.example.com")
        broker._client = lambda: cm
        result = await broker.init_project("my-proj")
        assert result == payload
        client.post.assert_called_once_with("https://cb.example.com/v1/projects/my-proj")


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------

class TestGetStats:
    @pytest.mark.asyncio
    async def test_returns_stats(self):
        payload = {"project": "p", "node_count": 10, "edge_count": 3, "type_counts": {}}
        cm, client = _mock_client(_mock_response(200, payload))
        broker = RemoteContextBroker("https://cb.example.com")
        broker._client = lambda: cm
        result = await broker.get_stats("p")
        assert result["node_count"] == 10
        client.get.assert_called_once_with("https://cb.example.com/v1/projects/p/stats")

    @pytest.mark.asyncio
    async def test_raises_on_404(self):
        cm, _ = _mock_client(_mock_response(404, {"detail": "Not found"}))
        broker = RemoteContextBroker("https://cb.example.com")
        broker._client = lambda: cm
        with pytest.raises(httpx.HTTPStatusError):
            await broker.get_stats("missing")


# ---------------------------------------------------------------------------
# query
# ---------------------------------------------------------------------------

class TestQuery:
    @pytest.mark.asyncio
    async def test_posts_correct_payload(self):
        payload = {"markdown": "## Context\n...", "nodes_returned": 3, "total_nodes": 5, "tokens_estimated": 100}
        cm, client = _mock_client(_mock_response(200, payload))
        broker = RemoteContextBroker("https://cb.example.com")
        broker._client = lambda: cm
        result = await broker.query("myproj", "build auth", hops=2, top_k=10)
        assert result["markdown"] == "## Context\n..."
        client.post.assert_called_once_with(
            "https://cb.example.com/v1/projects/myproj/query",
            json={"task": "build auth", "hops": 2, "top_k": 10},
        )

    @pytest.mark.asyncio
    async def test_default_hops_and_top_k(self):
        payload = {"markdown": "", "nodes_returned": 0, "total_nodes": 0, "tokens_estimated": 0}
        cm, client = _mock_client(_mock_response(200, payload))
        broker = RemoteContextBroker("https://cb.example.com")
        broker._client = lambda: cm
        await broker.query("myproj", "task")
        _, kwargs = client.post.call_args
        assert kwargs["json"]["hops"] == 3
        assert kwargs["json"]["top_k"] == 25

    @pytest.mark.asyncio
    async def test_raises_on_404(self):
        cm, _ = _mock_client(_mock_response(404, {}))
        broker = RemoteContextBroker("https://cb.example.com")
        broker._client = lambda: cm
        with pytest.raises(httpx.HTTPStatusError):
            await broker.query("gone", "task")


# ---------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------

class TestExtract:
    @pytest.mark.asyncio
    async def test_posts_correct_payload(self):
        payload = {"project": "p", "nodes_extracted": 5, "edges_extracted": 2,
                   "nodes_per_1k_chars": 1.2, "avg_tags_per_node": 3.1}
        cm, client = _mock_client(_mock_response(200, payload))
        broker = RemoteContextBroker("https://cb.example.com")
        broker._client = lambda: cm
        result = await broker.extract("p", "some text", source_name="my-session", verify=True)
        assert result["nodes_extracted"] == 5
        client.post.assert_called_once_with(
            "https://cb.example.com/v1/projects/p/extract",
            json={"text": "some text", "source_name": "my-session", "verify": True},
        )

    @pytest.mark.asyncio
    async def test_default_source_and_verify(self):
        payload = {"project": "p", "nodes_extracted": 0, "edges_extracted": 0,
                   "nodes_per_1k_chars": 0.0, "avg_tags_per_node": 0.0}
        cm, client = _mock_client(_mock_response(200, payload))
        broker = RemoteContextBroker("https://cb.example.com")
        broker._client = lambda: cm
        await broker.extract("p", "text")
        _, kwargs = client.post.call_args
        assert kwargs["json"]["source_name"] == "api"
        assert kwargs["json"]["verify"] is False

    @pytest.mark.asyncio
    async def test_raises_on_422(self):
        cm, _ = _mock_client(_mock_response(422, {"detail": "Input too large"}))
        broker = RemoteContextBroker("https://cb.example.com")
        broker._client = lambda: cm
        with pytest.raises(httpx.HTTPStatusError):
            await broker.extract("p", "x" * 200_001)


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

class TestExport:
    @pytest.mark.asyncio
    async def test_returns_markdown(self):
        payload = {"markdown": "## Decisions\n...", "node_count": 7}
        cm, client = _mock_client(_mock_response(200, payload))
        broker = RemoteContextBroker("https://cb.example.com")
        broker._client = lambda: cm
        result = await broker.export("myproj")
        assert result["node_count"] == 7
        client.get.assert_called_once_with("https://cb.example.com/v1/projects/myproj/export")

    @pytest.mark.asyncio
    async def test_raises_on_404(self):
        cm, _ = _mock_client(_mock_response(404, {}))
        broker = RemoteContextBroker("https://cb.example.com")
        broker._client = lambda: cm
        with pytest.raises(httpx.HTTPStatusError):
            await broker.export("gone")


# ---------------------------------------------------------------------------
# Auth header propagation
# ---------------------------------------------------------------------------

class TestAuthHeader:
    @pytest.mark.asyncio
    async def test_bearer_forwarded_in_client(self):
        """_client() builds AsyncClient with Authorization header."""
        broker = RemoteContextBroker("https://cb.example.com", api_key="my-key")
        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
                get=AsyncMock(return_value=_mock_response(200, [])),
            ))
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            await broker.list_projects()
        mock_cls.assert_called_once()
        _, kwargs = mock_cls.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer my-key"
