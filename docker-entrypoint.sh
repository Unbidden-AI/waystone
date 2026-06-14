#!/usr/bin/env sh
# Render /app/config.yaml from environment variables so the server picks up
# projects_dir + LLM settings without a mounted config file.
#
# Idempotent: if a config.yaml already exists (e.g. mounted in), it is respected
# and left untouched. The Postgres backend is NOT written here — it is read from
# WAYSTONE_STORE_BACKEND / DATABASE_URL env directly by config.load_config.
set -e

# --- Auth preflight: never boot a wide-open server -------------------------
# A license implies per-seat (admin-DB) mode; an explicit CB_USE_ADMIN_DB wins.
# If neither per-seat mode nor a shared WAYSTONE_API_KEY is configured, the API
# would accept unauthenticated requests — refuse with an actionable message
# instead (mirrors waystone.api_server._use_admin_db).
_admin="$(printf '%s' "${CB_USE_ADMIN_DB:-}" | tr '[:upper:]' '[:lower:]')"
case "$_admin" in
  1|true|yes)  _perseat=1 ;;
  0|false|no)  _perseat=0 ;;
  *) if [ -n "${WAYSTONE_LICENSE:-}" ] || [ -n "${WAYSTONE_LICENSE_FILE:-}" ]; then
       _perseat=1
     else
       _perseat=0
     fi ;;
esac
if [ "$_perseat" = "0" ] && [ -z "${WAYSTONE_API_KEY:-}" ]; then
  echo "waystone: refusing to start — no auth configured." >&2
  echo "  Set ONE of these in your .env, then 'docker compose up' again:" >&2
  echo "    WAYSTONE_LICENSE=<your license token>   # per-seat (you bought a license)" >&2
  echo "    WAYSTONE_API_KEY=<a strong random string> # one shared key for the team" >&2
  exit 1
fi

CONFIG=/app/config.yaml

if [ ! -f "$CONFIG" ]; then
cat > "$CONFIG" <<EOF
projects_dir: "${CB_PROJECTS_DIR:-/data/projects}"

llm:
  base_url: "${LLM_BASE_URL:-http://localhost:1234/v1}"
  model: "${LLM_MODEL:-gpt-4o-mini}"
  temperature: 0.1
  max_tokens: 4096
  timeout: 120.0
$([ -n "$LLM_API_KEY" ] && echo "  api_key: \"$LLM_API_KEY\"" || true)

defaults:
  hops: 3
  top_k: 25

strategies:
  superseded_pruning: true
  relevance_scoring: true
EOF
fi

exec "$@"
