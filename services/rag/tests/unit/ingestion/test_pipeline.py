import pytest

from siteops_rag.converter.base import RawDocument
from siteops_rag.converter.text import TextMarkdownConverter
from siteops_rag.embedder.base import EmbeddingError
from siteops_rag.ingestion.chunking import Chunker
from siteops_rag.ingestion.pipeline import RAGIngestionPipeline


@pytest.fixture
def pipeline(embedder, store) -> RAGIngestionPipeline:
    # 60 characters: each paragraph below is its own chunk
    return RAGIngestionPipeline(converter=TextMarkdownConverter(), chunker=Chunker(max_length=60), embedder=embedder, vector_store=store)


def txt(source: str, text: str) -> RawDocument:
    return RawDocument(source=source, data=text.encode(), media_type="text/plain")


def stored(store, embedder) -> dict[str, list[str]]:
    """Every stored chunk's text, grouped by source."""
    by_source: dict[str, list[str]] = {}
    for hit in store.search(embedder.embed("anything"), top_k=1000):
        by_source.setdefault(hit.chunk.source, []).append(hit.chunk.text)
    return {source: sorted(texts) for source, texts in by_source.items()}


LATENCY = "The p99 latency alert fires above 800 ms.\n\nRestart the api pods one at a time."


def test_ingested_document_is_searchable_under_its_source(pipeline, store, embedder) -> None:
    pipeline.run(txt("runbook_high_latency.txt", LATENCY))

    [hit] = store.search(embedder.embed("restart the api pods"), top_k=1)
    assert hit.chunk.source == "runbook_high_latency.txt"
    assert hit.chunk.text == "Restart the api pods one at a time."
    assert stored(store, embedder) == {"runbook_high_latency.txt": sorted(LATENCY.split("\n\n"))}


def test_ingesting_the_same_document_twice_changes_nothing(pipeline, store, embedder) -> None:
    pipeline.run(txt("runbook_high_latency.txt", LATENCY))
    before = stored(store, embedder)

    pipeline.run(txt("runbook_high_latency.txt", LATENCY))

    assert stored(store, embedder) == before


def test_edited_document_replaces_its_old_chunks(pipeline, store, embedder) -> None:
    pipeline.run(txt("runbook_high_latency.txt", LATENCY))

    pipeline.run(txt("runbook_high_latency.txt", "The p99 latency alert fires above 800 ms.\n\nScale the api deployment up."))

    # rag/12: the removed step no longer comes back in search results
    assert stored(store, embedder) == {
        "runbook_high_latency.txt": ["Scale the api deployment up.", "The p99 latency alert fires above 800 ms."]
    }


def test_other_documents_are_not_touched(pipeline, store, embedder) -> None:
    pipeline.run(txt("runbook_disk_full.txt", "Run df -h /var/log."))
    pipeline.run(txt("runbook_high_latency.txt", LATENCY))

    pipeline.run(txt("runbook_high_latency.txt", "Scale the api deployment up."))

    assert stored(store, embedder)["runbook_disk_full.txt"] == ["Run df -h /var/log."]


def test_document_emptied_since_last_run_disappears(pipeline, store, embedder) -> None:
    pipeline.run(txt("runbook_disk_full.txt", "Run df -h /var/log."))
    pipeline.run(txt("runbook_high_latency.txt", LATENCY))

    pipeline.run(txt("runbook_high_latency.txt", "  \n\n "))

    assert stored(store, embedder) == {"runbook_disk_full.txt": ["Run df -h /var/log."]}


def test_batch_skips_unconvertible_documents_and_ingests_the_rest(pipeline, store, embedder) -> None:
    documents = [
        RawDocument("runbook_disk_full.pdf", b"%PDF-1.7", "application/pdf"),  # the text converter has no PDF support
        RawDocument("broken.txt", b"\xff\xfe", "text/plain"),  # not UTF-8
        txt("runbook_high_latency.txt", LATENCY),
    ]

    skipped = pipeline.run_batch(documents)

    assert skipped == ["runbook_disk_full.pdf", "broken.txt"]
    assert list(stored(store, embedder)) == ["runbook_high_latency.txt"]


def test_batch_stops_when_the_embedder_fails(failing_embedder, store) -> None:
    # a backend outage is not a bad document: skipping would report a "successful" run with nothing ingested
    pipeline = RAGIngestionPipeline(TextMarkdownConverter(), Chunker(max_length=60), failing_embedder, store)

    with pytest.raises(EmbeddingError):
        pipeline.run_batch([txt("a.txt", "Restart the api pods."), txt("b.txt", "Run df -h.")])
