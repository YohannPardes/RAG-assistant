# tickets

An MCP server that opens incident tickets. It offers one tool, `create_ticket`, over streamable HTTP, stores tickets in SQLite, and deduplicates them: a second report of the same fault on the same host within an hour returns the open ticket instead of creating another.

Deduplication lives here, not in the agent, because this is the boundary a prompt-injected agent can't get around.

## Interface

| Endpoint | What it is | Auth |
|---|---|---|
| `POST /mcp` | MCP over streamable HTTP: stateless, JSON responses | `Authorization: Bearer <TICKETS_API_KEY>` |
| `GET /health` | `{"status": "ok"}`, for compose's health check | none |

### Tool: `create_ticket(ticket: TicketCreate) -> CreateTicketResult`

The tool's input and output schemas are generated from the models in [`packages/common`](../../packages/common/README.md), so the MCP schema and the agent's client can't drift apart.

| Field | Type / limit | Meaning |
|---|---|---|
| `fault_type` | `disk_full`, `pg_primary_down`, `pg_replica_lag`, `cert_expiry`, `high_latency`, `api_error_rate`, `pod_oomkilled`, `other` | The diagnosis |
| `host` | 1–255 chars | The root cause, which may differ from where the symptoms showed |
| `severity` | `SEV-1`, `SEV-2`, `SEV-3` | The runbooks' scale; SEV-1 is the worst |
| `title` | ≤ 200 chars | |
| `summary` | ≤ 4000 chars | The incident report |
| `suggested_fix` | ≤ 4000 chars | Advice for a human; never executed |
| `evidence` | 1–20 raw log lines, ≤ 2000 chars each | Quoted unchanged |
| `runbook` | `{chunk_id, source, heading_path}` or `null` | The runbook section the diagnosis relies on |
| `window_start`, `window_end` | Timezone-aware, in order | The log window the diagnosis comes from |

The reply is `{ticket, created}`. `ticket` is the stored `Ticket`: the input plus `id`, `created_at` and `fingerprint`. `created: false` means the ticket was deduplicated, and `ticket` is the open one it duplicates. Unknown fields and invalid values are rejected by the schema.

The tool's description, which is what an LLM client reads, covers when to open a ticket and how deduplication works. It doesn't mention any particular client.

## Deduplication

- **Fingerprint:** `fault_type|host`, with the host lower-cased and trimmed. It is defined once, as `fingerprint()` in `siteops_common`, so the agent's in-memory sink deduplicates exactly the same way.
- **Window:** `dedup_minutes` (60). A ticket whose fingerprint matches one created within the window is not stored, and the existing ticket is returned.
- **Consequences:** a retried `create_ticket` is harmless, so the agent can retry on connection errors. The same range triaged twice files nothing new. Two different faults of the same type on one host within an hour become one ticket.

Writes take SQLite's lock, so concurrent creates with the same fingerprint still store one ticket. Dedup holds across a restart, since it reads the database.

## Code map

```
src/siteops_tickets/
├── server.py   entry point and composition root: the MCP server, its HTTP app, the API-key middleware, /health
├── tools.py    TicketTools: every MCP tool and its description (adding a tool = a method + its name in register)
├── store.py    TicketStore: SQLite schema, fingerprint lookup within the window, inserts
└── config.py   settings from config.yaml; TICKETS_API_KEY from env / .env
```

The server uses the official `mcp` SDK (`MCPServer`) in stateless mode with JSON responses. One tool call is one HTTP request, with no session or SSE stream to keep, so any number of replicas could serve it. The key is checked by an ASGI middleware in constant time (`hmac.compare_digest`). When bound to `127.0.0.1`, the SDK's localhost-only Host check is also on. The container binds `0.0.0.0`, and there the key is the access check.

## Configuration

| Key | `config.yaml` | `config.docker.yaml` | Meaning |
|---|---|---|---|
| `db_path` | `tickets.db` | `/data/tickets.db` | The SQLite file; relative to the working directory |
| `db_lock_timeout` | `10.0` | `10.0` | Seconds a write waits for the database lock |
| `dedup_minutes` | `60` | `60` | The dedup window |
| `host`, `port` | `127.0.0.1`, `8000` | `0.0.0.0`, `8000` | Bind address |

**Secret:** `TICKETS_API_KEY`, at least 16 characters, from `services/tickets/.env` or the environment. The service refuses to start without it. Under compose it comes from the root `.env`, and the agent gets the same value as `AGENT_TICKETS__API_KEY`. Set `TICKETS_CONFIG_FILE` to use another YAML file.

## Running

In the stack, from the repo root: `make up`. The service is published on `127.0.0.1:8002`, and its database is on the `tickets-data` volume, which survives `make down`.

From source:

```bash
uv sync --package siteops-tickets
TICKETS_API_KEY=… uv run python -m siteops_tickets.server     # 127.0.0.1:8000, tickets.db in the working directory
```

The image runs as an unprivileged user that can write only `/data`.

## Tests

```bash
uv run pytest services/tickets
```

- **`test_store.py`:** the dedup window, host normalisation, dedup across a restart, and concurrent creates.
- **`test_server.py`:** the tool's schema, valid and invalid calls through an in-process MCP client, and the API key over HTTP.
- **`test_config.py`:** both real config files load; the key comes only from the environment or `.env`, and settings only from the YAML.

The agent's MCP client is tested against this server in `tests/e2e/test_agent_to_tickets.py`.

## Limitations

- One shared key: no per-client identity, revocation or audit of who opened a ticket.
- No way to list, update or close tickets. MCP has one write tool; a REST API for non-AI clients would sit beside it, backed by the same store.
- No backups of the volume. Tickets are reproducible by re-running triage, but their history isn't.
