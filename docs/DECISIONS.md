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
| `agent`   | [5. Agent, access &amp; safety](#5-agent-access--safety)                                                  | agent/1 – agent/10    |
| `tooling` | [6. Tooling, packaging &amp; testing](#6-tooling-packaging--testing)                                      | tooling/1 – tooling/6 |

## Open decisions by priority

| Priority                       | Decisions                                                                                                                                                                                                                                                                                              |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **P1 — before code**    | rag/5 embedding model · agent/2 access levels                                                                                                                                                                                              |
| **P2 — while building** | rag/6 chunking · rag/7 retrieval depth · rag/8 reranker model · rag/9 "I don't know" rule · rag/10 citations · agent/4 agent loop · agent/5 auth · scope/4 golden set · scope/5 eval thresholds · arch/7 timeouts/retries · arch/8 persistence · tooling/6 CI |
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

### scope/4 — Golden set: size and construction

**Status:** Proposed · **Priority:** P2

- How many question → expected-chunk pairs, how they're written (by hand from runbooks, or generated then reviewed), and how triage ground truth links to scope/2's seeded faults.
- **Recommendation (retrieval part, in `data/golden_set.jsonl`):** ~40 questions written by hand, ~10 per runbook, paraphrased rather than copied so they test meaning and not keyword overlap. Each line is `{question, expected_source, expected_section}`. A hit is a chunk with the same `source` whose `heading_path[-1]` equals `expected_section`, never a `chunk_id`, so the labels survive chunking changes (rag/6). Each question has one unambiguous section. `services/rag/tests/integration/test_golden_set.py` checks that every label names a section that ingestion really produces. Triage ground truth is still open.
- **Why:** with 4 runbooks, hand-written questions are cheap and more trustworthy than generated ones. Matching on section keeps recall@5 and MRR comparable across chunk sizes and with or without the reranker (scope/3).
- **Update (2026-11-01), corpus with distractors:** `data/runbooks/` grows to 30 runbooks in five formats (md, txt, html, docx, pdf). The golden set covers 10 of them (~1/3) with 91 questions; the other 20 are distractors. About a third of the new runbooks are *near misses* that share vocabulary with a covered one (MySQL next to PostgreSQL failover, internal mesh CA next to public TLS, 5xx rate next to p99 latency…), so retrieval has to tell look-alikes apart. A question that a near miss could also answer is rephrased to name its system ("before a PostgreSQL failover"). A few runbooks parse badly on purpose or by Docling quirk (a DOCX without heading styles, a PDF whose title is read as a table caption); none of them is in the golden set.
- **Why:** with 4 runbooks every question was found in the top 3 (recall@3 = 1.0), so the eval could not show a retrieval or reranker change (scope/3). With 30, recall@5 is 0.956 and MRR 0.841 (`data/eval_history.jsonl`).
- **Update (2026-11-01), triage ground truth:** `data/logs/ground_truth.json`, written by `scripts/generate_logs.py` along with the log (scope/2). It holds the generator's seed and the log's time span, plus one entry per seeded fault: `{id, fault_type, host, affected_hosts, start, end, expected_runbook}`, where `host` is the root cause, `affected_hosts` every host whose lines show the fault (root included; for `pg_primary_down`: `db-primary-01`, `db-replica-02`, `api-01..03`), and `expected_runbook` the runbook's `source` as `/search` returns it.- **Matching rule (2026-11-01):** a ticket carries a `fault_type` chosen by the agent from a fixed list (a field of `TicketCreate`, added with the ticket contract), not inferred from its cited runbook, so the eval scores diagnosis and citation separately. A ticket matches a fault when it has the fault's type and its host is in the fault's `affected_hosts`. A fault is **detected** when at least one ticket matches it; further matching tickets are **duplicates**, reported as their own metric (not false positives, not hidden). A ticket that matches no fault is a false positive. Time is not part of the match: each fault type is seeded once and faults never overlap (scope/2).
- **Why:** per-host windows (agent/9) see a fault's symptoms, not always its cause: a silent primary only shows up on other hosts. The agent's system prompt says "if the evidence points to another host as the cause, name that host in the ticket"; the duplicate count shows whether it does.

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
- **False premise (implemented in `SYSTEM_PROMPT`):** when the excerpts contradict what the question assumes ("since it's fine to delete /var/log/audit…"), the model answers with the correction instead of "I don't know". It goes through the same citation check, so it can't be invented.
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

**Status:** Decided (2026-11-01)

- Which fields define "the same fault" (agent/1), and for how long a duplicate is suppressed.
- **Decision:** fingerprint = `(fault_type, host)` of the `TicketCreate` (agent/10). A ticket whose fingerprint matches one created in the last **60 minutes** is not stored: `create_ticket` returns the existing ticket with `created=False`.
- **Enforced by the tickets service**, not the agent: it is the boundary a prompt-injected agent can't get around (agent/1). The agent's in-memory ticket sink applies the same rule, so its tests see the same behaviour.
- **Why:** a fault keeps logging for its whole duration and can open several windows (on one host, or on each affected host), each ending in a ticket attempt; one ticket per fault and host is what an on-call engineer wants. A retried `create_ticket` is then harmless too (arch/7).
- **Cost:** two different faults of the same type on the same host within an hour become one ticket. Symptoms of one fault on several hosts still give one ticket per host; scope/4 counts those as duplicates, and naming the root host (agent/10's `host`) is how the agent avoids them.

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

### agent/8 — The agent is the users' front door (amended)

**Status:** Decided (2026-10-27), amended (2026-11-01)

- Design unchanged: one agent, two modes on the same tools: **triage** (trigger: CLI / schedule) and **chat** (user request).
- **This version ships triage only** (`triage --since …`, optional `POST /triage`). `/chat` is deferred.
- **Why:** triage is measurable (seeded faults → precision/recall); chat has no ground truth yet, and adds user text as a second untrusted input to prompt-harden and test. Time budget before the interview goes to the measurable mode.
- **Next step:** `/chat` = the same agent factory with a chat system prompt and the conversation history from the client (stateless). Tools, rag and tickets unchanged.
- rag `/ask` remains the direct Q&A endpoint for users meanwhile.

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
- **Why:** `fault_type` is chosen by the agent, not inferred from its citation, so the eval scores diagnosis and citation separately (scope/4). `runbook` is optional because a forced citation would be an invented one.
- **Cost:** adding a fault type means changing `common`, hence rebuilding every image (arch/4). The fingerprint's composition is agent/3's, still open: the contract only carries it.

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
