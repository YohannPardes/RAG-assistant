"""Ingest every runbook of a folder, subfolders included, into the vector store: a job run once, not an endpoint.

Usage: uv run python scripts/ingest.py [folder]   (default: data/)

A file is ingested if its extension is one of MEDIA_TYPES; every other file gets a warning, and so does a document the
pipeline can't convert. A document's source is its path inside the folder: from data/, runbooks/runbook_x.pdf.
"""

import logging
import sys
from pathlib import Path

from siteops_rag.container import build_pipeline
from siteops_rag.converter.base import RawDocument

DEFAULT_FOLDER = Path(__file__).resolve().parents[1] / "data"
MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".html": "text/html",
}

logger = logging.getLogger("ingest")


def load_documents(folder: Path) -> tuple[list[RawDocument], list[str]]:
    """Every file under `folder` with a supported extension, as a document; and the other files' paths, left out."""
    documents, ignored = [], []
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        source = path.relative_to(folder).as_posix()  # "/" on every OS, wherever the folder lives: chunk ids depend on it
        if path.suffix.lower() in MEDIA_TYPES:
            documents.append(RawDocument(source=source, data=path.read_bytes(), media_type=MEDIA_TYPES[path.suffix.lower()]))
        else:
            ignored.append(source)
    return documents, ignored


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_FOLDER

    documents, ignored = load_documents(folder)
    for source in ignored:
        logger.warning("not ingested: %s (supported: %s)", source, ", ".join(MEDIA_TYPES))
    skipped = build_pipeline().run_batch(documents)  # it warns about each document it skips, with the reason
    print(f"ingested {len(documents) - len(skipped)} of {len(documents)} documents from {folder}; not ingested: {len(ignored) + len(skipped)} files")


if __name__ == "__main__":
    main()
