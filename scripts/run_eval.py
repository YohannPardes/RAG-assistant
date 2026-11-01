"""Eval over the golden set (scope/4): retrieval (recall@k, MRR) and, with --ask, answers. Printed as JSON.

Retrieval: does /search put a chunk of the expected source and section in the top k? Overall and per question
category, so "0.98 on lookups, 0.75 on tables" shows where it fails, not one average.
Answers (--ask, one LLM call per question, slow): /ask's false-answer rate on unanswerable questions (a confident
answer to something the runbooks don't cover: the main safety number), its answer rate on answerable ones, citation
accuracy (a citation of the expected section) and key facts (every `must_contain` string in the answer).

Golden-set fields: question, expected_source, expected_section; optional category (default "uncategorized"),
must_contain (a list of strings). Category "unanswerable" has no expected source or section: it is left out of
retrieval and scored on /ask only.
Every run is also appended as one line to data/eval_history.jsonl, so runs and their configs can be compared.
Searches the configured collection as it is: run scripts/ingest.py first.
Usage: uv run python scripts/run_eval.py [--ask] [--top-k N] [--golden-set data/golden_set.jsonl] [--history PATH | --no-history]
"""

import argparse
import hashlib
import json
import subprocess
import sys
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from siteops_common.contracts import AskResponse, Citation
from siteops_rag.config import Settings
from siteops_rag.container import build_rag
from siteops_rag.vector_store.base import ScoredChunk

REPO_ROOT = Path(__file__).resolve().parents[1]
NO_SECTION = "(no section)"
HISTORY = REPO_ROOT / "data" / "eval_history.jsonl"
UNANSWERABLE = "unanswerable"

Row = dict[str, Any]


def load_golden_set(path: Path) -> list[Row]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def category(row: Row) -> str:
    return str(row.get("category", "uncategorized"))


def answerable(row: Row) -> bool:
    return category(row) != UNANSWERABLE


def section(hit: ScoredChunk) -> str:
    return hit.chunk.heading_path[-1] if hit.chunk.heading_path else NO_SECTION


def first_hit_rank(row: Row, hits: list[ScoredChunk]) -> int | None:
    """1-based rank of the first chunk from the expected source and section; never matched by chunk_id (scope/4)."""
    for rank, hit in enumerate(hits, start=1):
        if hit.chunk.source == row["expected_source"] and section(hit) == row["expected_section"]:
            return rank
    return None


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 3) if denominator else None  # None, not 0: nothing was measured


def evaluate(rows: list[Row], search: Callable[[str, int], list[ScoredChunk]], top_k: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Recall@k (a correct chunk in the top k) and MRR (mean of 1/rank of the first correct chunk, 0 if none),
    over the answerable questions, overall and per category."""
    reciprocal_ranks: dict[str, list[float]] = defaultdict(list)  # category → one per question
    misses = []
    for row in filter(answerable, rows):
        hits = search(row["question"], top_k)
        rank = first_hit_rank(row, hits)
        reciprocal_ranks[category(row)].append(1 / rank if rank else 0.0)
        if rank is None:
            misses.append(
                {
                    "question": row["question"],
                    "category": category(row),
                    "expected": f"{row['expected_source']} › {row['expected_section']}",
                    "got": [f"{hit.chunk.source} › {section(hit)}" for hit in hits],
                }
            )

    def scores(ranks: list[float]) -> dict[str, Any]:
        found = sum(1 for r in ranks if r > 0)
        return {f"recall@{top_k}": _rate(found, len(ranks)), "mrr": round(sum(ranks) / len(ranks), 3) if ranks else None, "n_questions": len(ranks)}

    metrics = scores([r for ranks in reciprocal_ranks.values() for r in ranks])
    metrics["by_category"] = {name: scores(ranks) for name, ranks in sorted(reciprocal_ranks.items())}
    return metrics, misses


def _cites(citation: Citation, row: Row) -> bool:
    cited_section = citation.heading_path[-1] if citation.heading_path else NO_SECTION
    return citation.source == row["expected_source"] and cited_section == row["expected_section"]


def evaluate_answers(rows: list[Row], ask: Callable[[str], AskResponse]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """/ask scored per question kind: unanswerable ones must say "I don't know"; answerable ones should answer,
    cite the expected section and contain their key facts."""
    false_answers = answered = cited = facts_checked = facts_found = 0
    unanswerable = [row for row in rows if not answerable(row)]
    answerable_rows = [row for row in rows if answerable(row)]
    failures = []

    for row in unanswerable:
        response = ask(row["question"])
        if response.answered:
            false_answers += 1
            failures.append(
                {"question": row["question"], "kind": "false_answer", "answer": response.answer, "cited": [c.source for c in response.citations]}
            )

    for row in answerable_rows:
        response = ask(row["question"])
        problems = []
        if response.answered:
            answered += 1
        else:
            problems.append("no_answer")
        if any(_cites(c, row) for c in response.citations):
            cited += 1
        elif response.answered:
            problems.append("wrong_citation")
        if facts := row.get("must_contain"):
            facts_checked += 1
            missing = [fact for fact in facts if fact.lower() not in response.answer.lower()]
            if missing:
                problems.append("missing_facts")
            else:
                facts_found += 1
        else:
            missing = []
        if problems:
            failures.append(
                {
                    "question": row["question"],
                    "category": category(row),
                    "kind": problems,
                    "expected": f"{row['expected_source']} › {row['expected_section']}",
                    "cited": [f"{c.source} › {c.heading_path[-1] if c.heading_path else NO_SECTION}" for c in response.citations],
                    "missing_facts": missing,
                    "answer": response.answer,
                }
            )

    metrics = {
        "false_answer_rate": _rate(false_answers, len(unanswerable)),  # lower is better: the safety number
        "n_unanswerable": len(unanswerable),
        "answer_rate": _rate(answered, len(answerable_rows)),  # answerable questions that got an answer, not "I don't know"
        "citation_accuracy": _rate(cited, len(answerable_rows)),  # answerable questions with a citation of the expected section
        "key_facts": _rate(facts_found, facts_checked),  # questions with must_contain whose answer has every fact
        "n_answerable": len(answerable_rows),
        "n_with_facts": facts_checked,
    }
    return metrics, failures


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout.strip()


def git_commit() -> str:
    """Short HEAD hash, with "-dirty" when the tree has uncommitted changes: the numbers may then not be HEAD's."""
    try:
        # the history file is left out: appending to it must not mark the next run as dirty
        commit = _git("rev-parse", "--short", "HEAD")
        dirty = _git("status", "--porcelain", "--", ".", f":!{HISTORY.relative_to(REPO_ROOT).as_posix()}")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{commit}-dirty" if dirty else commit


def file_hash(path: Path) -> str:
    """Short sha256 of a file: two runs with the same hash scored the same questions."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def append_history(path: Path, report: dict[str, Any]) -> None:
    """One JSON line per run, oldest first; the file is only ever appended to."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as history:
        history.write(json.dumps(report, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ask", action="store_true", help="also score /ask's answers (one LLM call per question)")
    parser.add_argument("--top-k", type=int, help="default: retrieval.top_k from the rag config, as /search uses (rag/7)")
    parser.add_argument("--golden-set", type=Path, default=REPO_ROOT / "data" / "golden_set.jsonl")
    parser.add_argument("--history", type=Path, default=HISTORY)
    parser.add_argument("--no-history", action="store_true", help="print the report without recording the run")
    args = parser.parse_args()

    rows = load_golden_set(args.golden_set)
    if not rows:
        raise SystemExit(f"no questions in {args.golden_set}")

    settings = Settings()
    rag = build_rag(settings)
    top_k = settings.retrieval.top_k if args.top_k is None else args.top_k
    metrics, misses = evaluate(rows, rag.search, top_k)

    report: dict[str, Any] = {
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_commit": git_commit(),
        "config": {
            "embedding_model": settings.embedding.model,
            "embedding_dimension": settings.embedding.dimension,
            "chunk_max_length": settings.chunking.max_length,
            "vector_store": settings.vector_store.backend,
            "collection": settings.vector_store.collection,
            "top_k": top_k,
            "reranker": "none",  # no reranker stage yet (rag/8): read it from the settings once there is one
            "llm_model": settings.llm.model if args.ask else None,
            "golden_set": args.golden_set.name,
            "golden_set_sha256": file_hash(args.golden_set),
        },
        "metrics": metrics,
        "misses": misses,
    }
    if args.ask:
        report["answers"], report["answer_failures"] = evaluate_answers(rows, lambda question: rag.ask(question, top_k))
    if not args.no_history:
        append_history(args.history, report)

    # a redirected stdout would use the locale's encoding (cp1252 on Windows): keep the report UTF-8 JSON
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
