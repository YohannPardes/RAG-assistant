# Decision Log — Site Ops Assistant

Lightweight ADRs. One entry per decision: what, why, what it costs.

**Status:** **Decided** (made by me) · **Proposed** (a recommendation waiting for yes/no) · **Open** (undecided topic, no recommendation yet) · **Superseded**.

**Priority (Open entries only):** **P1** — decide before writing code · **P2** — decide while building that part · **P3** — cut candidate for the 8-day scope; confirming the cut is itself the decision.

IDs are permanent and never renumbered (entries cross-reference them). New decisions take the next free ID and go under the matching category.

## Index

| Category                                                                                                 | Decisions                                                                   |
| -------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| [1. Scope &amp; evaluation](#1-scope--evaluation)                                                         | D-001, D-002, D-005, D-034, D-035, D-040                                    |
| [2. Architecture &amp; service boundaries](#2-architecture--service-boundaries)                           | D-004, D-010, D-019, D-020, D-021, D-022, D-036, D-037, D-041, D-043        |
| [3. Code design &amp; dependency injection](#3-code-design--dependency-injection)                         | D-003, D-007, D-008, D-018                                                  |
| [4. RAG pipeline: ingestion, retrieval &amp; generation](#4-rag-pipeline-ingestion-retrieval--generation) | D-006, D-011, D-012, D-023, D-024, D-026, D-027, D-028, D-029, D-030, D-039 |
| [5. Agent, access &amp; safety](#5-agent-access--safety)                                                  | D-013, D-025, D-031, D-032, D-033, D-042                                    |
| [6. Tooling, packaging &amp; testing](#6-tooling-packaging--testing)                                      | D-009, D-014, D-015, D-016, D-017, D-038                                    |

## Open decisions by priority

| Priority                       | Decisions                                                                                                                                                                                                                                                                             |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **P1 — before code**    | D-021 own modules as services · D-022 MCP transport · D-023 LLM + serving · D-024 embedding model · D-025 access levels                                                                                                                                                           |
| **P2 — while building** | D-026 chunking · D-027 retrieval depth · D-028 reranker model · D-029 "I don't know" rule · D-030 citations · D-031 ticket fingerprint · D-032 agent loop · D-033 auth · D-034 golden set · D-035 eval thresholds · D-036 timeouts/retries · D-037 persistence · D-038 CI |
| **P3 — cut candidates** | D-039 hybrid search · D-040 LLM-judge metric · D-041 distributed tracing · D-042 scheduled triage · D-043 backups                                                                                                                                                                 |

---

# 1. Scope & evaluation

### D-001 — Project scope: one repo, two entry points, shared core

**Status:** Decided (2026-10-27)

- **Ask (RAG):** engineer asks about an error code → answer with citations from runbooks.
- **Triage (agent):** reads new logs, flags anomalies, retrieves runbook section, writes incident report, opens ticket via MCP.
- **Why:** covers RAG + agents + tools + MCP + Docker without becoming a big system; every component is defensible line by line.

### D-002 — Synthetic logs with seeded faults as ground truth

**Status:** Decided (2026-10-27)

- Faults (overheating, link drop, PSU error) + normal noise; each fault maps to a runbook entry.
- **Why:** makes the agent *measurable* — ticket precision/recall, correct citation, no duplicates.

### D-005 — Keep `reranker` and `api` in scope

**Status:** Decided (2026-10-27)

- **Note:** reranker's value must be shown by an eval number (recall@k / MRR with vs without).

### D-034 — Golden set: size and construction

**Status:** Open · **Priority:** P2

- How many question → expected-chunk pairs, how they're written (by hand from runbooks, or generated then reviewed), and how triage ground truth links to D-002's seeded faults.

### D-035 — Eval pass thresholds

**Status:** Open · **Priority:** P2

- Target numbers for recall@5, MRR (with vs without reranker, D-005), and ticket precision/recall.

### D-040 — LLM-as-judge for answer quality

**Status:** Open · **Priority:** P3 (cut candidate)

- Score generated answers with an LLM judge, or rely only on retrieval metrics + citation checks (D-030).

---

# 2. Architecture & service boundaries

### D-004 — Microservices architecture

**Status:** Decided (2026-10-27)

- Services talk over the network. Baseline set: `api`, `tickets` (MCP), `qdrant`, `llm`.
- **Open:** which of my own modules (e.g. reranker) become separate services → see D-021.
- **Cost:** serialization, timeouts, retries, extra failure modes to test.

### D-010 — Service boundary = adapter

**Status:** Proposed

- A module promoted to a service gets an HTTP adapter implementing the *same* interface (`HttpReranker` vs `CrossEncoderReranker`). Deployment becomes config, not code change.

### D-019 — Ticket service owns its contract

**Status:** Superseded by D-020

### D-020 — Shared `common` package for cross-service contracts

**Status:** Decided (2026-10-27)

- Each service has its own dependencies (own Docker image); a `common` package with shared schemas is copied into every image.
- **Rule:** `common` holds only *wire contracts* (models that cross a service boundary: ticket, query request/response, incident). No logic, no vendor deps beyond pydantic. Service-internal types (`Element`, `Chunk`) stay in their service.
- **Cost:** shared kernel = coupling — a change in `common` means rebuilding every image, and deployed services can run different versions. Mitigation: only backward-compatible changes (add optional fields, never rename/remove).
- **Mechanism:** uv workspace. Layout: `packages/common/src/common/`, `services/rag/src/site_ops/`, `services/tickets/src/tickets/`; each member has its own `pyproject.toml`. Root `pyproject.toml` with `[tool.uv.workspace]`, one shared `uv.lock`. Services depend on `common` via `[tool.uv.sources] common = { workspace = true }`.
- Docker: each image copies `common` + its own service, `uv sync --package <service>`; build context = repo root.
- MCP tool schemas are generated from `common` pydantic models (single source of truth for the wire format).

### D-036 — Timeout and retry policy for network calls

### D-021 — Which of my own modules become separate services

**Status:** Decided (2026-10-27)

- **Rule:** a component becomes a service only when something requires it: a different resource profile (GPU / large model), independent scaling, multiple consumers, or data locality. Otherwise it stays a module inside a service.
- **Embedding:** off-the-shelf inference server (e.g. text-embeddings-inference, or the Ollama/vLLM embeddings endpoint) behind an HTTP `Embedder` adapter. No service code of my own.
- **Reranker:** module of the RAG pipeline, in-process behind its interface. Can move to an inference server (TEI `/rerank`) via D-010 if evals justify it.
- **Ingestion:** a **job**, not a service: a one-off container (`docker compose run ingest`) that parses PDFs, writes to the vector store, and exits.
- **Cost:** inference servers are extra containers to run and health-check; their network calls inherit D-004's costs.

### D-022 — MCP transport for the tickets server

**Status:** Decided (2026-10-27)

- **Streamable HTTP.** Tickets runs in its own container and serves any number of MCP clients at once.
- **Why not stdio:** stdio runs the server as a subprocess of the agent, so the tickets code and its dependencies would live inside the agent image (breaks D-020) and its lifecycle would be tied to the agent's.
- **Non-AI clients:** if needed later, add a REST endpoint next to MCP, backed by the same store.
- **Cost:** it is a network endpoint, so it needs auth (API key) and timeouts/retries.
- **Tests:** unit tests call the tool functions directly or use an in-memory MCP client; one integration test over real HTTP.

**Status:** Open · **Priority:** P2

- Per-call timeouts, which calls retry (idempotent only?), backoff. Answers D-004's cost line.

### D-037 — Persistence: Docker volumes for Qdrant and SQLite

**Status:** Open · **Priority:** P2

- Named volumes vs bind mounts; what survives `docker compose down`.

### D-041 — Distributed tracing (OpenTelemetry)

**Status:** Open · **Priority:** P3 (cut candidate)

- Tracing across services vs structured logs with a propagated request ID only.

### D-043 — Backups for Qdrant / SQLite volumes

**Status:** Open · **Priority:** P3 (cut candidate)

- Data is reproducible from `scripts/ingest.py` and `scripts/generate_logs.py`.

---

# 3. Code design & dependency injection

### D-003 — Every external technology sits behind an interface

**Status:** Decided (2026-10-27)

- Interfaces: `DocumentParser`, `Embedder`, `VectorStore`, `Reranker`, `ChatModel`, `TicketSink`, `LogSource`.
- Own pure logic (chunker, log parser, prefilter) stays concrete — nothing to swap.
- **Why:** no component depends on a single technology; tests run on fakes/in-memory adapters.
- **Cost:** more files; each interface must have ≥2 implementations (one real, one fake) to justify existing. This rule is what stops speculative interfaces; it also keeps implementation details from leaking into the interface and makes testing easy.

### D-007 — Interfaces via `abc.ABC` (not `typing.Protocol`)

**Status:** Decided (2026-10-27)

- **Why:** explicit dependency — each adapter declares the interface it implements, and a missing method fails at instantiation instead of at first call.
- **Cost:** every adapter imports and subclasses the interface, so adapters depend on core. Changing an interface means changing every adapter.

### D-008 — Single composition root

**Status:** Proposed

- Only one place (`container.py` / factory) reads env and instantiates concrete adapters; everything else receives dependencies via `__init__`.

### D-018 — Configuration via environment (`pydantic-settings`) + `.env.example`

**Status:** Proposed

- Only the composition root (D-008) reads settings.

---

# 4. RAG pipeline: ingestion, retrieval & generation

### D-006 — Vector store: Qdrant (not Pinecone)

**Status:** Proposed

- **Why:** on-prem, matches target stack, `:memory:` mode for tests. Pinecone is SaaS-only.

### D-011 — Chunker consumes own `Element` IR, not Docling objects

**Status:** Proposed

- `Element(kind=heading|paragraph|table, text, page)`; `DocumentParser` adapters produce it.
- **Why:** chunker is pure and unit-testable without PDFs or Docling installed.

### D-012 — Deterministic `chunk_id`

**Status:** Proposed

- Derived from content + source (hash), so re-ingestion upserts instead of duplicating.

### D-023 — LLM: model and serving runtime

**Status:** Open · **Priority:** P1

- Which model, served by vLLM, Ollama or a proxy. Fixes the `llm` container in `docker-compose.yml` and what `llm.py`'s OpenAI-compatible client points at.

### D-024 — Embedding model

**Status:** Open · **Priority:** P1

- Which model, and its vector size (fixes the Qdrant collection). Whether it runs in-process or as a service is D-021.

### D-026 — Chunking strategy

**Status:** Open · **Priority:** P2

- Split by heading/section vs fixed size; size and overlap; tables kept whole or split.

### D-027 — Retrieval depth

**Status:** Open · **Priority:** P2

- k fetched from Qdrant before reranking, k kept after.

### D-028 — Reranker model

**Status:** Open · **Priority:** P2

- Which cross-encoder (or alternative). Value proven per D-005.

### D-029 — "I don't know" rule

**Status:** Open · **Priority:** P2

- Score threshold on retrieved chunks, LLM judgment, or both.

### D-030 — Citation format and check

**Status:** Open · **Priority:** P2

- Cite by page, section or chunk ID; how a citation is verified to exist in the retrieved context.

### D-039 — Hybrid search (BM25 + vectors)

**Status:** Open · **Priority:** P3 (cut candidate)

- Error codes are exact tokens, which is where keyword search helps; only worth it if D-035's recall numbers show a gap.

---

# 5. Agent, access & safety

### D-013 — Agent design: single agent + deterministic pre-filter; logs are untrusted

**Status:** Proposed

- Regex/threshold pre-filter flags windows; LLM reasons only on flagged windows.
- Agent's only side effect is `create_ticket`, deduplicated by fault fingerprint (prompt-injection blast radius).

### D-025 — Access-level model

**Status:** Open · **Priority:** P1

- Which levels exist, where a caller's level comes from, and how it's stored in the Qdrant payload (the filter lives in `qdrant_store.py`). Affects the chunk schema, so it's needed before ingestion.

### D-031 — Ticket fingerprint and dedup window

**Status:** Open · **Priority:** P2

- Which fields define "the same fault" (D-013), and for how long a duplicate is suppressed.

### D-032 — Agent loop: implementation and limits

**Status:** Open · **Priority:** P2

- Hand-written loop vs framework; max steps, tool-call budget, timeout.

### D-033 — Auth for the API and the MCP server

**Status:** Open · **Priority:** P2

- API key only? How keys are issued, stored and checked (the tickets store already plans an API-key check).

### D-042 — Scheduled / watcher-triggered triage

**Status:** Open · **Priority:** P3 (cut candidate)

- On-demand `/triage` only vs a scheduler or log watcher.


### D-044 — RAG and agent are separate services

**Status:** Decided (2026-10-27)

- Services I write: `rag`, `agent`, `tickets`. Layout adds `services/agent/` (D-020).
- **rag:** owns retrieval and generation and sits next to the vector store. Exposes `/search` (ranked chunks with metadata) and `/ask` (answer with citations). Serves two consumers: engineers (Ask) and the agent (Triage).
- **agent:** a pure orchestrator. Every tool is a network call: `search_runbooks` → rag `/search`, `create_ticket` → tickets via MCP, `read_logs` → log source. No model or vector-store dependencies, so the image is small.
- **Why:** RAG has multiple consumers and belongs next to its data. The agent has a different runtime (scheduled loop vs request/response). **Security isolation:** the agent processes untrusted logs, so a prompt-injected agent can only query rag and create (deduplicated) tickets. It cannot reach Qdrant, ingestion, or anything else.
- **Contracts:** `/search` and `/ask` request/response models live in `common` (D-020).
- **Cost:** a third service to build, deploy and test; agent → rag adds a network hop with its own timeouts and failure handling.

---

# 6. Tooling, packaging & testing

### D-014 — Tooling: `uv` + `hatchling`, Python 3.12

**Status:** Decided (2026-10-27)

- `uv` for env + lockfile (reproducible Docker builds); `hatchling` build backend; every workspace member uses a src layout (`<member>/src/<package>/`, see D-020).

### D-015 — Dependencies split into extras that mirror the adapters

**Status:** Decided (2026-10-27)

- Core = only what the domain needs (pydantic). Each technology is an extra (`qdrant`, `docling`, …).
- Dev tools (pytest, ruff, mypy) go in `[dependency-groups] dev` (PEP 735), not an extra — not shipped with the package.
- Consequence: adapters are imported lazily by the composition root, so `import site_ops` works without any extra.
- **Why:** core installs with zero vendor tech (enforces D-003); each service image installs only its extras (D-004).

### D-016 — Test tiers via pytest markers

**Status:** Decided (2026-10-27)

- Default `pytest` runs unit + contract (fast, no network). `integration` and `eval` are opt-in markers; `--strict-markers` on.

### D-017 — Lint + type check: `ruff` + `mypy` strict

**Status:** Decided (2026-10-27)

- **Why mypy on top of ABCs (D-007):** an ABC only checks that methods exist, and only at instantiation. mypy checks signatures and types at every call site, including the pydantic models that cross service boundaries (D-020).
- mypy `strict` on `packages/*/src` and `services/*/src` from day one; relaxed for `tests/`.

### D-009 — Contract tests per interface

**Status:** Proposed

- One test suite per interface, parametrized over all implementations (e.g. InMemory + Qdrant `:memory:`).

### D-038 — CI pipeline

**Status:** Open · **Priority:** P2

- GitHub Actions running ruff, mypy and the default (unit + contract) test tier; whether `eval` runs in CI.
