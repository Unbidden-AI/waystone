#!/usr/bin/env sh
# Write a config.yaml from environment variables so the server picks up
# the correct projects_dir and LLM settings without a mounted config file.
set -e

CONFIG=/app/config.yaml

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

exec "$@"
