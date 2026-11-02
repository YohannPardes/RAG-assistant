"""Regression guard: each eval run short, its numbers held to thresholds set a little below what was first measured
(scope/5, Proposed), so a real regression fails and run-to-run spread doesn't. Marked eval: `uv run pytest -m eval`,
by hand or nightly on a machine with the model; never in the fast tier. prefilter needs nothing running; rag needs the
configured collection ingested from data/; triage needs the rag service and the model server (it skips what isn't up)."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import httpx
import pytest

pytestmark = pytest.mark.eval

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "run_eval.py"

# first measured (2026-11-02): prefilter recall 1.0 and baseline precision 0.881 over 5 seeds; rag recall@10 1.0, MRR 0.85
# on 71 questions; triage on seeds 1-2 (2 h): recall 0.714-0.857, precision 1.0, no false positive
PREFILTER = {"prefilter_recall": 1.0}  # deterministic, no LLM: any lost fault is a regression
RAG = {"recall@10": 0.95, "mrr": 0.80}
TRIAGE = {"recall": 0.6, "precision": 0.9}


@pytest.fixture(scope="module")
def run_eval() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_eval", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_eval"] = module
    spec.loader.exec_module(module)
    return module


def run(run_eval: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *args: str) -> dict:
    """One eval run, kept out of the history; its report.json."""
    monkeypatch.setattr(sys, "argv", ["run_eval.py", *args, "--no-history", "--runs-dir", str(tmp_path)])
    run_eval.main()
    [folder] = tmp_path.iterdir()
    return json.loads((folder / "report.json").read_text(encoding="utf-8"))


def _up(url: str) -> bool:
    try:
        return httpx.get(url, timeout=3).status_code < 500
    except httpx.HTTPError:
        return False


def test_the_prefilter_still_covers_every_seeded_fault(run_eval, tmp_path, monkeypatch) -> None:
    metrics = run(run_eval, tmp_path, monkeypatch, "prefilter")["metrics"]

    assert metrics["prefilter_recall"] >= PREFILTER["prefilter_recall"], metrics


def test_retrieval_stays_above_its_thresholds(run_eval, tmp_path, monkeypatch) -> None:
    from siteops_rag.config import Settings

    if not _up(Settings().vector_store.url or ""):
        pytest.skip("the configured vector store is not running")
    metrics = run(run_eval, tmp_path, monkeypatch, "rag", "--top-k", "10")["metrics"]

    assert metrics["recall@10"] >= RAG["recall@10"] and metrics["mrr"] >= RAG["mrr"], metrics


def test_triage_stays_above_its_thresholds(run_eval, tmp_path, monkeypatch) -> None:
    from siteops_agent.config import Settings

    settings = Settings(tickets={"url": "http://unused.invalid", "api_key": "eval-dry-run-unused-key", "timeout": 1.0, "retries": 0})  # type: ignore[call-arg]
    for name, url in (("the rag service", f"{settings.rag.base_url}/health"), ("the model server", f"{settings.llm.base_url}/models")):
        if not _up(url):
            pytest.skip(f"{name} is not running at {url}")
    report = run(run_eval, tmp_path, monkeypatch, "triage", "--seeds", "1", "2", "--hours", "2")
    metrics = report["metrics"]

    assert not report["warnings"], report["warnings"]
    assert metrics["recall"] >= TRIAGE["recall"] and metrics["precision"] >= TRIAGE["precision"], metrics
