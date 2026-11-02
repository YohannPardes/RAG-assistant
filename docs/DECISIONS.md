# Decision Log — Site Ops Assistant

Lightweight ADRs. One entry per decision: what, why, what it costs.

**Status:** **Decided** (made by me) · **Proposed** (a recommendation waiting for yes/no) · **Open** (undecided topic, no recommendation yet) · **Deferred** (out of this version, may come back) · **Superseded**. "Decided (…, as built)" records a recommendation the code already follows.

**Priority (Open entries only):** **P1** — decide before writing code · **P2** — decide while building that part · **P3** — cut candidate for the 8-day scope; confirming the cut is itself the decision.

**IDs** are `subject/number`, numbered within each subject: `scope` · `arch` · `design` · `rag` · `agent` · `tooling` (e.g. `arch/4`). Cite decisions this way everywhere (docs, code comments, commits). A new decision takes the next free number in its subject and goes at the end of that section. Don't renumber existing IDs: other entries and the code cite them.

## Index

| Subject     | Category                                                                                                 | Decisions              |
| ----------- | -------------------------------------------------------------------------------------------------------- | ---------------------- |
| `scope`   | [1. Scope &amp; evaluation](#1-scope--evaluation)                                                         | scope/1 – scope/6     |
| `arch`    | [2. Architecture &amp; service boundaries](#2-architecture--service-boundaries)                           | arch/1 – arch/12      |
| `design`  | [3. Code design &amp; dependency injection](#3-code-design--dependency-injection)                         | design/1 – design/4   |
| `rag`     | [4. RAG pipeline: ingestion, retrieval &amp; generation](#4-rag-pipeline-ingestion-retrieval--generation) | rag/1 – rag/14        |
| `agent`   | [5. Agent, access &amp; safety](#5-agent-access--safety)                                                  | agent/1 – agent/15    |
| `tooling` | [6. Tooling, packaging &amp; testing](#6-tooling-packaging--testing)                                      | tooling/1 – tooling/6 |

## Open decisions by priority

| Priority                       | Decisions                                                                                                                                                                                                                                                                                              |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **P1 — before code**    | agent/2 access levels                                                                                                                                                                                                                  |
| **P2 — while building** | scope/5 eval thresholds                                                                                                                                                                                                                |
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
- **Amended (2026-11-01), faults from the corpus:** no runbook covers overheating, link drop or PSU errors, so the seeded faults are the ones the runbooks describe: `disk_full`, `pg_primary_down`, `pg_replica_lag`, `cert_expiry`, `high_latency`, `api_error_rate`, `pod_oomkilled`. Each one's runbook has golden-set questions (scope/4), so a missed citation is a triage problem and not a gap in the corpus. `high_latency` and `api_error_rate` hit the same service on purpose, so the agent has to pick the right one of two similar runbooks.
- **Generator:** `scripts/generate_logs.py` (seeded, deterministic) writes `data/logs/site.log`, one line per event: `<ISO 8601 UTC ms> <INFO|WARN|ERROR> <host> <component>: <message>` (agent/9's parser reads this format). It contains normal noise from 10 fake hosts, each fault once in its own time slot (no overlaps), and about two *decoys* an hour (a single slow request, one 504, a lag blip, a disk at 81% that logrotate fixes). Decoys are not faults: a ticket for one is a false positive. Normal noise is INFO only, so every WARN or ERROR outside a fault is a decoy, at least 5 minutes from any fault. A fault's effects can show up on other hosts (a primary that is down shows up as write errors on the API hosts); the fault's `host` is where it is.

### scope/3 — Keep `reranker` and `api` in scope

**Status:** Decided (2026-10-27)

- **Note:** reranker's value must be shown by an eval number (recall@k / MRR with vs without).
- **Amended (2026-11-02), reranker cut from this version:** it was never built, and the empty `reranker/` package is removed. rag answers from the `top_k` chunks the vector store returns (rag/7); `scripts/run_eval.py` records `"reranker": "none"` in every run. rag/8 is deferred with it. `api` stays: rag serves `/search` and `/ask`, the agent `/triage` and `/windows`.
- **Cost:** no number shows what a reranker would add at a smaller `top_k`, where recall@5 is 0.956. Adding one back means a `Reranker` interface with a passthrough fake (design/1) and an eval run with and without it.

### scope/4 — Golden set: size and construction

**Status:** Decided (2026-11-02, as built)

- How many question → expected-chunk pairs, how they're written (by hand from runbooks, or generated then reviewed), and how triage ground truth links to scope/2's seeded faults.
- **Recommendation (retrieval part, in `data/golden_set.jsonl`):** ~40 questions written by hand, ~10 per runbook, paraphrased rather than copied so they test meaning and not keyword overlap. Each line is `{question, expected_source, expected_section}`. A hit is a chunk with the same `source` whose `heading_path[-1]` equals `expected_section`, never a `chunk_id`, so the labels survive chunking changes (rag/6). Each question has one unambiguous section. `services/rag/tests/integration/test_golden_set.py` checks that every label names a section that ingestion really produces. Triage ground truth is still open.
- **Why:** with 4 runbooks, hand-written questions are cheap and more trustworthy than generated ones. Matching on section keeps recall@5 and MRR comparable across chunk sizes and with or without the reranker (scope/3).
- **Update (2026-11-01), corpus with distractors:** `data/runbooks/` grows to 30 runbooks in five formats (md, txt, html, docx, pdf). The golden set covers 10 of them (~1/3) with 91 questions; the other 20 are distractors. About a third of the new runbooks are *near misses* that share vocabulary with a covered one (MySQL next to PostgreSQL failover, internal mesh CA next to public TLS, 5xx rate next to p99 latency…), so retrieval has to tell look-alikes apart. A question that a near miss could also answer is rephrased to name its system ("before a PostgreSQL failover"). A few runbooks parse badly on purpose or by Docling quirk (a DOCX without heading styles, a PDF whose title is read as a table caption); none of them is in the golden set.
- **Why:** with 4 runbooks every question was found in the top 3 (recall@3 = 1.0), so the eval could not show a retrieval or reranker change (scope/3). With 30, recall@5 is 0.956 and MRR 0.841 (`data/eval_history.jsonl`).
- **Update (2026-11-01), triage ground truth:** `data/logs/ground_truth.json`, written by `scripts/generate_logs.py` along with the log (scope/2). It holds the generator's seed and the log's time span, plus one entry per seeded fault: `{id, fault_type, host, affected_hosts, start, end, expected_runbook}`, where `host` is the root cause, `affected_hosts` every host whose lines show the fault (root included; for `pg_primary_down`: `db-primary-01`, `db-replica-02`, `api-01..03`), and `expected_runbook` the runbook's `source` as `/search` returns it.
- **Matching rule (2026-11-01):** a ticket carries a `fault_type` chosen by the agent from a fixed list (a field of `TicketCreate`, added with the ticket contract), not inferred from its cited runbook, so the eval scores diagnosis and citation separately. A ticket matches a fault when it has the fault's type and its host is in the fault's `affected_hosts`. A fault is **detected** when at least one ticket matches it; further matching tickets are **duplicates**, reported as their own metric (not false positives, not hidden). A ticket that matches no fault is a false positive. Time is not part of the match: each fault type is seeded once and faults never overlap (scope/2).
- **Why:** per-host windows (agent/9) see a fault's symptoms, not always its cause: a silent primary only shows up on other hosts. The agent's system prompt says "if the evidence points to another host as the cause, name that host in the ticket"; the duplicate count shows whether it does.
- **Update (2026-11-01), categories and answers:** a golden-set question may carry a `category` (`lookup`, `exact_id`, `paraphrase`, `why`, `table`, `confusable`, `unanswerable`; default `uncategorized`) and `must_contain` (key facts). `scripts/run_eval.py` reports recall@k and MRR per category as well as overall, and with `--ask` scores `/ask`:
  - **false-answer rate** on `unanswerable` questions (no expected source or section, left out of retrieval): an answer instead of "I don't know" is a confident answer to something the runbooks don't cover, the main safety number;
  - **answer rate** and **citation accuracy** (a citation of the expected source and section) on answerable questions;
  - **key facts**: every `must_contain` string in the answer, case-insensitive.
  A rate with nothing to measure is `null`, not 0. A miss now names the runbook as well as the section it got.
- **State of the data (2026-11-02):** `data/golden_set.jsonl` has 91 questions, none with a `category` or `must_contain` yet: every question reports as `uncategorized`, and with no `unanswerable` questions the false-answer rate is `null`. `data/logs/ground_truth.json` exists, but no code scores a `TriageReport` against it yet (`tests/eval/` is empty), so the matching rule above is specified, not measured.

### scope/5 — Eval pass thresholds

**Status:** Open · **Priority:** P2

- Target numbers for recall@5, MRR (with vs without reranker, scope/3), and ticket precision/recall.
- **Note (2026-11-02):** the reranker comparison is gone with the reranker (scope/3, amended); ticket precision/recall needs the triage eval first (scope/4).

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
- **Amended (2026-11-01):** contracts may include **pure helpers that define a shared identity**, so two services can't disagree on it. First case: `fingerprint(fault_type, host)` (agent/3), used by the tickets store and the agent's in-memory ticket sink.
- **Names as built (2026-11-02):** the layout above used early names. The packages are `packages/common/src/siteops_common/`, `services/rag/src/siteops_rag/`, `services/agent/src/siteops_agent/` and `services/tickets/src/siteops_tickets/`, installed as `siteops-common`, `siteops-rag`, `siteops-agent` and `siteops-tickets`; services depend on `siteops-common = { workspace = true }`. Every Dockerfile copies all members' `pyproject.toml` (the workspace needs them to resolve the lock) but only `common` and its own service's code.

### arch/5 — Which of my own modules become separate services

**Status:** Decided (2026-10-27)

- **Rule:** a component becomes a service only when something requires it: a different resource profile (GPU / large model), independent scaling, multiple consumers, or data locality. Otherwise it stays a module inside a service.
- **Embedding:** off-the-shelf inference server (e.g. text-embeddings-inference, or the Ollama/vLLM embeddings endpoint) behind an HTTP `Embedder` adapter. No service code of my own.
- **Reranker:** module of the RAG pipeline, in-process behind its interface. Can move to an inference server (TEI `/rerank`) via arch/2 if evals justify it.
- **Ingestion:** a **job**, not a service: a one-off container (`docker compose run ingest`) that parses PDFs, writes to the vector store, and exits.
- **Cost:** inference servers are extra containers to run and health-check; their network calls inherit arch/1's costs.
- **As built (2026-11-02):**
  - **Embedding:** Ollama's OpenAI-compatible endpoint serving `bge-m3` (rag/5), through `embedder/openai_compat.py`; the same server runs the LLM.
  - **Reranker:** not built, cut from this version (scope/3, amended).
  - **Ingestion:** the compose service `ingest` in the `jobs` profile, so `up` doesn't start it: `make ingest` (`docker compose run --rm ingest`) runs `scripts/ingest.py` over `data/` and exits. Its image is rag's with the `docling` extra added; the rag API image doesn't carry Docling.

### arch/6 — MCP transport for the tickets server

**Status:** Decided (2026-10-27)

- **Streamable HTTP.** Tickets runs in its own container and serves any number of MCP clients at once.
- **Why not stdio:** stdio runs the server as a subprocess of the agent, so the tickets code and its dependencies would live inside the agent image (breaks arch/4) and its lifecycle would be tied to the agent's.
- **Non-AI clients:** if needed later, add a REST endpoint next to MCP, backed by the same store.
- **Cost:** it is a network endpoint, so it needs auth (API key) and timeouts/retries.
- **Tests:** unit tests call the tool functions directly or use an in-memory MCP client; one integration test over real HTTP.
- **Implemented (2026-11-01):** official `mcp` SDK 2.x (`MCPServer`), one tool `create_ticket(ticket: TicketCreate) -> CreateTicketResult` at `/mcp`, schemas from `common`. **Stateless, JSON responses:** one tool call is one request, so no session or SSE stream to keep, and any number of replicas can serve it. The SDK's DNS-rebinding check (Host header must be localhost) is on only when bound to 127.0.0.1; the container binds 0.0.0.0, where the API key (agent/5) is the access check. The HTTP-level tests use the ASGI app in process; the test over a real socket comes with the agent's MCP client.

### arch/7 — Timeout and retry policy for network calls

**Status:** Decided (2026-11-01)

- Per-call timeouts, which calls retry (idempotent only?), backoff. Answers arch/1's cost line.
- **Decision (the agent's clients):**
  - rag client: 10 s timeout, 2 retries on connection errors (`/search` is read-only).
  - MCP ticket sink: 10 s timeout, 2 retries on connection errors; safe because `create_ticket` is deduplicated by the server (agent/3).
  - model client: 120 s timeout, no retries (a slow model call is retried by nobody; the window ends unresolved).
- **Implemented (2026-11-01):** `HttpRagClient` and `McpTicketSink` retry only errors where the request never reached the service (connect error or connect timeout); a read timeout, an error status or an invalid reply is not retried. Both raise their own error type (`RagError`, `TicketSinkError`), so the triage loop marks the window `error` without knowing the transport.
- **Why:** simple fixed values; only calls that are safe to repeat are retried.
- **Cost:** no backoff tuning or circuit breaker; a rag outage makes every window of a run slow to fail.

### arch/8 — Persistence: Docker volumes for Qdrant and SQLite

**Status:** Decided (2026-11-02, as built)

- Named volumes vs bind mounts; what survives `docker compose down`.
- **Tickets, as built (2026-11-01):** named volume `tickets-data` mounted at `/data` (`TICKETS_DB_PATH=/data/tickets.db`): survives `down` and restarts (checked: dedup holds across a restart), removed by `down -v`. Qdrant still open.
- **Decision (2026-11-02): named volumes for what services write, read-only bind mounts for what they read.**
  - Named: `qdrant-data` (the ingested runbooks), `tickets-data` (the SQLite file), `docling-cache` (Docling's models, for the `ingest` job) and, with the GPU overlay, `ollama-models`. All survive `make down`; `docker compose down -v` removes them.
  - Bind-mounted read-only: `data/` into the `ingest` job, `data/logs/` into the agent (`site.log`, written on the host by `make generate-logs`).
- **Not persisted:** triage runs and their reports, kept in the agent's memory (agent/8). Losing a volume costs a re-ingest or the ticket history, both reproducible (arch/10).

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

### arch/12 — `docker compose up` gives a fully working environment

**Status:** Decided (2026-11-02, as built)

- **Goal:** `docker compose up` alone (plus a `.env` with the secrets, design/4) starts everything `/ask` and `/triage` need, end to end, with no service run by hand.
- **Then (2026-11-01):** only `tickets` is in `docker-compose.yml`. rag's image reads `config.docker.yaml`, which assumes a `qdrant` service on the compose network and Ollama on the host at `host.docker.internal:11434`.
- **To add:** `rag`, `qdrant` (with its volume, arch/8), the `ingest` job (`docker compose run ingest`, arch/5), `agent` once its container exists, and health checks with `depends_on` so services start in order.
- **To decide:** Ollama in compose (self-contained, but a 27B model needs a GPU passed through) or on the host (`extra_hosts: host-gateway` on Linux); whether ingestion runs automatically on first start or stays a manual `run`.
- **Decision (2026-11-02), as built:**
  - **The stack:** `qdrant`, `rag`, `tickets`, `agent`, an `ollama` entry and the `ingest` job (arch/5). Health checks and `depends_on` start them in order (qdrant → rag; rag and tickets → agent); `make up` is `docker compose up -d --build --wait`. Every port is published on 127.0.0.1 only, defaults overridable in `.env`.
  - **The model server stays on the host by default.** The `ollama` service is a TCP forwarder (`alpine/socat`) to the host's Ollama, or to any OpenAI-compatible server set in `LLM_UPSTREAM`. rag and the agent call it `ollama.internal`, since a host Ollama refuses requests whose Host header is `ollama`. `docker-compose.gpu.yml` replaces it with Ollama itself on an NVIDIA GPU (`make up-gpu`, then `make models` once).
  - **Ingestion stays manual:** `make ingest`, once or when the runbooks change. **Logs** are generated on the host (`make generate-logs`) and mounted into the agent.
  - **Secrets:** one root `.env` (from `.env.example`) holds `TICKETS_API_KEY` and `AGENT_API_KEY`; compose passes them to each service under its own names (design/4). Each image reads its `config.docker.yaml`.
  - `tests/test_stack.py` fails if the configs, Dockerfiles, port mappings and health checks disagree on ports, service names, aliases, mounts or models.
- **Cost:** `make up` alone isn't self-contained: it needs a model server on the host (or `up-gpu`), the models pulled, `make ingest` once and `make generate-logs` before the agent can triage.

---

# 3. Code design & dependency injection

### design/1 — Every external technology sits behind an interface

**Status:** Decided (2026-10-27)

- Interfaces: `DocumentParser`, `Embedder`, `VectorStore`, `Reranker`, `ChatModel`, `TicketSink`, `LogSource`.
- Own pure logic (chunker, log parser, prefilter) stays concrete — nothing to swap.
- **Why:** no component depends on a single technology; tests run on fakes/in-memory adapters.
- **Cost:** more files; each interface must have ≥2 implementations (one real, one fake) to justify existing. This rule is what stops speculative interfaces; it also keeps implementation details from leaking into the interface and makes testing easy.
- **Amended (2026-11-01): a framework's own abstraction counts as the interface.** When a framework we adopt already ships an interface with real and fake implementations, we use it instead of wrapping it in our own ABC. First case: the agent's chat model is LangChain's `BaseChatModel` (agent/4); production uses its OpenAI-compatible / Ollama implementation, tests use `langchain_core`'s fake chat models. The agent has no `llm/` package. The rag service keeps its own `llm/` ABC, since it uses no framework.
- **Interfaces as built (2026-11-02):** rag: `AbstractDocumentConverter` (`converter/`: Docling, and a plain-text one as the light second), `Embedder`, `VectorStore`, `ChatModel` (`llm/`). Agent: `RagClient`, `TicketSink`, `LogSource`, and `LogParser` (one adapter, agent/9's exception). `Reranker` is gone with the reranker (scope/3, amended).

### design/2 — Interfaces via `abc.ABC` (not `typing.Protocol`)

**Status:** Decided (2026-10-27)

- **Why:** explicit dependency — each adapter declares the interface it implements, and a missing method fails at instantiation instead of at first call.
- **Cost:** every adapter imports and subclasses the interface, so adapters depend on core. Changing an interface means changing every adapter.

### design/3 — Single composition root

**Status:** Decided (2026-11-02, as built)

- Only one place (`container.py` / factory) reads env and instantiates concrete adapters; everything else receives dependencies via `__init__`.
- **As built:** rag's `container.py` (`build_rag` for the API, `build_pipeline` for ingestion) and the agent's (`build_job` for the CLI and the API's runs, `build_api` for the HTTP app) are the only code that picks adapters. Optional extras are imported inside them, so a module never imports an adapter it might not have (tooling/2). The tickets service is small enough that `server.py` is both its entry point and its root. An entry point may load `Settings` itself to pass them in and to read its own host and port (`api/main.py`).

### design/4 — Configuration: YAML for settings, `.env` for secrets only

**Status:** Decided (2026-11-01)

- Only the composition root (design/3) reads settings, through `pydantic-settings`.
- **YAML is the only source of non-secret settings.** Each service's committed `config.yaml` holds every URL, model name, hyperparameter, timeout, retry count, path and port. Env vars do not override them. A deployment that needs other values (a container reaching `qdrant:6333` instead of `localhost:6333`, a test pointing at its own server) gets its own YAML file, selected with `<SERVICE>_CONFIG_FILE` (e.g. `RAG_CONFIG_FILE`), which is a path, not a setting.
- **Secrets come only from `.env`** (uncommitted, `services/<service>/.env`) or from env vars of the same name, which is how compose and CI inject them. Secrets are never in the YAML: a secret key there is an error. A committed `.env.example` lists the secret names with empty values.
- **No code defaults.** A setting missing from its source fails at startup instead of silently using a value nobody chose. The exceptions are `retrieval.top_k` (rag/7) and the rag service's model-server API keys, which default to `"EMPTY"` because local servers (Ollama, vLLM) ignore them. Adapter constructors take these values without defaults: the composition root always passes them.
- **Values that stay in code:** the prefilter's context sizes, window cap and `RULES` (agent/9, agent/12); the wire contracts' limits (`top_k` ≤ 50, ticket field lengths), which are part of the API, not tuning.
- **Why:** one place to read the whole configuration of a service, reviewed in git; secrets kept out of it.
- **Cost:** every environment that differs needs its own YAML file instead of a one-line env override.
- **Implemented (2026-11-01)** in all three services' `config.py`: env vars and `.env` are wrapped so that only the keys in `SECRETS` pass (any other key is an error), and the YAML source refuses those keys. Secrets: `RAG_EMBEDDING__API_KEY`, `RAG_LLM__API_KEY`, `TICKETS_API_KEY`, `AGENT_TICKETS__API_KEY`, listed in each service's `.env.example`. The rag and tickets images use their `config.docker.yaml` (`RAG_CONFIG_FILE`, `TICKETS_CONFIG_FILE`); rag's reaches Ollama on the host (`host.docker.internal:11434`) and Qdrant as `qdrant:6333`, and a test keeps its non-URL values equal to `config.yaml`'s.
- **Amended (2026-11-02), the agent:** its `container.py` reads the `Settings`; its secrets are `AGENT_TICKETS__API_KEY` and `AGENT_API__API_KEY` (the API's, optional for the CLI). Its image reads `config.docker.yaml` (`AGENT_CONFIG_FILE`), which differs from `config.yaml` only in where rag, tickets, the model and the log file are. Under compose, a single root `.env` holds the secrets and compose maps them to each service's names (`TICKETS_API_KEY` → `AGENT_TICKETS__API_KEY` too, `AGENT_API_KEY` → `AGENT_API__API_KEY`, arch/12).

---

# 4. RAG pipeline: ingestion, retrieval & generation

### rag/1 — Vector store: Qdrant (not Pinecone)

**Status:** Decided (2026-11-02, as built)

- **Why:** on-prem, matches target stack, `:memory:` mode for tests. Pinecone is SaaS-only.
- **As built:** Qdrant 1.20 (server image and `qdrant-client`), behind `VectorStore`, with `InMemoryVectorStore` as the fake; `vector_store.backend: memory | qdrant` picks one. One contract suite runs on the memory store, Qdrant `:memory:`, and a Qdrant server when one is running (tooling/5).

### rag/2 — Chunker consumes own `Element` IR, not Docling objects

**Status:** Decided (2026-11-02, as built)

- `Element(kind=heading|paragraph|table, text, page)`; `DocumentParser` adapters produce it.
- **Why:** chunker is pure and unit-testable without PDFs or Docling installed.
- **As built:** `Element(kind, text, page, level, rows, language)` in `models.py`, with kinds `heading`, `paragraph`, `table`, `code` and `caption`; `level` for headings (0 = the title), `rows` for tables, `language` for code. The interface is `AbstractDocumentConverter.convert_to_elements`. Ingestion uses the Docling converter for all five formats (md, txt, html, docx, pdf); `converter/text.py` handles plain text without Docling.

### rag/3 — Deterministic `chunk_id`

**Status:** Decided (2026-11-02, as built)

- Derived from content + source (hash), so re-ingestion upserts instead of duplicating.
- **As built:** `uuid5` of `source | heading path | text` under a fixed namespace (`chunking.py`): a valid Qdrant point ID, and stable across runs and machines. Stale chunks of an edited document are removed by rag/12.

### rag/4 — LLM: model and serving runtime

**Status:** Decided (2026-10-31)

- Which model, served by vLLM, Ollama or a proxy. Fixes the `llm` container in `docker-compose.yml` and what `llm.py`'s OpenAI-compatible client points at.
- **Decision:** `qwen3.5:27b` served by **Ollama**, reached through its OpenAI-compatible API (`llm.base_url: http://localhost:11434/v1`). The code only speaks that API, so changing server or model is config only.

### rag/5 — Embedding model

**Status:** Decided (2026-11-02, as built)

- Which model, and its vector size (fixes the Qdrant collection). Whether it runs in-process or as a service is arch/5.
- **As built:** `bge-m3`, 1024 dimensions, served by Ollama through its OpenAI-compatible API (the same server as the LLM, rag/4), batches of 32 (`embedding:` in `config.yaml`). On the golden set: recall@5 0.956, recall@10 0.989, MRR 0.841 at k = 5 (`data/eval_history.jsonl`).
- **Cost:** changing the model or its dimension means recreating the collection and re-ingesting. No other model was compared.

### rag/6 — Chunking strategy

**Status:** Decided (2026-11-02, as built)

- Split by heading/section vs fixed size; size and overlap; tables kept whole or split.
- **As built (`Chunker`, `chunking.py`):** by section, up to `chunking.max_length` = 1000 characters, the heading prefix included (rag/14). Phase 1 splits each element that doesn't fit into pieces, never mid-sentence, mid-row or inside a code block; phase 2 packs the consecutive pieces of one section into as few chunks as fit. A chunk never spans two sections. Tables are split by rows with the header repeated in every chunk; a row too long for table form becomes one `column: value` line per cell. **No overlap**: every chunk carries its heading path instead.
- **Cost:** a section's start and end can land in different chunks with no shared text. No other size was compared on the eval.

### rag/7 — Retrieval depth

**Status:** Decided (2026-11-01)

- k fetched from Qdrant before reranking, k kept after.
- **Where k is set (2026-11-01):** `retrieval.top_k` in `services/rag/config.yaml` (1–50) is used when a `/search` or `/ask` request sends no `top_k`; the contracts' `top_k` is optional (`None` = the service's default), so the number lives in one place. `scripts/run_eval.py` uses the same value unless `--top-k` is given. On the 91-question golden set, recall@5 is 0.956 and recall@10 0.989.
- **Decision (2026-11-01): the default is 10**, in `config.yaml` and as the code default when there is no file. Recall@10 is 0.989 against 0.956 at 5: about 3 more questions in 100 get the right runbook into the context.
- **Cost:** twice the context for `/ask`, so slower answers on the local model. The triage agent's `search_runbooks` can still ask for fewer (the probe used 3). How many chunks the reranker keeps is rag/8's.
- **Amended (2026-11-02):** with no reranker (scope/3, amended), the k fetched is the k given to the LLM. The agent asks for `hits_per_search` = 3 per search (`triage:` in its `config.yaml`).

### rag/8 — Reranker model

**Status:** Deferred (2026-11-02)

- Which cross-encoder (or alternative). Value proven per scope/3.
- Deferred with the reranker itself (scope/3, amended).

### rag/9 — "I don't know" rule

**Status:** Decided (2026-11-02, as built)

- Score threshold on retrieved chunks, LLM judgment, or both.
- **Recommendation (implemented in `rag.py`):** LLM judgment, checked. Nothing retrieved → "I don't know" without calling the LLM; otherwise the answer counts only if at least one of its citations passes rag/10's check. `AskResponse.answered` carries the result.
- **False premise (implemented in `SYSTEM_PROMPT`):** when the excerpts contradict what the question assumes ("since it's fine to delete /var/log/audit…"), the model answers with the correction instead of "I don't know". It goes through the same citation check, so it can't be invented.
- **Why:** no score threshold until scope/5's eval gives numbers to set one from; the citation check stops an answer the excerpts don't support.
- **Cost:** a correct answer with a badly copied quote is reported as "I don't know".

### rag/10 — Citation format and check

**Status:** Decided (2026-11-02, as built)

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

### rag/14 — Every heading path starts with the document's identity

**Status:** Decided (2026-11-01)

- **Problem:** a chunk's prefix (rag/2, the heading path that starts every chunk and is embedded with it) named only its sections when the converter found no level-0 title: `5. Escalation`, or nothing at all. Every runbook has an escalation table, so those chunks read, and embedded, alike, and retrieval couldn't tell them apart (the escalation misses of the golden set).
- **Decision:** the chunker starts every heading path with the document's title, its first non-blank level-0 heading, or its filename when it has none. Content before the title line is under the title too. Section headings at level 1 never replace it. A blank heading is ignored.
- **Today:** 28 of the 30 runbooks already had a title. `runbook_bastion_lockout.docx` (no headings recognised at all) and `runbook_log_shipping_broken.pdf` (sections, no title) now get their filename. In both the real title is the first paragraph, which the converter doesn't tag as a heading. Recognising it is a converter fix, left for later.
- **Golden-set labels are unchanged:** they name the last element of the path, and the integration test checks that every one still exists after ingestion.
- **Cost:** the prefix takes room from each chunk's text (a long title means more, smaller chunks), and chunk ids change for the affected documents, so `data/runbooks` must be re-ingested (rag/12 replaces the stale chunks).

---

# 5. Agent, access & safety

### agent/1 — Agent design: single agent + deterministic pre-filter; logs are untrusted

**Status:** Decided (2026-11-01)

- Regex/threshold pre-filter flags windows; LLM reasons only on flagged windows.
- Agent's only side effect is `create_ticket`, deduplicated by fault fingerprint (prompt-injection blast radius).

### agent/2 — Access-level model

**Status:** Open · **Priority:** P1

- Which levels exist, where a caller's level comes from, and how it's stored in the Qdrant payload (the filter lives in `qdrant_store.py`). Affects the chunk schema, so it's needed before ingestion.

### agent/3 — Ticket fingerprint and dedup window

**Status:** Decided (2026-11-01)

- Which fields define "the same fault" (agent/1), and for how long a duplicate is suppressed.
- **Decision:** fingerprint = `(fault_type, host)` of the `TicketCreate` (agent/10). A ticket whose fingerprint matches one created in the last **60 minutes** is not stored: `create_ticket` returns the existing ticket with `created=False`.
- **Enforced by the tickets service**, not the agent: it is the boundary a prompt-injected agent can't get around (agent/1). The agent's in-memory ticket sink applies the same rule, so its tests see the same behaviour.
- **Why:** a fault keeps logging for its whole duration and can open several windows (on one host, or on each affected host), each ending in a ticket attempt; one ticket per fault and host is what an on-call engineer wants. A retried `create_ticket` is then harmless too (arch/7).
- **Cost:** two different faults of the same type on the same host within an hour become one ticket. Symptoms of one fault on several hosts still give one ticket per host; scope/4 counts those as duplicates, and naming the root host (agent/10's `host`) is how the agent avoids them.

### agent/4 — Agent loop: implementation and limits

**Status:** Decided (2026-11-01)

- Hand-written loop vs framework; max steps, tool-call budget, timeout.
- **Decision:** triage is a **LangGraph `StateGraph`**: a workflow, not an open agent. Code fixes the steps (read logs → parse → prefilter → for each window: diagnose → decide → validate → act → report); the LLM is called only inside a window, with a narrow tool set (`search_runbooks`, the narrow `create_ticket` and `dismiss` of agent/10), and every window ends in a ticket or a dismissal.
- **Amended (2026-11-01): only the per-window loop is a graph.** The run (read → parse → prefilter → each window in turn → report) is plain sequential Python with no decision in it, so it stays framework-free and testable without LangGraph; it receives the per-window step as a callable. The one `StateGraph` is the window's: `diagnose ⇄ tools` until `create_ticket` or `dismiss` succeeds. Windows are processed one at a time.
- **Before building it:** check that the model calls tools reliably (two or three real windows through `bind_tools`, thinking off): `search_runbooks`, then `create_ticket` or `dismiss`, with valid arguments. If it often answers in plain text, the window ends with a structured-output `decide` step instead of terminal tools.
  - **Result (2026-11-01): terminal tools kept.** qwen3.5:27b on Ollama, thinking off, temperature 0, real retrieval, 4 hand-built seed-42 windows (high_latency, pg_primary_down seen from the replica, pod_oomkilled, the ERROR 504 decoy) × 2 runs: 8/8 as expected, every step a tool call, no invalid arguments, the same result on both runs, 4–24 s per window. It named the root host (`db-primary-01`) from the replica's lines and dismissed the decoy. One wrong citation: high_latency cited `runbook_search_latency.txt` after a third search, though its first search returned `runbook_high_latency.txt`. Citation quality is for the eval (scope/4), not a reason to change the loop.
- **Repeatability:** temperature 0 and a fixed seed make runs *more* repeatable, not deterministic (GPU inference still varies). Idempotency comes from the fixed `[since, until)` range and the server's dedup (agent/3), not from the model.
- The model is LangChain's `BaseChatModel` (design/1, amended); tests use `langchain_core`'s fake chat models.
- Tools are written once, as LangChain tools, so the chat agent (agent/11) can reuse them later.
- **Report (implemented, `report.py`):** a run returns a `TriageReport` (range, model, events read, unparsable, `totals` by status) with one `WindowOutcome` per window: its id (`Window.id`, a hash of host, rule and span, stable across runs), host, rule and span, a status (`ticketed`, `deduplicated`, `dismissed`, `unresolved`, `error`) with its details (draft and ticket id, dismissal, or error), the searches made and the chunks they returned, rejected `create_ticket` calls, LLM calls and duration. The eval scores this report, not the tickets store.
- **Limits:** `max_steps_per_window` (graph recursion limit) and `max_searches_per_window`; a window that exhausts its budget is reported as unresolved, never ticketed. The values are set when the graph is built. A cap on LLM calls per run and a per-window timeout are added only if a run misbehaves.
  - **As built (2026-11-02):** the budget is `max_llm_calls` per window (8), with the graph's recursion limit derived from it (`2 × max_llm_calls + 2`), plus `max_searches` (3), `max_log_queries` (2) and `hits_per_search` (3), all under `triage:` in `config.yaml`. A model that answers in plain text gets one reminder before the window ends unresolved.
- **The window's tools (2026-11-01):** `TriageTools` (`triage_tools.py`, CORE) is made fresh for every window and holds its memory: the lines shown and their numbers, the passages retrieved (only those may be cited), the searches and lookups made, the refused drafts and the decision. Its four methods are the tools; `tools.py` (FLOW) hands them to LangChain with their descriptions and their schemas as JSON, so the raw arguments reach `TriageTools`, which validates them itself.
  - **Two kinds of failure, kept apart:** the model's mistake (invalid arguments, a failed check, a budget used up) comes back to it as a refusal in plain words, and the window goes on; ours (rag or tickets down) raises `ToolFailure` and ends the window as `error`. The tool probe showed why: a failure in our sink, sent back as "try again", made the model retry nine times for nothing.
  - **Budgets per window** (`ToolLimits`, values from the config): searches, `log_context` calls, hits per search. A used-up budget is a refusal ("decide with what you have"), not an error.
- **Why:** triage must be predictable and measurable (scope/4): the LLM fills structured fields, code decides what happens next. LangGraph gives the per-window loop, the recursion limit and tracing without hand-writing them, and is a common industry choice.
- **Cost:** a framework dependency in the agent image and its abstractions to learn; the loop's behaviour is partly the framework's, so its version is pinned and the graph is tested on fake models.

### agent/5 — Auth for the API and the MCP server

**Status:** Decided (2026-11-02, as built)

- API key only? How keys are issued, stored and checked (the tickets store already plans an API-key check).
- **Recommendation (implemented in the tickets service):** one shared key, `TICKETS_API_KEY`, from the environment (compose takes it from the shell or a git-ignored `.env`), at least 16 characters; the service refuses to start without it. Clients send `Authorization: Bearer <key>`; an ASGI middleware compares it in constant time (`hmac.compare_digest`) and answers 401 otherwise. `/health` needs no key. The port is published on 127.0.0.1 only.
- **Why:** the only client is the agent, inside the compose network; per-client keys, rotation and scopes belong at the edge (arch/11).
- **Cost:** one key for every client: no per-client revocation or audit of who opened a ticket. Rotating it means restarting the service and the agent.
- **The agent's API (2026-11-01)** uses the same scheme: `AGENT_API__API_KEY` (a secret, design/4), `Authorization: Bearer`, compared in constant time, `/health` exempt. Only `api.py` needs it, so the setting is optional and the CLI runs without it; the API refuses to start without one.
- **As built (2026-11-02):** both services check the key in an ASGI middleware and answer `401` without it (the agent's with `{"detail", "code": "unauthorized"}`). rag's `/search` and `/ask` have no key: rag is reached only on the compose network and on 127.0.0.1, and its only clients are the agent and the developer.

### agent/6 — Scheduled / watcher-triggered triage

**Status:** Open · **Priority:** P3 (cut candidate)

- On-demand `/triage` only vs a scheduler or log watcher.
- **Note (2026-11-01):** there is no checkpoint store ("last run went up to here"). A run takes an explicit `[since, until)` range and relies on the server's dedup (agent/3) for overlaps; a saved checkpoint belongs here, with the scheduler.

### agent/7 — RAG and agent are separate services

**Status:** Decided (2026-10-27)

- Services I write: `rag`, `agent`, `tickets`. Layout adds `services/agent/` (arch/4).
- **rag:** owns retrieval and generation and sits next to the vector store. Exposes `/search` (ranked chunks with metadata) and `/ask` (answer with citations). Serves two consumers: engineers (Ask) and the agent (Triage).
- **agent:** a pure orchestrator. Every tool is a network call: `search_runbooks` → rag `/search`, `create_ticket` → tickets via MCP, `read_logs` → log source. No model or vector-store dependencies, so the image is small.
- **Why:** RAG has multiple consumers and belongs next to its data. The agent has a different runtime (scheduled loop vs request/response). **Security isolation:** the agent processes untrusted logs, so a prompt-injected agent can only query rag and create (deduplicated) tickets. It cannot reach Qdrant, ingestion, or anything else.
- **Contracts:** `/search` and `/ask` request/response models live in `common` (arch/4).
- **Cost:** a third service to build, deploy and test; agent → rag adds a network hop with its own timeouts and failure handling.

### agent/8 — The agent is the users' front door (amended)

**Status:** Decided (2026-10-27), amended (2026-11-01)

- Design unchanged: one agent, two modes on the same tools: **triage** (trigger: CLI / schedule) and **chat** (user request).
- **This version ships triage only** (`triage --since …`, optional `POST /triage`). `/chat` is deferred.
- **Why:** triage is measurable (seeded faults → precision/recall); chat has no ground truth yet, and adds user text as a second untrusted input to prompt-harden and test. Time budget before the interview goes to the measurable mode.
- **Next step:** `/chat` = the same agent factory with a chat system prompt and the conversation history from the client (stateless). Tools, rag and tickets unchanged.
- rag `/ask` remains the direct Q&A endpoint for users meanwhile.
- **Amended (2026-11-01), chat is in scope:** the frontend's chat goes through the agent's API (agent/15). The engine behind it (agent/11) is built with the triage graph; until then `/chat` runs on a fake engine in tests only.
- **Amended (2026-11-01), `POST /triage` is in scope:** the agent's HTTP API (`api.py`) runs the same job as the CLI.
  - A run takes minutes, so `POST /triage {since, until, dry_run}` queues it and answers `202 {run_id}` at once; `GET /triage/{run_id}` returns its state (`queued`, `running`, `done` with the `TriageReport`, `failed` with the error) and `GET /triage` the recent runs.
  - **One run at a time, in order:** one worker thread takes queued runs first in, first out. Parallel runs would share one model server: they wouldn't finish sooner and their calls would hit the 120 s timeout (arch/7).
  - **A queue of 5:** up to 5 runs wait behind the running one; one more gets `503` with `Retry-After: 60` (the server is at capacity, the request isn't at fault). The same range submitted twice is two runs; the tickets service's dedup (agent/3) keeps the second from filing duplicates.
  - **One process only:** the queue and the registry are in memory, so several API workers or replicas would each have their own. Scaling out would need a shared queue (a database table, or Redis).
  - `GET /windows?since&until` previews the prefilter's windows (counts, no log text, no LLM): the debugging entry point.
  - Runs are kept in memory (the last 50; past that the oldest finished ones are dropped, never a queued or running one): a restart forgets them, not their tickets. Writing reports to a volume is arch/8's, not done.
  - Routes only: the work is `TriageRuns` (`runs.py`) and the job built by `container.py`; handlers are plain `def`, so the synchronous graph and the MCP sink's own event loop run off the server's loop.
- **Amended (2026-11-02), chat out again:** `/chat` and its engine are removed until one is built (agent/15, amended; agent/11 stays Proposed). This version's API is `/health`, `/triage` and `/windows`. It is the `api/` package (one router per area), built by `container.build_api`, started by `api/main.py`, and runs in compose as the `agent` service (arch/12).

### agent/9 — Log parser and prefilter: contract

**Status:** Decided (2026-11-01), amended (2026-11-01)

- **Pure logic:** no I/O, no LLM, same input → same output. `LogEvent` and `Window` live in `models.py`, shared with the agent and its tools.
- **Line format:** the generator's (scope/2), `<ISO 8601 timestamp with zone, ms optional> <LEVEL> <host> <component>: <message>`. A message may hold `key=value` fields; the parser extracts them when present (the generator writes none today: `POST /v2/orders 201 1168ms`). Levels: DEBUG, INFO, WARN, ERROR, CRITICAL (the generator emits fewer).
- **Parser is an ABC + one adapter per line format (amended):** `log_parser/base.py` holds `LogParser`, whose guarantees every parser shares (below); `log_parser/syslog.py` holds `SyslogStyleParser`, the generator's format. The ABC exists to state that contract once, not as a swap point: a log format is an external technology like a document format (`converter/`), so a second one (e.g. JSON lines) is a new adapter, not a change to callers. **Exception to design/1:** one implementation and no fake, since the real parser is already pure and fast. The prefilter stays concrete.
- **Parser:** `LogParser.parse_line(line) -> LogEvent | None`, `LogParser.parse_lines(lines) -> (events, unparsable_count)`. Never raises: a malformed line, an unknown level included, gives `None` and is counted; the count goes in the triage summary, since a jump in it is itself a signal. Time is always timezone-aware UTC (correct `--since` comparisons). `raw` is the line unchanged (minus its line ending), quoted as the ticket's evidence. Colons and `=` inside text are not mangled. A timestamp without a zone is malformed (the zone is never guessed); blank lines are skipped, not counted; a repeated key keeps its first value.
- **Prefilter:** `find_windows(events, rules) -> list[Window]` selects where the LLM looks; it does not diagnose. `Window.rule` is a hint, not a verdict.
  - Rule kinds: **pattern** (one matching line fires), **threshold** (`min_count` matches `within` a span, e.g. a request slower than 800 ms five times in 5 minutes) and a **catch-all** `unknown`, last in the list, taking ERROR and CRITICAL events no other rule matched, so a fault without a rule still reaches the agent.
  - An event goes to the first rule in list order that matches it.
  - Windows group by (host, rule); matches merge when the gap between them is at most the rule's `within`. A threshold window starts at the first event of the run that reached `min_count`.
  - Context: the 5 events of the same host before the first match, the 2 after the last.
  - At most 50 events per window, the first and the last ones; `Window.omitted` counts the rest. Every event goes into the prompt, so this bounds its size.
  - Windows are sorted by start time; same events → same windows (reproducible eval).
  - Pure INFO noise gives no windows. Decoys (scope/2) may: the ERROR 504 one gives an `unknown` window, which the agent should decline to ticket.
- **Why:** cost (the LLM sees a few windows, not 10,000 lines); testability (rules have exact, fast tests, only the diagnosis is left to the LLM); security (most untrusted log text never reaches the model, agent/1).
- **Cost:** triage recall is capped by the prefilter: a fault no rule matches and that logs no ERROR is invisible. That is why recall is measured end to end (scope/4).
- **Known limitation:** a host that goes silent produces no window. Detecting a *missing* signal needs another kind of detector (a scan over time per host, not a check per event); it is the next rule type, not built. `pg_primary_down` is caught through its symptoms on other hosts instead, and scope/4's `affected_hosts` makes that count.

### agent/10 — Ticket contract

**Status:** Decided (2026-11-01)

- In `common` (arch/4): `FaultType`, `TicketCreate` (what the agent sends), `Ticket` (plus `id`, `created_at`, `fingerprint`, set by the tickets service) and `CreateTicketResult` (`created=False` = deduplicated, agent/3).
- `FaultType` = the seven seeded faults (scope/2) + `"other"`. One list for the agent's prompt, the tickets service and the eval (scope/4); a test checks it matches the generator's.
- `TicketCreate`: `fault_type`, `host` (root cause, may differ from the window's host), `severity` (SEV-1..3, the runbooks' scale), `title`, `summary` (incident report), **`suggested_fix`** (the agent's proposed remediation), `evidence` (raw log lines), `runbook` (chunk_id, source, heading path; `None` when none fits), `window_start` / `window_end` (timezone-aware, in order).
- **`suggested_fix` is advice for a human, never executed** by any service. It is written by an agent that reads untrusted logs, so an injected log line could steer it ("run rm -rf …"): the agent loop (agent/4) should ground it in the cited runbook, and readers must treat it as unverified.
- Every text field and list is bounded (title 200, summary and fix 4000, 1–20 evidence lines of ≤2000 chars): limits what a prompt-injected agent can write.
- **The LLM never sees this contract (2026-11-01).** The MCP tool's schema is the full `TicketCreate`; handing it to the model would let it write facts (evidence text, window times, citations). The agent gives its LLM its own narrow `create_ticket` tool instead:
  - **the LLM fills** the judgement: `fault_type`, `severity`, `title`, `summary`, `suggested_fix`, `host`, `evidence` as **line numbers into the window** (it can only point at real lines), `runbook` as a **chunk_id from a search it made in this loop**;
  - **code checks** that `host` appears in the window's events (it may be the root cause, not the symptom host, scope/4), that every line number exists and the chunk_id was retrieved; a failed check goes back to the model as a tool error;
  - **code fills** the facts: window times, evidence text (the events' `raw`), the full `RunbookRef` from the retrieved hit; then it calls the MCP tool through a plain `mcp` client session (no MCP-to-LLM adapter).
  - **Implemented** in `decision.py` (`TicketDraft`, `Dismissal`, `check_draft`, `to_ticket_create`) and `prompts.py` (line numbers). A host counts as in the window if it wrote a line or a line names it as a whole word; every failed check is reported at once; an evidence line longer than 2000 characters is cut and marked `…`.
  - **Amended (2026-11-01), lines shown:** evidence numbers and the host check cover every line *shown* to the model for the window, not only the window's: `ShownLines` numbers the window 1 to N and each `log_context` result after that (agent/14). The ticket's `window_start` / `window_end` stay the window's span.
  - **Amended (2026-11-01), a real server:** the `host` must also be a host that wrote a line somewhere in the run (`known_hosts`). Being named in a line was not enough: the tool probe's model filed `orders-db`, a service named in the API hosts' messages, so those tickets matched no fault (scope/4).
  - **Every tool calls our backend, never the materials (2026-11-01):** `search_runbooks` and `runbook_context` go through `RagClient` to rag (never Qdrant), `log_context` through `LogContext` over the events this run read (never the file or the source), `create_ticket` through `TicketSink` to the tickets service (never SQLite). Limits and checks live in that backend code, where the model can't skip them.
  - The narrow tool's description is the agent's (e.g. "use `dismiss` for transient or harmless events"); the MCP tool's description doesn't name agent tools, since any client may call it.
- **Rejected (2026-11-01): code fixes `host` to the window's host and `fault_type` to the window's rule.** It would take dedup out of the model's hands, but a rule is only a hint (agent/9) and a fault's symptoms show up on other hosts: `pg_primary_down` would give one ticket per symptom host (agent/3, scope/4). The model's reach stays bounded instead: `host` must appear in the window, `fault_type` is one of the `FaultType` values, and the eval counts duplicates.
- **Why:** `fault_type` is chosen by the agent, not inferred from its citation, so the eval scores diagnosis and citation separately (scope/4). `runbook` is optional because a forced citation would be an invented one.
- **Cost:** adding a fault type means changing `common`, hence rebuilding every image (arch/4). The fingerprint's composition is agent/3's; `common` defines it once, `fingerprint(fault_type, host)` (arch/4).

### agent/11 — Chat agent: LangChain `create_agent`

**Status:** Proposed

- For `/chat`, deferred by agent/8: nothing of it is built in this version.
- **Recommendation:** an open tool-calling agent, LangChain's `create_agent`, on the same model and the same tools as triage (agent/4), with its own system prompt and limits. Its tools are read-only by default (`search_runbooks`, a bounded log query, a ticket lookup); creating a ticket from chat would need the user's confirmation (human-in-the-loop interrupt). Conversation history comes from the client (stateless, agent/8).
- **Why:** user questions have no fixed path, so the model chooses the tools; triage's guarantees come from its graph, which chat doesn't need.
- **Cost:** user text is a second untrusted input (agent/8); who may ask what depends on agent/2.

### agent/12 — Prefilter rules

**Status:** Decided (2026-11-01)

- The rules `find_windows` (agent/9) runs on, in this order; an event goes to the first rule whose component, level and pattern match it (a threshold rule consumes the event even when its count is never reached).
- **One shape:** `Rule(name, pattern, components=None, min_level, min_count=1, within)`. A pattern rule is `min_count=1`; a threshold rule fires when `min_count` matches fall within `within`.

| # | Rule | Component · level · message pattern | `min_count` / `within` | Seeded fault (scope/2) |
|---|---|---|---|---|
| 1 | `db_leader_unreachable` | `patroni` · WARN+ · `unreachable\|Connection refused\|no automatic failover` | 1 / 10 min | pg_primary_down (replica) |
| 2 | `db_write_failed` | `public-api` · ERROR+ · `write failed\|recovery mode` | 1 / 5 min | pg_primary_down (API hosts) |
| 3 | `tls_certificate` | any · WARN+ · `certificate has expired\|x509\|SSL_do_handshake\(\) failed\|renewal failed` | 1 / 10 min | cert_expiry |
| 4 | `disk_pressure` | any · WARN+ · `usage (8[5-9]\|9\d\|100)%\|No space left on device` | 1 / 10 min | disk_full |
| 5 | `pod_crashloop` | `kubelet` · WARN+ · `OOMKilled\|CrashLoopBackOff` | 1 / 10 min | pod_oomkilled |
| 6 | `replica_lag` | `pg-monitor` · WARN+ · `replica lag` | 3 / 5 min | pg_replica_lag |
| 7 | `slow_requests` | `public-api` · WARN+ · `^\S+ \S+ \d{3} \d+ms$` (an access line: a 504 timeout has another shape) | 5 / 5 min | high_latency |
| 8 | `http_5xx` | `public-api` · ERROR+ · `\s50[0-3]\s` | 3 / 5 min | api_error_rate |
| 9 | `unknown` | any · ERROR+ · anything | 1 / 5 min | catch-all |

- **Principles:** one rule per symptom family, so a fault opens one window per host (fewer LLM calls); specific before generic (`db_write_failed` before `http_5xx`); thresholds where one line means nothing, patterns where one line is already a fault; a 10-minute `within` for faults that repeat slowly (OOMKill back-off up to 5 min, renewal failures every 5 min).
- **Implemented** in `prefilter.py` as `RULES`; a test runs them on the generator's logs (seeds 1, 7, 42): every fault gets a window from its rule, and outside the faults only the ERROR 504 decoy opens one (seed 42: 13 windows, 4 of them pg_primary_down's, 2 cert_expiry's).
- **Decoys:** the 1450 ms request, the 22 MB lag line, `log-01` at 81 %, the kubelet `exitCode=1` restart and the retried probe give no window; the ERROR 504 gives an `unknown` window for the agent to dismiss (504 is kept out of `slow_requests` and `http_5xx` for that).
- **Why:** each seeded fault is caught by a named rule, so the rule is a useful hint (agent/9) and prefilter recall is 1 on the generator's logs, which a unit test can check without an LLM.
- **Cost:** tuned to the generator's lines (they read as ordinary SRE alerts, but a new fault needs a new rule); end-to-end recall (scope/4) keeps that honest. The API hosts' pg_primary_down lines never name `db-primary-01`, and the probe's cert_expiry lines never name `ingress-01`: those windows can only ticket the symptom host, which scope/4 matches but counts as a duplicate.

### agent/13 — Log source: one contract, whatever the source

**Status:** Decided (2026-11-01)

- `LogSource.read(since, until) -> LogBatch(events, unparsable)`: a source gives **`LogEvent`s**, not lines, so nothing downstream (prefilter, agent, report) knows where the logs came from. A text source (file, HTTP stream) composes a `LogParser` (agent/9); a structured one (database rows) builds the events itself and needs no parser.
- **Guarantees, stated once in the ABC** (`read` is concrete, subclasses implement `_read`, as in `LogParser`): only events with `since <= time < until`; time order, ties in the source's order; `since` and `until` must be timezone-aware and in order, else `ValueError`.
- **A closed range, never "until now":** the same range read twice gives the same batch, which makes a triage run repeatable (agent/4). Following a live stream is the watcher's job (agent/6), a different method when it is built.
- `_read` may return events outside the range (`read` drops them), so a source that can't filter by time doesn't have to; one that can (a `WHERE time >= …` query) should, to read less.
- `unparsable` counts the records read that gave no event, whatever their time (an unparsable line has none); it goes in the triage report.
- **Adapters (design/1):** `FileLogSource(path, parser=SyslogStyleParser())`, UTF-8 with undecodable bytes replaced, a missing file is an error (configuration, not an empty log); `MemoryLogSource(events, unparsable=0)` is the fake. One contract suite runs on both (tooling/5).
- **Why:** sources differ in transport and format, not in what triage needs: events in a time range, plus a count of what couldn't be read.
- **Cost:** a file is parsed whole on every read (fine for hours of logs; a large file would want an index or a seek by time). A source returns a batch, not an iterator, so a range must fit in memory.

### agent/14 — `log_context`: more logs around a window

**Status:** Decided (2026-11-01)

- A tool for the triage model: `log_context(host, components, min_level, pattern, before, after)` returns the matching events up to `before` before the window, in its span, and up to `after` after it. Every filter is optional.
- **Format-agnostic:** it filters `LogEvent` fields; `pattern` is a regex on the **message**, never on the raw line, so the model needn't know the log format and any source works (agent/13).
- **Any host:** the model may read other hosts' events. The case it is for: an API-host window of `pg_primary_down` never names `db-primary-01`; the replica's lines do (agent/12's known cost).
- **Backend, not material:** `LogContext` (CORE, `log_context.py`) over the `LogBatch` this run already read, so it can only see the run's `[since, until)` range and the same range gives the same context.
- **Limits, in code (API bounds, design/4):** `before`, `after` ≤ 20; at most 30 events from the window's span (first and last 15, the rest counted as omitted); a pattern of ≤ 200 characters, matched with the `regex` package under a 100 ms budget for the whole query. A bad or slow pattern is an error the model reads and can correct.
- **Its lines are evidence:** numbered after the window's (`ShownLines`, agent/10 amended), and the host check accepts a host named in them.
- **Why:** per-host windows see symptoms, not always the cause; asking for more beats widening every window for every run.
- **Cost:** more untrusted log text reaches the model (agent/1, agent/9's security argument), bounded by the limits above; each call is a step of the window's budget (agent/4).

### agent/15 — The agent's HTTP API for the frontend: chat, tickets, meta

**Status:** Decided (2026-11-01)

- The frontend is its own service and calls the agent through its own server under `/api/*` (no CORS). One router per area in `siteops_agent/api/` (`chat.py`, `tickets.py`, `meta.py`, `health.py`, `triage.py`, `windows.py`), assembled in `app.py`.
- **`POST /chat`**, stateless (agent/8): `{messages, since, until, draft_ticket}` → `{turn_id, answered, answer, citations, steps, pending_ticket, warnings, model, latency_ms}`.
  - `messages` is the whole history, as plain role and content (`user` / `assistant`; the last is the user's; at most 40, 8000 characters each). `since` / `until` is the log range in view.
  - Every `[n]` marker of `answer` is a `citations[].n`, enforced by the contract (`chat/base.py`): the engine drops failed citations with their markers. A runbook citation carries `quote_start` / `quote_end` inside `chunk_text`, so the frontend can highlight the quote; a log citation carries the lines seen around it, the cited one flagged.
  - `steps` lists the tool calls; a search step carries its hits, flagged `cited`, which the "I don't know" card shows when `answered` is false.
  - A service the turn can do without (rag) degrades it with a `warning`; one it needs (the model) fails it with 502, or 504 when slow.
- **Tickets from chat: a signed draft, confirmed by the user.** On a `draft_ticket` turn the engine proposes a ticket built by code from checked judgement, as in triage (agent/10), and `pending_ticket` returns it for display with a `draft_token`: the whole `TicketCreate` and an expiry (30 minutes), signed with HMAC-SHA256 (`drafts.py`). `POST /tickets {draft_token}` verifies it and files it through the `TicketSink`, answering `CreateTicketResult` (`created: false` = already open, agent/3).
  - **Why a token:** the browser can't edit what gets filed. If it sent the ticket back, the facts the code filled in (evidence text, window times) could be forged on the way. Signing keeps the agent stateless.
  - The chat engine never files a ticket itself: only the user's confirmation does.
- **`GET /meta`:** `{model, log_range: {earliest, latest}, range_presets, tickets_enabled}`, read once at startup; `tickets_enabled` is false when no tickets service is configured, and `POST /tickets` then answers 503.
- **One error shape** for every route (`errors.py`): `{"detail", "code"}`, `code` stable for the frontend to branch on (`invalid_request` 422, `unauthorized` 401, `invalid_draft` 422, `queue_full` / `tickets_disabled` 503, `upstream_failed` 502, `upstream_timeout` 504, `run_not_found` 404).
- **Not built:** `GET /runbooks/section` (it needs rag to return a chunk's neighbours, which the tool probe showed adds nothing for triage), streaming steps over SSE, per-user auth (agent/2).
- **Cost:** the draft secret must be shared by every API process, or a draft signed by one fails on another; the API runs as one process (agent/8). A draft expires after 30 minutes and must be written again.
- **Amended (2026-11-02), chat removed for now:** the agent's API serves `/health`, `/triage` and `/windows` only. `/chat`, `/tickets`, `/meta`, the turn contract (`chat/`) and the signed drafts (`drafts.py`) are removed until a chat engine is built (agent/11 stays Proposed); they are in git history (commit `6503461`). The service is built by `container.build_api` and started by `api/main.py`.
  - **Error codes in use:** `invalid_request` 422, `unauthorized` 401, `queue_full` 503 (with `Retry-After`), `run_not_found` 404; any other HTTP error becomes `http_<status>`. The rest of the list above goes with chat.

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
- **Amended (2026-11-01):** a service's core may depend on the framework it is built on. The agent requires `langgraph` and `langchain-core` (agent/4); its adapters stay extras (`openai-compat`, `mcp`, `http`).
- **Extras as built (2026-11-02):** rag: `openai-compat`, `qdrant`, `docling`; its core also requires FastAPI and uvicorn, since the API is the service. Agent: `openai-compat`, `mcp`, `http`, `api` (FastAPI and uvicorn; the CLI doesn't need them). Tickets has no extras: its only adapter is its own store. Each image installs the extras it uses through its Dockerfile's `EXTRAS` argument; the `ingest` job builds rag's image with `docling` added. The package is `siteops_rag`, not `site_ops` (arch/4, names as built).

### tooling/3 — Test tiers via pytest markers

**Status:** Decided (2026-10-27)

- Default `pytest` runs unit + contract (fast, no network). `integration` and `eval` are opt-in markers; `--strict-markers` on.
- **As built (2026-11-02):** `integration` tests skip whatever isn't running (Qdrant, Ollama, rag). The `eval` marker is declared but no test uses it yet: the retrieval and `/ask` eval is `scripts/run_eval.py`, which appends each run to `data/eval_history.jsonl`. `make test` and `make test-integration` run the two tiers.

### tooling/4 — Lint + type check: `ruff` + `mypy` strict

**Status:** Decided (2026-10-27)

- **Why mypy on top of ABCs (design/2):** an ABC only checks that methods exist, and only at instantiation. mypy checks signatures and types at every call site, including the pydantic models that cross service boundaries (arch/4).
- mypy `strict` on `packages/*/src` and `services/*/src` from day one; relaxed for `tests/`.

### tooling/5 — Contract tests per interface

**Status:** Decided (2026-11-02, as built)

- One test suite per interface, parametrized over all implementations (e.g. InMemory + Qdrant `:memory:`).
- **As built:** `VectorStore` (memory, Qdrant `:memory:`, Qdrant server under `integration`), `LogSource` (memory, file) and `TicketSink` (memory, MCP against a stand-in tickets server on a local port), each in its folder's `test_base.py`. `Embedder`'s `test_base.py` tests the checks its base class makes for every adapter. The model adapters are tested on their own, the real ones under `integration`.

### tooling/6 — CI pipeline

**Status:** Decided (2026-11-02, as built)

- GitHub Actions running ruff, mypy and the default (unit + contract) test tier; whether `eval` runs in CI.
- **As built (`.github/workflows/ci.yml`):** on every pull request and push to `main`, three jobs: `lint` (`ruff check`), `test` (the default tier) and `integration` (all extras installed, `mypy`, then `pytest -m integration` with a Qdrant 1.20 service container; tests that need Ollama skip themselves). mypy runs in the job with the extras, since it needs Docling's types.
- **Not in CI:** the eval (it needs the 27B model) and `ruff format --check`, which `make lint` runs.
