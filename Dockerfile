FROM python:3.13-slim

WORKDIR /app

# Build tools for any native extensions (psycopg/pgvector ship wheels, but keep
# build-essential for uncommon platforms).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml ./
COPY waystone/ ./waystone/

# api = FastAPI/uvicorn, team = psycopg + pgvector (the Postgres Team backend),
# monitoring = Sentry. No dev/semantic extras (the server doesn't extract locally).
RUN pip install --no-cache-dir -e ".[api,team,monitoring]"

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Runtime env defaults (all overridable at deploy time)
ENV PROJECTS_DIR=/data/projects \
    CB_USE_ADMIN_DB=1 \
    PORT=8000

EXPOSE 8000

# Entrypoint renders /app/config.yaml from env (only if absent — a mounted config
# wins), then execs the CMD. Postgres backend comes from WAYSTONE_STORE_BACKEND /
# DATABASE_URL env (read directly by config.load_config).
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["sh", "-c", "uvicorn waystone.api_server:app --host 0.0.0.0 --port ${PORT}"]
