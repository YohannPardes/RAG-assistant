from dataclasses import dataclass
from typing import Literal

ElementKind = Literal["heading", "paragraph", "table", "code", "caption"]


@dataclass(frozen=True)
class Element:
    kind: ElementKind
    text: str
    page: int | None = None
    level: int | None = None  # headings only: 0 = document title, 1 = top section, ...


class Chunk:

    """This class defines what is a document chunk """