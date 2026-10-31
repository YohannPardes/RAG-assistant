"""Retrieval eval over the golden set (scope/4): recall@k and MRR of search, printed as JSON.

Every run is also appended as one line to data/eval_history.jsonl, so runs and their configs can be compared.
Searches the configured collection as it is: run scripts/ingest.py first.
Usage: uv run python scripts/run_eval.py [--top-k 5] [--golden-set data/golden_set.jsonl] [--history PATH | --no-history]
"""

import argparse
import hashlib
import json
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from siteops_rag.config import Settings
from siteops_rag.container import build_rag
from siteops_rag.vector_store.base import ScoredChunk

REPO_ROOT = Path(__file__).resolve().parents[1]
NO_SECTION = "(no section)"
HISTORY = REPO_ROOT / "data" / "eval_history.jsonl"


def load_golden_set(path: Path) -> list[dict[str, str]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def section(hit: ScoredChunk) -> str:
    return hit.chunk.heading_path[-1] if hit.chunk.heading_path else NO_SECTION


def first_hit_rank(row: dict[str, str], hits: list[ScoredChunk]) -> int | None:
    """1-based rank of the first chunk from the expected source and section; never matched by chunk_id (scope/4)."""
    for rank, hit in enumerate(hits, start=1):
        if hit.chunk.source == row["expected_source"] and section(hit) == row["expected_section"]:
            return rank
    return None


def evaluate(
    rows: list[dict[str, str]], search: Callable[[str, int], list[ScoredChunk]], top_k: int
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Recall@k (a correct chunk in the top k) and MRR (mean of 1/rank of the first correct chunk, 0 if none)."""
    reciprocal_ranks = []
    misses = []
    for row in rows:
        hits = search(row["question"], top_k)
        rank = first_hit_rank(row, hits)
        reciprocal_ranks.append(1 / rank if rank else 0.0)
        if rank is None:
            misses.append({
                "question": row["question"],
                "expected": f"{row['expected_source']} › {row['expected_section']}",
                "got": [section(hit) for hit in hits],
            })

    n = len(rows)
    metrics = {
        f"recall@{top_k}": round((n - len(misses)) / n, 3),
        "mrr": round(sum(reciprocal_ranks) / n, 3),
        "n_questions": n,
    }
    return metrics, misses


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
    parser.add_argument("--top-k", type=int, default=5)  # same default as /search (rag/7)
    parser.add_argument("--golden-set", type=Path, default=REPO_ROOT / "data" / "golden_set.jsonl")
    parser.add_argument("--history", type=Path, default=HISTORY)
    parser.add_argument("--no-history", action="store_true", help="print the report without recording the run")
    args = parser.parse_args()

    rows = load_golden_set(args.golden_set)
    if not rows:
        raise SystemExit(f"no questions in {args.golden_set}")

    settings = Settings()
    rag = build_rag(settings)
    metrics, misses = evaluate(rows, rag.search, args.top_k)

    report = {
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_commit": git_commit(),
        "config": {
            "embedding_model": settings.embedding.model,
            "embedding_dimension": settings.embedding.dimension,
            "chunk_max_length": settings.chunking.max_length,
            "vector_store": settings.vector_store.backend,
            "collection": settings.vector_store.collection,
            "top_k": args.top_k,
            "reranker": "none",  # no reranker stage yet (rag/8): read it from the settings once there is one
            "golden_set": args.golden_set.name,
            "golden_set_sha256": file_hash(args.golden_set),
        },
        "metrics": metrics,
        "misses": misses,
    }
    if not args.no_history:
        append_history(args.history, report)

    # a redirected stdout would use the locale's encoding (cp1252 on Windows): keep the report UTF-8 JSON
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
