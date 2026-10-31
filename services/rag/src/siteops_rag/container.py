"""Composition root (design/3): the only module that reads Settings and picks the adapters."""

from siteops_rag.config import Settings
from siteops_rag.embedder.openai_compat import OpenAICompatEmbedder
from siteops_rag.ingestion.chunking import Chunker
from siteops_rag.ingestion.pipeline import RAGIngestionPipeline
from siteops_rag.llm.openai_compat import OpenAICompatChatModel
from siteops_rag.rag import RAG
from siteops_rag.vector_store.base import VectorStore
from siteops_rag.vector_store.memory import InMemoryVectorStore


def build_rag(settings: Settings | None = None) -> RAG:
    settings = settings or Settings()
    llm = settings.llm
    chat_model = OpenAICompatChatModel(
        base_url=llm.base_url,
        model=llm.model,
        timeout=llm.timeout,
        max_retries=llm.max_retries,
        temperature=llm.temperature,
        reasoning_effort=llm.reasoning_effort,
        api_key=llm.api_key.get_secret_value(),
    )
    return RAG(embedder=_embedder(settings), vector_store=_vector_store(settings), chat_model=chat_model)


def build_pipeline(settings: Settings | None = None) -> RAGIngestionPipeline:
    settings = settings or Settings()
    # imported here: docling is an optional extra (the API image does not have it)
    from siteops_rag.converter.docling import DoclingDocumentConverter

    return RAGIngestionPipeline(
        converter=DoclingDocumentConverter(),
        chunker=Chunker(max_length=settings.chunking.max_length),
        embedder=_embedder(settings),
        vector_store=_vector_store(settings),
    )


def _embedder(settings: Settings) -> OpenAICompatEmbedder:
    emb = settings.embedding
    return OpenAICompatEmbedder(
        base_url=emb.base_url,
        embedding_model=emb.model,
        timeout=emb.timeout,
        batch_size=emb.batch_size,
        dimension=emb.dimension,
        max_retries=emb.max_retries,
        api_key=emb.api_key.get_secret_value(),
    )


def _vector_store(settings: Settings) -> VectorStore:
    store, emb = settings.vector_store, settings.embedding
    if store.backend == "memory":
        return InMemoryVectorStore(dimension=emb.dimension, embedding_model=emb.model)

    # imported here: qdrant-client is an optional extra
    from qdrant_client import QdrantClient

    from siteops_rag.vector_store.qdrant import QdrantVectorStore

    return QdrantVectorStore(
        client=QdrantClient(url=store.url),
        collection_name=store.collection,
        dimension=emb.dimension,
        embedding_model=emb.model,
    )
