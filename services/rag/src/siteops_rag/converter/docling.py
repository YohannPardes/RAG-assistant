from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING

from docling.datamodel.document import DocumentStream, InputFormat
from docling.document_converter import DocumentConverter
from docling.exceptions import BaseError as DoclingError
from docling_core.types.doc import (
    CodeItem,
    DocItem,
    DocItemLabel,
    DoclingDocument,
    ListItem,
    PictureItem,
    SectionHeaderItem,
    TableItem,
    TextItem,
    TitleItem,
)

from siteops_rag.converter.base import (
    ConversionError,
    DocumentMarkdownConverter,
    UnsupportedMediaType,
)
from siteops_rag.converter.text import TextMarkdownConverter
from siteops_rag.models import Element

if TYPE_CHECKING:
    from docling.datamodel.document import ConversionResult


PAGE_BREAK = "<!-- page-break -->"
IMAGE_PLACEHOLDER = "<!-- image -->"

# Docling detects the format from the file extension, so each media type maps to one.
_EXTENSIONS = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/markdown": ".md",
}


_PARAGRAPH_LABELS = {
    DocItemLabel.TEXT,
    DocItemLabel.PARAGRAPH,
    DocItemLabel.FOOTNOTE,
    DocItemLabel.FORMULA,
}


class DoclingMarkdownConverter(DocumentMarkdownConverter):
    def __init__(self) -> None:
        super().__init__()
        self.converter = DocumentConverter(
            allowed_formats=[InputFormat.PDF, InputFormat.DOCX, InputFormat.MD]
        )
        self.text_converter = TextMarkdownConverter()
    def convert_to_elements(self, data: bytes, media_type: str) -> list[Element]:
        # Docling has no plain-text input format; plain text is already valid markdown.
        if media_type == "text/plain":
            return self.text_converter.convert_to_elements(data, media_type=media_type)

        extension = _EXTENSIONS.get(media_type)
        if extension is None:
            raise UnsupportedMediaType(media_type)

        stream = DocumentStream(name=f"document{extension}", stream=BytesIO(data))
        try:
            result = self.converter.convert(stream)
        except DoclingError as exc:
            raise ConversionError(str(exc)) from exc
        return DoclingMarkdownConverter.elements_from_result(result)


    @staticmethod
    def elements_from_result(result: ConversionResult) -> list[Element]:
        return DoclingMarkdownConverter.elements_from_document(result.document)

    @staticmethod
    def elements_from_document(doc: DoclingDocument) -> list[Element]:
        elements: list[Element] = []
        for item, _depth in doc.iterate_items():
            element = DoclingMarkdownConverter._to_element(item, doc)
            if element is not None and element.text.strip():
                elements.append(element)
        return elements

    @staticmethod
    def _page(item: DocItem) -> int | None:
        """Exctract the page number, if any."""
        return item.prov[0].page_no if item.prov else None

    @staticmethod
    def _to_element(item: object, doc: DoclingDocument) -> Element | None:
        if not isinstance(item, DocItem):
            return None  # groups (lists, sections) carry no text of their own
        page = DoclingMarkdownConverter._page(item)

        # Order matters: TitleItem, SectionHeaderItem, ListItem and CodeItem are all TextItems.
        if isinstance(item, TitleItem):
            return Element("heading", item.text, page, level=0)
        if isinstance(item, SectionHeaderItem):
            return Element("heading", item.text, page, level=item.level)
        if isinstance(item, CodeItem):
            return Element("code", item.text, page)
        if isinstance(item, ListItem):
            marker = item.marker or "-"
            return Element("paragraph", f"{marker} {item.text}", page)
        if isinstance(item, TableItem):
            # export_to_markdown already puts the table's caption above it.
            return Element("table", item.export_to_markdown(doc), page)
        if isinstance(item, PictureItem):
            # The image itself is not searchable; its caption is the only trace of it.
            caption = item.caption_text(doc)
            return Element("caption", caption, page) if caption else None
        if isinstance(item, TextItem) and item.label in _PARAGRAPH_LABELS:
            return Element("paragraph", item.text, page)
        # Captions are emitted with their table/picture above; page headers/footers, index, etc. are noise.
        return None