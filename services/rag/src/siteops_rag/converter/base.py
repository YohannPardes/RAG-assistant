from abc import ABC, abstractmethod

from siteops_rag.models import Element


class UnsupportedMediaType(Exception):
    """The converter cannot handle this format."""


class ConversionError(Exception):
    """The input has a supported format but could not be converted."""


class DocumentMarkdownConverter(ABC):

    """The abstraction of the Document -> Markdown pipeline"""

    @abstractmethod
    def convert_to_elements(self, data : bytes, media_type : str) -> list[Element]:
        """return the document as markdownwith page markers."""
