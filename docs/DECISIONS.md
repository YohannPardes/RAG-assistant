# Decision Log — Site Ops Assistant

Lightweight ADRs. One entry per decision: what, why, what it costs.

**Status:** **Decided** (made by me) · **Proposed** (a recommendation waiting for yes/no) · **Open** (undecided topic, no recommendation yet) · **Superseded**.

**Priority (Open entries only):** **P1** — decide before writing code · **P2** — decide while building that part · **P3** — cut candidate for the 8-day scope; confirming the cut is itself the decision.

**IDs** are `subject/number`, numbered within each subject: `scope` · `arch` · `design` · `rag` · `agent` · `tooling` (e.g. `arch/4`). Cite decisions this way everywhere (docs, code comments, commits). A new decision takes the next free number in its subject and goes at the end of that section. Don't renumber existing IDs: other entries and the code cite them.

## Index

| Subject     | Category                                                                                                 | Decisions              |
| ----------- | -------------------------------------------------------------------------------------------------------- | ---------------------- |
| `scope`   | [1. Scope &amp; evaluation](#1-scope--evaluation)                                                         | scope/1 – scope/6     |
| `arch`    | [2. Architecture &amp; service boundaries](#2-architecture--service-boundaries)                           | arch/1 – arch/11      |
| `design`  | [3. Code design &amp; dependency injection](#3-code-design--dependency-injection)                         | design/1 – design/4   |
| `rag`     | [4. RAG pipeline: ingestion, retrieval &amp; generation](#4-rag-pipeline-ingestion-retrieval--generation) | rag/1 – rag/13        |
| `agent`   | [5. Agent, access &amp; safety](#5-agent-access--safety)                                                  | agent/1 – agent/8     |
| `tooling` | [6. Tooling, packaging &amp; testing](#6-tooling-packaging--testing)                                      | tooling/1 – tooling/6 |

## Open decisions by priority

| Priority                       | Decisions                                                                                                                                                                                                                                                                                              |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **P1 — before code**    | rag/5 embedding model · agent/2 access levels                                                                                                                                                                                              |
| **P2 — while building** | rag/6 chunking · rag/7 retrieval depth · rag/8 reranker model · rag/9 "I don't know" rule · rag/10 citations · agent/3 ticket fingerprint · agent/4 agent loop · agent/5 auth · scope/4 golden set · scope/5 eval thresholds · arch/7 timeouts/retries · arch/8 persistence · tooling/6 CI |
| **P3 — cut candidates** | rag/11 hybrid search · scope/6 LLM-judge metric · arch/9 distributed tracing · agent/6 scheduled triage · arch/10 backups                                                                                                                                                                          |

---

# 1. Scope & evaluation

### scope/1 — Project scope: one repo, two entry points, shared core

**Status:** Decided (2026-10-27)

- **Ask (RAG):** engineer asks about an error code → answer with citations from runbooks.
- **Triage (agent):** reads new logs, flags anomalies, retrieves runbook section, writes incident report, opens ticket via MCP.
- **Why:** covers RAG + agents + tools + MCP + Docker without becoming a big system; every component is defensible line by line.

### scope/2 — Synthetic logs with seeded faults as ground truth

**Status:** Decided (2026-10-27)

- Faults (overheating, link drop, PSU error) + normal noise; each fault maps to a runbook entry.
- **Why:** makes the agent *measurable* — ticket precision/recall, correct citation, no duplicates.

### scope/3 — Keep `reranker` and `api` in scope

**Status:** Decided (2026-10-27)

- **Note:** reranker's value must be shown by an eval number (recall@k / MRR with vs without).

### scope/4 — Golden set: size and construction

**Status:** Proposed · **Priority:** P2

- How many question → expected-chunk pairs, how they're written (by hand from runbooks, or generated then reviewed), and how triage ground truth links to scope/2's seeded faults.
- **Recommendation (retrieval part, in `data/golden_set.jsonl`):** ~40 questions written by hand, ~10 per runbook, paraphrased rather than copied so they test meaning and not keyword overlap. Each line is `{question, expected_source, expected_section}`. A hit is a chunk with the same `source` whose `heading_path[-1]` equals `expected_section`, never a `chunk_id`, so the labels survive chunking changes (rag/6). Each question has one unambiguous section. Triage ground truth is still open.
- **Why:** with 4 runbooks, hand-written questions are cheap and more trustworthy than generated ones. Matching on section keeps recall@5 and MRR comparable across chunk sizes and with or without the reranker (scope/3).

### scope/5 — Eval pass thresholds

**Status:** Open · **Priority:** P2

- Target numbers for recall@5, MRR (with vs without reranker, scope/3), and ticket precision/recall.

### scope/6 — LLM-as-judge for answer quality

**Status:** Open · **Priority:** P3 (cut candidate)

- Score generated answers with an LLM judge, or rely only on retrieval metrics + citation checks (rag/10).

---

# 2. Architecture & service boundaries

### arch/1 — Microservices architecture

**Status:** Decided (2026-10-27)

- Services talk over the network. Baseline set: `api`, `tickets` (MCP), `qdrant`, `llm`.
- **Open:** which of my own modules (e.g. reranker) become separate services → see arch/5.
- **Cost:** serialization, timeouts, retries, extra failure modes to test.

### arch/2 — Service boundary = adapter

**Status:** Proposed

- A module promoted to a service gets an HTTP adapter implementing the *same* interface (`HttpReranker` vs `CrossEncoderReranker`). Deployment becomes config, not code change.

### arch/3 — Ticket service owns its contract

**Status:** Superseded by arch/4

### arch/4 — Shared `common` package for cross-service contracts

**Status:** Decided (2026-10-27)

- Each service has its own dependencies (own Docker image); a `common` package with shared schemas is copied into every image.
- **Rule:** `common` holds only *wire contracts* (models that cross a service boundary: ticket, query request/response, incident). No logic, no vendor deps beyond pydantic. Service-internal types (`Element`, `Chunk`) stay in their service.
- **Cost:** shared kernel = coupling — a change in `common` means rebuilding every image, and deployed services can run different versions. Mitigation: only backward-compatible changes (add optional fields, never rename/remove).
- **Mechanism:** uv workspace. Layout: `packages/common/src/common/`, `services/rag/src/site_ops/`, `services/tickets/src/tickets/`; each member has its own `pyproject.toml`. Root `pyproject.toml` with `[tool.uv.workspace]`, one shared `uv.lock`. Services depend on `common` via `[tool.uv.sources] common = { workspace = true }`.
- Docker: each image copies `common` + its own service, `uv sync --package <service>`; build context = repo root.
- MCP tool schemas are generated from `common` pydantic models (single source of truth for the wire format).

### arch/5 — Which of my own modules become separate services

**Status:** Decided (2026-10-27)

- **Rule:** a component becomes a service only when something requires it: a different resource profile (GPU / large model), independent scaling, multiple consumers, or data locality. Otherwise it stays a module inside a service.
- **Embedding:** off-the-shelf inference server (e.g. text-embeddings-inference, or the Ollama/vLLM embeddings endpoint) behind an HTTP `Embedder` adapter. No service code of my own.
- **Reranker:** module of the RAG pipeline, in-process behind its interface. Can move to an inference server (TEI `/rerank`) via arch/2 if evals justify it.
- **Ingestion:** a **job**, not a service: a one-off container (`docker compose run ingest`) that parses PDFs, writes to the vector store, and exits.
- **Cost:** inference servers are extra containers to run and health-check; their network calls inherit arch/1's costs.

### arch/6 — MCP transport for the tickets server

**Status:** Decided (2026-10-27)

- **Streamable HTTP.** Tickets runs in its own container and serves any number of MCP clients at once.
- **Why not stdio:** stdio runs the server as a subprocess of the agent, so the tickets code and its dependencies would live inside the agent image (breaks arch/4) and its lifecycle would be tied to the agent's.
- **Non-AI clients:** if needed later, add a REST endpoint next to MCP, backed by the same store.
- **Cost:** it is a network endpoint, so it needs auth (API key) and timeouts/retries.
- **Tests:** unit tests call the tool functions directly or use an in-memory MCP client; one integration test over real HTTP.

### arch/7 — Timeout and retry policy for network calls

**Status:** Open · **Priority:** P2

- Per-call timeouts, which calls retry (idempotent only?), backoff. Answers arch/1's cost line.

### arch/8 — Persistence: Docker volumes for Qdrant and SQLite

**Status:** Open · **Priority:** P2

- Named volumes vs bind mounts; what survives `docker compose down`.

### arch/9 — Distributed tracing (OpenTelemetry)

**Status:** Open · **Priority:** P3 (cut candidate)

- Tracing across services vs structured logs with a propagated request ID only.

### arch/10 — Backups for Qdrant / SQLite volumes

**Status:** Open · **Priority:** P3 (cut candidate)

- Data is reproducible from `scripts/ingest.py` and `scripts/generate_logs.py`.


### arch/11 — No API gateway in this version

**Status:** Deferred (2026-10-27)

- Auth, rate limits, TLS, routing belong at the edge (API gateway), not duplicated per service.
- Not built: one public entry point (agent `/chat`); scope stays on the AI components.
- Next step: edge gateway (Envoy / Kong / Traefik) + LLM gateway (e.g. LiteLLM proxy) for per-user token budgets, model routing, fallback.

---

# 3. Code design & dependency injection

### design/1 — Every external technology sits behind an interface

**Status:** Decided (2026-10-27)

- Interfaces: `DocumentParser`, `Embedder`, `VectorStore`, `Reranker`, `ChatModel`, `TicketSink`, `LogSource`.
- Own pure logic (chunker, log parser, prefilter) stays concrete — nothing to swap.
- **Why:** no component depends on a single technology; tests run on fakes/in-memory adapters.
- **Cost:** more files; each interface must have ≥2 implementations (one real, one fake) to justify existing. This rule is what stops speculative interfaces; it also keeps implementation details from leaking into the interface and makes testing easy.

### design/2 — Interfaces via `abc.ABC` (not `typing.Protocol`)

**Status:** Decided (2026-10-27)

- **Why:** explicit dependency — each adapter declares the interface it implements, and a missing method fails at instantiation instead of at first call.
- **Cost:** every adapter imports and subclasses the interface, so adapters depend on core. Changing an interface means changing every adapter.

### design/3 — Single composition root

**Status:** Proposed

- Only one place (`container.py` / factory) reads env and instantiates concrete adapters; everything else receives dependencies via `__init__`.

### design/4 — Configuration via environment (`pydantic-settings`) + `.env.example`

**Status:** Proposed

- Only the composition root (design/3) reads settings.

---

# 4. RAG pipeline: ingestion, retrieval & generation

### rag/1 — Vector store: Qdrant (not Pinecone)

**Status:** Proposed

- **Why:** on-prem, matches target stack, `:memory:` mode for tests. Pinecone is SaaS-only.

### rag/2 — Chunker consumes own `Element` IR, not Docling objects

**Status:** Proposed

- `Element(kind=heading|paragraph|table, text, page)`; `DocumentParser` adapters produce it.
- **Why:** chunker is pure and unit-testable without PDFs or Docling installed.

### rag/3 — Deterministic `chunk_id`

**Status:** Proposed

- Derived from content + source (hash), so re-ingestion upserts instead of duplicating.

### rag/4 — LLM: model and serving runtime

**Status:** Decided (2026-10-31)

- Which model, served by vLLM, Ollama or a proxy. Fixes the `llm` container in `docker-compose.yml` and what `llm.py`'s OpenAI-compatible client points at.
- **Decision:** `qwen3.5:27b` served by **Ollama**, reached through its OpenAI-compatible API (`llm.base_url: http://localhost:11434/v1`). The code only speaks that API, so changing server or model is config only.

### rag/5 — Embedding model

**Status:** Open · **Priority:** P1

- Which model, and its vector size (fixes the Qdrant collection). Whether it runs in-process or as a service is arch/5.

### rag/6 — Chunking strategy

**Status:** Open · **Priority:** P2

- Split by heading/section vs fixed size; size and overlap; tables kept whole or split.

### rag/7 — Retrieval depth

**Status:** Open · **Priority:** P2

- k fetched from Qdrant before reranking, k kept after.

### rag/8 — Reranker model

**Status:** Open · **Priority:** P2

- Which cross-encoder (or alternative). Value proven per scope/3.

### rag/9 — "I don't know" rule

**Status:** Proposed · **Priority:** P2

- Score threshold on retrieved chunks, LLM judgment, or both.
- **Recommendation (implemented in `rag.py`):** LLM judgment, checked. Nothing retrieved → "I don't know" without calling the LLM; otherwise the answer counts only if at least one of its citations passes rag/10's check. `AskResponse.answered` carries the result.
- **Why:** no score threshold until scope/5's eval gives numbers to set one from; the citation check stops an answer the excerpts don't support.
- **Cost:** a correct answer with a badly copied quote is reported as "I don't know".

### rag/10 — Citation format and check

**Status:** Proposed · **Priority:** P2

- Cite by page, section or chunk ID; how a citation is verified to exist in the retrieved context.
- **Recommendation (implemented in `rag.py`):** the LLM gets numbered excerpts and replies in JSON with `{"excerpt": n, "quote": "..."}` citations. A citation is kept only if `n` is a retrieved excerpt and the quote appears in its text word for word (whitespace ignored). `Citation` carries `chunk_id`, source, heading path, page and the quote.
- **Why:** checkable without an LLM judge; the eval can re-run the same check on `AskResponse`.
- **Cost:** needs a server with JSON mode (`response_format`); small models may paraphrase quotes, which drops the citation.

### rag/11 — Hybrid search (BM25 + vectors)

**Status:** Open · **Priority:** P3 (cut candidate)

- Error codes are exact tokens, which is where keyword search helps; only worth it if scope/5's recall numbers show a gap.

### rag/12 — `upsert` replaces its sources: write, then delete their stale chunks

**Status:** Decided (2026-10-31)

- `VectorStore.upsert(chunks, embeddings)` treats the chunks as the complete set for their sources: it writes them, then deletes those sources' chunks whose ID is not among them. The pipeline calls it once per document, after embedding.
- **Why:** rag/3's deterministic IDs only overwrite unchanged chunks; an edited or removed section keeps its old point forever. Write-first means a crash between the two steps leaves a few stale chunks (cleaned up on the next run), never a missing runbook.
- **Cost:** a source's chunks must arrive in one `upsert` call (no splitting a document over several). A document that becomes empty sends no chunks, so the pipeline calls `delete_by_source` for it instead. Not atomic. Files deleted from the runbooks folder are not cleaned up; that needs a sweep over stored sources.

### rag/13 — Similarity metric: cosine only

**Status:** Decided (2026-10-31)

- Every `VectorStore` scores by cosine similarity (higher = more similar); Qdrant collections are created with `Distance.COSINE`. No configurable metric.
- **Why:** embedding models like bge-m3 are trained for cosine; one metric keeps scores comparable across stores, so the contract tests and a future score threshold (rag/9) hold for all of them.
- **Cost:** a model trained for dot product or Euclidean would need this revisited, and the collection recreated.

---

# 5. Agent, access & safety

### agent/1 — Agent design: single agent + deterministic pre-filter; logs are untrusted

**Status:** Proposed

- Regex/threshold pre-filter flags windows; LLM reasons only on flagged windows.
- Agent's only side effect is `create_ticket`, deduplicated by fault fingerprint (prompt-injection blast radius).

### agent/2 — Access-level model

**Status:** Open · **Priority:** P1

- Which levels exist, where a caller's level comes from, and how it's stored in the Qdrant payload (the filter lives in `qdrant_store.py`). Affects the chunk schema, so it's needed before ingestion.

### agent/3 — Ticket fingerprint and dedup window

**Status:** Open · **Priority:** P2

- Which fields define "the same fault" (agent/1), and for how long a duplicate is suppressed.

### agent/4 — Agent loop: implementation and limits

**Status:** Open · **Priority:** P2

- Hand-written loop vs framework; max steps, tool-call budget, timeout.

### agent/5 — Auth for the API and the MCP server

**Status:** Open · **Priority:** P2

- API key only? How keys are issued, stored and checked (the tickets store already plans an API-key check).

### agent/6 — Scheduled / watcher-triggered triage

**Status:** Open · **Priority:** P3 (cut candidate)

- On-demand `/triage` only vs a scheduler or log watcher.

### agent/7 — RAG and agent are separate services

**Status:** Decided (2026-10-27)

- Services I write: `rag`, `agent`, `tickets`. Layout adds `services/agent/` (arch/4).
- **rag:** owns retrieval and generation and sits next to the vector store. Exposes `/search` (ranked chunks with metadata) and `/ask` (answer with citations). Serves two consumers: engineers (Ask) and the agent (Triage).
- **agent:** a pure orchestrator. Every tool is a network call: `search_runbooks` → rag `/search`, `create_ticket` → tickets via MCP, `read_logs` → log source. No model or vector-store dependencies, so the image is small.
- **Why:** RAG has multiple consumers and belongs next to its data. The agent has a different runtime (scheduled loop vs request/response). **Security isolation:** the agent processes untrusted logs, so a prompt-injected agent can only query rag and create (deduplicated) tickets. It cannot reach Qdrant, ingestion, or anything else.
- **Contracts:** `/search` and `/ask` request/response models live in `common` (arch/4).
- **Cost:** a third service to build, deploy and test; agent → rag adds a network hop with its own timeouts and failure handling.

### agent/8 — The agent is the user's front door

**Status:** Decided (2026-10-27)

- Users (UI, SDK) talk to the **agent** (`/chat`). The agent decides which tools to call: search runbooks (rag), open a ticket (tickets MCP).
- The agent has two modes on the same tools: **chat** (user request) and **triage** (trigger: schedule / CLI).
- **rag `/ask` is kept** as a direct Q&A endpoint and as the **evaluation baseline**: answer quality is measured without the agent loop's noise.
- **Why:** matches the "autonomous agents" focus; one entry point; tools are reused across both modes.
- **Cost:** every user question goes through an LLM loop (latency, cost, less determinism). The agent now receives **two untrusted inputs**, user text and logs, while holding the ticket permission, so tool permissions and ticket dedup matter more.

---

# 6. Tooling, packaging & testing

### tooling/1 — Tooling: `uv` + `hatchling`, Python 3.12

**Status:** Decided (2026-10-27)

- `uv` for env + lockfile (reproducible Docker builds); `hatchling` build backend; every workspace member uses a src layout (`<member>/src/<package>/`, see arch/4).

### tooling/2 — Dependencies split into extras that mirror the adapters

**Status:** Decided (2026-10-27)

- Core = only what the domain needs (pydantic). Each technology is an extra (`qdrant`, `docling`, …).
- Dev tools (pytest, ruff, mypy) go in `[dependency-groups] dev` (PEP 735), not an extra — not shipped with the package.
- Consequence: adapters are imported lazily by the composition root, so `import site_ops` works without any extra.
- **Why:** core installs with zero vendor tech (enforces design/1); each service image installs only its extras (arch/1).

### tooling/3 — Test tiers via pytest markers

**Status:** Decided (2026-10-27)

- Default `pytest` runs unit + contract (fast, no network). `integration` and `eval` are opt-in markers; `--strict-markers` on.

### tooling/4 — Lint + type check: `ruff` + `mypy` strict

**Status:** Decided (2026-10-27)

- **Why mypy on top of ABCs (design/2):** an ABC only checks that methods exist, and only at instantiation. mypy checks signatures and types at every call site, including the pydantic models that cross service boundaries (arch/4).
- mypy `strict` on `packages/*/src` and `services/*/src` from day one; relaxed for `tests/`.

### tooling/5 — Contract tests per interface

**Status:** Proposed

- One test suite per interface, parametrized over all implementations (e.g. InMemory + Qdrant `:memory:`).

### tooling/6 — CI pipeline

**Status:** Open · **Priority:** P2

- GitHub Actions running ruff, mypy and the default (unit + contract) test tier; whether `eval` runs in CI.
