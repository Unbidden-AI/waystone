# Context Broker API Server
#
# Build:
#   docker build -t context-broker .
#
# Run (local, no auth):
#   docker run -p 8000:8000 -v /host/projects:/data/projects context-broker
#
# Run (production, with auth):
#   docker run -p 8000:8000 \
#     -v /host/projects:/data/projects \
#     -e CB_API_KEY=secret \
#     -e LLM_BASE_URL=https://api.openai.com/v1 \
#     -e LLM_MODEL=gpt-4o-mini \
#     -e LLM_API_KEY=sk-... \
#     context-broker

FROM python:3.12-slim

WORKDIR /app

# System deps for tiktoken / native tokeniser compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency manifest first for layer caching
COPY pyproject.toml ./
COPY context_broker/__init__.py ./context_broker/__init__.py

# Install package with API extras (fastapi + uvicorn)
RUN pip install --no-cache-dir -e ".[api]"

# Copy source after deps to keep the expensive install layer cached
COPY context_broker/ ./context_broker/

# Persistent project storage — mount a volume here
RUN mkdir -p /data/projects

# Entrypoint writes a minimal config.yaml from env vars so the server
# sees the right projects_dir and LLM settings without needing a mounted file.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Optional API key — unset = open access (local dev)
ENV CB_API_KEY=""
ENV CB_PROJECTS_DIR="/data/projects"
ENV LLM_BASE_URL="http://localhost:1234/v1"
ENV LLM_MODEL="gpt-4o-mini"
# Set LLM_API_KEY at runtime; leave blank for local endpoints

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["uvicorn", "context_broker.api_server:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1"]
