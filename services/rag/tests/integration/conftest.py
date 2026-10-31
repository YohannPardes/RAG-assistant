"""Fixtures for the integration tier. Each one needs a real service and skips its tests when that service is
not there: Ollama only runs on a dev machine, Qdrant also runs in CI (as a service container)."""

import importlib.util
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

import httpx
import pytest

from siteops_rag.config import EmbeddingSettings, LLMSettings, Settings, VectorStoreSettings

if TYPE_CHECKING:  # allow for type checking without importing at runtime
    from siteops_rag.converter.docling import DoclingDocumentConverter

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
INGEST_SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "ingest.py"


@pytest.fixture(scope="session")
def docling_converter() -> "DoclingDocumentConverter":
    from siteops_rag.converter.docling import DoclingDocumentConverter

    return DoclingDocumentConverter()


@pytest.fixture(scope="session")
def configured() -> Settings:
    """The service's real config.yaml: integration tests run against the models it names."""
    return Settings(_env_file=None)


@pytest.fixture(scope="session")
def ollama_models() -> set[str]:
    """Names of the models Ollama has pulled ("bge-m3" and "bge-m3:latest" both count)."""
    try:
        tags = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=2).raise_for_status().json()
    except httpx.HTTPError:
        pytest.skip(f"no Ollama server at {OLLAMA_URL}")
    names = {m["name"] for m in tags["models"]}
    return names | {name.removesuffix(":latest") for name in names}


def _need(models: set[str], name: str) -> None:
    if name not in models:
        pytest.skip(f"Ollama has not pulled {name!r}")


@pytest.fixture(scope="session")
def embedding_settings(configured: Settings, ollama_models: set[str]) -> EmbeddingSettings:
    _need(ollama_models, configured.embedding.model)
    return configured.embedding.model_copy(update={"base_url": f"{OLLAMA_URL}/v1"})


@pytest.fixture(scope="session")
def llm_settings(configured: Settings, ollama_models: set[str]) -> LLMSettings:
    _need(ollama_models, configured.llm.model)
    return configured.llm.model_copy(update={"base_url": f"{OLLAMA_URL}/v1", "timeout": 600.0})


@pytest.fixture(scope="session")
def live_settings(
    configured: Settings, embedding_settings: EmbeddingSettings, llm_settings: LLMSettings, qdrant_url: str
) -> Iterator[Settings]:
    """The real config, pointed at the local servers and at a collection of its own (deleted afterwards)."""
    from qdrant_client import QdrantClient

    collection = f"test_{uuid.uuid4().hex}"
    store = VectorStoreSettings(backend="qdrant", url=qdrant_url, collection=collection)
    yield configured.model_copy(update={"embedding": embedding_settings, "llm": llm_settings, "vector_store": store})
    QdrantClient(url=qdrant_url).delete_collection(collection)


@pytest.fixture(scope="session")
def ingest_script() -> ModuleType:
    """scripts/ingest.py, loaded from its path (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location("ingest", INGEST_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def ingested(live_settings: Settings, ingest_script: ModuleType, fixtures_dir: Path) -> Settings:
    """The sample runbooks ingested once, the way scripts/ingest.py does it."""
    pytest.importorskip("docling")
    from siteops_rag.container import build_pipeline

    skipped = build_pipeline(live_settings).run_batch(ingest_script.load_documents(fixtures_dir))
    assert skipped == []
    return live_settings
