# CLAUDE.md

Site Ops Assistant: RAG over runbooks (`/ask`) + log-triage agent that opens tickets via MCP (`/triage`), uv workspace, Python 3.12, microservices.

## Rules

1. **No code unless explicitly asked.** Explain, plan, update docs or create empty files/folders freely; write implementation code, tests or config contents only when the user asks for it in so many words.
2. **Respect `DECISIONS.md`.** Read it before proposing or writing anything.
   - **Decided** entries are binding. If a request or your own plan conflicts with one, stop and point out the conflict (cite the ID) instead of working around it.
   - **Proposed** and **Open** entries are not settled. Don't silently pick an answer: say which entry your work depends on and ask, or add/update the entry as **Proposed** with your recommendation.
   - When a new decision is made, record it in `DECISIONS.md` (next free ID, under the matching category, update both index tables). Never renumber existing IDs.
3. **Keep the file structure below correct.** Whenever you add, move, rename or delete a file or folder, update the tree in this file in the same change. If the tree and the disk disagree, flag it.

## File structure

Every service has its own `Dockerfile`, all built with the repo root as build context (D-020). Each service has one composition root, `container.py`, next to its `config.py` (D-008).

```
rag_project/
├── packages/
│   └── common/                       # shared wire contracts only (D-020)
│       ├── pyproject.toml
│       └── src/common/
│           └── schemas.py            # /search + /ask request/response, ticket, incident
│
├── services/
│   ├── rag/                          # retrieval + generation, next to the vector store (D-044)
│   │   ├── Dockerfile                # also runs the ingest job with a different command (D-021)
│   │   ├── pyproject.toml
│   │   └── src/site_ops/
│   │       ├── config.py
│   │       ├── container.py          # composition root: reads config, builds adapters
│   │       ├── schemas.py            # service-internal types (Element, Chunk)
│   │       ├── ingestion/
│   │       │   ├── docling_loader.py
│   │       │   ├── chunking.py
│   │       │   └── pipeline.py
│   │       ├── retrieval/
│   │       │   ├── store.py          # VectorStore interface (ABC, D-007)
│   │       │   ├── qdrant_store.py   # the only DB access; access-level filter here
│   │       │   ├── memory_store.py   # for tests
│   │       │   └── reranker.py       # in-process (D-021)
│   │       ├── embedding.py          # HTTP Embedder adapter → embedder container (D-021)
│   │       ├── llm.py                # OpenAI-compatible client (vLLM / Ollama / proxy)
│   │       ├── rag.py                # retrieve → answer with citations, or "I don't know"
│   │       └── api.py                # FastAPI: /ask, /search
│   │
│   ├── agent/                        # triage orchestrator; every tool is a network call (D-044)
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   └── src/agent/
│   │       ├── config.py
│   │       ├── container.py          # composition root
│   │       ├── log_parser.py
│   │       ├── prefilter.py          # deterministic anomaly flags
│   │       ├── tools.py              # search_runbooks → rag /search, create_ticket → tickets (MCP), read_logs
│   │       ├── llm.py                # OpenAI-compatible client to the llm container
│   │       ├── agent.py              # the agent loop
│   │       └── api.py                # /triage
│   │
│   └── tickets/                      # MCP server over streamable HTTP: a real trust boundary (D-022)
│       ├── Dockerfile
│       ├── pyproject.toml
│       └── src/tickets/
│           ├── config.py
│           ├── container.py          # composition root
│           ├── server.py
│           └── store.py              # SQLite, fingerprint dedup, API-key check
│
├── scripts/
│   ├── generate_logs.py              # seeded faults = ground truth (D-002)
│   ├── ingest.py
│   └── run_eval.py
├── data/
│   ├── runbooks/                     # ~10 PDFs
│   ├── logs/
│   └── golden_set.jsonl
├── tests/
│   ├── unit/
│   ├── integration/                  # in-memory Qdrant, MCP round-trip
│   └── eval/                         # recall@5, triage precision/recall
│
├── docker-compose.yml                # services: rag, agent, tickets, qdrant, llm, embedder; job: ingest (rag image, D-021)
├── pyproject.toml                    # workspace root: [tool.uv.workspace]
├── Makefile
├── CLAUDE.md
├── DECISIONS.md
└── README.md
```
