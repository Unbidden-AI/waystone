"""Async HTTP client for the Context Broker hosted API.

When ``api_url`` is set in config, operations route here instead of
touching a local SQLite database.

Usage::

    client = RemoteContextBroker("https://cb.example.com", api_key="sk-...")
    result = await client.query("myproject", "implement auth")
    print(result["markdown"])
"""

from __future__ import annotations

import httpx

DEFAULT_TIMEOUT = 120.0


class RemoteContextBroker:
    """Thin async client over the Context Broker REST API.

    All methods raise ``httpx.HTTPStatusError`` on 4xx/5xx responses.
    """

    def __init__(
        self,
        api_url: str,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = api_url.rstrip("/")
        self._headers: dict[str, str] = {}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(headers=self._headers, timeout=self._timeout)

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def health(self) -> dict:
        """GET /v1/health — returns {status, version}."""
        async with httpx.AsyncClient(headers=self._headers, timeout=10.0) as c:
            r = await c.get(f"{self.base_url}/v1/health")
            r.raise_for_status()
            return r.json()

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    async def list_projects(self) -> list[dict]:
        """GET /v1/projects — returns [{name, node_count, edge_count}]."""
        async with self._client() as c:
            r = await c.get(f"{self.base_url}/v1/projects")
            r.raise_for_status()
            return r.json()

    async def init_project(self, project: str) -> dict:
        """POST /v1/projects/{project} — create project (idempotent)."""
        async with self._client() as c:
            r = await c.post(f"{self.base_url}/v1/projects/{project}")
            r.raise_for_status()
            return r.json()

    async def get_stats(self, project: str) -> dict:
        """GET /v1/projects/{project}/stats — returns {node_count, edge_count, type_counts}."""
        async with self._client() as c:
            r = await c.get(f"{self.base_url}/v1/projects/{project}/stats")
            r.raise_for_status()
            return r.json()

    # ------------------------------------------------------------------
    # Query / Extract / Export
    # ------------------------------------------------------------------

    async def query(
        self,
        project: str,
        task: str,
        *,
        hops: int = 3,
        top_k: int = 25,
    ) -> dict:
        """POST /v1/projects/{project}/query.

        Returns {markdown, nodes_returned, total_nodes, tokens_estimated}.
        """
        async with self._client() as c:
            r = await c.post(
                f"{self.base_url}/v1/projects/{project}/query",
                json={"task": task, "hops": hops, "top_k": top_k},
            )
            r.raise_for_status()
            return r.json()

    async def extract(
        self,
        project: str,
        text: str,
        *,
        source_name: str = "api",
        verify: bool = False,
    ) -> dict:
        """POST /v1/projects/{project}/extract.

        Returns {project, nodes_extracted, edges_extracted,
                 nodes_per_1k_chars, avg_tags_per_node}.
        """
        async with self._client() as c:
            r = await c.post(
                f"{self.base_url}/v1/projects/{project}/extract",
                json={"text": text, "source_name": source_name, "verify": verify},
            )
            r.raise_for_status()
            return r.json()

    async def export(self, project: str) -> dict:
        """GET /v1/projects/{project}/export — returns {markdown, node_count}."""
        async with self._client() as c:
            r = await c.get(f"{self.base_url}/v1/projects/{project}/export")
            r.raise_for_status()
            return r.json()
