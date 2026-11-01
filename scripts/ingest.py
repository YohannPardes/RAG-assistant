"""Ingest every runbook of a folder into the vector store (arch/5: a job, not an endpoint).

Usage: uv run python scripts/ingest.py [folder]   (default: data/runbooks)
"""

import logging
import sys
from pathlib import Path

from siteops_rag.container import build_pipeline
from siteops_rag.converter.base import RawDocument

MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".html": "text/html",
}


def load_documents(folder: Path) -> list[RawDocument]:
    """Every runbook under `folder`, subfolders included; other files (images...) are ignored."""
    return [
        # source = path inside the folder, so it stays the same wherever the folder lives (citations, chunk ids)
        RawDocument(source=path.relative_to(folder).as_posix(), data=path.read_bytes(), media_type=MEDIA_TYPES[path.suffix.lower()])
        for path in sorted(folder.rglob("*"))
        if path.suffix.lower() in MEDIA_TYPES
    ]


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "data" / "runbooks"

    documents = load_documents(folder)
    skipped = build_pipeline().run_batch(documents)
    print(f"ingested {len(documents) - len(skipped)} of {len(documents)} documents; skipped: {skipped or 'none'}")


if __name__ == "__main__":
    main()
