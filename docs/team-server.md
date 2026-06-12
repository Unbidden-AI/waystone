# Waystone Team Server (self-hosted)

Run one shared knowledge graph for your whole team. Each member's Claude Code
session injects the team's context and writes new decisions back to the same
graph — on your own infrastructure, no data leaves your network.

The server is the FastAPI app backed by a multi-writer **PostgreSQL + pgvector**
graph (`store_backend: postgres`). Clients talk to it over HTTP in
`backend: remote` mode.

## 1. Start the server

```bash
cp .env.example .env
# edit .env: set WAYSTONE_API_KEY (shared team key) and LLM_API_KEY (extraction LLM)
docker compose up -d
curl localhost:8000/v1/health      # {"status":"ok", ...}
```

`docker compose up` brings up two services:

| service | image | role |
|---------|-------|------|
| `db`    | `pgvector/pgvector:pg16` | the shared graph (data in the `waystone-pgdata` volume) |
| `server`| built from `./Dockerfile` | the API; reads `WAYSTONE_STORE_BACKEND=postgres` + `DATABASE_URL` |

The Postgres schema is created automatically on the first request — no migration
step.

### Required `.env` values

- `WAYSTONE_API_KEY` — the shared bearer token teammates authenticate with.
  Generate one: `python -c "import secrets; print('waystone_'+secrets.token_urlsafe(32))"`
- `LLM_API_KEY` (+ optional `LLM_BASE_URL`, `LLM_MODEL`) — the OpenAI-compatible
  endpoint the **server** uses to extract facts. Defaults target Gemini 2.5 Flash.

> Auth here is a single shared key (`CB_USE_ADMIN_DB=0`). Per-seat keys, RBAC, and
> usage metering are the Enterprise tier — separate from this quickstart.

## 2. Point a client at it

In each teammate's `~/.waystone/config.yaml`:

```yaml
backend: remote
api_url: http://<server-host>:8000
api_key: <the WAYSTONE_API_KEY you set>
```

That's it. Now:

- The **UserPromptSubmit hook** injects shared context from the team graph on every
  prompt (fail-open, capped at ~8s so a slow server never blocks you).
- Extraction is routed to the server — the assistant's turns and your decisions land
  in the shared graph automatically.
- `waystone query <project> "<task>"`, `waystone extract`, `waystone show`,
  `waystone export`, and `waystone init` all operate against the Team Server.

Use `backend: local` to force a machine back to its private SQLite graph even if
`api_url` is set.

## Notes & ops

- **Data** lives in the `waystone-pgdata` Docker volume. Back it up with
  `docker compose exec db pg_dump -U waystone waystone > backup.sql`.
- **Upgrades**: `docker compose pull && docker compose up -d --build`. Schema
  migrations are additive (`ADD COLUMN IF NOT EXISTS`), so rolling forward is safe.
- **TLS / public exposure**: put the server behind a reverse proxy (Caddy/nginx) or
  a private network (Tailscale) — the bearer key is the only gate.
- **Tenancy**: each `project` is an isolated tenant inside the one Postgres graph.
