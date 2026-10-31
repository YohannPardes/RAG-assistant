import pytest

from siteops_rag.converter.base import ConversionError, RawDocument, UnsupportedMediaType
from siteops_rag.converter.text import TextMarkdownConverter
from siteops_rag.ingestion.chunking import Chunker
from siteops_rag.models import Document


def convert(text: str | bytes, media_type: str = "text/plain") -> list[tuple[str, str]]:
    data = text.encode() if isinstance(text, str) else text
    elements = TextMarkdownConverter().convert_to_elements(RawDocument("runbook.txt", data, media_type))
    return [(e.kind, e.text) for e in elements]


def headings(text: str) -> list[tuple[int | None, str]]:
    elements = TextMarkdownConverter().convert_to_elements(RawDocument("runbook.txt", text.encode(), "text/plain"))
    return [(e.level, e.text) for e in elements if e.kind == "heading"]


def test_blank_lines_separate_paragraphs() -> None:
    text = "Alert fires.\n\nCheck the pods.\n   \t\nRestart them."  # a whitespace-only line is blank too

    assert convert(text) == [("paragraph", "Alert fires."), ("paragraph", "Check the pods."), ("paragraph", "Restart them.")]


def test_line_breaks_inside_a_block_are_kept() -> None:
    # an ASCII table or an aligned list must stay readable
    table = "Endpoint        p99\nPOST /v2/orders 800 ms"

    assert convert(f"Thresholds:\n\n{table}\n") == [("paragraph", "Thresholds:"), ("paragraph", table)]


def test_windows_line_endings_give_the_same_elements_as_unix_ones() -> None:
    unix = "Alert fires.\n\nEndpoint        p99\nPOST /v2/orders 800 ms\n"

    # no "\r" left inside a block: it would end up in chunks, embeddings and quotes
    assert convert(unix.replace("\n", "\r\n")) == convert(unix)


@pytest.mark.parametrize("text", ["", "\n\n  \n"], ids=["empty", "blank-only"])
def test_empty_document_gives_no_elements(text) -> None:
    assert convert(text) == []


def test_other_media_types_are_unsupported() -> None:
    with pytest.raises(UnsupportedMediaType):
        convert("# Title", media_type="text/markdown")


def test_bytes_that_are_not_utf8_are_a_conversion_error() -> None:
    with pytest.raises(ConversionError):
        convert(b"\xff\xfe broken")


def test_underlined_lines_are_headings() -> None:
    # same levels as Docling gives a markdown runbook: "=" underlines the title, "-" a numbered section
    text = "RB-415: HIGH API LATENCY\n========================\n\n1. SYMPTOMS\n-----------\n\nAlert fires."

    assert headings(text) == [(0, "RB-415: HIGH API LATENCY"), (1, "1. SYMPTOMS")]


def test_a_numbered_line_on_its_own_is_a_heading_one_level_deeper_per_number() -> None:
    text = "3. DIAGNOSIS\n------------\n\n3.1 Is it one pod or all of them?\n\n    kubectl -n api top pods"

    assert headings(text) == [(1, "3. DIAGNOSIS"), (2, "3.1 Is it one pod or all of them?")]


def test_a_numbered_step_spanning_several_lines_is_not_a_heading() -> None:
    text = "1. If CPU is above 80% on all pods, scale out:\n     kubectl -n api scale deployment public-api --replicas=12"

    assert headings(text) == []


def test_every_chunk_of_the_txt_runbook_has_a_section(fixtures_dir) -> None:
    # without one, a chunk can't be cited by section and never matches a golden-set label (scope/4)
    raw = RawDocument("runbook_high_latency.txt", (fixtures_dir / "runbook_high_latency.txt").read_bytes(), "text/plain")
    elements = TextMarkdownConverter().convert_to_elements(raw)
    chunks = Chunker(max_length=1000).chunk_elements(Document(source=raw.source, elements=elements))

    assert all(chunk.heading_path for chunk in chunks)
    assert {"1. SYMPTOMS", "3.2 Is a dependency slow?", "4. REMEDIATION", "5. ESCALATION"} <= {
        chunk.heading_path[-1] for chunk in chunks
    }
