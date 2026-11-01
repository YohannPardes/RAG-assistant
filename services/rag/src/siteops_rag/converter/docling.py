from __future__ import annotations

from dataclasses import replace
from io import BytesIO
from typing import TYPE_CHECKING

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
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
    RawDocument,
    UnsupportedMediaType,
)
from siteops_rag.converter.text import TextMarkdownConverter
from siteops_rag.models import Element, TableRows, numbering_level, render_table

if TYPE_CHECKING:
    from docling.datamodel.base_models import Page
    from docling.datamodel.document import ConversionResult
    from docling_core.types.doc.base import BoundingBox
    from docling_core.types.doc.page import TextCell

# Docling detects the format from the file extension, so each media type maps to one.
_EXTENSIONS = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/markdown": ".md",
    "text/html": ".html",
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
        # keep each PDF page's text lines: Docling joins the lines of a code block or a list, they split them again
        pdf = PdfFormatOption(pipeline_options=PdfPipelineOptions(generate_parsed_pages=True))
        self.converter = DocumentConverter(
            allowed_formats=[InputFormat.PDF, InputFormat.DOCX, InputFormat.MD, InputFormat.HTML],
            format_options={InputFormat.PDF: pdf},
        )
        self.text_converter = TextMarkdownConverter()

    def convert_to_elements(self, raw_document: RawDocument) -> list[Element]:
        media_type = raw_document.media_type
        data = raw_document.data

        if media_type == "text/plain":
            return self.text_converter.convert_to_elements(raw_document)

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
        """Elements of a converted document, using the PDF pages' text lines when there are any."""
        # PDF only (empty otherwise): pages by number, 1-based like an item's prov.page_no
        pages = {page.page_no: page for page in result.pages}
        return DoclingDocumentConverter.elements_from_document(result.document, pages)

    @staticmethod
    def elements_from_document(doc: DoclingDocument, pages: dict[int, Page] | None = None) -> list[Element]:
        """Elements in reading order, empty ones dropped, headings nested by their numbering if Docling left them flat."""
        elements: list[Element] = []
        for item, _depth in doc.iterate_items():
            lines = DoclingDocumentConverter._lines(item, pages or {})
            for element in DoclingDocumentConverter._to_elements(item, doc, lines):
                if element.text.strip():
                    elements.append(element)
        return DoclingDocumentConverter._nest_flat_headings(elements)

    @staticmethod
    def _to_elements(item: object, doc: DoclingDocument, lines: list[TextCell]) -> list[Element]:
        """One element per item, rebuilt from its PDF text lines where Docling merged them."""
        if isinstance(item, ListItem) and len(steps := DoclingDocumentConverter._steps(lines)) > 1:
            # several numbered steps merged into one item: one element each
            marker = item.marker or "-"
            return [Element("paragraph", f"{marker} {step}", DoclingDocumentConverter._page(item)) for step in steps]

        element = DoclingDocumentConverter._to_element(item, doc)
        if element is None:
            return []
        if element.kind == "code" and len(lines) > 1:
            # joined with spaces, two commands read as one that can't be run: one per line again
            element = replace(element, text="\n".join(line.text for line in lines))
        return [element]

    @staticmethod
    def _lines(item: object, pages: dict[int, Page]) -> list[TextCell]:
        """The PDF text lines inside the item's box, top to bottom; [] without them (not a PDF, no box)."""
        if not isinstance(item, DocItem) or not item.prov:
            return []
        prov = item.prov[0]
        page = pages.get(prov.page_no)
        if page is None or page.parsed_page is None or page.size is None:
            return []

        height = page.size.height
        box = prov.bbox.to_top_left_origin(height)
        boxes = [(cell, cell.rect.to_bounding_box().to_top_left_origin(height)) for cell in page.parsed_page.textline_cells]
        inside = [(cell, b) for cell, b in boxes if DoclingDocumentConverter._inside(b, box)]
        return [cell for cell, _ in sorted(inside, key=lambda pair: (pair[1].t, pair[1].l))]

    @staticmethod
    def _inside(inner: BoundingBox, outer: BoundingBox, tolerance: float = 1.0) -> bool:
        """Top-left origin boxes: t < b."""
        return (outer.l - tolerance <= inner.l and inner.r <= outer.r + tolerance
                and outer.t - tolerance <= inner.t and inner.b <= outer.b + tolerance)

    @staticmethod
    def _steps(lines: list[TextCell]) -> list[str]:
        """Group a list's lines into items: a line at the left edge starts one, an indented line continues it."""
        if not lines:
            return []
        left = min(line.rect.to_bounding_box().l for line in lines)
        steps: list[str] = []
        for line in lines:
            if not steps or line.rect.to_bounding_box().l <= left + 1:
                steps.append(line.text)
            else:
                steps[-1] += " " + line.text
        return steps

    @staticmethod
    def _nest_flat_headings(elements: list[Element]) -> list[Element]:
        """Docling gives every heading of a PDF the same level, title included: take levels from the numbering.
        Only when all levels are equal, so the real levels of a markdown or docx document are kept."""
        if len({e.level for e in elements if e.kind == "heading"}) != 1:
            return elements

        nested, numbered_seen = [], False
        for element in elements:
            if element.kind == "heading":
                level = numbering_level(element.text)
                if level is None:
                    # unnumbered: the title before the first numbered heading, left as it is after
                    level = element.level if numbered_seen else 0
                else:
                    numbered_seen = True
                element = replace(element, level=level)
            nested.append(element)
        return nested

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
