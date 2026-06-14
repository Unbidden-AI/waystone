# Waystone Team Server (self-hosted)

Run one shared knowledge graph for your whole team. Each member's Claude Code
session injects the team's context and writes new decisions back to the same
graph — on your own infrastructure, no data leaves your network.

The server is the FastAPI app backed by a multi-writer **PostgreSQL + pgvector**
graph (`store_backend: postgres`). Clients talk to it over HTTP in
`backend: remote` mode.

## 1. Start the server

You need [Docker](https://docs.docker.com/get-docker/) and **one** of:

- a **license token** (from your purchase email) → per-seat mode, each teammate
  gets their own key, or
- any **shared key** you make up → everyone uses the one key.

### Fastest: no clone, no build (pull the published image)

```bash
curl -O https://unbidden.ai/team-server/docker-compose.yml
curl -o .env https://unbidden.ai/team-server/env.example
# edit .env — paste WAYSTONE_LICENSE (or set WAYSTONE_API_KEY) + LLM_API_KEY
docker compose up -d
curl localhost:8000/v1/health      # {"status":"ok", ...}
```

### Or build from source (clone the repo)

```bash
git clone https://github.com/Unbidden-AI/waystone && cd waystone
cp .env.example .env               # then edit it the same way
docker compose up -d
```

Either way `docker compose up` brings up two services:

| service | image | role |
|---------|-------|------|
| `db`    | `pgvector/pgvector:pg16` | the shared graph (data in the `waystone-pgdata` volume) |
| `server`| `ghcr.io/unbidden-ai/waystone-server` (or built from `./Dockerfile`) | the API; reads `WAYSTONE_STORE_BACKEND=postgres` + `DATABASE_URL` |

The Postgres schema is created automatically on the first request — no migration
step.

### What to put in `.env`

Set **one** auth value:

- **Bought a license?** Paste it as `WAYSTONE_LICENSE=<token>`. That alone switches
  the server into per-seat mode — issue each teammate a key (see §3). No other flag.
- **Just want one shared key?** Set `WAYSTONE_API_KEY` instead (leave the license
  blank). Generate one:
  `python -c "import secrets; print('waystone_'+secrets.token_urlsafe(32))"`

Plus the extraction LLM:

- `LLM_API_KEY` (+ optional `LLM_BASE_URL`, `LLM_MODEL`) — the OpenAI-compatible
  endpoint the **server** uses to extract facts. Defaults target Gemini 2.5 Flash.

> The server **refuses to start** if you set neither a license nor a shared key —
> it won't silently come up unauthenticated.

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

## 3. Per-seat licensing

If you set `WAYSTONE_LICENSE` in step 1, the server is already in per-seat mode —
just hand out keys:

```bash
docker compose exec server waystone team issue alice@acme.com   # prints her key
docker compose exec server waystone team members      # who has a seat
docker compose exec server waystone team license      # seats used / total
docker compose exec server waystone team revoke bob@acme.com    # free a seat
```

Each member sets their own issued key as `api_key`. Seats are enforced offline
against the signed license — **no phone-home**. Without a license you get
`TRIAL_SEATS` (3) to evaluate; your license raises the cap (issuing the seat past
the limit is refused with a clear message). Licenses are Ed25519-signed tokens
verified locally against a public key bundled in the server; the admin DB (member
keys) persists in the `waystone-serverdata` volume.

> Evaluating before you buy? Skip the license and run with the 3 trial seats, or
> use a single shared `WAYSTONE_API_KEY`. Add the license later with no data loss —
> it's just an env change + `docker compose up -d`.

## Notes & ops

- **Data** lives in the `waystone-pgdata` Docker volume. Back it up with
  `docker compose exec db pg_dump -U waystone waystone > backup.sql`.
- **Upgrades**: image deploy → `docker compose pull && docker compose up -d`;
  source build → `git pull && docker compose up -d --build`. Schema migrations are
  additive (`ADD COLUMN IF NOT EXISTS`), so rolling forward is safe.
- **TLS / public exposure**: put the server behind a reverse proxy (Caddy/nginx) or
  a private network (Tailscale) — the bearer key is the only gate.
- **Tenancy**: each `project` is an isolated tenant inside the one Postgres graph.
