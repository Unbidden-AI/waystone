#!/usr/bin/env sh
# Render /app/config.yaml from environment variables so the server picks up
# projects_dir + LLM settings without a mounted config file.
#
# Idempotent: if a config.yaml already exists (e.g. mounted in), it is respected
# and left untouched. The Postgres backend is NOT written here — it is read from
# WAYSTONE_STORE_BACKEND / DATABASE_URL env directly by config.load_config.
set -e

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
