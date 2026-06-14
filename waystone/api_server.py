"""Waystone REST API server.

Exposes extraction and retrieval as HTTP endpoints so remote clients
(CLI, MCP server, SDKs) can share a single graph store across machines.

Usage:
    waystone serve                           # default port 8000
    waystone serve --port 9000
    uvicorn waystone.api_server:app --reload   # dev

Auth:
    Set WAYSTONE_API_KEY env var on the server.  Clients pass it as:
        Authorization: Bearer <key>
    When WAYSTONE_API_KEY is not set, auth is skipped (local dev mode).
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, Security, status
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

log = logging.getLogger(__name__)

from contextlib import asynccontextmanager

from . import __version__ as _WAYSTONE_VERSION
from .billing import (
    RateLimiter,
    check_node_limit,
    check_project_limit,
    create_key,
    get_or_create_key_by_email,
    is_team_license_price,
    open_admin_db,
    revoke_key_by_stripe_customer,
    send_key_email,
    send_license_email,
    tier_from_price,
    validate_key,
    verify_stripe_signature,
)
from .config import load_config
from .extractor import extract as _extract
from .extractor import extract_chunked as _extract_chunked
from .extractor import score_extraction_quality, verify_extraction
from .monitoring import init_sentry
from .retriever import retrieve_with_stats
from .store import GraphStore

_SERVER_START_TIME = time.time()
_ADMIN_EMAILS: frozenset[str] = frozenset(
    e.strip()
    for e in os.environ.get(
        "WAYSTONE_ADMIN_EMAILS",
        "justin.walton@gmail.com,justin@unbidden.ai",
    ).split(",")
    if e.strip()
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan handler for startup checks."""
    # Startup: initialize Sentry
    init_sentry()

    # Startup: check for missing STRIPE_WEBHOOK_SECRET in production mode
    if _use_admin_db() and not os.environ.get("STRIPE_WEBHOOK_SECRET", ""):
        log.warning(
            "WARNING: STRIPE_WEBHOOK_SECRET is not set. Stripe webhooks will be rejected — "
            "customers who pay will not receive API keys."
        )
    yield
    # Shutdown: cleanup if needed


app = FastAPI(
    title="Waystone API",
    version=_WAYSTONE_VERSION,
    description="DAG-based context intelligence layer for LLM workflows",
    lifespan=lifespan,
)

_bearer = HTTPBearer(auto_error=False)
_rate_limiter = RateLimiter()

# Seats granted for a Team-license purchase when the Stripe session carries no
# explicit `seats` metadata (use distinct price IDs or set metadata.seats per tier).
_DEFAULT_LICENSE_SEATS = 5


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _use_admin_db() -> bool:
    """Whether the server runs in multi-tenant admin-DB mode (per-key auth, rate
    limits, billing).

    Read at call time — NOT frozen at import. Freezing it meant the mode depended
    on whether some other module imported api_server first: in the test suite a
    non-admin test importing the module first silently disabled auth for every
    admin test that ran later, and in an embedding host the same race could leave
    auth off. A per-call env read costs nothing and removes the ordering hazard.

    A self-hosted Team **license** implies per-seat mode: a buyer who pastes their
    WAYSTONE_LICENSE gets admin-DB auth automatically, without also having to flip
    CB_USE_ADMIN_DB. An explicit CB_USE_ADMIN_DB (0/1) always wins, so the hosted
    billing server (which never sets a license token) is unaffected.
    """
    explicit = os.environ.get("CB_USE_ADMIN_DB", "").strip().lower()
    if explicit in ("1", "true", "yes"):
        return True
    if explicit in ("0", "false", "no"):
        return False
    return bool(
        os.environ.get("WAYSTONE_LICENSE", "").strip()
        or os.environ.get("WAYSTONE_LICENSE_FILE", "").strip()
    )


def _hosted_saas() -> bool:
    """Whether this is the multi-tenant HOSTED service (many separate customer orgs
    behind one deployment), signalled by a configured Stripe webhook secret — the
    same signal the rate limiter uses for "production mode". A self-hosted Team
    Server does NOT set it.
    """
    return bool(os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip())


def _isolate_by_key(key_info: dict) -> bool:
    """Whether projects should be scoped per API key.

    TRUE only on the hosted SaaS, where each key belongs to a different customer and
    must never see another's data. On a SELF-HOSTED Team Server every issued key
    belongs to the ONE org that runs the server, so members SHARE the project graph
    — that shared "team brain" is the whole product. Scoping self-hosted members
    apart by key silently broke that (each member saw only their own writes).
    """
    return _hosted_saas() and _use_admin_db() and bool(key_info.get("key_hash"))


# ---------------------------------------------------------------------------
# Clerk JWT validation (for /account/key)
# ---------------------------------------------------------------------------

_CLERK_JWKS_URL = "https://clerk.unbidden.ai/.well-known/jwks.json"
_CLERK_SECRET_KEY = os.environ.get("CLERK_SECRET_KEY", "")

_jwks_cache: dict | None = None
_jwks_fetched_at: float = 0.0
_JWKS_TTL = 3600.0  # refresh JWKS hourly


def _get_clerk_jwks() -> dict:
    global _jwks_cache, _jwks_fetched_at
    if _jwks_cache and (time.time() - _jwks_fetched_at) < _JWKS_TTL:
        return _jwks_cache
    import httpx
    r = httpx.get(_CLERK_JWKS_URL, timeout=5.0)
    r.raise_for_status()
    _jwks_cache = r.json()
    _jwks_fetched_at = time.time()
    return _jwks_cache


def _validate_clerk_jwt(token: str) -> dict:
    """Validate a Clerk session JWT and return its decoded claims.

    Requires PyJWT[crypto] (pyjwt + cryptography) to be installed.
    Raises HTTPException 401 on any validation failure.
    """
    try:
        import jwt
        from jwt import PyJWKClient
    except ImportError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="JWT validation library not installed (pip install 'pyjwt[crypto]')",
        )

    try:
        jwks_client = PyJWKClient(_CLERK_JWKS_URL, cache_jwk_set=True, lifespan=3600)
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
        return claims
    except Exception as exc:
        log.warning("Clerk JWT validation failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session token")


def _get_email_from_clerk_sub(sub: str) -> str | None:
    """Look up a user's primary email via Clerk Backend API using the user's sub (ID).

    Requires CLERK_SECRET_KEY env var. Returns None if unavailable.
    """
    if not _CLERK_SECRET_KEY:
        return None
    try:
        import httpx
        r = httpx.get(
            f"https://api.clerk.com/v1/users/{sub}",
            headers={"Authorization": f"Bearer {_CLERK_SECRET_KEY}"},
            timeout=5.0,
        )
        if r.status_code != 200:
            log.warning("Clerk user lookup failed: %s %s", r.status_code, r.text[:200])
            return None
        data = r.json()
        addresses = data.get("email_addresses", [])
        primary_id = data.get("primary_email_address_id")
        for addr in addresses:
            if addr.get("id") == primary_id:
                return addr.get("email_address")
        if addresses:
            return addresses[0].get("email_address")
    except Exception as exc:
        log.warning("Clerk user lookup error: %s", exc)
    return None


def _check_auth(
    creds: Annotated[HTTPAuthorizationCredentials | None, Security(_bearer)],
) -> dict:
    """Validate Bearer token and check rate limit.

    Priority:
      1. If CB_USE_ADMIN_DB=1 — look up key in admin.db, return key row dict.
      2. If WAYSTONE_API_KEY is set — simple env-var comparison (self-hosted mode).
         Returns a synthetic dict with tier="local".
      3. Otherwise — open access (local dev). Returns {"tier": "local"}.

    Rate limiting is enforced only when CB_USE_ADMIN_DB=1 and STRIPE_WEBHOOK_SECRET is set.
    """
    if _use_admin_db():
        if not creds:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing API key")
        conn = open_admin_db()
        key_info = validate_key(conn, creds.credentials)
        conn.close()
        if key_info is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or revoked API key")

        # Check rate limit only if we have a webhook secret (production mode)
        if os.environ.get("STRIPE_WEBHOOK_SECRET", ""):
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

    required = os.environ.get("WAYSTONE_API_KEY", "")
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


def _get_project_dir(config: dict, key_info: dict, project: str) -> Path:
    """Return the project directory. Scoped per API key only on the hosted SaaS;
    self-hosted Team Server members share a project (see _isolate_by_key)."""
    base = Path(config.get("projects_dir", "~/.waystone/projects")).expanduser()
    if _isolate_by_key(key_info):
        key_prefix = key_info["key_hash"][:12]
        return base / key_prefix / project
    return base / project


def _get_db_path(config: dict, key_info: dict, project: str) -> Path:
    return _get_project_dir(config, key_info, project) / "context.db"


def _add_rate_limit_headers(key_info: dict, response: Response) -> None:
    """Add rate limit headers to response if key_info contains rate limit info."""
    from waystone.billing import RATE_LIMITS

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


def _open_store(config: dict, key_info: dict, project: str, must_exist: bool = True):
    """Open the graph store for (api-key, project). Postgres (shared, multi-writer)
    when store_backend=postgres, else a per-project SQLite file."""
    if config.get("store_backend") == "postgres":
        from .pg_store import PostgresGraphStore
        # tenant = <key-prefix>:<project> ONLY on the hosted SaaS (per-customer
        # isolation); on a self-hosted Team Server every member shares the project
        # graph, so tenant = the project name. The tenant's schema auto-creates, so
        # there's no "project not found" — a fresh tenant simply has zero nodes.
        prefix = key_info.get("key_hash", "")[:12] if _isolate_by_key(key_info) else ""
        tenant = f"{prefix}:{project}" if prefix else project
        return PostgresGraphStore(config["database_url"], tenant_id=tenant)
    db_path = _get_db_path(config, key_info, project)
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


class AccountResponse(BaseModel):
    tier: str
    email: str | None = None
    node_count: int
    project_count: int
    rate_limits: dict[str, int]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/v1/health")
def health() -> dict:
    return {"status": "ok", "version": _WAYSTONE_VERSION}


@app.get("/v1/account")
def get_account(key_info: AuthDep, response: Response) -> AccountResponse:
    """Get account information including tier, project count, and rate limits."""
    _add_rate_limit_headers(key_info, response)

    tier = key_info.get("tier", "local")
    email = key_info.get("email")

    # Count nodes and projects across user's scoped projects
    config = _cfg()
    projects_base = Path(config.get("projects_dir", "~/.waystone/projects")).expanduser()

    if _isolate_by_key(key_info):
        key_prefix = key_info["key_hash"][:12]
        projects_dir = projects_base / key_prefix
    else:
        projects_dir = projects_base

    total_nodes = 0
    project_count = 0

    if projects_dir.exists():
        for d in projects_dir.iterdir():
            if d.is_dir() and (d / "context.db").exists():
                project_count += 1
                try:
                    store = GraphStore(d / "context.db")
                    stats = store.get_stats()
                    total_nodes += stats.get("node_count", 0)
                    store.close()
                except Exception as _proj_exc:
                    log.warning("Could not open project stats for %s: %s", d.name, _proj_exc)

    from waystone.billing import RATE_LIMITS
    limits = RATE_LIMITS.get(tier, RATE_LIMITS.get("free", {}))

    return AccountResponse(
        tier=tier,
        email=email,
        node_count=total_nodes,
        project_count=project_count,
        rate_limits={
            "requests_per_minute": limits.get("requests_per_minute", 10),
            "requests_per_day": limits.get("requests_per_day", 100),
        },
    )


@app.get("/v1/projects")
def list_projects(key_info: AuthDep, response: Response) -> list[ProjectInfo]:
    _add_rate_limit_headers(key_info, response)

    config = _cfg()
    projects_base = Path(config.get("projects_dir", "~/.waystone/projects")).expanduser()

    if _isolate_by_key(key_info):
        key_prefix = key_info["key_hash"][:12]
        projects_dir = projects_base / key_prefix
    else:
        projects_dir = projects_base

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
    db_path = _get_db_path(config, key_info, project)
    if db_path.exists():
        return {"project": project, "created": False}

    # Enforce project limit for admin-DB-managed keys
    if _isolate_by_key(key_info):
        key_prefix = key_info["key_hash"][:12]
        projects_base = Path(config.get("projects_dir", "~/.waystone/projects")).expanduser()
        user_projects_dir = projects_base / key_prefix
        current_count = sum(
            1 for d in user_projects_dir.iterdir()
            if d.is_dir() and (d / "context.db").exists()
        ) if user_projects_dir.exists() else 0
        try:
            check_project_limit(None, key_info["key_hash"], key_info.get("tier", "free"), current_count)  # type: ignore[arg-type]
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={"message": str(exc), "upgrade_url": "https://unbidden.ai/pricing/"},
            )

    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = GraphStore(db_path)
    store.close()
    return {"project": project, "created": True}


@app.get("/v1/projects/{project}/stats")
def get_stats(project: str, key_info: AuthDep, response: Response) -> StatsResponse:
    _add_rate_limit_headers(key_info, response)

    config = _cfg()
    store = _open_store(config, key_info, project)
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
    store = _open_store(config, key_info, project)
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
    # SQLite scopes a project to an on-disk directory, so "not found" is a real
    # state. Postgres tenants auto-create their schema — a fresh tenant just has
    # zero nodes — so there's nothing to 404 on.
    if config.get("store_backend") != "postgres":
        db_path = _get_db_path(config, key_info, project)
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
            except Exception as _ve:
                log.warning("Verification pass failed (non-fatal): %s", _ve, exc_info=True)

    now = datetime.now(timezone.utc).isoformat()
    for node in nodes:
        node["source_transcript"] = req.source_name
        node.setdefault("created_at", now)

    store = _open_store(config, key_info, project, must_exist=False)

    # Wrap merge in a transaction to ensure atomicity
    # Node limit check and merge must happen together to prevent TOCTOU race
    try:
        with store.conn:  # SQLite context manager for transaction
            # Enforce node limit before writing
            if _use_admin_db() and key_info.get("tier") not in (None, "local"):
                current_stats = store.get_stats()
                try:
                    check_node_limit(key_info.get("tier", "free"), current_stats["node_count"])
                except ValueError as exc:
                    # Transaction will auto-rollback when exiting the context
                    raise HTTPException(
                        status_code=status.HTTP_402_PAYMENT_REQUIRED,
                        detail={"message": str(exc), "upgrade_url": "https://unbidden.ai/pricing/"},
                    )

            store.merge_extraction(nodes, edges)
    finally:
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
    store = _open_store(config, key_info, project)
    all_nodes = store.get_all_nodes()
    store.close()

    if not all_nodes:
        return {"markdown": "", "node_count": 0}

    by_type: dict[str, list] = {}
    for n in all_nodes:
        by_type.setdefault(n["type"], []).append(n)

    lines = [f"# Waystone Export — {project}\n"]
    for type_name, nodes in sorted(by_type.items()):
        lines.append(f"## {type_name.replace('_', ' ').title()}\n")
        for n in sorted(nodes, key=lambda x: x.get("confidence", 0), reverse=True):
            lines.append(f"- {n['fact']} *(confidence: {n.get('confidence', 0):.1f})*")
        lines.append("")

    return {"markdown": "\n".join(lines), "node_count": len(all_nodes)}


# ---------------------------------------------------------------------------
# OpenAI-compatible proxy endpoint
# ---------------------------------------------------------------------------
#
# Usage (drop-in replacement for any OpenAI SDK call):
#
#   client = openai.OpenAI(
#       base_url="http://localhost:8000/v1",
#       api_key="any",          # Waystone auth uses WAYSTONE_API_KEY header, not this
#   )
#   client.chat.completions.create(
#       model="gemini-2.5-flash",
#       messages=[{"role": "user", "content": "..."}],
#       extra_headers={"X-Waystone-Project": "MyProject"},
#   )
#
# Waystone injects retrieved graph context as a system message, then forwards
# the augmented request to the configured LLM endpoint (llm.base_url in
# config.yaml).  The response is passed through unchanged so it is fully
# compatible with any downstream OpenAI SDK parser.


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = ""
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None


@app.post("/v1/chat/completions")
async def chat_completions(
    req: ChatCompletionRequest,
    key_info: AuthDep,
    raw_response: Response,
    x_waystone_project: str | None = Header(default=None, alias="X-Waystone-Project"),
) -> Response:
    """OpenAI-compatible chat completions proxy with Waystone context injection.

    Pass ``X-Waystone-Project: <name>`` to inject graph context for that project.
    The last user message is used as the retrieval query.  Context is prepended
    as a system message (or merged into an existing system message).

    Supports both streaming (``stream: true``) and non-streaming responses.
    """
    _add_rate_limit_headers(key_info, raw_response)

    config = _cfg()
    llm_cfg = config.get("llm", {})

    # Build mutable message list
    messages: list[dict] = [{"role": m.role, "content": m.content} for m in req.messages]

    # --- context injection ---------------------------------------------------
    if x_waystone_project:
        db_path = _get_db_path(config, key_info, x_waystone_project)
        if db_path.exists():
            query = next(
                (m.content for m in reversed(req.messages) if m.role == "user"),
                None,
            )
            if query:
                store = GraphStore(db_path)
                try:
                    result = retrieve_with_stats(
                        store,
                        query,
                        hops=config.get("defaults", {}).get("hops", 3),
                        top_k=config.get("defaults", {}).get("top_k", 25),
                        strategies=dict(config.get("strategies", {})),
                    )
                    if result.markdown:
                        context_block = (
                            f"## Waystone Project Knowledge ({x_waystone_project})\n\n"
                            f"{result.markdown}"
                        )
                        if messages and messages[0]["role"] == "system":
                            messages[0]["content"] = (
                                context_block + "\n\n---\n\n" + messages[0]["content"]
                            )
                        else:
                            messages.insert(0, {"role": "system", "content": context_block})
                finally:
                    store.close()

    # --- build forwarded payload ---------------------------------------------
    base_url = llm_cfg.get("base_url", "http://localhost:1234/v1").rstrip("/")
    model = req.model or llm_cfg.get("model", "")

    payload: dict = {"model": model, "messages": messages, "stream": req.stream}
    if req.temperature is not None:
        payload["temperature"] = req.temperature
    if req.max_tokens is not None:
        payload["max_tokens"] = req.max_tokens

    forward_headers: dict[str, str] = {"Content-Type": "application/json"}
    api_key_env = llm_cfg.get("api_key_env")
    api_key: str | None = None
    if api_key_env:
        api_key = os.environ.get(api_key_env)
    elif llm_cfg.get("api_key"):
        api_key = llm_cfg["api_key"]
    if api_key:
        forward_headers["Authorization"] = f"Bearer {api_key}"

    import httpx

    upstream_url = f"{base_url}/chat/completions"
    timeout = llm_cfg.get("timeout", 300.0)

    # --- streaming path ------------------------------------------------------
    if req.stream:
        async def _stream() -> bytes:  # type: ignore[return]
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST", upstream_url, json=payload, headers=forward_headers
                ) as resp:
                    async for chunk in resp.aiter_bytes():
                        yield chunk

        return StreamingResponse(_stream(), media_type="text/event-stream")

    # --- non-streaming path --------------------------------------------------
    async with httpx.AsyncClient(timeout=timeout) as client:
        upstream = await client.post(upstream_url, json=payload, headers=forward_headers)

    return Response(
        content=upstream.content,
        media_type="application/json",
        status_code=upstream.status_code,
    )


# ---------------------------------------------------------------------------
# Stripe webhook
# ---------------------------------------------------------------------------

def _issue_and_email_license(email: str, seats: int, event_type: str) -> dict:
    """Mint a self-hosted Team license token for `email` (seats, default-clamped) and
    email it. Shared by the initial purchase + subscription renewals. Returns a webhook
    ack dict; never 500s on a missing signing key (the customer already paid)."""
    from .licensing import issue_license_from_env, verify_license
    if seats <= 0:
        log.warning("Team license for %s: no seats resolved — defaulting to %d",
                    email, _DEFAULT_LICENSE_SEATS)
        seats = _DEFAULT_LICENSE_SEATS
    token = issue_license_from_env(seats=seats, org=email)
    if not token:
        log.error("Team license for %s but WAYSTONE_LICENSE_PRIVKEY is not configured — "
                  "cannot mint (support must re-issue manually)", email)
        return {"ok": True, "event": event_type, "license": "deferred"}
    try:
        expires_at = verify_license(token).expires_at
    except Exception as exc:  # a just-minted token failing self-verify = a bug
        log.error("Minted Team license for %s failed self-verification: %s", email, exc)
        expires_at = None
    conn = open_admin_db()
    try:
        send_license_email(email, token, seats=seats, expires_at=expires_at, admin_conn=conn)
    finally:
        conn.close()
    return {"ok": True, "event": event_type, "license_seats": seats}


@app.post("/webhooks/stripe", status_code=status.HTTP_200_OK)
async def stripe_webhook(request: Request) -> dict:
    """Handle Stripe subscription lifecycle events.

    Events handled:
      checkout.session.completed     → provision API key + email customer
      customer.subscription.deleted  → revoke keys for Stripe customer

    Signature verification requires STRIPE_WEBHOOK_SECRET env var.
    If unset, the endpoint rejects all requests (fail-safe).

    Tier is determined from metadata.price_id if set on the checkout session.
    For Payment Links (which don't embed metadata), the handler fetches line
    items via the Stripe API using STRIPE_SECRET_KEY to read the price ID.
    Falls back to "pro" if tier cannot be determined (safe: all paid plans).
    """
    if not os.environ.get("STRIPE_WEBHOOK_SECRET", ""):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook secret not configured — contact support",
        )

    body = await request.body()
    sig_header = request.headers.get("Stripe-Signature", "")

    if not verify_stripe_signature(body, sig_header):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook signature")

    try:
        import json
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON payload")

    event_type = payload.get("type", "")
    obj = payload.get("data", {}).get("object", {})

    if event_type == "checkout.session.completed":
        email = obj.get("customer_email", "")
        customer_id = obj.get("customer", "")
        metadata = obj.get("metadata") or {}
        price_id = metadata.get("price_id", "")

        # Payment Links don't embed metadata.price_id — fall back to fetching
        # the first line item from the Stripe API to determine the price.
        line_qty = 0  # checkout line-item quantity (→ seats for adjustable-quantity links)
        if not price_id:
            secret_key = os.environ.get("STRIPE_SECRET_KEY", "")
            session_id = obj.get("id", "")
            if secret_key and session_id:
                try:
                    import stripe as _stripe
                    _stripe.api_key = secret_key
                    items = _stripe.checkout.Session.list_line_items(session_id, limit=1)
                    if items and items.data:
                        try:
                            line_qty = int(items.data[0].quantity or 0)
                        except (TypeError, ValueError):
                            line_qty = 0
                        price_obj = items.data[0].price
                        if price_obj:
                            price_id = price_obj.id
                except Exception:
                    pass  # degraded gracefully — tier defaults to "pro" below

        tier = tier_from_price(price_id) if price_id else metadata.get("tier", "pro")

        if not email:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Missing customer_email",
            )

        # Self-hosted Team Server license: mint a signed license token and email it
        # (no API key — the customer runs their own server). Distinct from the hosted
        # "team" API tier handled below.
        if is_team_license_price(price_id):
            # Seats: explicit `seats` metadata wins; else the purchased quantity
            # (adjustable-quantity links → seats = quantity); else the default.
            try:
                seats = int(str(metadata.get("seats", "")).strip() or 0)
            except ValueError:
                seats = 0
            if seats <= 0 and line_qty > 0:
                seats = line_qty
            # NOTE: like the API-key path below, this is not idempotent — a duplicate
            # Stripe delivery mints a second (equally valid) license. Acceptable for now
            # (seats are per-token, not cumulative); an issued_licenses dedup table keyed
            # on the checkout session id is the follow-up.
            return _issue_and_email_license(email, seats, event_type)

        conn = open_admin_db()
        raw_key = create_key(conn, email=email, tier=tier, stripe_customer_id=customer_id)
        send_key_email(email, raw_key, tier, admin_conn=conn)
        conn.close()

        return {"ok": True, "event": event_type, "tier": tier}

    elif event_type in ("invoice.paid", "invoice.payment_succeeded"):
        # Subscription RENEWAL → re-mint a fresh Team-license token (the initial
        # purchase comes through checkout.session.completed above). Only act on the
        # recurring cycle, not the first invoice (billing_reason=subscription_create),
        # to avoid double-issuing on signup.
        if obj.get("billing_reason") != "subscription_cycle":
            return {"ok": True, "event": event_type, "skipped": "not a renewal cycle"}
        lines = (obj.get("lines") or {}).get("data") or []
        line = lines[0] if lines else {}
        price_id = ((line.get("price") or {}).get("id")
                    or (line.get("plan") or {}).get("id") or "")
        if not is_team_license_price(price_id):
            return {"ok": True, "event": event_type, "skipped": "not a team license"}
        try:
            seats = int(line.get("quantity") or 0)
        except (TypeError, ValueError):
            seats = 0
        email = obj.get("customer_email", "")
        if not email:
            log.error("Team license renewal but invoice carries no customer_email "
                      "(customer=%s) — cannot email a token", obj.get("customer"))
            return {"ok": True, "event": event_type, "license": "deferred"}
        return _issue_and_email_license(email, seats, event_type)

    elif event_type == "customer.subscription.deleted":
        customer_id = obj.get("customer", "")
        if customer_id:
            conn = open_admin_db()
            count = revoke_key_by_stripe_customer(conn, customer_id)
            conn.close()
            return {"ok": True, "event": event_type, "revoked": count}
        return {"ok": True, "event": event_type, "revoked": 0}

    # Unknown events are acknowledged but ignored
    return {"ok": True, "event": event_type, "ignored": True}


# ---------------------------------------------------------------------------
# Admin metrics endpoint (Clerk-authenticated, admin email only)
# ---------------------------------------------------------------------------

@app.get("/v1/admin/metrics")
async def admin_metrics(request: Request) -> dict:
    """Return internal platform metrics. Requires Clerk JWT for the admin email.

    Protected by WAYSTONE_ADMIN_EMAIL (default: justin.walton@gmail.com).
    Returns key counts by tier, recent signups, usage activity, and server uptime.
    """
    if not _use_admin_db():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Admin metrics only available on hosted service")

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing session token")

    claims = _validate_clerk_jwt(auth_header[7:])

    email = claims.get("email") or claims.get("primary_email_address")
    if not email:
        sub = claims.get("sub", "")
        if sub:
            email = _get_email_from_clerk_sub(sub)

    if email not in _ADMIN_EMAILS:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    conn = open_admin_db()
    try:
        # Keys by tier (active only)
        tier_rows = conn.execute(
            "SELECT tier, COUNT(*) as cnt FROM api_keys WHERE is_revoked=0 GROUP BY tier"
        ).fetchall()
        keys_by_tier = {r["tier"]: r["cnt"] for r in tier_rows}
        total_keys = sum(keys_by_tier.values())

        # Recent signups
        signups_24h = conn.execute(
            "SELECT COUNT(*) FROM api_keys WHERE is_revoked=0 AND created_at > datetime('now', '-1 day')"
        ).fetchone()[0]
        signups_7d = conn.execute(
            "SELECT COUNT(*) FROM api_keys WHERE is_revoked=0 AND created_at > datetime('now', '-7 days')"
        ).fetchone()[0]

        # Usage in last 24h by action type
        usage_rows = conn.execute(
            "SELECT action, COUNT(*) as cnt FROM usage_log "
            "WHERE timestamp > datetime('now', '-1 day') GROUP BY action"
        ).fetchall()
        usage_24h = {r["action"]: r["cnt"] for r in usage_rows}
        total_requests_24h = sum(usage_24h.values())

        # Usage in last 7d (total count)
        total_requests_7d = conn.execute(
            "SELECT COUNT(*) FROM usage_log WHERE timestamp > datetime('now', '-7 days')"
        ).fetchone()[0]
    finally:
        conn.close()

    return {
        "server": {
            "version": _WAYSTONE_VERSION,
            "uptime_seconds": int(time.time() - _SERVER_START_TIME),
        },
        "keys": {
            "free": keys_by_tier.get("free", 0),
            "pro": keys_by_tier.get("pro", 0),
            "team": keys_by_tier.get("team", 0),
            "total": total_keys,
        },
        "signups": {
            "last_24h": signups_24h,
            "last_7d": signups_7d,
        },
        "usage": {
            "last_24h": usage_24h,
            "total_requests_24h": total_requests_24h,
            "total_requests_7d": total_requests_7d,
        },
    }


# ---------------------------------------------------------------------------
# Account key endpoint (Clerk-authenticated)
# ---------------------------------------------------------------------------

@app.get("/account/key")
async def get_account_key(request: Request):
    """Return the caller's Waystone API key, authenticated via Clerk session JWT.

    The browser calls this with: Authorization: Bearer <clerk-session-token>
    The endpoint validates the JWT against Clerk's JWKS, extracts the user's
    email via the Clerk Backend API, then looks up (or creates) their API key.

    Requires: CB_USE_ADMIN_DB=1, CLERK_SECRET_KEY env vars on the server.
    """
    if not _use_admin_db():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Account key endpoint is only available on the hosted service",
        )

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing session token")

    token = auth_header[7:]
    claims = _validate_clerk_jwt(token)

    # Try to get email from JWT claims first (works if Clerk JWT template includes email)
    email = claims.get("email") or claims.get("primary_email_address")

    # Fall back to Clerk Backend API using the sub (user ID)
    if not email:
        sub = claims.get("sub", "")
        if sub:
            email = _get_email_from_clerk_sub(sub)

    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not determine email from session. Ensure CLERK_SECRET_KEY is set.",
        )

    conn = open_admin_db()
    try:
        key = get_or_create_key_by_email(conn, email)
        # Fetch tier from the row we just found/created
        row = conn.execute(
            "SELECT tier FROM api_keys WHERE email = ? AND is_revoked = 0 ORDER BY created_at DESC LIMIT 1",
            (email,),
        ).fetchone()
        tier = row["tier"] if row else "free"
    finally:
        conn.close()

    return {"key": key, "tier": tier}
