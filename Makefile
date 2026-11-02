# Run everything from here. The stack is docker compose; secrets in .env (copy .env.example).
# make help lists the targets.

COMPOSE := docker compose
GPU := -f docker-compose.yml -f docker-compose.gpu.yml
MODELS := qwen3.5:27b bge-m3

.PHONY: help up up-gpu models down ps logs ingest generate-logs triage test test-integration lint typecheck

help:  ## this list
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/'

# --- the stack ---

up:  ## build and start the stack; the model server is the host's Ollama (or LLM_UPSTREAM)
	$(COMPOSE) up -d --build --wait

up-gpu:  ## the same, with Ollama itself in the stack on the GPU (then make models, once)
	$(COMPOSE) $(GPU) up -d --build --wait

models:  ## pull the models into the stack's Ollama (after make up-gpu)
	for model in $(MODELS); do $(COMPOSE) $(GPU) exec ollama ollama pull $$model; done

down:  ## stop the stack; the volumes (runbooks, tickets, models) are kept
	$(COMPOSE) $(GPU) down

ps:  ## what runs, and its health
	$(COMPOSE) ps

logs:  ## follow the services' logs
	$(COMPOSE) logs -f

# --- data and jobs ---

ingest:  ## load the runbooks of data/ into Qdrant (once, or when they change)
	$(COMPOSE) run --rm --build ingest

generate-logs:  ## write data/logs/site.log and ground_truth.json (the agent reads them)
	uv run python scripts/generate_logs.py

triage:  ## one triage run, e.g. make triage SINCE=2026-11-01T07:00Z UNTIL=2026-11-01T07:15Z DRY_RUN=1
	$(COMPOSE) exec agent python -m siteops_agent.cli --since $(SINCE) --until $(UNTIL) $(if $(DRY_RUN),--dry-run)

# --- development, on the host ---

test:  ## the fast tier: unit and contract tests
	uv run pytest

test-integration:  ## the tests that need real services (Qdrant, Ollama, rag)
	uv run pytest -m integration

lint:  ## ruff, and the format check
	uv run ruff check .
	uv run ruff format --check .

typecheck:  ## mypy, strict
	uv run mypy
