"""scripts/ingest.py is not a package: load it from its path."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "ingest.py"


@pytest.fixture(scope="module")
def ingest() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ingest", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_collects_the_runbooks_of_every_format_and_lists_the_other_files(ingest, tmp_path) -> None:
    (tmp_path / "network").mkdir()
    (tmp_path / "images").mkdir()
    files = {
        "disk_full.pdf": b"%PDF",
        "db_failover.docx": b"PK",
        "network/link_drop.md": b"# Link drop",
        "LATENCY.TXT": b"p99 above 800 ms",  # extension case does not matter
        "network/dns.html": b"<h1>DNS</h1>",
        "images/flow.png": b"\x89PNG",
        ".gitkeep": b"",
    }
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)

    loaded, ignored = ingest.load_documents(tmp_path)
    documents = {d.source: d for d in loaded}

    # the source is the path inside the folder, with "/" on every OS: citations and chunk ids depend on it
    assert sorted(documents) == ["LATENCY.TXT", "db_failover.docx", "disk_full.pdf", "network/dns.html", "network/link_drop.md"]
    assert documents["network/link_drop.md"].media_type == "text/markdown"
    assert documents["network/link_drop.md"].data == b"# Link drop"
    assert documents["disk_full.pdf"].media_type == "application/pdf"
    assert documents["db_failover.docx"].media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert documents["LATENCY.TXT"].media_type == "text/plain"
    assert documents["network/dns.html"].media_type == "text/html"
    assert ignored == [".gitkeep", "images/flow.png"]  # every file left out, for the warning


def test_source_does_not_depend_on_where_the_folder_lives(ingest, tmp_path) -> None:
    for root in (tmp_path / "a", tmp_path / "b" / "deeper"):
        root.mkdir(parents=True)
        (root / "runbook.md").write_bytes(b"# R")

    sources = [ingest.load_documents(root)[0][0].source for root in (tmp_path / "a", tmp_path / "b" / "deeper")]

    assert sources == ["runbook.md", "runbook.md"]


def test_the_default_folder_is_data_so_the_runbooks_are_named_from_there(ingest) -> None:
    documents, _ = ingest.load_documents(ingest.DEFAULT_FOLDER)

    assert ingest.DEFAULT_FOLDER.name == "data"
    assert "runbooks/runbook_disk_full.pdf" in {document.source for document in documents}  # as the golden set names it


def test_every_file_left_out_gets_a_warning(ingest, tmp_path, monkeypatch, caplog, capsys) -> None:
    (tmp_path / "runbook.md").write_bytes(b"# R")
    (tmp_path / "notes.json").write_bytes(b"{}")

    class Pipeline:
        def run_batch(self, documents: list) -> list[str]:
            return []

    monkeypatch.setattr(ingest, "build_pipeline", Pipeline)
    monkeypatch.setattr("sys.argv", ["ingest.py", str(tmp_path)])
    ingest.main()

    assert [record.getMessage() for record in caplog.records if record.levelname == "WARNING"] == [
        "not ingested: notes.json (supported: .pdf, .docx, .md, .txt, .html)"
    ]
    assert "ingested 1 of 1 documents" in capsys.readouterr().out
