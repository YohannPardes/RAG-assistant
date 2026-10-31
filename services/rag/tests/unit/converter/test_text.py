import pytest

from siteops_rag.converter.base import ConversionError, RawDocument, UnsupportedMediaType
from siteops_rag.converter.text import TextMarkdownConverter


def convert(text: str | bytes, media_type: str = "text/plain") -> list[tuple[str, str]]:
    data = text.encode() if isinstance(text, str) else text
    elements = TextMarkdownConverter().convert_to_elements(RawDocument("runbook.txt", data, media_type))
    return [(e.kind, e.text) for e in elements]


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
