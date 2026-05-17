FROM python:3.13-slim

WORKDIR /app

# Install build tools for any native extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml ./
COPY waystone/ ./waystone/

# Install with api + monitoring extras (no dev or semantic extras)
RUN pip install --no-cache-dir -e ".[api,monitoring]"

# Runtime env defaults (all overridable at deploy time)
ENV PROJECTS_DIR=/data/projects \
    CB_USE_ADMIN_DB=1 \
    PORT=8000

EXPOSE 8000

# Entrypoint: start uvicorn using PORT env var so Fly.io/Railway can override
CMD ["sh", "-c", "uvicorn waystone.api_server:app --host 0.0.0.0 --port ${PORT}"]
