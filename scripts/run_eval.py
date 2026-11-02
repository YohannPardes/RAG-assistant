"""Evals of the stack, one subcommand each. Every run prints a report for people and records itself for code.

  rag        retrieval over the golden set: recall@k and MRR, overall and per question category; with --ask, /ask's
             answers too (false answers on unanswerable questions, answer rate, citation accuracy, key facts)
  prefilter  the windows the prefilter gives seeded logs, no LLM: its recall (the ceiling of triage recall) and the
             prefilter-only baseline (every window a ticket)
  triage     the whole agent on seeded logs: recall, precision, duplicates, decoys dismissed, root host and runbook,
             the windows' confusion matrix, and the stage each missed fault was lost at (prefilter, retrieval, model)

Each run writes eval_runs/<run_id>/ (git-ignored; not under data/, which ingestion reads whole):
  report.md     the report printed at the end
  failures.md   one row per failure with an empty "why" column, for error analysis
  report.json   everything the run measured, for code
  items.jsonl   one flat row per scored item (question, fault), with the run's config as config.* columns: load many
                runs' files into one DataFrame to plot a metric against a setting
  seed-<n>/     prefilter and triage: the generated log and its ground truth; triage: the agent's report and transcripts
and appends one line (run_id, kind, config, metrics, failures) to data/eval_history.jsonl.
scripts/compare_evals.py compares two runs.

Golden-set fields: question, expected_source, expected_section; optional category (default "uncategorized"),
must_contain (a list of strings), held_out (true: scored only with --held-out, to check a gain on questions nothing
was tuned on). Category "unanswerable" has no expected source or section: it is left out of retrieval.
rag searches the configured collection as it is (ingest data/ first). triage uses the agent's config: the model
server and the rag service must be running; it files nothing (an in-memory ticket sink per seed).

Usage:
  uv run python scripts/run_eval.py rag [--ask] [--top-k N] [--held-out]
  uv run python scripts/run_eval.py prefilter [--seeds 1 2 3 4 5] [--hours 4]
  uv run python scripts/run_eval.py triage [--seeds 1 2 3 4 5] [--hours 4] [--repeats 1]
                                           [--max-searches N] [--max-log-queries N] [--hits-per-search N] [--model NAME]
Common: [--history PATH | --no-history] [--runs-dir PATH]
"""

import argparse
import hashlib
import importlib.util
import json
import logging
import subprocess
import sys
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from siteops_common.contracts import AskResponse, Citation
from siteops_rag.vector_store.base import ScoredChunk

REPO_ROOT = Path(__file__).resolve().parents[1]
NO_SECTION = "(no section)"
HISTORY = REPO_ROOT / "data" / "eval_history.jsonl"
RUNS = REPO_ROOT / "eval_runs"  # not under data/: ingestion reads all of data/
UNANSWERABLE = "unanswerable"
LOG_START = datetime(2026, 11, 1, 6, tzinfo=UTC)  # generate_logs.py's default: the seeded logs start here

Row = dict[str, Any]


def _script(name: str) -> ModuleType:
    """A sibling script (generate_logs, triage_eval): scripts/ is not a package, so it is loaded from its path."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def held_out(row: Row) -> bool:
    return bool(row.get("held_out", False))


def load_golden_set(path: Path) -> list[Row]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def category(row: Row) -> str:
    return str(row.get("category", "uncategorized"))


def answerable(row: Row) -> bool:
    return category(row) != UNANSWERABLE


def section(hit: ScoredChunk) -> str:
    return hit.chunk.heading_path[-1] if hit.chunk.heading_path else NO_SECTION


def first_hit_rank(row: Row, hits: list[ScoredChunk]) -> int | None:
    """1-based rank of the first chunk from the expected source and section; never matched by chunk_id."""
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


# --- recording a run ---


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


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def append_history(path: Path, report: dict[str, Any]) -> None:
    """One JSON line per run, oldest first; the file is only ever appended to."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as history:
        history.write(json.dumps(report, ensure_ascii=False) + "\n")


def new_run(kind: str) -> tuple[str, str]:
    now = datetime.now(UTC)
    return f"{kind}-{now:%Y%m%d-%H%M%S}", now.strftime("%Y-%m-%dT%H:%M:%SZ")


def flat_config(config: dict[str, Any]) -> dict[str, Any]:
    """The config as columns: a list becomes a string, so every row stays one flat record."""
    return {f"config.{key}": " ".join(map(str, value)) if isinstance(value, list) else value for key, value in config.items()}


def record(args: argparse.Namespace, run: dict[str, Any], report_md: str, failures_md: str, items: list[dict[str, Any]]) -> Path:
    """The run's folder (report.md, failures.md, report.json, items.jsonl) and its history line; prints the report."""
    folder = args.runs_dir / run["run_id"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "report.md").write_text(report_md + "\n", encoding="utf-8")
    (folder / "failures.md").write_text(failures_md + "\n", encoding="utf-8")
    (folder / "report.json").write_text(json.dumps(run, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    base = {
        "run_id": run["run_id"],
        "kind": run["kind"],
        "timestamp": run["timestamp"],
        "git_commit": run["git_commit"],
        **flat_config(run["config"]),
    }
    with (folder / "items.jsonl").open("w", encoding="utf-8") as rows:
        for item in items:
            rows.write(json.dumps(base | item, ensure_ascii=False) + "\n")
    if not args.no_history:
        line = {key: run[key] for key in ("run_id", "kind", "timestamp", "git_commit", "config", "metrics", "failures")}
        append_history(args.history, line | {key: run[key] for key in ("misses", "answers", "answer_failures") if key in run})
    # a redirected stdout would use the locale's encoding (cp1252 on Windows): keep the report UTF-8
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    print(report_md)
    print(f"\nRun folder: {folder}  (report.md, failures.md, report.json, items.jsonl)")
    return folder


# --- rag ---


def render_rag(run: dict[str, Any]) -> str:
    table = _script("triage_eval").table
    m, config = run["metrics"], run["config"]
    recall = f"recall@{config['top_k']}"
    scope = "held-out questions only" if config["held_out"] else "held-out questions left out"
    sections = [
        f"# RAG eval {run['run_id']}",
        f"Golden set {config['golden_set']} ({config['golden_set_sha256']}, {scope}), {config['embedding_model']}, top {config['top_k']}, "
        f"chunks of {config['chunk_max_length']} characters, commit {run['git_commit']}.",
        "## Retrieval",
        table(
            ["questions", recall, "MRR", "what it means"],
            [
                [
                    m["n_questions"],
                    m[recall],
                    m["mrr"],
                    f"a chunk of the expected section in the top {config['top_k']}; MRR: mean 1/rank of the first one",
                ]
            ],
        ),
        "## Per category",
        table(["category", "questions", recall, "MRR"], [[name, c["n_questions"], c[recall], c["mrr"]] for name, c in m["by_category"].items()]),
        "## Missed questions",
        table(
            ["category", "question", "expected", "top 3 returned"],
            [[x["category"], x["question"], x["expected"], "; ".join(x["got"][:3])] for x in run["misses"]],
        )
        if run["misses"]
        else "None.",
    ]
    if "answers" in run:
        a = run["answers"]
        sections += [
            "## Answers (/ask)",
            table(
                ["metric", "value", "what it means"],
                [
                    [
                        "false answer rate",
                        a["false_answer_rate"],
                        f"unanswerable questions answered anyway, of {a['n_unanswerable']}: lower is better",
                    ],
                    ["answer rate", a["answer_rate"], f"answerable questions answered, of {a['n_answerable']}"],
                    ["citation accuracy", a["citation_accuracy"], "answerable questions citing the expected section"],
                    ["key facts", a["key_facts"], f"answers with every must_contain fact, of {a['n_with_facts']}"],
                ],
            ),
        ]
    return "\n\n".join(sections)


def run_rag(args: argparse.Namespace) -> None:
    from siteops_rag.config import Settings
    from siteops_rag.container import build_rag

    rows = [row for row in load_golden_set(args.golden_set) if held_out(row) == args.held_out]
    if not rows:
        raise SystemExit(f"no {'held-out ' if args.held_out else ''}questions in {args.golden_set}")
    settings = Settings()
    rag = build_rag(settings)
    top_k = settings.retrieval.top_k if args.top_k is None else args.top_k
    run_id, timestamp = new_run("rag")
    metrics, misses = evaluate(rows, rag.search, top_k)
    run: dict[str, Any] = {
        "run_id": run_id,
        "kind": "rag",
        "timestamp": timestamp,
        "git_commit": git_commit(),
        "config": {
            "embedding_model": settings.embedding.model,
            "embedding_dimension": settings.embedding.dimension,
            "chunk_max_length": settings.chunking.max_length,
            "vector_store": settings.vector_store.backend,
            "collection": settings.vector_store.collection,
            "top_k": top_k,
            "reranker": "none",  # no reranker stage yet: read it from the settings once there is one
            "llm_model": settings.llm.model if args.ask else None,
            "golden_set": args.golden_set.name,
            "golden_set_sha256": file_hash(args.golden_set),
            "held_out": args.held_out,
        },
        "metrics": metrics,
        "misses": misses,
        "failures": [{"item": miss["question"], "failure": "retrieval miss", "blame": "retrieval", "detail": miss["expected"]} for miss in misses],
    }
    if args.ask:
        run["answers"], run["answer_failures"] = evaluate_answers(rows, lambda question: rag.ask(question, top_k))
        run["failures"] += [
            {
                "item": f["question"],
                "failure": " ".join(f["kind"]) if isinstance(f["kind"], list) else f["kind"],
                "blame": "answer",
                "detail": f["answer"][:200],
            }
            for f in run["answer_failures"]
        ]
    missed = {miss["question"] for miss in misses}
    items = [
        {
            "item": row["question"],
            "category": category(row),
            "expected": f"{row['expected_source']} › {row['expected_section']}",
            "found": row["question"] not in missed,
        }
        for row in rows
        if answerable(row)
    ]
    failures_md = _script("triage_eval").render_failures(run_id, run["failures"], "the report's missed questions list what was returned")
    record(args, run, render_rag(run), failures_md, items)


# --- seeded logs, for prefilter and triage ---


def seeded_logs(folder: Path, seed: int, hours: float) -> tuple[Path, dict[str, Any]]:
    """The seed's log and ground truth, written as scripts/generate_logs.py writes them, in the run's folder."""
    truth = _script("generate_logs").write(folder / f"seed-{seed}", seed, LOG_START, hours)
    return folder / f"seed-{seed}" / "site.log", truth


def span(truth: dict[str, Any]) -> tuple[datetime, datetime]:
    return datetime.fromisoformat(truth["start"].replace("Z", "+00:00")), datetime.fromisoformat(truth["end"].replace("Z", "+00:00"))


# --- prefilter ---


def run_prefilter(args: argparse.Namespace) -> None:
    from siteops_agent.log_source.file import FileLogSource
    from siteops_agent.prefilter import find_windows

    triage_eval = _script("triage_eval")
    run_id, timestamp = new_run("prefilter")
    folder = args.runs_dir / run_id
    seeds, items = [], []
    for seed in args.seeds:
        log, truth = seeded_logs(folder, seed, args.hours)
        windows = [
            {"host": w.host, "rule": w.rule, "start": w.start.isoformat(), "end": w.end.isoformat()}
            for w in find_windows(FileLogSource(log).read(*span(truth)).events)
        ]
        scored = triage_eval.score_prefilter(truth, windows)
        seeds.append({"seed": seed, **scored})
        items += [
            {"item": f"seed {seed} {f['fault']} {f['fault_type']}", "seed": seed, **f, "covered": f["windows"] > 0} for f in scored["per_fault"]
        ]
    run = {
        "run_id": run_id,
        "kind": "prefilter",
        "timestamp": timestamp,
        "git_commit": git_commit(),
        "config": {"seeds": args.seeds, "hours": args.hours},
        "metrics": triage_eval.prefilter_metrics(seeds),
        "seeds": seeds,
        "failures": [
            {
                "item": f"seed {s['seed']} {f['fault']} {f['fault_type']}",
                "failure": "no window",
                "blame": "prefilter",
                "detail": f"root host {f['host']}",
            }
            for s in seeds
            for f in s["per_fault"]
            if not f["windows"]
        ],
    }
    failures_md = triage_eval.render_failures(run_id, run["failures"], f"{folder}/seed-<n>/ (site.log, ground_truth.json)")
    record(args, run, triage_eval.render_prefilter(run), failures_md, items)


# --- triage ---


def run_triage(args: argparse.Namespace) -> None:
    from siteops_agent import prompts
    from siteops_agent.config import LogSettings, Settings
    from siteops_agent.container import build_job
    from siteops_agent.runs import TriageRequest

    triage_eval = _script("triage_eval")
    run_id, timestamp = new_run("triage")
    folder = args.runs_dir / run_id
    # a dry run never calls the tickets service: a stand-in key, so the eval runs without the real one
    tickets = {"url": "http://dry-run.invalid/mcp", "api_key": "eval-dry-run-unused-key", "timeout": 1.0, "retries": 0}
    base = Settings(tickets=tickets)  # type: ignore[call-arg]
    overrides = {"max_searches": args.max_searches, "max_log_queries": args.max_log_queries, "hits_per_search": args.hits_per_search}
    budgets = base.triage.model_copy(update={key: value for key, value in overrides.items() if value is not None})
    llm = base.llm.model_copy(update={"model": args.model}) if args.model else base.llm
    logs = {seed: seeded_logs(folder, seed, args.hours) for seed in args.seeds}

    repeats = []
    for repeat in range(1, args.repeats + 1):
        seeds = []
        for seed, (log, truth) in logs.items():
            settings = base.model_copy(update={"triage": budgets, "llm": llm, "logs": LogSettings(path=log)})
            out = log.parent / f"repeat-{repeat}"
            out.mkdir(exist_ok=True)
            started = time.monotonic()
            since, until = span(truth)
            with Transcripts(out / "transcripts.log"):
                report = build_job(settings)(f"{run_id}-s{seed}-r{repeat}", TriageRequest(since=since, until=until, dry_run=True))
            (out / "triage_report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
            scored = {"seed": seed, **triage_eval.score_triage(truth, report.model_dump(mode="json")["outcomes"])}
            seeds.append(scored)
            found = sum(fault["detected"] for fault in scored["faults"])
            print(
                f"repeat {repeat}, seed {seed}: {scored['windows']} windows, {found}/{len(scored['faults'])} faults detected, "
                f"{time.monotonic() - started:.0f} s",
                file=sys.stderr,
            )
        repeats.append({"repeat": repeat, "seeds": seeds, "metrics": triage_eval.triage_metrics(seeds)})

    run: dict[str, Any] = {
        "run_id": run_id,
        "kind": "triage",
        "timestamp": timestamp,
        "git_commit": git_commit(),
        "config": {
            "model": llm.model,
            "temperature": llm.temperature,
            "model_seed": llm.seed,
            "seeds": args.seeds,
            "hours": args.hours,
            "repeats": args.repeats,
            "max_llm_calls": budgets.max_llm_calls,
            "max_searches": budgets.max_searches,
            "max_log_queries": budgets.max_log_queries,
            "hits_per_search": budgets.hits_per_search,
            "rag": base.rag.base_url,
            "prompt_sha256": text_hash(prompts.SYSTEM_PROMPT),
        },
        "metrics": triage_eval.over_repeats([repeat["metrics"] for repeat in repeats]),
        "repeats": repeats,
        "warnings": triage_eval.warnings(repeats),
    }
    run["failures"] = triage_eval.triage_failures(run)
    items = [
        {
            "item": f"seed {s['seed']} {f['fault']} {f['fault_type']}",
            "repeat": r["repeat"],
            "seed": s["seed"],
            **f,
            "what_happened": "; ".join(f["what_happened"]),
        }
        for r in repeats
        for s in r["seeds"]
        for f in s["faults"]
    ]
    failures_md = triage_eval.render_failures(run_id, run["failures"], f"{folder}/seed-<n>/repeat-<r>/ (triage_report.json, transcripts.log)")
    record(args, run, triage_eval.render_triage(run), failures_md, items)


class Transcripts:
    """The agent's per-window transcripts go to a file, not to the console: they are long."""

    def __init__(self, path: Path) -> None:
        self.handler = logging.FileHandler(path, encoding="utf-8")
        self.logger = logging.getLogger("siteops_agent.agent")

    def __enter__(self) -> None:
        self.logger.addHandler(self.handler)
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

    def __exit__(self, *exc: object) -> None:
        self.logger.removeHandler(self.handler)
        self.handler.close()


# --- the command line ---


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--history", type=Path, default=HISTORY)
    common.add_argument("--no-history", action="store_true", help="report the run without appending it to the history")
    common.add_argument("--runs-dir", type=Path, default=RUNS, help="where each run's folder goes")
    seeded = argparse.ArgumentParser(add_help=False)
    seeded.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3, 4, 5], help="one generated log per seed")
    seeded.add_argument("--hours", type=float, default=4.0, help="length of each generated log, at least 2")
    commands = parser.add_subparsers(dest="kind", required=True)

    rag = commands.add_parser("rag", parents=[common], help="retrieval over the golden set; --ask: /ask's answers too")
    rag.add_argument("--ask", action="store_true", help="also score /ask's answers (one LLM call per question)")
    rag.add_argument("--top-k", type=int, help="default: retrieval.top_k from the rag config, as /search uses")
    rag.add_argument("--golden-set", type=Path, default=REPO_ROOT / "data" / "golden_set.jsonl")
    rag.add_argument("--held-out", action="store_true", help="score only the held-out questions (left out otherwise)")
    rag.set_defaults(run=run_rag)

    prefilter = commands.add_parser("prefilter", parents=[common, seeded], help="the prefilter's windows on seeded logs: no LLM")
    prefilter.set_defaults(run=run_prefilter)

    triage = commands.add_parser("triage", parents=[common, seeded], help="the whole agent on seeded logs (dry run: nothing filed)")
    triage.add_argument("--repeats", type=int, default=1, help="run each seed this many times: the LLM's run-to-run spread")
    triage.add_argument("--max-searches", type=int, help="override the config's budget (an ablation)")
    triage.add_argument("--max-log-queries", type=int, help="override the config's budget; 0: no log_context")
    triage.add_argument("--hits-per-search", type=int, help="override the config's budget")
    triage.add_argument("--model", help="override the config's model")
    triage.set_defaults(run=run_triage)

    args = parser.parse_args()
    if getattr(args, "hours", 2) < 2:
        parser.error("--hours must be at least 2 (every fault needs its own slot)")
    args.run(args)


if __name__ == "__main__":
    main()
