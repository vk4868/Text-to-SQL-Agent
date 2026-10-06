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
from time import perf_counter
from pathlib import Path
from typing import Any, Sequence

from evaluation.cases import (
    ATTACK_CASES_PATH,
    GOLDEN_CASES_PATH,
    AttackCase,
    load_attack_cases,
    load_golden_cases,
)
from evaluation.harness import classify_failure, evaluate_case
from evaluation.manifest import build_manifest, ollama_model_identity
from evaluation.report import (
    build_json_report,
    redact_project_id,
    render_markdown,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
EVALUATION_DIR = Path(__file__).resolve().parent
REPORTS_DIR = EVALUATION_DIR / "reports"
EXPECTED_FACTS_PATH = EVALUATION_DIR / "golden_expected_facts.yaml"

MANIFEST_SECTIONS = (
    "git",
    "dataset",
    "model",
    "config",
    "cases",
    "files",
    "environment",
    "versions",
)
DATASETS_DIR = REPO_ROOT / "Datasets"

EXIT_OK = 0
EXIT_BELOW_THRESHOLD = 1
EXIT_ERROR = 2


def run_guardrail_suite(
    execution_pipeline: Any, cases: list[AttackCase] | None = None
) -> dict[str, Any]:
    """Feed every adversarial query straight into the guardrails."""

    if cases is None:
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


def _model_unavailable(outcome: dict[str, Any]) -> bool:
    """Whether a run's failure says the LLM provider could not be reached.

    Shares its keyword list with ``harness.classify_failure``.
    """

    return classify_failure({"outcome": outcome}) == "unavailable"


def manifest_problems(manifest: dict[str, Any]) -> list[str]:
    """Every reason this manifest cannot back a valid baseline."""

    problems: list[str] = []

    if "error" in manifest:
        problems.append(f"manifest unavailable: {manifest['error']}")

    for name in MANIFEST_SECTIONS:
        section = manifest.get(name)
        if isinstance(section, dict) and "error" in section:
            problems.append(f"manifest section {name}: {section['error']}")

    if (manifest.get("dataset") or {}).get("all_match") is not True:
        problems.append("dataset reconciliation does not fully match")

    return problems


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not an integer"
        ) from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return value


def _report_name(text: str) -> str:
    """Reject names that could escape reports/ or shadow the committed set."""

    if (
        not text
        or "/" in text
        or "\\" in text
        or ".." in text
        or text == "latest"
        or text.startswith("report-")
    ):
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a valid report name (no path separators or "
            "'..', not 'latest', not starting with 'report-')"
        )
    return text


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
        "--overwrite",
        action="store_true",
        help="allow --report-name to replace existing files",
    )
    parser.add_argument(
        "--run-log",
        default=None,
        metavar="PATH",
        help="write this run's JSONL run log (and app log) next to PATH",
    )
    parser.add_argument(
        "--warm-up-question",
        default=None,
        metavar="TEXT",
        help="run this question once before trial 1 (not scored)",
    )
    parser.add_argument(
        "--allow-manifest-errors",
        action="store_true",
        help=(
            "do not abort on a manifest error or dataset mismatch (the "
            "report is then marked as not a valid baseline)"
        ),
    )
    parser.add_argument(
        "--report-name",
        type=_report_name,
        default=None,
        metavar="STEM",
        help=(
            "write only reports/STEM.md and STEM.json (no latest.* or "
            "report-*)"
        ),
    )
    parser.add_argument(
        "--trials",
        type=_positive_int,
        default=1,
        metavar="N",
        help="run every selected case N times (default 1)",
    )
    parser.add_argument(
        "--notes",
        default="",
        metavar="TEXT",
        help="free text stored in the manifest (e.g. the warm-up procedure)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print only the summary line",
    )
    return parser


def write_reports(
    markdown: str,
    payload: dict[str, Any],
    *,
    reports_dir: Path,
    timestamp: str,
    report_name: str | None = None,
) -> list[Path]:
    """Write the report files and return the paths written."""

    reports_dir.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, indent=2, default=str)

    if report_name:
        targets = [
            (reports_dir / f"{report_name}.md", markdown),
            (reports_dir / f"{report_name}.json", body),
        ]
    else:
        stamp = timestamp.replace(":", "-").replace(".", "-")
        targets = [
            (reports_dir / "latest.md", markdown),
            (reports_dir / "latest.json", body),
            (reports_dir / f"report-{stamp}.md", markdown),
            (reports_dir / f"report-{stamp}.json", body),
        ]

    for path, text in targets:
        path.write_text(text)

    return [path for path, _ in targets]


def main(
    argv: Sequence[str] | None = None,
    *,
    reports_dir: Path | None = None,
) -> int:
    args = build_parser().parse_args(argv)

    if reports_dir is None:
        reports_dir = REPORTS_DIR

    if args.report_name and not args.no_write and not args.guardrails_only:
        existing = [
            path
            for path in (
                reports_dir / f"{args.report_name}.md",
                reports_dir / f"{args.report_name}.json",
            )
            if path.exists()
        ]
        if existing and not args.overwrite:
            print(
                f"Refusing to overwrite {[str(p) for p in existing]}; "
                "pass --overwrite to replace.",
                file=sys.stderr,
            )
            return EXIT_ERROR

    from src import config

    if args.run_log:
        # Call-time seam: the same attributes the test conftest redirects.
        run_log = Path(args.run_log).resolve()
        config.RUN_LOG_FILE = str(run_log)
        config.LOG_DIRECTORY = str(run_log.parent)
        config.APPLICATION_LOG_FILE = str(
            run_log.parent / "evaluation_run.log"
        )

    from src.bigquery_service import BigQueryService
    from src.observability import utc_timestamp
    from src.sql_execution_pipeline import SQLExecutionPipeline

    timestamp = utc_timestamp()
    bigquery_service = BigQueryService()
    execution_pipeline = SQLExecutionPipeline(
        bigquery_service=bigquery_service
    )

    attack_summary = run_guardrail_suite(
        execution_pipeline,
        load_attack_cases(project_id=config.PROJECT_ID),
    )

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

    cases = load_golden_cases(project_id=config.PROJECT_ID)

    if args.case_ids:
        wanted = set(args.case_ids)
        cases = [case for case in cases if case.case_id in wanted]
        if not cases:
            print(
                f"No cases matched {sorted(wanted)}", file=sys.stderr
            )
            return EXIT_ERROR

    print(f"Running {len(cases)} golden case(s) against {model_name}...")

    model_identity: dict[str, Any] = {}
    manifest: dict[str, Any] = {}
    try:
        model_identity = ollama_model_identity(
            config.OLLAMA_MODEL,
            config.OLLAMA_HOST,
            config.OLLAMA_TIMEOUT_SECONDS,
        )
        manifest = build_manifest(
            repo_root=REPO_ROOT,
            bigquery_service=bigquery_service,
            execution_pipeline=execution_pipeline,
            csv_dir=DATASETS_DIR,
            case_paths=[ATTACK_CASES_PATH, GOLDEN_CASES_PATH],
            model_identity=model_identity,
            trials=args.trials,
            notes=args.notes,
            extra_files=(
                [EXPECTED_FACTS_PATH] if EXPECTED_FACTS_PATH.exists() else []
            ),
        )
    except Exception as error:  # noqa: BLE001 - never crash the evaluation
        manifest = {
            "error": f"{type(error).__name__}: {error}",
            "model": model_identity,
        }
        print(f"Manifest unavailable: {manifest['error']}", file=sys.stderr)

    warm_up: dict[str, Any] | None = None
    warm_up_unavailable = False

    if args.warm_up_question:
        print("Warm-up run (not scored)...")
        started = perf_counter()
        warm_record = agent.run(args.warm_up_question)
        duration_ms = round((perf_counter() - started) * 1000, 2)
        warm_outcome = warm_record.get("outcome") or {}
        warm_up = {
            "question": args.warm_up_question,
            "status": warm_outcome.get("status"),
            "terminal_stage": warm_outcome.get("terminal_stage"),
            "duration_ms": duration_ms,
            "graph_run_id": warm_record.get("graph_run_id"),
            "llm_call_count": len(
                (warm_record.get("tokens") or {}).get("llm_calls") or []
            ),
        }
        warm_up_unavailable = _model_unavailable(warm_outcome)

    problems = manifest_problems(manifest)

    if warm_up_unavailable:
        print(
            "BASELINE ABORTED: model unavailable during warm-up "
            f"({warm_up})",
            file=sys.stderr,
        )
        return EXIT_ERROR

    invalid_baseline = bool(problems)
    if problems:
        if not args.allow_manifest_errors:
            print(
                f"BASELINE ABORTED: {'; '.join(problems)}", file=sys.stderr
            )
            return EXIT_ERROR
        print(
            f"WARNING: {'; '.join(problems)}; continuing because "
            "--allow-manifest-errors was given. Not a valid baseline.",
            file=sys.stderr,
        )

    loaded_before_trial: dict[str, Any] = {}

    results = []
    for trial in range(1, args.trials + 1):
        try:
            loaded_before_trial[str(trial)] = ollama_model_identity(
                config.OLLAMA_MODEL,
                config.OLLAMA_HOST,
                config.OLLAMA_TIMEOUT_SECONDS,
            ).get("loaded_models", [])
        except Exception as error:  # noqa: BLE001
            loaded_before_trial[str(trial)] = {"error": str(error)}

        prefix = f"[trial {trial}/{args.trials}] " if args.trials > 1 else ""

        for index, case in enumerate(cases, start=1):
            result = evaluate_case(
                case,
                agent=agent,
                execution_pipeline=execution_pipeline,
            )
            result.trial = trial
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
                    f"  {prefix}[{index}/{len(cases)}] {mark} "
                    f"{result.case.case_id}"
                    f"  {result.duration_ms / 1000:.1f}s{suffix}"
                )

    if not isinstance(manifest.get("trials"), dict):
        manifest["trials"] = {"count": args.trials, "notes": args.notes}
    manifest["trials"]["loaded_before_trial"] = loaded_before_trial
    manifest["trials"]["run_log_file"] = str(
        Path(config.RUN_LOG_FILE).resolve()
    )
    manifest["trials"]["warm_up"] = warm_up

    markdown = render_markdown(
        results,
        attack_summary=attack_summary,
        model_name=model_name,
        timestamp=timestamp,
        manifest=manifest,
        warning=(
            "WARNING: manifest incomplete or dataset mismatch; not a "
            f"valid baseline ({'; '.join(problems)})"
            if invalid_baseline
            else ""
        ),
    )
    payload = build_json_report(
        results,
        attack_summary=attack_summary,
        model_name=model_name,
        timestamp=timestamp,
        manifest=manifest,
    )

    payload["summary"]["invalid_baseline"] = invalid_baseline
    payload["summary"]["invalid_baseline_reasons"] = list(problems)

    markdown = redact_project_id(markdown, config.PROJECT_ID)
    payload = redact_project_id(payload, config.PROJECT_ID)

    if not args.no_write:
        written = write_reports(
            markdown,
            payload,
            reports_dir=reports_dir,
            timestamp=timestamp,
            report_name=args.report_name,
        )
        print(f"\nReport written to {written[0]}")

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
