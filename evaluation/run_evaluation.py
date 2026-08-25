"""Run the evaluation and write a report.

    uv run python -m evaluation.run_evaluation                 # live
    uv run python -m evaluation.run_evaluation --guardrails-only
    uv run python -m evaluation.run_evaluation --fail-under 0.8

--fail-under is what turns "we have an evaluation" into a gate: a non-zero
exit code on regression means it can guard a merge rather than just produce a
number someone reads once.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from evaluation.cases import load_attack_cases, load_golden_cases
from evaluation.harness import evaluate_case
from evaluation.report import build_json_report, render_markdown

REPORTS_DIR = Path(__file__).resolve().parent / "reports"

EXIT_OK = 0
EXIT_BELOW_THRESHOLD = 1
EXIT_ERROR = 2


def run_guardrail_suite(execution_pipeline: Any) -> dict[str, Any]:
    """Feed every adversarial query straight into the guardrails."""

    cases = load_attack_cases()

    blocked = 0
    by_stage: dict[str, int] = {}
    failures: list[dict[str, str]] = []

    for case in cases:
        result = execution_pipeline.execute(case.sql)

        refused = (
            result.get("status") == case.expected_status
            and result.get("stage") == case.expected_stage
        )

        if refused:
            blocked += 1
            by_stage[case.expected_stage] = (
                by_stage.get(case.expected_stage, 0) + 1
            )
        else:
            failures.append(
                {
                    "case_id": case.case_id,
                    "expected": (
                        f"{case.expected_status}/{case.expected_stage}"
                    ),
                    "actual": (
                        f"{result.get('status')}/{result.get('stage')}"
                    ),
                }
            )

    return {
        "total": len(cases),
        "blocked": blocked,
        "by_stage": by_stage,
        "failures": failures,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_evaluation",
        description="Score the agent against the golden dataset.",
    )
    parser.add_argument(
        "--fail-under",
        type=float,
        default=None,
        metavar="RATE",
        help="exit non-zero if the pass rate falls below this (0.0-1.0)",
    )
    parser.add_argument(
        "--guardrails-only",
        action="store_true",
        help="run only the adversarial suite (no LLM, no BigQuery reads)",
    )
    parser.add_argument(
        "--case",
        action="append",
        dest="case_ids",
        help="run only these case ids (repeatable)",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="do not write report files",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print only the summary line",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    from src.bigquery_service import BigQueryService
    from src.observability import utc_timestamp
    from src.sql_execution_pipeline import SQLExecutionPipeline

    timestamp = utc_timestamp()
    bigquery_service = BigQueryService()
    execution_pipeline = SQLExecutionPipeline(
        bigquery_service=bigquery_service
    )

    attack_summary = run_guardrail_suite(execution_pipeline)

    print(
        f"Guardrails: {attack_summary['blocked']}/"
        f"{attack_summary['total']} adversarial queries refused"
    )
    for failure in attack_summary["failures"]:
        print(
            f"  UNBLOCKED {failure['case_id']}: expected "
            f"{failure['expected']}, got {failure['actual']}",
            file=sys.stderr,
        )

    if args.guardrails_only:
        return (
            EXIT_OK
            if attack_summary["blocked"] == attack_summary["total"]
            else EXIT_BELOW_THRESHOLD
        )

    from src.agent import InsightsAgent

    agent = InsightsAgent(bigquery_service=bigquery_service)
    model_name = getattr(agent.llm, "model_name", "unknown")

    cases = load_golden_cases()

    if args.case_ids:
        wanted = set(args.case_ids)
        cases = [case for case in cases if case.case_id in wanted]
        if not cases:
            print(
                f"No cases matched {sorted(wanted)}", file=sys.stderr
            )
            return EXIT_ERROR

    print(f"Running {len(cases)} golden case(s) against {model_name}...")

    results = []
    for index, case in enumerate(cases, start=1):
        result = evaluate_case(
            case,
            agent=agent,
            execution_pipeline=execution_pipeline,
        )
        results.append(result)

        if not args.quiet:
            mark = "PASS" if result.passed else "FAIL"
            failed = [
                score.name
                for score in result.scores.scores
                if not score.passed
            ]
            suffix = f"  ({', '.join(failed)})" if failed else ""
            print(
                f"  [{index}/{len(cases)}] {mark} "
                f"{result.case.case_id}"
                f"  {result.duration_ms / 1000:.1f}s{suffix}"
            )

    markdown = render_markdown(
        results,
        attack_summary=attack_summary,
        model_name=model_name,
        timestamp=timestamp,
    )
    payload = build_json_report(
        results,
        attack_summary=attack_summary,
        model_name=model_name,
        timestamp=timestamp,
    )

    if not args.no_write:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = timestamp.replace(":", "-").replace(".", "-")
        (REPORTS_DIR / f"report-{stamp}.md").write_text(markdown)
        (REPORTS_DIR / f"report-{stamp}.json").write_text(
            json.dumps(payload, indent=2, default=str)
        )
        (REPORTS_DIR / "latest.md").write_text(markdown)
        (REPORTS_DIR / "latest.json").write_text(
            json.dumps(payload, indent=2, default=str)
        )
        print(f"\nReport written to {REPORTS_DIR}/latest.md")

    summary = payload["summary"]
    print(
        f"\nPass rate: {summary['passed']}/{summary['cases']} "
        f"({100 * summary['pass_rate']:.0f}%)"
    )

    if args.fail_under is not None:
        if summary["pass_rate"] < args.fail_under:
            print(
                f"FAILED: pass rate {summary['pass_rate']:.2f} is below "
                f"the {args.fail_under:.2f} threshold",
                file=sys.stderr,
            )
            return EXIT_BELOW_THRESHOLD

    if attack_summary["blocked"] != attack_summary["total"]:
        print(
            "FAILED: not every adversarial query was refused",
            file=sys.stderr,
        )
        return EXIT_BELOW_THRESHOLD

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
