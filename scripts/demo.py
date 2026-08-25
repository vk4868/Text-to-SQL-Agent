"""A short, safe live demo.

    uv run python scripts/demo.py
    uv run python scripts/demo.py --guardrails-only

Three questions chosen to show the happy path, a join, and a query that
matches nothing — plus the adversarial suite, which needs no model and
finishes in about a second.
"""

import argparse
import sys
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

QUESTIONS = [
    "What were total net sales by month in 2025?",
    "Which product category generated the most profit in 2025?",
    "What were net sales by month in 1999?",
]

RULE = "═" * 72


def banner(text: str) -> None:
    print()
    print(RULE)
    print(f"  {text}")
    print(RULE)


def show_guardrails() -> int:
    """Feed hostile SQL straight into the pipeline. No LLM, no question."""

    from evaluation.cases import load_attack_cases
    from evaluation.run_evaluation import run_guardrail_suite
    from src.bigquery_service import BigQueryService
    from src.sql_execution_pipeline import SQLExecutionPipeline

    banner("GUARDRAILS — hostile SQL, no model involved")

    pipeline = SQLExecutionPipeline(bigquery_service=BigQueryService())
    cases = load_attack_cases()

    for case in cases[:5]:
        result = pipeline.execute(case.sql)
        first_line = " ".join(case.sql.split())[:52]
        print(
            f"  {result['status']:>8} at {result['stage']:<14} "
            f"{first_line}…"
        )

    summary = run_guardrail_suite(pipeline)

    print()
    print(
        f"  {summary['blocked']}/{summary['total']} adversarial queries "
        "refused, every one during parsing —"
    )
    print("  nothing reached BigQuery.")

    return 0 if summary["blocked"] == summary["total"] else 1


def show_questions(max_rows: int) -> int:
    from src.agent import InsightsAgent
    from src.presentation import format_run_for_terminal

    banner("ASKING QUESTIONS")

    agent = InsightsAgent()
    failures = 0

    for question in QUESTIONS:
        record = agent.run(question)

        print()
        print(
            format_run_for_terminal(
                record, show_trace=True, max_rows=max_rows
            )
        )

        if record.get("outcome", {}).get("status") != "success":
            failures += 1

        analysis = record.get("analysis", {})
        if analysis.get("ungrounded_numbers"):
            print()
            print(
                "  ^ the guard flagged a figure that is not derivable "
                "from those rows."
            )

    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="demo",
        description="Run a short live demonstration.",
    )
    parser.add_argument(
        "--guardrails-only",
        action="store_true",
        help="skip the model and show only the adversarial suite",
    )
    parser.add_argument("--max-rows", type=int, default=6)

    args = parser.parse_args(argv)

    code = show_guardrails()

    if args.guardrails_only:
        return code

    return show_questions(args.max_rows) or code


if __name__ == "__main__":
    raise SystemExit(main())
