"""scripts/compare_evals.py on hand-made history lines. The script is not a package: load it from its path."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "compare_evals.py"


@pytest.fixture(scope="module")
def compare() -> ModuleType:
    spec = importlib.util.spec_from_file_location("compare_evals", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def triage(run_id: str, recall: float, failures: list[str], max_searches: int = 3) -> dict:
    return {
        "run_id": run_id,
        "kind": "triage",
        "git_commit": "abc",
        "config": {"model": "m", "max_searches": max_searches},
        "metrics": {"recall": recall, "precision": 0.9, "n_faults": 35, "duplicates": 2},
        "failures": [{"item": item, "failure": "missed"} for item in failures],
    }


@pytest.fixture
def history(tmp_path) -> Path:
    lines = [
        {"git_commit": "old", "config": {"top_k": 10}, "metrics": {"recall@10": 0.95, "mrr": 0.8, "n_questions": 91}, "misses": [{"question": "q1"}]},
        triage("triage-1", 0.8, ["seed 1 F1", "seed 2 F3"]),
        {**triage("rag-2", 0.0, []), "kind": "rag", "metrics": {"recall@10": 0.96, "mrr": 0.81, "n_questions": 91}, "failures": []},
        triage("triage-2", 0.886, ["seed 2 F3", "seed 4 F2"], max_searches=1),
    ]
    path = tmp_path / "history.jsonl"
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return path


def test_runs_are_picked_by_id_or_by_position_among_their_kind(compare, history) -> None:
    runs = compare.load_history(history)

    assert compare.pick(runs, "-1", "triage")["run_id"] == "triage-2"
    assert compare.pick(runs, "-2", "triage")["run_id"] == "triage-1"
    assert compare.pick(runs, "triage-1", None)["kind"] == "triage"
    assert compare.pick(runs, "-2", "rag")["run_id"] == "rag-#1"  # an older line: a rag run, named by its position


def test_metric_changes_flag_one_item_moves_as_noise(compare, history) -> None:
    runs = compare.load_history(history)
    rows = {row["metric"]: row for row in compare.compare_metrics(runs[1], runs[3])}

    assert rows["recall"]["change"] == 0.086 and rows["recall"]["note"] == ""  # 3 of 35 faults: a real move
    assert rows["precision"]["change"] == 0 and rows["duplicates"]["change"] == 0
    rag = {row["metric"]: row for row in compare.compare_metrics(runs[0], runs[2])}
    assert rag["recall@10"]["note"] == "within noise (≤1 item)"  # 0.01 of 91 questions: one question


def test_items_that_newly_fail_or_pass(compare, history) -> None:
    runs = compare.load_history(history)

    assert compare.compare_items(runs[1], runs[3]) == (["seed 4 F2: missed"], ["seed 1 F1: missed"])


def test_the_report_names_the_config_change_and_refuses_two_kinds(compare, history, monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["compare_evals.py", "-2", "-1", "--kind", "triage", "--history", str(history)])
    compare.main()
    printed = capsys.readouterr().out
    assert "max_searches: 3 → 1" in printed and "## Newly failing (1)" in printed and "seed 4 F2" in printed

    monkeypatch.setattr(sys, "argv", ["compare_evals.py", "triage-1", "rag-2", "--history", str(history)])
    with pytest.raises(SystemExit, match="one kind"):
        compare.main()
