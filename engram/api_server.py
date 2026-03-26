"""Engram REST API server.

Exposes extraction and retrieval as HTTP endpoints so remote clients
(CLI, MCP server, SDKs) can share a single graph store across machines.

Usage:
    engram serve                           # default port 8000
    engram serve --port 9000
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

from fastapi import Depends, FastAPI, HTTPException, Request, Response, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from .billing import (
    check_node_limit,
    check_project_limit,
    create_key,
    open_admin_db,
    revoke_key_by_email,
    send_key_email,
    tier_from_variant,
    validate_key,
    verify_ls_signature,
    RateLimiter,
)
from .config import get_db_path, load_config
from .extractor import extract as _extract, extract_chunked as _extract_chunked, score_extraction_quality, verify_extraction
from .retriever import retrieve_with_stats
from .store import GraphStore

app = FastAPI(
    title="Engram API",
    version="0.1.0",
    description="DAG-based context intelligence layer for LLM workflows",
)

_bearer = HTTPBearer(auto_error=False)
_rate_limiter = RateLimiter()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

_USE_ADMIN_DB = os.environ.get("CB_USE_ADMIN_DB", "").lower() in ("1", "true", "yes")


def _check_auth(
    creds: Annotated[HTTPAuthorizationCredentials | None, Security(_bearer)],
) -> dict:
    """Validate Bearer token and check rate limit.

    Priority:
      1. If CB_USE_ADMIN_DB=1 — look up key in admin.db, return key row dict.
      2. If CB_API_KEY is set — simple env-var comparison (self-hosted mode).
         Returns a synthetic dict with tier="local".
      3. Otherwise — open access (local dev). Returns {"tier": "local"}.

    Rate limiting is enforced only when CB_USE_ADMIN_DB=1 and LS_WEBHOOK_SECRET is set.
    """
    if _USE_ADMIN_DB:
        if not creds:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing API key")
        conn = open_admin_db()
        key_info = validate_key(conn, creds.credentials)
        conn.close()
        if key_info is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or revoked API key")

        # Check rate limit only if we have a webhook secret (production mode)
        if os.environ.get("LS_WEBHOOK_SECRET", ""):
            tier = key_info.get("tier", "free")
            allowed, reason, remaining_minute, remaining_day = _rate_limiter.check(
                creds.credentials, tier
            )
            if not allowed:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=reason,
                    headers={"Retry-After": "60"},
                )
            # Store rate limit info in key_info for use in endpoints
            key_info["_rate_limit_remaining_minute"] = remaining_minute
            key_info["_rate_limit_remaining_day"] = remaining_day

        return key_info

    required = os.environ.get("CB_API_KEY", "")
    if not required:
        return {"tier": "local"}  # open access — local dev mode
    if not creds or creds.credentials != required:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
    return {"tier": "local"}


AuthDep = Annotated[dict, Depends(_check_auth)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg() -> dict:
    return load_config(None)


def _add_rate_limit_headers(key_info: dict, response: Response) -> None:
    """Add rate limit headers to response if key_info contains rate limit info."""
    from engram.billing import RATE_LIMITS

    tier = key_info.get("tier", "free")
    limits = RATE_LIMITS.get(tier, RATE_LIMITS["free"])

    if "_rate_limit_remaining_minute" in key_info:
        response.headers["X-RateLimit-Limit-Minute"] = str(limits["requests_per_minute"])
        response.headers["X-RateLimit-Remaining-Minute"] = str(
            key_info["_rate_limit_remaining_minute"]
        )
    if "_rate_limit_remaining_day" in key_info:
        response.headers["X-RateLimit-Limit-Day"] = str(limits["requests_per_day"])
        response.headers["X-RateLimit-Remaining-Day"] = str(
            key_info["_rate_limit_remaining_day"]
        )


def _open_store(config: dict, project: str, must_exist: bool = True) -> GraphStore:
    db_path = get_db_path(config, project)
    if must_exist and not db_path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
    return GraphStore(db_path)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

_AUTO_CHUNK_THRESHOLD = 20_000   # chars; texts above this are auto-chunked
_AUTO_CHUNK_SIZE = 20_000        # target chunk size when auto-chunking


class ExtractRequest(BaseModel):
    text: str
    source_name: str = "api"
    verify: bool = False
    chunk_size: int | None = None  # set to split large texts; None = auto


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
def list_projects(key_info: AuthDep, response: Response) -> list[ProjectInfo]:
    _add_rate_limit_headers(key_info, response)

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
def init_project(project: str, key_info: AuthDep, response: Response) -> dict:
    """Create a new project (idempotent)."""
    _add_rate_limit_headers(key_info, response)

    config = _cfg()
    db_path = get_db_path(config, project)
    if db_path.exists():
        return {"project": project, "created": False}

    # Enforce project limit for admin-DB-managed keys
    if _USE_ADMIN_DB and key_info.get("key_hash"):
        projects_dir = Path(config.get("projects_dir", "~/.context-broker/projects")).expanduser()
        current_count = sum(
            1 for d in projects_dir.iterdir()
            if d.is_dir() and (d / "context.db").exists()
        ) if projects_dir.exists() else 0
        try:
            check_project_limit(None, key_info["key_hash"], key_info.get("tier", "free"), current_count)  # type: ignore[arg-type]
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(exc))

    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = GraphStore(db_path)
    store.close()
    return {"project": project, "created": True}


@app.get("/v1/projects/{project}/stats")
def get_stats(project: str, key_info: AuthDep, response: Response) -> StatsResponse:
    _add_rate_limit_headers(key_info, response)

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
def query_project(project: str, req: QueryRequest, key_info: AuthDep, response: Response) -> QueryResponse:
    _add_rate_limit_headers(key_info, response)

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
async def extract_project(project: str, req: ExtractRequest, key_info: AuthDep, response: Response) -> ExtractResponse:
    _add_rate_limit_headers(key_info, response)

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

    # Determine effective chunk size:
    #   - explicit chunk_size param overrides everything
    #   - auto-chunk texts over threshold at the default chunk size
    #   - otherwise single-pass
    effective_chunk = req.chunk_size
    if effective_chunk is None and len(req.text) > _AUTO_CHUNK_THRESHOLD:
        effective_chunk = _AUTO_CHUNK_SIZE

    if effective_chunk:
        result = await _extract_chunked(req.text, config, effective_chunk, verify=req.verify)
        nodes = result["nodes"]
        edges = result["edges"]
    else:
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

    # Enforce node limit before writing
    if _USE_ADMIN_DB and key_info.get("tier") not in (None, "local"):
        current_stats = store.get_stats()
        try:
            check_node_limit(key_info.get("tier", "free"), current_stats["node_count"])
        except ValueError as exc:
            store.close()
            raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(exc))

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
def export_project(project: str, key_info: AuthDep, response: Response) -> dict:
    """Export the full project graph as markdown."""
    _add_rate_limit_headers(key_info, response)

    config = _cfg()
    store = _open_store(config, project)
    all_nodes = store.get_all_nodes()
    store.close()

    if not all_nodes:
        return {"markdown": "", "node_count": 0}

    by_type: dict[str, list] = {}
    for n in all_nodes:
        by_type.setdefault(n["type"], []).append(n)

    lines = [f"# Engram Export — {project}\n"]
    for type_name, nodes in sorted(by_type.items()):
        lines.append(f"## {type_name.replace('_', ' ').title()}\n")
        for n in sorted(nodes, key=lambda x: x.get("confidence", 0), reverse=True):
            lines.append(f"- {n['fact']} *(confidence: {n.get('confidence', 0):.1f})*")
        lines.append("")

    return {"markdown": "\n".join(lines), "node_count": len(all_nodes)}


# ---------------------------------------------------------------------------
# LemonSqueezy webhook
# ---------------------------------------------------------------------------

@app.post("/webhooks/lemonsqueezy", status_code=status.HTTP_200_OK)
async def lemonsqueezy_webhook(request: Request) -> dict:
    """Handle LemonSqueezy subscription lifecycle events.

    Events handled:
      subscription_created  → provision API key + email customer
      subscription_cancelled → revoke all keys for email
      order_created         → provision one-time purchase key (future)

    Signature verification requires LS_WEBHOOK_SECRET env var.
    If unset, the endpoint rejects all requests (fail-safe).
    """
    body = await request.body()
    signature = request.headers.get("X-Signature", "")

    if not verify_ls_signature(body, signature):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook signature")

    try:
        import json
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON payload")

    event = payload.get("meta", {}).get("event_name", "")
    attrs = payload.get("data", {}).get("attributes", {})

    if event == "subscription_created":
        email = attrs.get("user_email", "")
        variant_id = str(attrs.get("variant_id", ""))
        tier = tier_from_variant(variant_id)

        if not email:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Missing user_email")

        conn = open_admin_db()
        raw_key = create_key(conn, email=email, tier=tier)
        conn.close()

        send_key_email(email, raw_key, tier)
        return {"ok": True, "event": event, "tier": tier}

    elif event == "subscription_cancelled":
        email = attrs.get("user_email", "")
        if email:
            conn = open_admin_db()
            count = revoke_key_by_email(conn, email)
            conn.close()
            return {"ok": True, "event": event, "revoked": count}
        return {"ok": True, "event": event, "revoked": 0}

    # Unknown events are acknowledged but ignored
    return {"ok": True, "event": event, "ignored": True}
