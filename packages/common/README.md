# siteops-common

The wire contracts shared by the services: the pydantic models that cross a service boundary, plus `fingerprint()`, the one definition of "the same fault". Every service image installs this package.

It holds no logic and has no dependency beyond pydantic. Service-internal types (`Element`, `Chunk`, `LogEvent`, `Window`…) stay in their service.

## Contents (`siteops_common.contracts`)

| Model | Used by | Purpose |
|---|---|---|
| `SearchRequest`, `SearchResponse`, `SearchHit` | rag `/search` ← agent, eval | A query and its ranked chunks with cosine scores |
| `AskRequest`, `AskResponse`, `Citation` | rag `/ask` ← engineers, eval | An answer with checked citations; `answered: false` is "I don't know" |
| `FaultType`, `Severity` | agent, tickets, eval | The seven seeded faults plus `other`; `SEV-1`…`SEV-3` |
| `TicketCreate`, `RunbookRef` | agent → tickets (MCP) | What the agent asks to open; every text field has a length limit |
| `Ticket`, `CreateTicketResult` | tickets → agent | The stored ticket (plus `id`, `created_at`, `fingerprint`); `created: false` means deduplicated |
| `fingerprint(fault_type, host)` | tickets store, agent's in-memory sink | `"fault_type\|host"`, host lower-cased and trimmed |

Every model forbids unknown fields and is frozen: a received message can't be changed, and a typo in a field name is an error, not silently dropped. `top_k` is limited to 1–50, and `TicketCreate` checks that `window_end` isn't before `window_start` and that both carry a timezone.

## Rules for changing it

- **Only backward-compatible changes:** add optional fields, never rename or remove one. Services are deployed separately, so two images can run different versions of this package.
- **A change here means rebuilding every image.** For example, adding a `FaultType` also means a prefilter rule, a log generator fault and its ground truth.
- **Only wire contracts and pure helpers that define a shared identity.** Anything else belongs in a service.

## Use

Services depend on it through the uv workspace:

```toml
[project]
dependencies = ["siteops-common"]

[tool.uv.sources]
siteops-common = { workspace = true }
```

```python
from siteops_common.contracts import TicketCreate, fingerprint
```

## Tests

```bash
uv run pytest packages/common
```

`tests/test_contracts.py` covers the validation rules (limits, unknown fields, time order, timezones), a JSON round trip of the responses and tickets, and `fingerprint`'s normalisation. A test in the agent checks that `FaultType` matches the faults `scripts/generate_logs.py` seeds.
