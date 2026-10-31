"""The sample runbooks through Docling, the configured embedding model and a real Qdrant."""

import pytest

from siteops_rag.config import Settings
from siteops_rag.container import build_rag

pytestmark = pytest.mark.integration

SOURCES = {"runbook_cert_expiry.md", "runbook_db_failover.docx", "runbook_disk_full.pdf", "runbook_high_latency.txt"}


def test_every_runbook_is_stored_with_its_metadata(ingested: Settings) -> None:
    rag = build_rag(ingested)

    hits = rag.vector_store.search(rag.embedder.embed("runbook"), top_k=1000)

    assert {h.chunk.source for h in hits} == SOURCES
    assert all(h.chunk.heading_path for h in hits if h.chunk.source != "runbook_high_latency.txt")  # .txt has no headings
    assert {h.chunk.page for h in hits if h.chunk.source == "runbook_disk_full.pdf"} >= {1, 2}  # only the PDF has pages
    assert all(len(h.chunk.text) <= ingested.chunking.max_length for h in hits)


@pytest.mark.parametrize(
    ("question", "source"),
    [
        ("The disk is full: how do I find what uses the space on /var/log?", "runbook_disk_full.pdf"),
        ("How do I renew an expiring TLS certificate?", "runbook_cert_expiry.md"),
        ("The primary PostgreSQL database is down, how do I fail over?", "runbook_db_failover.docx"),
        ("p99 latency of the API is above 800 ms", "runbook_high_latency.txt"),
    ],
)
def test_a_question_retrieves_its_runbook_first(ingested: Settings, question: str, source: str) -> None:
    assert build_rag(ingested).search(question, top_k=1)[0].chunk.source == source


def test_ingesting_again_does_not_duplicate_anything(ingested: Settings, ingest_script, fixtures_dir) -> None:
    # running the job twice (a cron, a retry) must leave the store as one run does (rag/3, rag/12)
    from siteops_rag.container import build_pipeline

    rag = build_rag(ingested)
    before = rag.vector_store.count()

    build_pipeline(ingested).run_batch(ingest_script.load_documents(fixtures_dir))

    assert rag.vector_store.count() == before
