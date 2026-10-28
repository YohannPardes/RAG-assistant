import logging
import uuid
from collections.abc import Callable

from siteops_rag.models import Chunk, Document, Element, render_table

logger = logging.getLogger(__name__)


CHUNK_NAMESPACE = uuid.UUID("89c30320-bb66-4158-906c-fbd729a1df0b")   # any fixed UUID: generate one once with uuid.uuid4() and paste it in

# Split points from coarsest to finest: paragraph, line, sentence, word. Then a hard cut.
SEPARATORS = ("\n\n", "\n", ". ", " ")

# Split points inside one too-long command: line continuation, then shell operators, then word.
CODE_SEPARATORS = ("\\\n", " && ", " || ", " | ", "; ", " ")


class Chunker:

    def __init__(self, max_length : int, length_func : Callable[[str], int] = len) -> None:

        self.max_length = max_length
        self.length_func = length_func

    def chunk_elements(self, doc : Document) -> list[Chunk]:
        """Chunk the document into smaller pieces, each with a heading path and page number.
        Phase 1 splits each element into pieces that fit; phase 2 packs the pieces of one section together.
        """

        chunks : list[Chunk] = []
        headings : list[tuple[int, str]] = []  # (level, text) of the open headings, outermost first
        section : list[tuple[str, int | None]] = []  # (text, page) pieces of the current section
        elem_list = doc.elements
        for element in elem_list:
            heading_path = tuple(text for _, text in headings)

            # a heading is not a piece: it closes the section, so a chunk never spans two sections
            if element.kind == "heading" and element.level is not None:
                chunks.extend(self._pack_section(doc.source, heading_path, section))
                section = []

                # it closes every open heading at its level or deeper, then opens itself
                # (levels may skip or not start at 0, e.g. a docx without a title)
                while headings and headings[-1][0] >= element.level:
                    headings.pop()
                headings.append((element.level, element.text))
                continue

            # phase 1: split the element into pieces that fit next to the heading-path prefix
            limit = self.max_length - self.length_func(self._prefix(heading_path))
            section.extend((piece, element.page) for piece in self._chunk_element(element, limit))

        chunks.extend(self._pack_section(doc.source, tuple(text for _, text in headings), section))
        return chunks

    @staticmethod
    def _prefix(heading_path : tuple[str, ...]) -> str:
        """Every chunk starts with its heading path, so it reads (and embeds) on its own."""
        return f"{' > '.join(heading_path)}\n\n" if heading_path else ""

    def _pack_section(self, source : str, heading_path : tuple[str, ...],
                      pieces : list[tuple[str, int | None]]) -> list[Chunk]:
        """Phase 2: pack consecutive pieces of one section into chunks, as many as fit.
        The prefix counts toward the limit; a chunk's page is the page of its first piece.
        """
        prefix = self._prefix(heading_path)
        limit = self.max_length - self.length_func(prefix)

        def fits(group : list[tuple[str, int | None]]) -> bool:
            return self.length_func("\n\n".join(text for text, _ in group)) <= limit

        groups = []
        current : list[tuple[str, int | None]] = []
        for piece in pieces:
            # the piece fits with the current ones (or starts an empty chunk): add it
            if not current or fits([*current, piece]):
                current.append(piece)
                continue

            # an intro ending in ":" ("Update the secret:") is never last: it moves on with what it introduces
            carry = []
            if len(current) > 1 and current[-1][0].rstrip().endswith(":") and fits([current[-1], piece]):
                carry = [current.pop()]

            groups.append(current)
            current = [*carry, piece]
        if current:
            groups.append(current)

        chunks = []
        for group in groups:
            text = prefix + "\n\n".join(text for text, _ in group)
            chunks.append(Chunk(
                chunk_id = str(uuid.uuid5(CHUNK_NAMESPACE, f"{source}|{' > '.join(heading_path)}|{text}")),
                source = source,
                text = text,
                heading_path = heading_path,
                page = group[0][1]
            ))
        return chunks

    def _chunk_element(self, elem : Element, limit : int) -> list[str]:
        """This function chunks the text of an element into smaller pieces if it exceeds the limit
        It never cuts a phrase in the middle, a table mid row, or a code block in the middle.
        As much as possible.
        """
        # code first: it is always fenced, even when it fits
        if elem.kind == "code":
            return self._chunk_code(elem, limit)

        if self.length_func(elem.text) <= limit:
            return [elem.text]

        # if the text is longer than authorized, we need to chunk it into smaller pieces
        if elem.kind in ["paragraph", "caption", "heading"]:
            return self._chunk_regular_text(elem.text, limit=limit)

        if elem.kind ==  "table":
            return self._chunk_table(elem, limit)

        # fallback: chunk it as regular text
        return self._chunk_regular_text(elem.text, limit=limit)
        

    def _chunk_table(self, table : Element, limit : int) -> list[str]:
        """Chunk a table by rows, repeating the header in every chunk so each one reads on its own."""

        # no structured rows (e.g. a table from plain text): treat it as regular text
        if not table.rows:
            return self._chunk_regular_text(table.text, limit=limit)

        header, *body = table.rows

        chunks = []
        current_rows: list[tuple[str, ...]] = []
        for row in body:
            # mismatched row: warn only, it is still chunked with the header like any other row
            if len(row) != len(header):
                logger.warning("Table row has %d cells, but header has %d: %s", len(row), len(header), row)

            # Try to fit as many rows as possible in each chunk, while respecting the limit
            candidate = render_table((header, *current_rows, row))
            if self.length_func(candidate) <= limit:
                current_rows.append(row)
                continue

            # the row doesn't fit with the others: close the current chunk first
            if current_rows:
                chunks.append(render_table((header, *current_rows)))
                current_rows = []

            # the row fits on its own (with the header): start a new chunk with it
            if self.length_func(render_table((header, row))) <= limit:
                current_rows.append(row)
                continue

            # if a row is not fitting, split it into chunks of as many cells as fit
            chunks.extend(self._chunk_row_cells(header, row, limit))

        if current_rows:
            chunks.append(render_table((header, *current_rows)))
        return chunks

    def _chunk_row_cells(self, header : tuple[str, ...], row : tuple[str, ...], limit : int) -> list[str]:
        """Chunk one row that is too big for table form: one "column : value" line per cell,
        packing as many whole cells as fit into each chunk.
        """
        # every chunk after the first starts with the row key (first column), so it says which row it belongs to
        # (skipped when the key is the only column, or too long to repeat)
        key = f"{header[0]} : {row[0]}" if len(header) > 1 and row else ""
        if key and self.length_func(key) <= limit // 2:
            limit -= self.length_func(key + "\n")
        else:
            key = ""

        chunks = []
        current_cells: list[str] = []
        for name, value in zip(header, row, strict=False):
            label = f"{name} : "
            cell = label + value

            # the cell fits with the current ones: add it
            if self.length_func("\n".join([*current_cells, cell])) <= limit:
                current_cells.append(cell)
                continue

            # it doesn't: close the current chunk
            if current_cells:
                chunks.append("\n".join(current_cells))
                current_cells = []

            # the cell fits on its own: start a new chunk with it
            if self.length_func(cell) <= limit:
                current_cells.append(cell)
                continue

            # a single cell is too big: split its value, keeping the "column : " label on every piece
            pieces = self._chunk_regular_text(value, limit=limit - self.length_func(label))
            chunks.extend(label + piece for piece in pieces)

        if current_cells:
            chunks.append("\n".join(current_cells))
        if not key:
            return chunks
        # a first chunk holding only the key adds nothing: every other chunk repeats it
        first = chunks[:1] if chunks and chunks[0] != key else []
        return first + [f"{key}\n{chunk}" for chunk in chunks[1:]]

    def _chunk_code(self, code : Element, limit : int) -> list[str]:
        """Chunk a code block between lines only (blank lines first, then line breaks), never inside a line.
        Every chunk is fenced, so the LLM knows it is code and reproduces it verbatim.
        """
        # keep whitespace exactly: only drop the empty lines at the very start and end
        lines = code.text.split("\n")
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            return []  # empty or whitespace-only block: nothing to retrieve

        def fence(body : str) -> str:
            return f"```{code.language or ''}\n{body}\n```"

        # the fences count toward the limit
        budget = limit - self.length_func(fence(""))

        chunks = []
        current_blocks: list[str] = []
        for block in "\n".join(lines).split("\n\n"):
            # the block fits with the current ones: add it
            if self.length_func("\n\n".join([*current_blocks, block])) <= budget:
                current_blocks.append(block)
                continue

            # it doesn't: close the current chunk
            if current_blocks:
                chunks.append(fence("\n\n".join(current_blocks)))
                current_blocks = []

            # the block fits on its own: start a new chunk with it
            if self.length_func(block) <= budget:
                current_blocks.append(block)
                continue

            # a single block is too big: split it between commands
            chunks.extend(fence(body) for body in self._chunk_code_lines(block, budget))

        if current_blocks:
            chunks.append(fence("\n\n".join(current_blocks)))
        return chunks

    def _chunk_code_lines(self, block : str, budget : int) -> list[str]:
        """Pack as many whole commands of a block as fit. A command longer than the budget
        is cut at its operators (&&, |, ; ...), and per word only if nothing else helps.
        """
        # a line ending in "\" continues on the next one: keep the whole command together
        commands: list[str] = []
        for line in block.split("\n"):
            if commands and commands[-1].endswith("\\"):
                commands[-1] += "\n" + line
            else:
                commands.append(line)

        chunks = []
        current = ""
        for command in commands:
            # a command too long on its own: close the current chunk, then cut the command
            if self.length_func(command) > budget:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.extend(self._chunk_regular_text(command, CODE_SEPARATORS, budget))
                continue

            candidate = f"{current}\n{command}" if current else command
            if self.length_func(candidate) <= budget or not current:
                current = candidate
            else:
                chunks.append(current)
                current = command
        if current:
            chunks.append(current)
        return chunks

    def _chunk_regular_text(self, text : str, separators : tuple[str, ...] = SEPARATORS,
                            limit : int | None = None) -> list[str]:
        """This function chunks a regular text (paragraph, caption, heading) into smaller pieces if it exceeds the max_tokens limit
        It tries to never cut a phrase in the middle.
        """
        # a label, fence or heading prefix can eat the whole limit: keep at least 1 so no text is lost
        limit = max(1, self.max_length if limit is None else limit)
        chunks = []
        current_chunk = ""
        for line in self._split_recursive(text, separators, limit):

            if self.length_func(current_chunk + line) > limit:
                chunks.append(current_chunk)
                current_chunk = line
            else:
                current_chunk += line
        if current_chunk:
            chunks.append(current_chunk)
        return chunks

    def _split_recursive(self, text : str, separators : tuple[str, ...] = SEPARATORS,
                         limit : int | None = None) -> list[str]:
        """Split text until every piece fits in limit (default max_tokens), trying the coarsest separator first.
        Separators are kept at the end of each piece, so "".join(pieces) == text.
        """
        limit = max(1, self.max_length if limit is None else limit)

        # small enough: nothing to split
        if self.length_func(text) <= limit:
            return [text]

        # no separator left: hard cut every limit characters
        if not separators:
            return [text[i:i + limit] for i in range(0, len(text), limit)]

        separator, finer = separators[0], separators[1:]
        parts = text.split(separator)

        # separator not in the text: try the next, finer one
        if len(parts) == 1:
            return self._split_recursive(text, finer, limit)

        # put the separator back on every part but the last, so no text is lost
        parts = [part + separator for part in parts[:-1]] + [parts[-1]]

        # parts that fit are kept as is; parts still too long go one level finer
        pieces = []
        for part in parts:
            if part:
                pieces.extend(self._split_recursive(part, finer, limit))
        return pieces


