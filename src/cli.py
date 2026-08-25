"""Command line entry point.

Exit codes are meaningful so the CLI can be used in a pipeline:
0 the question was answered, 1 it was rejected or failed, 2 bad usage.
"""

import argparse
import json
import sys
from typing import Any, Sequence

from src.presentation import format_run_for_terminal

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sql-bigquery-agent",
        description=(
            "Ask a business question in plain English. The agent grounds "
            "itself in the live BigQuery schema, writes SQL, proves it safe, "
            "runs it read-only, and explains the result."
        ),
    )

    parser.add_argument(
        "question",
        nargs="?",
        help="the business question to answer",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit the full run record as JSON instead of prose",
    )
    parser.add_argument(
        "--sql-only",
        action="store_true",
        help="print only the final SQL",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="include per-node timings",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=10,
        help="how many result rows to display (default: 10)",
    )

    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    agent: Any = None,
) -> int:
    """Run one question. `agent` is injectable so this is testable."""

    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.question or not args.question.strip():
        parser.print_usage(sys.stderr)
        print(
            "\nA question is required, for example:\n"
            '  uv run python main.py "What were total net sales by '
            'month in 2025?"',
            file=sys.stderr,
        )
        return EXIT_USAGE

    if agent is None:
        from src.agent import InsightsAgent

        agent = InsightsAgent()

    record = agent.run(args.question)

    return _report(record, args)


def _report(record: dict[str, Any], args: argparse.Namespace) -> int:
    status = record.get("outcome", {}).get("status")
    succeeded = status == "success"

    if args.as_json:
        print(json.dumps(record, indent=2, default=str))
        return EXIT_OK if succeeded else EXIT_FAILED

    if args.sql_only:
        final_sql = record.get("sql", {}).get("final_sql")
        if final_sql:
            print(final_sql)
            return EXIT_OK if succeeded else EXIT_FAILED

        print(
            "No SQL was produced: "
            f"{record.get('outcome', {}).get('error_message')}",
            file=sys.stderr,
        )
        return EXIT_FAILED

    rendered = format_run_for_terminal(
        record,
        show_trace=args.trace,
        max_rows=args.max_rows,
    )

    print(rendered, file=sys.stdout if succeeded else sys.stderr)

    return EXIT_OK if succeeded else EXIT_FAILED
