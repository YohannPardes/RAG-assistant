"""This file contains the pipeline logic for the ingestion process of the RAG system.
It orchestrates the flow of data from raw input to processed and stored embeddings,
ensuring that each step is executed in the correct order and that any necessary transformations are applied.
"""

import logging

from siteops_rag.converter.base import AbstractDocumentConverter, ConversionError, RawDocument, UnsupportedMediaType
from siteops_rag.embedder.base import Embedder
from siteops_rag.ingestion.chunking import Chunker
from siteops_rag.models import Document
from siteops_rag.vector_store.base import VectorStore

logger = logging.getLogger(__name__)


class RAGIngestionPipeline:
    def __init__(self, converter: AbstractDocumentConverter, chunker: Chunker, embedder: Embedder, vector_store: VectorStore):
        self.converter = converter
        self.chunker = chunker
        self.embedder = embedder
        self.vector_store = vector_store

    def run(self, raw_document: RawDocument) -> None:
        """Run the ingestion pipeline on the provided raw data."""
        # step 1: Convert raw data to a standard format
        elements = self.converter.convert_to_elements(raw_document)

        # Step 2: Chunk the raw data
        document = Document(source=raw_document.source, elements=tuple(elements))
        chunks = self.chunker.chunk_elements(document)
        if not chunks:
            # an emptied document sends no chunks, so upsert can't clean it up (rag/12)
            self.vector_store.delete_by_source(raw_document.source)
            return

        # Step 3: Generate embeddings for each chunk
        embeddings = self.embedder.embed_batch([chunk.text for chunk in chunks])

        # Step 4: Upsert the chunks and their embeddings into the vector store
        self.vector_store.upsert(chunks, embeddings)

    def run_batch(self, raw_documents: list[RawDocument]) -> list[str]:
        """Run the ingestion pipeline on a batch of raw documents. Returns the sources that were skipped.

        Only per-document errors are caught; anything else (embedder, vector store, bugs) stops the batch.
        """
        failed: list[str] = []
        for raw_document in raw_documents:
            try:
                self.run(raw_document)
            except (UnsupportedMediaType, ConversionError) as exc:
                logger.warning("skipping %s: %s", raw_document.source, exc)
                failed.append(raw_document.source)
        return failed
