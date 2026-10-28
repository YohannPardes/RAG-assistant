from pathlib import Path

import pytest

from ..conftest import FIXTURES


@pytest.fixture(scope = "module")
def docling_converter():
    from siteops_rag.converter.docling import DoclingMarkdownConverter
    return DoclingMarkdownConverter()

def test_converting_empty_doc(docling_converter) -> None:

    document = ""

    encoded_document = document.encode("utf-8")
    out = docling_converter.convert_to_markdown(encoded_document, media_type="text/plain")

    assert out == ""

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
def test_converting_without_errors(filename, media_type, expected_text, docling_converter) -> None:

    data = Path(f"{FIXTURES}/{filename}").read_bytes()

    out = docling_converter.convert_to_markdown(data, media_type=media_type)

    print(f"Converted {filename} to markdown:\n{out}")

    assert expected_text in out

@pytest.mark.parametrize(
    ("filename", "media_type", "expected_page_break_count"),
    [
        ("runbook_cert_expiry.md", 
         "text/markdown", 
         0
         ),
        ("runbook_disk_full.pdf", 
         "application/pdf", 
         2
         ),
        ("runbook_db_failover.docx", 
         "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         0
         ),
        ("runbook_high_latency.txt",
         "text/plain",
         0
         ),
    ],
)
def test_page_breaks_in_converted_markdown(filename, media_type, 
                                        expected_page_break_count, docling_converter) -> None:

    data = Path(f"{FIXTURES}/{filename}").read_bytes()

    out = docling_converter.convert_to_markdown(data, media_type=media_type)

    page_break_count = out.count("<!-- page-break -->")

    assert page_break_count == expected_page_break_count

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
                           expected_row, docling_converter) -> None:

    data = Path(f"{FIXTURES}/{filename}").read_bytes()

    out = docling_converter.convert_to_markdown(data, media_type=media_type)

    rows = _table_rows(out)

    assert expected_header in rows
    assert expected_row in rows

@pytest.mark.parametrize(
    ("filename", "media_type", "expected_image_count"),
    [
        ("runbook_cert_expiry.md",
         "text/markdown",
         1
         ),
        ("runbook_disk_full.pdf",
         "application/pdf",
         1
         ),
        ("runbook_db_failover.docx",
         "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         1
         ),
        ("runbook_high_latency.txt",
         "text/plain",
         0
         ),
    ],
)
def test_images_yield_image_placeholder(filename, media_type,
                                        expected_image_count, docling_converter) -> None:
    from siteops_rag.converter.docling import IMAGE_PLACEHOLDER

    data = Path(f"{FIXTURES}/{filename}").read_bytes()

    out = docling_converter.convert_to_markdown(data, media_type=media_type)

    assert out.count(IMAGE_PLACEHOLDER) == expected_image_count

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
                                       expected_command, docling_converter) -> None:

    data = Path(f"{FIXTURES}/{filename}").read_bytes()

    out = docling_converter.convert_to_markdown(data, media_type=media_type)

    assert expected_command in out
    assert "&amp;" not in out and "&lt;" not in out