from abc import ABC, abstractmethod
from dataclasses import dataclass

from siteops_rag.models import Element


class UnsupportedMediaType(Exception):
    """The converter cannot handle this format."""


class ConversionError(Exception):
    """The input has a supported format but could not be converted."""


@dataclass(frozen=True)
class RawDocument:
    source: str  # stable name: "network/link_drop.md" (citations + chunk ids)
    data: bytes  # the file's content
    media_type: str  # "application/pdf", "text/markdown"… (picks the conversion)


class AbstractDocumentConverter(ABC):
    """The abstraction of the Document -> Elements(kind, text, page, levels, row, language)"""

    @abstractmethod
    def convert_to_elements(self, raw_document: RawDocument) -> list[Element]:
        """return a list of Elements from the input data"""
