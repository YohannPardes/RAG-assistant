# rag

Retrieval and question answering over the site's runbooks. It ingests runbooks in five formats into Qdrant and serves two endpoints:

- `POST /search`: ranked chunks, for the triage agent and the eval.
- `POST /ask`: an answer whose every citation has been checked against the retrieved text, or "I don't know".

rag owns the vector store: no other service talks to Qdrant.

## API

| Method | Path | Body → reply | Errors |
|---|---|---|---|
| `GET` | `/health` | → `{"status": "ok"}` | — |
| `POST` | `/search` | `SearchRequest` → `SearchResponse` | 422 invalid body, 502 embedding server failed |
| `POST` | `/ask` | `AskRequest` → `AskResponse` | 422 invalid body, 502 embedding or LLM server failed |

The models are defined in [`packages/common`](../../packages/common/README.md). `top_k` (1–50) is optional: without it, `retrieval.top_k` from the config is used. rag has no API key: it is reached only on the compose network and on `127.0.0.1`.

```bash
curl -s localhost:8000/ask -H 'Content-Type: application/json' \
  -d '{"question": "Which alert fires before a public TLS certificate expires?", "top_k": 5}'
```

```jsonc
{
  "answered": true,
  "answer": "...",
  "citations": [
    {"chunk_id": "…", "source": "runbooks/runbook_cert_expiry.md",
     "heading_path": ["RB-310: TLS Certificate Expiry", "1. Symptoms"], "page": null,
     "quote": "…copied word for word from the chunk…"}
  ],
  "hits": [ /* the chunks the LLM saw, best first, with their cosine scores */ ],
  "model": "qwen3.5:27b",
  "embedding_model": "bge-m3",
  "latency_ms": 2140.5
}
```

Callers check `answered`, not the answer text. `hits` is the context the model saw, which tells "not retrieved" apart from "retrieved but not used".

## How it works

### Ingestion (a job, not an endpoint)

```
file bytes ──► converter ──► Elements ──► Chunker ──► Chunks ──► Embedder ──► VectorStore.upsert
              (Docling)    heading, paragraph,      ≤ 1000 chars,       bge-m3,        Qdrant: write, then
                           table, code, caption     heading path first  1024-d         delete stale chunks
```

- **Convert.** `DoclingDocumentConverter` turns PDF, DOCX, HTML, Markdown and plain text into rag's own `Element` list: kind, text, page, heading level, table rows, code language. Nothing after this step depends on Docling.
- **Chunk.** `Chunker` works by section, up to `chunking.max_length` characters, heading prefix included. It never cuts mid-sentence, mid-row or inside a code block, and never spans two sections. Large tables are split by rows with the header repeated in every chunk.
  - Every chunk starts with its heading path, and the path always begins with the document's title (its filename when it has none). Without the title, "5. Escalation" in one runbook would read and embed like "5. Escalation" in any other.
- **Identify.** A chunk's ID is a `uuid5` of its source, heading path and text, so re-ingesting an unchanged document overwrites the same points.
- **Store.** `upsert` treats a document's chunks as its complete set: it writes them, then deletes that source's chunks with any other ID. An edited section therefore leaves nothing stale behind, and a document that is now empty is deleted. Files removed from the folder are *not* cleaned up.

`scripts/ingest.py` ingests every supported file under a folder (default `data/`), names each one by its path inside that folder (`runbooks/runbook_disk_full.pdf`), and warns about files it skips. A document that can't be converted is skipped with a warning. An embedding or Qdrant failure stops the run.

### Answering

1. Embed the question and take the top-k chunks by cosine similarity. If nothing is retrieved, answer "I don't know" without calling the LLM.
2. Send the LLM numbered excerpts. It must reply in JSON (`response_format: json_object`):
   `{"answer": "...", "citations": [{"excerpt": 1, "quote": "..."}]}`.
3. Keep a citation only if its excerpt number was retrieved and its quote appears in that chunk word for word (whitespace ignored). Code fills in each citation's `chunk_id`, source, heading path and page.
4. The answer counts only if at least one citation survives. Otherwise the reply is `answered: false` with "I don't know".

When the excerpts contradict what the question assumes, the model is told to answer with the correction, and the correction goes through the same citation check. The cost of this design: a correct answer with a badly copied quote is reported as "I don't know".

## Code map

```
src/siteops_rag/
├── api.py              routes only; the RAG object is built once, at startup
├── container.py        the composition root: build_rag (API), build_pipeline (ingestion)
├── config.py           settings from config.yaml, secrets from env / .env
├── rag.py              search and ask: retrieval, the prompt, the citation check
├── models.py           Element, Document, Chunk (internal, never on the wire)
├── ingestion/          chunking.py (Chunker), pipeline.py (convert → chunk → embed → upsert)
├── converter/          base.py + docling.py (every format), text.py (plain text, no Docling)
├── embedder/           base.py + openai_compat.py, fake.py
├── vector_store/       base.py + qdrant.py, memory.py
└── llm/                base.py + openai_compat.py, fake.py
```

Each adapter folder holds an `abc.ABC` in `base.py`, a real implementation and a fake. The base classes enforce the contract every adapter shares, such as an embedder returning one vector of the right size per input. Optional dependencies are imported only inside `container.py`, so `import siteops_rag` works without any extras.

| Extra | Brings | Needed by |
|---|---|---|
| `openai-compat` | httpx | The embedder and LLM clients (any OpenAI-compatible server) |
| `qdrant` | qdrant-client | `vector_store.backend: qdrant` |
| `docling` | Docling | Ingestion only. The API image doesn't carry it; the `ingest` job's image does. |

## Configuration

`config.yaml` (on the host) and `config.docker.yaml` (in the image) differ only in URLs. Every key is required.

| Key | Default | Meaning |
|---|---|---|
| `embedding.base_url`, `.model`, `.dimension` | `localhost:11434/v1`, `bge-m3`, `1024` | The embedding server; the dimension fixes the Qdrant collection |
| `embedding.batch_size`, `.timeout`, `.max_retries` | `32`, `30.0`, `3` | Retries cover connection failures only, not 5xx |
| `llm.base_url`, `.model` | `localhost:11434/v1`, `qwen3.5:27b` | The answering model |
| `llm.reasoning_effort`, `.temperature` | `"none"`, `0.0` | Thinking off: about ten times faster answers |
| `llm.timeout`, `.max_retries` | `120.0`, `2` | |
| `chunking.max_length` | `1000` | Characters per chunk, heading prefix included |
| `retrieval.top_k` | `10` | Chunks retrieved and given to the LLM when a request sends none |
| `vector_store.backend`, `.url`, `.collection` | `qdrant`, `localhost:6333`, `runbooks` | `memory` keeps vectors in process (tests) |

**Secrets** (`.env` or environment): `RAG_EMBEDDING__API_KEY` and `RAG_LLM__API_KEY`. Both are optional, since local servers ignore them (`"EMPTY"` is sent). Set `RAG_CONFIG_FILE` to use another YAML file. Changing the embedding model or dimension means deleting the collection and re-ingesting.

## Running

In the stack (from the repo root):

```bash
make up         # qdrant, rag and the rest
make ingest     # docker compose run --rm ingest: data/ into Qdrant
```

From source, with Qdrant and a model server on their default ports:

```bash
uv sync --package siteops-rag --all-extras
uv run python scripts/ingest.py                      # or a folder: scripts/ingest.py path/to/runbooks
uv run uvicorn siteops_rag.api:app --port 8000
```

## Tests

```bash
uv run pytest services/rag                    # unit: fakes and the in-memory store, no extras needed
uv run pytest services/rag -m integration     # Docling, Ollama, a Qdrant server; skips what isn't there
```

- **Unit:** the chunker, the pipeline, `rag.py`'s citation check and "I don't know" paths, the API on fakes, the config rules, and the `ingest.py` and `run_eval.py` scripts.
- **Contract:** `vector_store/test_base.py` runs one suite on the memory store, Qdrant `:memory:` and (under `integration`) a Qdrant server.
- **Integration:** Docling on the fixture runbooks (one per format), the Ollama clients, the API's real lifespan, and `test_golden_set.py`, which checks that every golden-set label names a section that ingesting `data/` actually produces.

Retrieval quality is measured by `scripts/run_eval.py` (see the [root README](../../README.md#evaluation)).

## Limitations

- No reranker and no hybrid (keyword) search: retrieval is vector search only.
- Two runbooks lose their real title to Docling (it's read as a paragraph), so their chunks fall back to the filename.
- Runbooks deleted from the folder stay in Qdrant until the collection is rebuilt.
- No access control on chunks: every caller can retrieve every runbook.
