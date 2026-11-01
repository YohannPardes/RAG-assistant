"""scripts/run_eval.py's scoring, on fake search and ask functions. The script is not a package: load it from its path."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from siteops_common.contracts import AskResponse, Citation
from siteops_rag.models import Chunk
from siteops_rag.vector_store.base import ScoredChunk

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "run_eval.py"

DISK = {"expected_source": "disk.pdf", "expected_section": "4. Fix"}
CERT = {"expected_source": "cert.md", "expected_section": "6. Escalation"}


@pytest.fixture(scope="module")
def run_eval() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_eval", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def scored(source: str, *heading_path: str) -> ScoredChunk:
    chunk = Chunk(chunk_id=f"{source}|{heading_path}", source=source, text="…", heading_path=heading_path, page=None)
    return ScoredChunk(chunk=chunk, score=0.5)


def citation(source: str, *heading_path: str) -> Citation:
    return Citation(chunk_id="c", source=source, heading_path=list(heading_path), page=None, quote="…")


def reply(answer: str, *citations: Citation) -> AskResponse:
    return AskResponse(answered=bool(citations), answer=answer, citations=list(citations), hits=[], model="m", embedding_model="e", latency_ms=1.0)


# --- retrieval ---


def test_recall_and_mrr_are_reported_per_category(run_eval) -> None:
    rows = [
        {"question": "lookup hit at 1", "category": "lookup", **DISK},
        {"question": "lookup hit at 2", "category": "lookup", **DISK},
        {"question": "table miss", "category": "table", **CERT},
    ]
    results = {
        "lookup hit at 1": [scored("disk.pdf", "RB-101", "4. Fix")],
        "lookup hit at 2": [scored("cert.md", "RB-310", "1. Symptoms"), scored("disk.pdf", "RB-101", "4. Fix")],
        "table miss": [scored("disk.pdf", "RB-101", "6. Escalation")],  # right section name, wrong runbook
    }

    metrics, misses = run_eval.evaluate(rows, lambda question, k: results[question][:k], top_k=5)

    assert metrics["recall@5"] == 0.667 and metrics["mrr"] == 0.5 and metrics["n_questions"] == 3
    assert metrics["by_category"]["lookup"] == {"recall@5": 1.0, "mrr": 0.75, "n_questions": 2}
    assert metrics["by_category"]["table"] == {"recall@5": 0.0, "mrr": 0.0, "n_questions": 1}
    [miss] = misses
    assert miss["category"] == "table" and miss["got"] == ["disk.pdf › 6. Escalation"]  # the miss names the runbook, not only the section


def test_a_question_without_a_category_is_uncategorized(run_eval) -> None:
    metrics, _ = run_eval.evaluate([{"question": "q", **DISK}], lambda q, k: [scored("disk.pdf", "4. Fix")], top_k=5)

    assert list(metrics["by_category"]) == ["uncategorized"]


def test_unanswerable_questions_are_left_out_of_retrieval(run_eval) -> None:
    searched = []
    rows = [{"question": "office printer?", "category": "unanswerable"}, {"question": "q", **DISK}]

    metrics, _ = run_eval.evaluate(rows, lambda q, k: searched.append(q) or [scored("disk.pdf", "4. Fix")], top_k=5)

    assert searched == ["q"] and metrics["n_questions"] == 1 and "unanswerable" not in metrics["by_category"]


# --- answers (/ask) ---


def test_a_confident_answer_to_an_unanswerable_question_is_a_false_answer(run_eval) -> None:
    rows = [{"question": "printer?", "category": "unanswerable"}, {"question": "vpn?", "category": "unanswerable"}]
    replies = {"printer?": reply("I don't know."), "vpn?": reply("Restart the VPN.", citation("disk.pdf", "4. Fix"))}

    metrics, failures = run_eval.evaluate_answers(rows, replies.__getitem__)

    assert metrics["false_answer_rate"] == 0.5 and metrics["n_unanswerable"] == 2
    assert [f["question"] for f in failures] == ["vpn?"] and failures[0]["kind"] == "false_answer"


def test_answerable_questions_score_answer_citation_and_key_facts(run_eval) -> None:
    rows = [
        {"question": "good", **DISK, "must_contain": ["logrotate -f", "95%"]},
        {"question": "wrong citation", **DISK},
        {"question": "missing fact", **DISK, "must_contain": ["PagerDuty: platform-ops"]},
        {"question": "refused", **CERT},
    ]
    replies = {
        "good": reply("Run LOGROTATE -f once usage passes 95%.", citation("disk.pdf", "RB-101", "4. Fix")),  # facts match case-insensitively
        "wrong citation": reply("Something.", citation("disk.pdf", "RB-101", "1. Symptoms")),
        "missing fact": reply("Page the on-call.", citation("disk.pdf", "RB-101", "4. Fix")),
        "refused": reply("I don't know."),
    }

    metrics, failures = run_eval.evaluate_answers(rows, replies.__getitem__)

    assert metrics["answer_rate"] == 0.75 and metrics["citation_accuracy"] == 0.5
    assert metrics["key_facts"] == 0.5 and metrics["n_with_facts"] == 2
    kinds = {f["question"]: f["kind"] for f in failures}
    assert kinds == {"wrong citation": ["wrong_citation"], "missing fact": ["missing_facts"], "refused": ["no_answer"]}


def test_rates_with_nothing_to_measure_are_none_not_zero(run_eval) -> None:
    # a golden set without unanswerable questions measured no false answers: 0.0 would claim a result
    metrics, _ = run_eval.evaluate_answers([{"question": "q", **DISK}], lambda q: reply("a", citation("disk.pdf", "4. Fix")))

    assert metrics["false_answer_rate"] is None and metrics["key_facts"] is None
