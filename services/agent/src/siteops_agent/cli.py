"""Run one triage from the command line: for a scheduler, the eval, or by hand. The same job as POST /triage.

Usage: uv run python -m siteops_agent.cli --since 2026-11-01T09:00Z --until 2026-11-01T10:00Z [--dry-run] [--report FILE] [--log FILE]

The report (JSON) goes to --report, or to stdout; the totals go to stderr. Each window's full transcript is logged:
to --log, or to stderr. A window that failed is in the report as "error"; only a run that can't run at all fails.
"""

import argparse
import logging
import sys
import uuid
from datetime import datetime
from pathlib import Path

from siteops_agent.container import build_job
from siteops_agent.runs import TriageRequest


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, filename=args.log, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    # 1. the request, checked as the API checks it: aware times, since before until
    request = TriageRequest(since=args.since, until=args.until, dry_run=args.dry_run)

    # 2. the run: built by the container, run here and waited for
    report = build_job()(uuid.uuid4().hex, request)

    # 3. the report for its readers, the totals for whoever ran it
    if args.report is None:
        print(report.model_dump_json(indent=2))
    else:
        args.report.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    print(f"run {report.run_id}: {report.events_read} events, {len(report.outcomes)} windows, {report.totals}", file=sys.stderr)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="siteops-triage", description="Triage the logs of [since, until).")
    parser.add_argument("--since", required=True, type=datetime.fromisoformat, help="start, included; with a timezone, e.g. 2026-11-01T09:00Z")
    parser.add_argument("--until", required=True, type=datetime.fromisoformat, help="end, excluded; with a timezone")
    parser.add_argument("--dry-run", action="store_true", help="tickets go to an in-memory sink: nothing is filed")
    parser.add_argument("--report", type=Path, help="write the report (JSON) here instead of stdout")
    parser.add_argument("--log", help="write the log (each window's full transcript) here instead of stderr")
    return parser


if __name__ == "__main__":
    main()
