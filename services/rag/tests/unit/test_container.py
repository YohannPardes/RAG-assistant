import os

import pytest
from pydantic import SecretStr

from siteops_rag.config import ChunkingSettings, EmbeddingSettings, LLMSettings, RetrievalSettings, Settings, VectorStoreSettings
from siteops_rag.container import build_pipeline, build_rag
from siteops_rag.vector_store.memory import InMemoryVectorStore


@pytest.fixture
def settings(monkeypatch, tmp_path) -> Settings:
    for name in list(os.environ):
        if name.startswith("RAG_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("RAG_CONFIG_FILE", str(tmp_path / "none.yaml"))
    return Settings(
        _env_file=None,
        embedding=EmbeddingSettings(base_url="http://emb:1/v1", model="emb-model", dimension=8, api_key=SecretStr("emb-key")),
        llm=LLMSettings(base_url="http://llm:2/v1", model="llm-model", api_key=SecretStr("llm-key"), reasoning_effort="none"),
        chunking=ChunkingSettings(max_length=321),
        retrieval=RetrievalSettings(top_k=7),
        vector_store=VectorStoreSettings(backend="memory"),
    )


def test_rag_is_built_from_the_settings(settings) -> None:
    rag = build_rag(settings)

    assert rag.embedder.embedding_model == "emb-model"
    assert rag.embedder.dimension == 8
    assert rag.chat_model.model == "llm-model"
    assert rag.chat_model.reasoning_effort == "none"
    assert rag.top_k == 7
    # the store must expect the embedder's vectors, or every upsert fails
    assert isinstance(rag.vector_store, InMemoryVectorStore)
    assert (rag.vector_store.dimension, rag.vector_store.embedding_model) == (8, "emb-model")


def test_adapters_call_the_configured_servers_with_the_real_keys(settings) -> None:
    rag = build_rag(settings)

    # a SecretStr passed as is would send "Bearer **********"
    assert str(rag.embedder._client.base_url) == "http://emb:1/v1/"
    assert rag.embedder._client.headers["Authorization"] == "Bearer emb-key"
    assert str(rag.chat_model._client.base_url) == "http://llm:2/v1/"
    assert rag.chat_model._client.headers["Authorization"] == "Bearer llm-key"


def test_pipeline_shares_the_embedder_settings_and_uses_the_chunking_limit(settings) -> None:
    pytest.importorskip("docling")  # optional extra: not in the fast CI tier

    pipeline = build_pipeline(settings)

    assert pipeline.chunker.max_length == 321
    assert pipeline.embedder.embedding_model == "emb-model"
    assert (pipeline.vector_store.dimension, pipeline.vector_store.embedding_model) == (8, "emb-model")
