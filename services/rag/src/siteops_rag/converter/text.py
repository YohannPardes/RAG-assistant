import re

from siteops_rag.converter.base import (
    AbstractDocumentConverter,
    ConversionError,
    RawDocument,
    UnsupportedMediaType,
)
from siteops_rag.models import Element, numbering_level

# One or more empty (or whitespace-only) lines separate two blocks.
_BLANK_LINES = re.compile(r"\n\s*\n")

# The underline of a setext heading: "=" under the document title, "-" under a section.
_UNDERLINE = re.compile(r"={3,}|-{3,}")


class TextMarkdownConverter(AbstractDocumentConverter):
    """Plain text -> one Element per blank-line-separated block, a heading or a paragraph. No Docling."""

    def convert_to_elements(self, raw_document: RawDocument) -> list[Element]:
        media_type = raw_document.media_type
        data = raw_document.data

        if media_type != "text/plain":
            raise UnsupportedMediaType(media_type)

        # Bytes -> str; a file that isn't valid UTF-8 is a conversion failure
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ConversionError(str(exc)) from exc

        # Normalise Windows line endings so the blank-line split works on any file.
        text = text.replace("\r\n", "\n")

        # Keep line breaks inside a block: ASCII tables and aligned lists stay readable.
        blocks = (block.rstrip() for block in _BLANK_LINES.split(text))
        return [self._to_element(block) for block in blocks if block.strip()]

    @staticmethod
    def _to_element(block: str) -> Element:
        """A heading if the block is an underlined title or a numbered line on its own, else a paragraph."""
        lines = block.split("\n")

        # a title and its underline (setext style), with the levels Docling gives a markdown runbook
        if len(lines) == 2 and _UNDERLINE.fullmatch(lines[1].strip()):
            title = lines[0].strip()
            level = 0 if lines[1].strip().startswith("=") else numbering_level(title) or 1
            return Element("heading", title, level=level)

        # a numbered line on its own, not indented: "3.1 Is it one pod or all of them?"
        # (indented, or with more lines, it is a step of a procedure)
        if len(lines) == 1 and not block[0].isspace() and (number_level := numbering_level(block)) is not None:
            return Element("heading", block, level=number_level)

        return Element("paragraph", block)
