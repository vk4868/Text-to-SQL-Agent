"""Extract the run-log evidence behind one evaluation report.

    uv run python -m evaluation.evidence --runs logs/baseline.jsonl \\
        --report evaluation/reports/baseline.json --out baseline_evidence.jsonl

The run log also holds attack queries, reference queries, manifest queries and
warm-up runs. This keeps only the ``graph_run`` records the report scored
(tagged with ``case_id`` and ``trial``) and the ``sql_execution`` records they
link to, redacts the project id, and strips anything that should never have
been logged (rows, raw model output, prompts).
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from evaluation.cases import PROJECT_PLACEHOLDER
from evaluation.report import redact_project_id

EVIDENCE_VERSION = 1

FORBIDDEN_KEYS = {"rows", "raw_model_output", "prompt"}


def _strip_forbidden(value: Any, counter: dict[str, int]) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if key in FORBIDDEN_KEYS:
                counter["stripped"] += 1
                continue
            cleaned[key] = _strip_forbidden(item, counter)
        return cleaned
    if isinstance(value, list):
        return [_strip_forbidden(item, counter) for item in value]
    return value


def extract_evidence(
    report: dict[str, Any],
    records: list[dict[str, Any]],
    project_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return (evidence records, summary) for ``report`` over ``records``."""

    graph_runs: dict[str, dict[str, Any]] = {}
    sql_runs: dict[str, list[dict[str, Any]]] = {}

    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("event") == "graph_run":
            graph_runs.setdefault(str(record.get("graph_run_id")), record)
        elif record.get("event") == "sql_execution":
            sql_runs.setdefault(str(record.get("run_id")), []).append(record)

    counter = {"stripped": 0}
    kept: list[dict[str, Any]] = []
    missing: list[str] = []
    graph_count = 0
    sql_count = 0
    seen_sql: set[str] = set()

    for case in report.get("cases") or []:
        run_id = case.get("graph_run_id")
        record = graph_runs.get(str(run_id)) if run_id else None
        if record is None:
            missing.append(str(run_id))
            continue

        tagged = {
            **record,
            "case_id": case.get("case_id"),
            "trial": case.get("trial"),
            "evidence_version": EVIDENCE_VERSION,
        }
        kept.append(_strip_forbidden(tagged, counter))
        graph_count += 1

        linked = (record.get("runtime") or {}).get(
            "sql_execution_run_ids"
        ) or []
        for sql_id in linked:
            if str(sql_id) in seen_sql:
                continue
            seen_sql.add(str(sql_id))
            for sql_record in sql_runs.get(str(sql_id), []):
                kept.append(_strip_forbidden(sql_record, counter))
                sql_count += 1

    kept = redact_project_id(kept, project_id)

    summary = {
        "graph_runs": graph_count,
        "sql_executions": sql_count,
        "missing_graph_run_ids": missing,
        "stripped_keys": counter["stripped"],
    }
    return kept, summary


def _read_jsonl(paths: Sequence[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        with Path(path).open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    loaded = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(loaded, dict):
                    records.append(loaded)
    return records


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evidence",
        description="Extract the run-log evidence behind a report.",
    )
    parser.add_argument(
        "--runs", action="append", required=True, metavar="PATH",
        help="run-log JSONL file (repeatable)",
    )
    parser.add_argument("--report", required=True, metavar="REPORT_JSON")
    parser.add_argument("--out", required=True, metavar="OUT_JSONL")
    parser.add_argument(
        "--project-id", default=None,
        help="project id to redact (default: src.config.PROJECT_ID)",
    )
    parser.add_argument(
        "--allow-placeholder",
        action="store_true",
        help=(
            "proceed when the project id is the placeholder (nothing can be "
            "redacted, so the output is NOT known to be free of the real id)"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.project_id is not None:
        project_id = args.project_id
    else:
        from src import config

        project_id = config.PROJECT_ID

    if project_id == PROJECT_PLACEHOLDER and not args.allow_placeholder:
        print(
            f"Refusing to run: the project id is the placeholder "
            f"({PROJECT_PLACEHOLDER!r}), so nothing could be redacted. Set "
            "GCP_PROJECT_ID or pass --project-id, or pass "
            "--allow-placeholder to proceed anyway.",
            file=sys.stderr,
        )
        return 2

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    records = _read_jsonl(args.runs)

    kept, summary = extract_evidence(report, records, project_id)

    serialised = [
        json.dumps(record, ensure_ascii=False, default=str) + "\n"
        for record in kept
    ]
    if project_id != PROJECT_PLACEHOLDER and any(
        project_id in line for line in serialised
    ):
        print(
            "Refusing to write: the real project id is still present in "
            "the extracted evidence after redaction.",
            file=sys.stderr,
        )
        return 2

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        handle.writelines(serialised)

    print(
        f"graph_run kept: {summary['graph_runs']}\n"
        f"sql_execution kept: {summary['sql_executions']}\n"
        f"stripped keys: {summary['stripped_keys']}\n"
        f"missing graph_run_ids: {summary['missing_graph_run_ids']}",
        file=sys.stderr,
    )

    return 1 if summary["missing_graph_run_ids"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
