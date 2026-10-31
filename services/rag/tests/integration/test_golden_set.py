"""Every golden-set label must name a section that ingestion really produces. A label no chunk carries
scores as a retrieval miss forever, whatever the retriever does (scope/4)."""

import json
from pathlib import Path
from types import ModuleType

import pytest

from siteops_rag.config import Settings
from siteops_rag.ingestion.chunking import Chunker
from siteops_rag.models import Document

pytestmark = pytest.mark.integration

DATA_DIR = Path(__file__).resolve().parents[4] / "data"
GOLDEN_SET = [
    json.loads(line)
    for line in (DATA_DIR / "golden_set.jsonl").read_text(encoding="utf-8").splitlines()
    if line.strip()
]

# strict: once the text converter emits headings these pass, and the marker must go
NO_TXT_HEADINGS = pytest.mark.xfail(strict=True, reason="TextMarkdownConverter emits no headings: .txt chunks have none")


@pytest.fixture(scope="module")
def sections(docling_converter, ingest_script: ModuleType, configured: Settings) -> set[tuple[str, str]]:
    """(source, last heading) of every chunk of data/runbooks, chunked the way the configured pipeline does it."""
    chunker = Chunker(max_length=configured.chunking.max_length)
    found = set()
    for raw in ingest_script.load_documents(DATA_DIR / "runbooks"):
        elements = docling_converter.convert_to_elements(raw)
        for chunk in chunker.chunk_elements(Document(source=raw.source, elements=elements)):
            if chunk.heading_path:
                found.add((chunk.source, chunk.heading_path[-1]))
    return found


@pytest.mark.parametrize(
    "row",
    [
        pytest.param(row, id=f"{i}-{row['expected_source']}",
                     marks=NO_TXT_HEADINGS if row["expected_source"].endswith(".txt") else ())
        for i, row in enumerate(GOLDEN_SET)
    ],
)
def test_expected_section_is_a_chunk_section(row, sections) -> None:
    assert (row["expected_source"], row["expected_section"]) in sections
