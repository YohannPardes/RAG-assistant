import re

from siteops_rag.converter.base import (
    AbstractDocumentConverter,
    ConversionError,
    UnsupportedMediaType,
)
from siteops_rag.models import Element

# One or more empty (or whitespace-only) lines separate two blocks.
_BLANK_LINES = re.compile(r"\n\s*\n")


class TextMarkdownConverter(AbstractDocumentConverter):
    """Plain text -> one paragraph Element per blank-line-separated block. No Docling."""

    def convert_to_elements(self, data: bytes, media_type: str) -> list[Element]:
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
        return [Element("paragraph", block) for block in blocks if block.strip()]
