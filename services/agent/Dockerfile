# agent service image. Build from the repo root: docker build -f services/agent/Dockerfile .
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /bin/uv

# no dev group in the image; copy packages into the venv instead of linking to uv's cache
ENV UV_NO_DEV=1 UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
ARG EXTRAS="--extra openai-compat --extra mcp --extra http --extra api"

WORKDIR /app

# 1. dependencies only: this layer is rebuilt only when a pyproject or the lock changes
COPY pyproject.toml uv.lock ./
COPY packages/common/pyproject.toml packages/common/
COPY services/rag/pyproject.toml services/rag/
COPY services/agent/pyproject.toml services/agent/
COPY services/tickets/pyproject.toml services/tickets/
RUN uv sync --frozen --package siteops-agent $EXTRAS --no-install-workspace

# 2. code
COPY packages/common packages/common
COPY services/agent services/agent
RUN uv sync --frozen --package siteops-agent $EXTRAS

# not root: the process only reads the logs
RUN useradd --system --no-create-home agent
USER agent

# config.docker.yaml: the container's view of rag, tickets, the model and the mounted logs; the API keys come from compose
ENV PATH="/app/.venv/bin:$PATH" AGENT_CONFIG_FILE=/app/services/agent/config.docker.yaml
EXPOSE 8000
CMD ["python", "-m", "siteops_agent.api.main"]
