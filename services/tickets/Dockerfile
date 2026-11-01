# tickets service image. Build from the repo root: docker build -f services/tickets/Dockerfile .
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /bin/uv

# no dev group in the image; copy packages into the venv instead of linking to uv's cache
ENV UV_NO_DEV=1 UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1

WORKDIR /app

# 1. dependencies only: this layer is rebuilt only when a pyproject or the lock changes
COPY pyproject.toml uv.lock ./
COPY packages/common/pyproject.toml packages/common/
COPY services/rag/pyproject.toml services/rag/
COPY services/agent/pyproject.toml services/agent/
COPY services/tickets/pyproject.toml services/tickets/
RUN uv sync --frozen --package siteops-tickets --no-install-workspace

# 2. code
COPY packages/common packages/common
COPY services/tickets services/tickets
RUN uv sync --frozen --package siteops-tickets

# not root: the process only needs to write its database
RUN useradd --system --no-create-home tickets && mkdir /data && chown tickets /data
USER tickets

# 0.0.0.0: reachable from other containers; the API key is the access check (TICKETS_API_KEY, required)
ENV PATH="/app/.venv/bin:$PATH" TICKETS_HOST=0.0.0.0 TICKETS_PORT=8000 TICKETS_DB_PATH=/data/tickets.db
EXPOSE 8000
CMD ["python", "-m", "siteops_tickets.server"]
