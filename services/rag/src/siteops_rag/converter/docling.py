from io import BytesIO

from docling.datamodel.base_models import DocumentStream, InputFormat
from docling.document_converter import DocumentConverter
from docling.exceptions import BaseError as DoclingError

from siteops_rag.converter.base import (
    ConversionError,
    DocumentMarkdownConverter,
    UnsupportedMediaType,
)

PAGE_BREAK = "<!-- page-break -->"
IMAGE_PLACEHOLDER = "<!-- image -->"

# Docling detects the format from the file extension, so each media type maps to one.
_EXTENSIONS = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/markdown": ".md",
}


class DoclingMarkdownConverter(DocumentMarkdownConverter):
    def __init__(self) -> None:
        super().__init__()
        self.converter = DocumentConverter(
            allowed_formats=[InputFormat.PDF, InputFormat.DOCX, InputFormat.MD]
        )

    def convert_to_markdown(self, data: bytes, media_type: str) -> str:
        # Docling has no plain-text input format; plain text is already valid markdown.
        if media_type == "text/plain":
            return data.decode("utf-8")

        extension = _EXTENSIONS.get(media_type)
        if extension is None:
            raise UnsupportedMediaType(media_type)

        stream = DocumentStream(name=f"document{extension}", stream=BytesIO(data))
        try:
            result = self.converter.convert(stream)
        except DoclingError as exc:
            raise ConversionError(str(exc)) from exc
        # escape_html=False keeps commands copy-pasteable: "&&" must not become "&amp;&amp;".
        return result.document.export_to_markdown(
            page_break_placeholder=PAGE_BREAK,
            image_placeholder=IMAGE_PLACEHOLDER,
            escape_html=False,
        )
