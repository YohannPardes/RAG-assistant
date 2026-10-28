from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING: # allow for type checking without importing at runtime
    from siteops_rag.converter.docling import DoclingMarkdownConverter

@pytest.fixture(scope="session")
def docling_converter() -> DoclingMarkdownConverter:
    from siteops_rag.converter.docling import DoclingMarkdownConverter
    return DoclingMarkdownConverter()