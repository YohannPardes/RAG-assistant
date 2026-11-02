"""Compare two eval runs of the same kind: each metric's change, and which items newly fail or newly pass.

A run is named by its run_id (e.g. triage-20261102-153000) or by its position in the history: -1 the last run,
-2 the one before; with a kind (rag, prefilter, triage), only that kind's runs count: "-1 triage" is the last triage
run. Older history lines without a run_id or a kind are rag runs, named by their position.
A rate's change worth one item or less (e.g. one question of 91) is marked "within noise": with an LLM in the loop,
and test sets this small, a one-item move is not a result.
Usage: uv run python scripts/compare_evals.py BEFORE AFTER [--kind triage] [--history data/eval_history.jsonl]
       uv run python scripts/compare_evals.py -2 -1 --kind triage     (the last two triage runs)
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
HISTORY = REPO_ROOT / "data" / "eval_history.jsonl"
SIZE = {"rag": "n_questions", "prefilter": "n_faults", "triage": "n_faults"}  # how many items a rate is over

Run = dict[str, Any]


def load_history(path: Path) -> list[Run]:
    runs = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for position, run in enumerate(runs):
        run.setdefault("kind", "rag")
        run.setdefault("run_id", f"{run['kind']}-#{position + 1}")
    return runs


def pick(runs: list[Run], name: str, kind: str | None) -> Run:
    candidates = [run for run in runs if kind is None or run["kind"] == kind]
    if name.lstrip("-").isdigit() and name.startswith("-"):
        if int(name) < -len(candidates):
            raise SystemExit(f"only {len(candidates)} {kind or ''} runs in the history")
        return candidates[int(name)]
    for run in candidates:
        if run["run_id"] == name:
            return run
    raise SystemExit(f"no run {name!r} in the history")


def _number(value: Any) -> float | None:
    if isinstance(value, dict) and "mean" in value:  # a triage run with repeats: its mean
        return float(value["mean"])
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def compare_metrics(before: Run, after: Run) -> list[dict[str, Any]]:
    """Every numeric metric in both runs, with its change; a rate's change worth one item or less is noise."""
    size = SIZE.get(after["kind"], "")
    items = _number(after["metrics"].get(size)) or _number(before["metrics"].get(size))
    rows = []
    for key in after["metrics"]:
        old, new = _number(before["metrics"].get(key)), _number(after["metrics"].get(key))
        if old is None or new is None or key == size:
            continue
        delta = round(new - old, 3)
        rate = 0 <= old <= 1 and 0 <= new <= 1 and isinstance(after["metrics"][key], float | dict)
        noise = rate and items is not None and abs(delta) * items <= 1
        rows.append({"metric": key, "before": old, "after": new, "change": delta, "note": "within noise (≤1 item)" if noise and delta else ""})
    return rows


def _failing(run: Run) -> set[str]:
    """The items that failed in a run, by name: a question, a seed's fault, a window."""
    if "failures" in run:
        return {f"{failure['item']}: {failure['failure']}" for failure in run["failures"]}
    return {f"{miss['question']}: retrieval miss" for miss in run.get("misses", [])}  # older rag lines


def compare_items(before: Run, after: Run) -> tuple[list[str], list[str]]:
    old, new = _failing(before), _failing(after)
    return sorted(new - old), sorted(old - new)


def _config_changes(before: Run, after: Run) -> list[str]:
    old, new = before.get("config", {}), after.get("config", {})
    return [f"{key}: {old.get(key)} → {new.get(key)}" for key in sorted(set(old) | set(new)) if old.get(key) != new.get(key)]


def _shown(value: float, sign: bool = False) -> str:
    """A count as a count (13, not 13.0), a rate as it is."""
    text = str(int(value)) if value == int(value) else str(value)
    return f"+{text}" if sign and value > 0 else text


def render(before: Run, after: Run) -> str:
    rows = compare_metrics(before, after)
    newly_failing, newly_passing = compare_items(before, after)
    lines = [
        f"# {before['run_id']} → {after['run_id']} ({after['kind']})",
        f"Commits {before.get('git_commit')} → {after.get('git_commit')}.",
        "## What changed in the config",
        "\n".join(f"- {change}" for change in _config_changes(before, after))
        or "Nothing: the same config (a change is the code's or the LLM's spread).",
        "## Metrics",
        "\n".join(
            [
                "| metric | before | after | change | |",
                "|---|---|---|---|---|",
                *(f"| {r['metric']} | {_shown(r['before'])} | {_shown(r['after'])} | {_shown(r['change'], sign=True)} | {r['note']} |" for r in rows),
            ]
        ),
        f"## Newly failing ({len(newly_failing)})",
        "\n".join(f"- {item}" for item in newly_failing) or "None.",
        f"## Newly passing ({len(newly_passing)})",
        "\n".join(f"- {item}" for item in newly_passing) or "None.",
    ]
    return "\n\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("before", help="a run_id, or -2 (the one before last)")
    parser.add_argument("after", help="a run_id, or -1 (the last one)")
    parser.add_argument("--kind", choices=sorted(SIZE), help="count positions among this kind's runs only")
    parser.add_argument("--history", type=Path, default=HISTORY)
    args = parser.parse_args()

    runs = load_history(args.history)
    before, after = pick(runs, args.before, args.kind), pick(runs, args.after, args.kind)
    if before["kind"] != after["kind"]:
        raise SystemExit(f"{before['run_id']} is a {before['kind']} run and {after['run_id']} a {after['kind']} run: compare runs of one kind")
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    print(render(before, after))


if __name__ == "__main__":
    main()
