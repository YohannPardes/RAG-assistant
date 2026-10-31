import re
from dataclasses import dataclass
from typing import Literal

ElementKind = Literal["heading", "paragraph", "table", "code", "caption"]

TableRows = tuple[tuple[str, ...], ...]

@dataclass(frozen=True)
class Element:
    kind: ElementKind
    text: str
    page: int | None = None
    level: int | None = None  # headings only: 0 = document title, 1 = top section, ...
    rows: TableRows | None = None
    language: str | None = None  # code only: fence language, e.g. "bash" (None when unknown)


# A section number at the start of a heading ("4." or "4.1"), then its title.
_SECTION_NUMBER = re.compile(r"(\d+(?:\.\d+)*)\.?\s+\S")


def numbering_level(text: str) -> int | None:
    """Heading level from its section number: "4. Diagnosis" -> 1, "4.1 Find what..." -> 2; None without one."""
    match = _SECTION_NUMBER.match(text)
    return match.group(1).count(".") + 1 if match else None


def render_table(rows: TableRows, caption: str = "") -> str:
    """Render a table as GitHub-flavoured markdown.

    Pipes inside cells are escaped as ``\\|`` (the markdown standard), so the
    table structure survives while commands like ``du | sort`` stay readable.
    """
    if not rows:
        return caption

    def line(cells: tuple[str, ...]) -> str:
        return "| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |"

    header, *body = rows
    parts = [line(header), "| " + " | ".join("---" for _ in header) + " |"]
    parts += [line(r) for r in body]
    table = "\n".join(parts)
    return f"{caption}\n\n{table}" if caption else table

@dataclass(frozen=True)
class Document:
    source: str
    elements: tuple[Element, ...]


@dataclass(frozen=True)
class Chunk:

    """This class defines what is a document chunk """
    chunk_id: str
    source: str                     # document id, e.g. "runbook_disk_full.pdf"
    text: str                       # the content
    heading_path: tuple[str, ...]   # ("RB-101: Disk full", "4. Diagnosis", "4.1 Find what uses space")
    page: int | None                # page of the first element