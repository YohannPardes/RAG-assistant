import pytest

from siteops_rag.converter.base import RawDocument
from siteops_rag.models import Element

pytestmark = pytest.mark.integration

# helpers 
def _convert(converter, fixtures_dir, filename, media_type) -> list[Element]:
    data = (fixtures_dir / filename).read_bytes()
    return converter.convert_to_elements(RawDocument(filename, data, media_type))

def _all_text(elements: list[Element]) -> str:
    return "\n".join(e.text for e in elements)


def test_converting_empty_doc(docling_converter) -> None:

    document = ""

    encoded_document = document.encode("utf-8")
    out = docling_converter.convert_to_elements(RawDocument("empty.txt", encoded_document, "text/plain"))

    assert out == []

@pytest.mark.parametrize(
    ("filename", "media_type", "expected_text"),
    [
        ("runbook_cert_expiry.md", 
         "text/markdown", 
         "Figure 1: cert-manager renews 21 days before expiry using an ACME DNS-01 challenge."
         ),
        
        ("runbook_disk_full.pdf", 
         "application/pdf",
         "below 70% with df -h /var/log."
         ),
        ("runbook_db_failover.docx", 
         "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         "PostgreSQL Primary Failover"
         ),
        ("runbook_high_latency.txt",
         "text/plain",
         "Alert ApiLatencyP99High fires: p99 latency above 800 ms for 5 minutes."
         ),
    ],
)
def test_converting_without_errors(filename, media_type, expected_text, 
                                   docling_converter, fixtures_dir) -> None:

    data = (fixtures_dir / filename).read_bytes()

    out = docling_converter.convert_to_elements(RawDocument(filename, data, media_type))

    assert expected_text in _all_text(out)

@pytest.mark.parametrize(
    ("filename", "media_type", "out_pages"),
    [
        ("runbook_cert_expiry.md", 
         "text/markdown", 
         {None}
         ),
        ("runbook_disk_full.pdf", 
         "application/pdf", 
         {1, 2, 3}
         ),
        ("runbook_db_failover.docx", 
         "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         {None}
         ),
        ("runbook_high_latency.txt",
         "text/plain",
         {None}
         ),
    ],
)
def test_page_breaks_in_converted_markdown(filename, media_type, 
                                out_pages, docling_converter, fixtures_dir) -> None:

    data = (fixtures_dir / filename).read_bytes()

    out = docling_converter.convert_to_elements(RawDocument(filename, data, media_type))

    pages = {e.page for e in out}
    assert pages == out_pages

def _table_rows(markdown: str) -> list[list[str]]:
    """Every markdown table row as a list of stripped cells (ignores column padding)."""
    return [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in markdown.splitlines()
        if line.strip().startswith("|")
    ]

@pytest.mark.parametrize(
    ("filename", "media_type", "expected_header", "expected_row"),
    [
        ("runbook_cert_expiry.md",
         "text/markdown",
         ["Level", "Who", "Contact"],
         ["L2", "Edge Networking on-call", "PagerDuty: edge-net"]
         ),
        ("runbook_disk_full.pdf",
         "application/pdf",
         ["Mount", "Warning", "Critical", "Action"],
         ["/var/log", "85%", "95%", "Follow this runbook"]
         ),
        ("runbook_db_failover.docx",
         "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         ["Step", "Command", "Expected result"],
         ["Unmask service", "sudo systemctl unmask patroni", "Unit loaded"]
         ),
        ("runbook_high_latency.txt",
         "text/plain",
         ["Endpoint", "p50", "p99", "Timeout"],
         ["POST /v2/orders", "90 ms", "800 ms", "5 s"]
         ),
    ],
)
def test_tables_are_parsed(filename, media_type, expected_header,
                           expected_row, docling_converter, fixtures_dir) -> None:

    data = (fixtures_dir / filename).read_bytes()

    out = docling_converter.convert_to_elements(RawDocument(filename, data, media_type))

    out = _all_text(out)

    rows = _table_rows(out)

    assert expected_header in rows
    assert expected_row in rows

@pytest.mark.parametrize(
    ("filename", "media_type", "expected_command"),
    [
        ("runbook_db_failover.docx",
         "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         "sudo systemctl stop patroni && sudo systemctl mask patroni"
         ),
        ("runbook_cert_expiry.md",
         "text/markdown",
         "--from-literal=token=<NEW_TOKEN>"
         ),
        ("runbook_high_latency.txt",
         "text/plain",
         "kubectl -n api delete pod <POD_NAME>"
         ),
    ],
)
def test_commands_are_not_html_escaped(filename, media_type,
                                       expected_command, docling_converter, fixtures_dir) -> None:

    data = (fixtures_dir / filename).read_bytes()

    out = docling_converter.convert_to_elements(RawDocument(filename, data, media_type))
    out = _all_text(out)

    assert expected_command in out
    assert "&amp;" not in out and "&lt;" not in out

def test_headings_carry_their_level(docling_converter, fixtures_dir) -> None:

    elements = _convert(docling_converter, fixtures_dir, "runbook_cert_expiry.md", "text/markdown")

    levels = {e.text: e.level for e in elements if e.kind == "heading"}

    assert elements[0].kind == "heading"
    assert elements[0].text == "RB-310: TLS Certificate Expiry"
    assert levels["3.1 Check the certificate status"] > levels["3. Diagnosis"]