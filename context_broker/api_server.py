"""Context Broker REST API server.

Exposes extraction and retrieval as HTTP endpoints so remote clients
(CLI, MCP server, SDKs) can share a single graph store across machines.

Usage:
    ctx serve                           # default port 8000
    ctx serve --port 9000
    uvicorn context_broker.api_server:app --reload   # dev

Auth:
    Set CB_API_KEY env var on the server.  Clients pass it as:
        Authorization: Bearer <key>
    When CB_API_KEY is not set, auth is skipped (local dev mode).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from .config import get_db_path, load_config
from .extractor import extract as _extract, score_extraction_quality, verify_extraction
from .retriever import retrieve_with_stats
from .store import GraphStore

app = FastAPI(
    title="Context Broker API",
    version="0.1.0",
    description="DAG-based context intelligence layer for LLM workflows",
)

_bearer = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _check_auth(
    creds: Annotated[HTTPAuthorizationCredentials | None, Security(_bearer)],
) -> None:
    """Validate Bearer token against CB_API_KEY env var.

    Auth is skipped when CB_API_KEY is not set (local dev / single-user mode).
    """
    required = os.environ.get("CB_API_KEY", "")
    if not required:
        return  # open access — local dev mode
    if not creds or creds.credentials != required:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )


AuthDep = Annotated[None, Depends(_check_auth)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg() -> dict:
    return load_config(None)


def _open_store(config: dict, project: str, must_exist: bool = True) -> GraphStore:
    db_path = get_db_path(config, project)
    if must_exist and not db_path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
    return GraphStore(db_path)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ExtractRequest(BaseModel):
    text: str
    source_name: str = "api"
    verify: bool = False


class QueryRequest(BaseModel):
    task: str
    hops: int = 3
    top_k: int = 25


class QueryResponse(BaseModel):
    markdown: str
    nodes_returned: int
    total_nodes: int
    tokens_estimated: int


class ExtractResponse(BaseModel):
    project: str
    nodes_extracted: int
    edges_extracted: int
    nodes_per_1k_chars: float
    avg_tags_per_node: float


class StatsResponse(BaseModel):
    project: str
    node_count: int
    edge_count: int
    type_counts: dict[str, int]


class ProjectInfo(BaseModel):
    name: str
    node_count: int
    edge_count: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/v1/health")
def health() -> dict:
    return {"status": "ok", "version": "0.1.0"}


@app.get("/v1/projects")
def list_projects(_: AuthDep) -> list[ProjectInfo]:
    config = _cfg()
    projects_dir = Path(config.get("projects_dir", "~/.context-broker/projects")).expanduser()
    if not projects_dir.exists():
        return []
    result = []
    for d in sorted(projects_dir.iterdir()):
        if d.is_dir() and (d / "context.db").exists():
            store = GraphStore(d / "context.db")
            stats = store.get_stats()
            store.close()
            result.append(ProjectInfo(
                name=d.name,
                node_count=stats["node_count"],
                edge_count=stats["edge_count"],
            ))
    return result


@app.post("/v1/projects/{project}", status_code=status.HTTP_201_CREATED)
def init_project(project: str, _: AuthDep) -> dict:
    """Create a new project (idempotent)."""
    config = _cfg()
    db_path = get_db_path(config, project)
    if db_path.exists():
        return {"project": project, "created": False}
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = GraphStore(db_path)
    store.close()
    return {"project": project, "created": True}


@app.get("/v1/projects/{project}/stats")
def get_stats(project: str, _: AuthDep) -> StatsResponse:
    config = _cfg()
    store = _open_store(config, project)
    stats = store.get_stats()
    store.close()
    return StatsResponse(
        project=project,
        node_count=stats["node_count"],
        edge_count=stats["edge_count"],
        type_counts=stats.get("type_counts", {}),
    )


@app.post("/v1/projects/{project}/query")
def query_project(project: str, req: QueryRequest, _: AuthDep) -> QueryResponse:
    config = _cfg()
    store = _open_store(config, project)
    stats = store.get_stats()
    if stats["node_count"] == 0:
        store.close()
        return QueryResponse(markdown="", nodes_returned=0, total_nodes=0, tokens_estimated=0)

    defaults = config.get("defaults", {})
    strategies = dict(config.get("strategies", {}))
    result = retrieve_with_stats(
        store, req.task,
        hops=req.hops,
        top_k=req.top_k or defaults.get("top_k", 25),
        strategies=strategies,
    )
    store.close()
    return QueryResponse(
        markdown=result.markdown,
        nodes_returned=result.nodes_after_strategies,
        total_nodes=stats["node_count"],
        tokens_estimated=result.tokens_estimated,
    )


@app.post("/v1/projects/{project}/extract")
async def extract_project(project: str, req: ExtractRequest, _: AuthDep) -> ExtractResponse:
    if len(req.text) > 200_000:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Text exceeds 200,000 character limit. Split into smaller chunks.",
        )

    config = _cfg()
    db_path = get_db_path(config, project)
    if not db_path.parent.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Project '{project}' not found. Create it first: POST /v1/projects/{project}",
        )

    result = await _extract(req.text, config)
    nodes = result["nodes"]
    edges = result["edges"]

    if req.verify:
        try:
            v = await verify_extraction(req.text, nodes, config)
            nodes = nodes + v["nodes"]
            edges = edges + v["edges"]
        except Exception:
            pass  # verification failure is non-fatal

    now = datetime.now(timezone.utc).isoformat()
    for node in nodes:
        node["source_transcript"] = req.source_name
        node.setdefault("created_at", now)

    store = _open_store(config, project, must_exist=False)
    store.merge_extraction(nodes, edges)
    store.close()

    quality = score_extraction_quality(nodes, edges, req.text)
    return ExtractResponse(
        project=project,
        nodes_extracted=len(nodes),
        edges_extracted=len(edges),
        nodes_per_1k_chars=quality["nodes_per_1k_chars"],
        avg_tags_per_node=quality["avg_tags_per_node"],
    )


@app.get("/v1/projects/{project}/export")
def export_project(project: str, _: AuthDep) -> dict:
    """Export the full project graph as markdown."""
    config = _cfg()
    store = _open_store(config, project)
    all_nodes = store.get_all_nodes()
    store.close()

    if not all_nodes:
        return {"markdown": "", "node_count": 0}

    by_type: dict[str, list] = {}
    for n in all_nodes:
        by_type.setdefault(n["type"], []).append(n)

    lines = [f"# Context Broker Export — {project}\n"]
    for type_name, nodes in sorted(by_type.items()):
        lines.append(f"## {type_name.replace('_', ' ').title()}\n")
        for n in sorted(nodes, key=lambda x: x.get("confidence", 0), reverse=True):
            lines.append(f"- {n['fact']} *(confidence: {n.get('confidence', 0):.1f})*")
        lines.append("")

    return {"markdown": "\n".join(lines), "node_count": len(all_nodes)}
