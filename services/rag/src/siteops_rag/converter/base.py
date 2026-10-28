from abc import ABC, abstractmethod


class UnsupportedMediaType(Exception):
    """The converter cannot handle this format."""


class ConversionError(Exception):
    """The input has a supported format but could not be converted."""


class DocumentMarkdownConverter(ABC):

    """The abstraction of the Document -> Markdown pipeline"""

    @abstractmethod
    def convert_to_markdown(self, data : bytes, media_type : str) -> str:
        """return the document as markdownwith page markers."""
