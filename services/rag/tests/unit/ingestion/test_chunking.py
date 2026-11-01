import pytest

from siteops_rag.models import Chunk, Document, Element, render_table

MAX = 100


@pytest.fixture(scope = "module")
def chunker():
    from siteops_rag.ingestion.chunking import Chunker
    return Chunker(max_length=MAX)

# helpers
def _doc(*elements: Element) -> Document:
    return Document("runbook.md", tuple(elements))

def _chunk(doc: Document, max_chars: int = MAX) -> list[Chunk]:
    from siteops_rag.ingestion.chunking import Chunker
    chunks = Chunker(max_length=max_chars).chunk_elements(doc)
    assert_valid(chunks, doc, max_chars)
    return chunks

def _body(text: str) -> str:
    """Chunk text without its heading-path prefix."""
    return text.split("\n\n", 1)[-1]

def _text(chunk: Chunk) -> str:
    """Chunk text without its heading-path prefix, removed exactly (the body may hold blank lines)."""
    return chunk.text.removeprefix(f"{' > '.join(chunk.heading_path)}\n\n")

def _code_body(text: str) -> str:
    """Chunk text without its opening and closing fences."""
    return text.split("\n", 1)[1].removesuffix("\n```")

def assert_valid(chunks: list[Chunk], doc: Document, max_chars: int) -> None:
    """Invariants every result must hold."""
    for c in chunks:
        assert c.text.strip()
        assert c.source == doc.source
        assert len(c.text) <= max_chars


def test_empty_input_gives_no_chunks(chunker) -> None:
    assert chunker.chunk_elements(_doc()) == []

def test_content_before_the_title_is_under_the_title() -> None:
    doc = _doc(
        Element("paragraph", "Read this first."),
        Element("heading", "RB-1", level=0),
        Element("paragraph", "Intro."),
        Element("heading", "1. Scope", level=1),
        Element("paragraph", "Body."),
    )

    paths = {_body(c.text): c.heading_path for c in _chunk(doc)}

    assert paths["Read this first."] == ("RB-1",)  # rag/14: the document's identity, even before its title line
    assert paths["Intro."] == ("RB-1",)
    assert paths["Body."] == ("RB-1", "1. Scope")

def test_heading_path_follows_the_outline() -> None:
    doc = _doc(
        Element("heading", "RB-310", level=0),
        Element("heading", "3. Diagnosis", level=1),
        Element("heading", "3.1 Check", level=2),
        Element("paragraph", "Check A."),
        Element("heading", "4. Fix", level=1),
        Element("paragraph", "Fix B."),
    )

    paths = {_body(c.text): c.heading_path for c in _chunk(doc)}

    assert paths["Check A."] == ("RB-310", "3. Diagnosis", "3.1 Check")
    assert paths["Fix B."] == ("RB-310", "4. Fix")

def test_without_a_title_the_filename_starts_every_path() -> None:
    # e.g. a docx with no Title style: the outline starts at level 1, and sibling headings replace each other
    doc = _doc(
        Element("heading", "1. Detect", level=1),
        Element("paragraph", "Detect A."),
        Element("heading", "2. Fix", level=1),
        Element("heading", "2.1 Restart", level=2),
        Element("paragraph", "Fix B."),
    )

    paths = {_body(c.text): c.heading_path for c in _chunk(doc)}

    assert paths["Detect A."] == ("runbook.md", "1. Detect")
    assert paths["Fix B."] == ("runbook.md", "2. Fix", "2.1 Restart")

def test_the_same_section_of_two_runbooks_reads_differently() -> None:
    # rag/14: the escalation misses. Without the title, both chunks were "5. Escalation\n\n| Level | Who |..." and
    # embedded alike; with it, each names its runbook
    def runbook(source: str, title: str) -> list[Chunk]:
        rows = (("Level", "Who"), ("L2", "on-call"))
        return _chunk(Document(source, (
            Element("heading", title, level=0),
            Element("heading", "5. Escalation", level=1),
            Element("table", render_table(rows), rows=rows),
        )), max_chars=200)

    [disk] = runbook("runbook_disk_full.pdf", "RB-101: Disk Full")
    [cert] = runbook("runbook_cert_expiry.md", "RB-310: TLS Certificate Expiry")

    assert disk.text.startswith("RB-101: Disk Full > 5. Escalation\n\n")
    assert cert.text.startswith("RB-310: TLS Certificate Expiry > 5. Escalation\n\n")
    assert _text(disk) == _text(cert) and disk.chunk_id != cert.chunk_id


def test_a_document_without_headings_is_under_its_filename() -> None:
    [chunk] = _chunk(_doc(Element("paragraph", "RB-803: Bastion Host Lockout"), Element("paragraph", "Body.")))

    assert chunk.heading_path == ("runbook.md",)
    assert chunk.text.startswith("runbook.md\n\n")


def test_sections_at_level_1_never_replace_the_title() -> None:
    doc = _doc(
        Element("heading", "RB-104: Log Shipping", level=0),
        Element("heading", "1. Symptoms", level=1), Element("paragraph", "A."),
        Element("heading", "6. Escalation", level=1), Element("paragraph", "B."),
    )

    paths = {_body(c.text): c.heading_path for c in _chunk(doc)}

    assert paths["A."] == ("RB-104: Log Shipping", "1. Symptoms")
    assert paths["B."] == ("RB-104: Log Shipping", "6. Escalation")


def test_the_first_title_is_the_documents_and_a_blank_one_does_not_count() -> None:
    doc = _doc(
        Element("heading", "  ", level=0),  # an empty Title style: no title
        Element("heading", "RB-7: Real Title", level=0),
        Element("paragraph", "Body."),
    )

    [chunk] = _chunk(doc)

    assert chunk.heading_path == ("RB-7: Real Title",)


def test_every_chunk_starts_with_its_heading_path() -> None:
    text = " ".join(f"Sentence number {i} is here." for i in range(20))
    doc = _doc(
        Element("heading", "RB-310", level=0),
        Element("heading", "4. Fix", level=1),
        Element("paragraph", text),
    )

    chunks = _chunk(doc)

    assert len(chunks) > 1
    assert all(c.text.startswith("RB-310 > 4. Fix\n\n") for c in chunks)
    assert "".join(_body(c.text) for c in chunks) == text

def test_page_is_the_page_of_the_first_piece() -> None:
    with_pages = _doc(
        Element("paragraph", "One.", page=1),
        Element("paragraph", "Two.", page=2),
        Element("heading", "Next", level=0),
        Element("paragraph", "Three.", page=3),
    )
    without_pages = _doc(Element("paragraph", "One."), Element("paragraph", "Two."))

    assert [c.page for c in _chunk(with_pages)] == [1, 3]
    assert {c.page for c in _chunk(without_pages)} == {None}

def test_ids_are_deterministic() -> None:
    # one section per paragraph, so each paragraph is its own chunk
    def doc_with(second: str) -> Document:
        return _doc(
            Element("heading", "A", level=0), Element("paragraph", "One."),
            Element("heading", "B", level=0), Element("paragraph", second),
            Element("heading", "C", level=0), Element("paragraph", "Three."),
        )
    doc, edited = doc_with("Two."), doc_with("Two, edited.")

    ids = [c.chunk_id for c in _chunk(doc)]
    edited_ids = [c.chunk_id for c in _chunk(edited)]

    assert ids == [c.chunk_id for c in _chunk(doc)]
    assert [a == b for a, b in zip(ids, edited_ids, strict=True)] == [True, False, True]

def test_short_paragraphs_of_a_section_are_packed() -> None:
    doc = _doc(Element("heading", "Fix", level=0), Element("paragraph", "P1."), Element("paragraph", "P2."))

    assert [c.text for c in _chunk(doc)] == ["Fix\n\nP1.\n\nP2."]

def test_heading_change_starts_a_new_chunk() -> None:
    doc = _doc(
        Element("heading", "RB-1", level=0),
        Element("heading", "Detect", level=1), Element("paragraph", "P1."),
        Element("heading", "Fix", level=1), Element("paragraph", "P2."),
    )

    assert [c.text for c in _chunk(doc)] == ["RB-1 > Detect\n\nP1.", "RB-1 > Fix\n\nP2."]

def test_numbered_procedure_is_one_chunk() -> None:
    steps = [Element("paragraph", f"{i}. Step {i}.") for i in range(1, 11)]

    chunks = _chunk(_doc(Element("heading", "Fix", level=0), *steps), max_chars=200)

    assert len(chunks) == 1
    assert all(step.text in chunks[0].text for step in steps)

def test_packing_respects_the_limit_and_loses_nothing() -> None:
    paragraphs = [Element("paragraph", f"Paragraph {i} explains one more step.") for i in range(10)]

    chunks = _chunk(_doc(Element("heading", "Fix", level=0), *paragraphs))  # assert_valid checks the limit

    assert len(chunks) > 1
    packed = "\n\n".join(_body(c.text) for c in chunks)
    assert packed == "\n\n".join(p.text for p in paragraphs)

def test_intro_ending_with_colon_stays_with_its_code() -> None:
    doc = _doc(
        Element("paragraph", "Check the certificate expiry date in the dashboard and note it."),
        Element("paragraph", "Update the secret:"),
        Element("code", "kubectl apply -f secret.yaml", language="bash"),
    )

    chunks = _chunk(doc)

    # the two paragraphs fit together, but the intro moves to the chunk of its command
    assert len(chunks) == 2
    assert _text(chunks[1]).startswith("Update the secret:\n\n```bash\n")

def test_long_paragraph_is_split_without_loss() -> None:
    text = " ".join(f"Sentence number {i} is here." for i in range(20))

    chunks = _chunk(_doc(Element("paragraph", text)))

    assert len(chunks) > 1
    assert "".join(_text(c) for c in chunks) == text

def test_word_longer_than_limit_is_hard_cut() -> None:
    word = "x" * 250

    chunks = _chunk(_doc(Element("paragraph", word)))

    assert "".join(_text(c) for c in chunks) == word

def test_big_table_repeats_header_and_keeps_rows_whole() -> None:
    rows = (("Mount", "Warning", "Critical"), *((f"/mnt/disk{i}", "85%", "95%") for i in range(10)))
    header_lines = render_table(rows[:1])
    row_lines = [render_table((rows[0], row)).splitlines()[-1] for row in rows[1:]]

    chunks = _chunk(_doc(Element("table", render_table(rows), rows=rows)))

    assert len(chunks) > 1
    for c in chunks:
        assert _text(c).startswith(header_lines)
        # no row split: every line after the header is a whole row
        assert all(line in row_lines for line in _text(c).splitlines()[2:])
    for line in row_lines:
        assert sum(_text(c).splitlines().count(line) for c in chunks) == 1

def test_row_too_big_for_table_form_keeps_labels() -> None:
    header = ("Step", "Command", "Who")
    command = ("patronictl reinit orders-db db-primary-01 then wait until "
               "the member state shows streaming again and confirm lag is zero")
    rows = (header, ("Rebuild", command, "DBA on-call"))

    chunks = _chunk(_doc(Element("table", render_table(rows), rows=rows)), max_chars=60)

    # pieces packed together in phase 2 are separated by a blank line
    lines = [line for c in chunks for line in _text(c).split("\n") if line]
    assert all(line.split(" : ", 1)[0] in header for line in lines)
    # every piece of the oversized cell keeps its label, and together they give the value back
    command_pieces = [line.removeprefix("Command : ") for line in lines if line.startswith("Command : ")]
    assert len(command_pieces) > 1
    assert "".join(command_pieces) == command

def test_every_chunk_of_a_split_row_starts_with_the_row_key() -> None:
    header = ("Step", "Command", "Who")
    command = ("patronictl reinit orders-db db-primary-01 then wait until "
               "the member state shows streaming again and confirm lag is zero")
    rows = (header, ("Rebuild", command, "DBA on-call"))

    chunks = _chunk(_doc(Element("table", render_table(rows), rows=rows)), max_chars=60)

    assert len(chunks) > 1
    assert all(_text(c).startswith("Step : Rebuild\n") for c in chunks)  # no chunk holds the key alone

def test_header_only_table_too_long_is_not_lost() -> None:
    header = ("Mount", "Warning threshold", "Critical threshold", "Action to take")
    rows = (header,)

    chunks = _chunk(_doc(Element("table", render_table(rows), rows=rows)), max_chars=40)

    text = "".join(c.text for c in chunks)
    assert all(name in text for name in header)

def test_no_cell_of_a_split_row_is_lost() -> None:
    # the row has one cell more than the header, and is too big for table form
    rows = (("Step", "Command"), ("Rebuild", "patronictl reinit orders-db db-primary-01 then wait", "EXTRA-CELL"))

    chunks = _chunk(_doc(Element("table", render_table(rows), rows=rows)), max_chars=60)

    assert any("EXTRA-CELL" in c.text for c in chunks)

def test_pipe_in_a_cell_is_escaped() -> None:
    rows = (("Command",), ("du -sh * | sort -h",))

    chunks = _chunk(_doc(Element("table", render_table(rows), rows=rows)))

    assert "du -sh * \\| sort -h" in chunks[0].text

def test_label_longer_than_limit_loses_no_text() -> None:
    from siteops_rag.ingestion.chunking import Chunker
    label = "A column name longer than the whole limit : "
    value = "patronictl reinit orders-db db-primary-01"
    rows = ((label.removesuffix(" : "),), (value,))

    # no assert_valid: the label alone is over the limit, so chunks can't fit; they must still be complete
    chunks = Chunker(max_length=40).chunk_elements(_doc(Element("table", render_table(rows), rows=rows)))

    assert all(_text(c).startswith(label) for c in chunks)
    assert "".join(_text(c).removeprefix(label) for c in chunks) == value

def test_code_is_fenced_and_split_between_lines() -> None:
    continued = ("kubectl -n cert-manager create secret generic dns \\\n"
                 "  --from-literal=token=<NEW_TOKEN>")
    code = "\n".join([
        continued,
        "kubectl -n cert-manager get secret dns -o yaml",
        "spec:",
        "  dnsNames:",
        "    - api.example.com",
    ])

    # 120: the continued command, its fences and the "runbook.md" prefix fit in one chunk
    chunks = _chunk(_doc(Element("code", f"\n{code}\n", language="bash")), max_chars=120)
    bodies = [_code_body(_text(c)) for c in chunks]

    assert len(chunks) > 1
    assert all(_text(c).startswith("```bash\n") and c.text.endswith("\n```") for c in chunks)
    # never cut mid-line, whitespace kept exactly, nothing lost
    assert "\n".join(bodies) == code
    assert any(continued in body for body in bodies)

def test_oversized_command_is_split_without_loss() -> None:
    command = "sudo systemctl stop patroni && sudo systemctl mask patroni && sudo systemctl status patroni"

    chunks = _chunk(_doc(Element("code", command, language="bash")), max_chars=60)

    assert len(chunks) > 1
    assert "".join(_code_body(_text(c)) for c in chunks) == command

@pytest.mark.parametrize("code", ["", "   \n\n  "])
def test_empty_code_block_gives_no_chunk(code) -> None:
    assert _chunk(_doc(Element("code", code))) == []
