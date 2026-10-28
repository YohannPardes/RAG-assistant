from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING

from docling.datamodel.base_models import InputFormat
from docling.document_converter import DocumentConverter
from docling.exceptions import BaseError as DoclingError
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.items.code import CodeItem
from docling_core.types.doc.items.node import DocItem
from docling_core.types.doc.items.picture.picture import PictureItem
from docling_core.types.doc.items.table.table import TableItem
from docling_core.types.doc.items.text import ListItem, SectionHeaderItem, TextItem, TitleItem
from docling_core.types.doc.labels import CodeLanguageLabel, DocItemLabel
from docling_core.types.io import DocumentStream

from siteops_rag.converter.base import (
    AbstractDocumentConverter,
    ConversionError,
    UnsupportedMediaType,
)
from siteops_rag.converter.text import TextMarkdownConverter
from siteops_rag.models import Element, TableRows, render_table

if TYPE_CHECKING:
    from docling.datamodel.document import ConversionResult

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


class DoclingDocumentConverter(AbstractDocumentConverter):
    def __init__(self) -> None:
        super().__init__()
        self.converter = DocumentConverter(
            allowed_formats=[InputFormat.PDF, InputFormat.DOCX, InputFormat.MD]
        )
        self.text_converter = TextMarkdownConverter()
    def convert_to_elements(self, data: bytes, media_type: str) -> list[Element]:
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
        return DoclingDocumentConverter.elements_from_result(result)


    @staticmethod
    def elements_from_result(result: ConversionResult) -> list[Element]:
        return DoclingDocumentConverter.elements_from_document(result.document)

    @staticmethod
    def elements_from_document(doc: DoclingDocument) -> list[Element]:
        elements: list[Element] = []
        for item, _depth in doc.iterate_items():
            element = DoclingDocumentConverter._to_element(item, doc)
            if element is not None and element.text.strip():
                elements.append(element)
        return elements

    @staticmethod
    def _page(item: DocItem) -> int | None:
        """Extract the page number, if any."""
        return item.prov[0].page_no if item.prov else None

    @staticmethod
    def _table_rows(item: TableItem) -> TableRows:
        """The table as a matrix of cell texts; merged cells repeat their text in every slot."""
        return tuple(tuple(cell.text for cell in row) for row in item.data.grid)

    @staticmethod
    def _to_element(item: object, doc: DoclingDocument) -> Element | None:
        if not isinstance(item, DocItem):
            return None  # groups (lists, sections) carry no text of their own
        page = DoclingDocumentConverter._page(item)

        # Order matters: TitleItem, SectionHeaderItem, ListItem and CodeItem are all TextItems.
        if isinstance(item, TitleItem):
            return Element("heading", item.text, page, level=0)
        if isinstance(item, SectionHeaderItem):
            return Element("heading", item.text, page, level=item.level)
        if isinstance(item, CodeItem):
            # Often UNKNOWN: the chunk is then fenced without a language.
            unknown = item.code_language == CodeLanguageLabel.UNKNOWN
            language = None if unknown else item.code_language.value.lower()
            return Element("code", item.text, page, language=language)
        if isinstance(item, ListItem):
            marker = item.marker or "-"
            return Element("paragraph", f"{marker} {item.text}", page)
        if isinstance(item, TableItem):
            # Read the structured grid, not export_to_markdown(): the markdown export
            # escapes pipes inside cells as "&#124;", which breaks shell commands.
            rows = DoclingDocumentConverter._table_rows(item)
            text = render_table(rows, caption=item.caption_text(doc))
            return Element("table", text, page, rows=rows)
        if isinstance(item, PictureItem):
            # The image itself is not searchable; its caption is the only trace of it.
            caption = item.caption_text(doc)
            return Element("caption", caption, page) if caption else None
        if isinstance(item, TextItem) and item.label in _PARAGRAPH_LABELS:
            return Element("paragraph", item.text, page)
        # Captions are emitted with their table/picture above; page headers/footers, index, etc. are noise.
        return None