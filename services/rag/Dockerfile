# rag service image. Build from the repo root: docker build -f services/rag/Dockerfile .
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /bin/uv

# fonts for docling's PDF conversion: the runbooks' PDFs don't embed theirs (Helvetica, Courier...), and docling finds
# headings on an image of each page. With no font to draw them, headings merge into the text and sections are lost.
# Liberation has the same metrics as Helvetica, Times and Courier.
RUN apt-get update && apt-get install -y --no-install-recommends fontconfig fonts-liberation && rm -rf /var/lib/apt/lists/*

# no dev group in the image; copy packages into the venv instead of linking to uv's cache
ENV UV_NO_DEV=1 UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
ARG EXTRAS="--extra openai-compat --extra qdrant"

WORKDIR /app

# 1. dependencies only: this layer is rebuilt only when a pyproject or the lock changes
COPY pyproject.toml uv.lock ./
COPY packages/common/pyproject.toml packages/common/
COPY services/rag/pyproject.toml services/rag/
COPY services/agent/pyproject.toml services/agent/
COPY services/tickets/pyproject.toml services/tickets/
RUN uv sync --frozen --package siteops-rag $EXTRAS --no-install-workspace

# 2. code
COPY packages/common packages/common
COPY services/rag services/rag
RUN uv sync --frozen --package siteops-rag $EXTRAS

# config.docker.yaml: Ollama on the host, Qdrant on the compose network; API keys, if any, come from compose
ENV PATH="/app/.venv/bin:$PATH" RAG_CONFIG_FILE=/app/services/rag/config.docker.yaml
EXPOSE 8000
CMD ["uvicorn", "siteops_rag.api:app", "--host", "0.0.0.0", "--port", "8000"]
