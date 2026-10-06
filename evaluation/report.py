"""Turn evaluation results into a report a human can act on.

A pass rate alone tells you nothing about what to fix, so the report ends
with a failure appendix showing the agent's SQL against the reference and the
first differing row.
"""

import re
import statistics
from typing import Any

from evaluation.cases import PROJECT_PLACEHOLDER
from evaluation.harness import (
    SUPPLEMENTARY_SQL_ACCURACY,
    CaseResult,
    classify_failure,
)

METRIC_ORDER = [
    "guardrail_pass",
    "executed",
    "table_grounding",
    "execution_accuracy",
    "repair_efficiency",
    "analysis_grounding",
]

METRIC_MEANING = {
    "guardrail_pass": "not refused by its own safety layer",
    "executed": "produced a result",
    "table_grounding": "used the expected tables",
    "execution_accuracy": "result set matches the reference query",
    "repair_efficiency": "stayed within the repair budget",
    "analysis_grounding": "every figure in the prose is derivable",
}


def summarise_metrics(
    results: list[CaseResult],
) -> dict[str, dict[str, int]]:
    """Count passes per metric across every case that ran it."""

    summary: dict[str, dict[str, int]] = {}

    for result in results:
        for score in result.scores.scores:
            entry = summary.setdefault(
                score.name, {"passed": 0, "total": 0}
            )
            entry["total"] += 1
            entry["passed"] += 1 if score.passed else 0

    return summary


def _percent(passed: int, total: int) -> str:
    if not total:
        return "n/a"
    return f"{100 * passed / total:.0f}%"


def _percentile(values: list[float], fraction: float) -> float:
    # p50/p95 are index-based, not nearest-rank and not interpolated: the
    # index is round(fraction * (n - 1)) using Python's round-half-to-even,
    # clamped to [0, n - 1]. The reported
    # "median" is statistics.median (which does average the middle pair), so
    # p50 and median can differ for even n.
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(
        len(ordered) - 1,
        max(0, int(round(fraction * (len(ordered) - 1)))),
    )
    return ordered[index]


def _lat(values: list[float]) -> dict[str, float]:
    return {
        "mean": round(statistics.fmean(values), 1) if values else 0.0,
        "median": round(statistics.median(values), 1) if values else 0.0,
        "p95": round(_percentile(values, 0.95), 1),
    }


def _node_latency(results: list[CaseResult]) -> dict[str, dict[str, float]]:
    by_node: dict[str, list[float]] = {}
    for result in results:
        trace = (result.record.get("runtime") or {}).get("node_trace") or []
        for entry in trace:
            if not isinstance(entry, dict) or "node" not in entry:
                continue
            by_node.setdefault(str(entry["node"]), []).append(
                float(entry.get("duration_ms") or 0.0)
            )
    return {
        node: {
            "count": len(values),
            "mean_ms": round(statistics.fmean(values), 1),
            "p95_ms": round(_percentile(values, 0.95), 1),
        }
        for node, values in by_node.items()
    }


def _llm_latency(results: list[CaseResult]) -> dict[str, dict[str, float]]:
    times: dict[str, list[float]] = {}
    toks: dict[str, list[float]] = {}
    for result in results:
        calls = (result.record.get("tokens") or {}).get("llm_calls") or []
        for call in calls:
            if not isinstance(call, dict):
                continue
            purpose = str(call.get("purpose") or "unknown")
            times.setdefault(purpose, []).append(
                float(call.get("response_time_ms") or 0.0)
            )
            toks.setdefault(purpose, []).append(
                float(call.get("total_tokens") or 0)
            )
    return {
        purpose: {
            "count": len(values),
            "mean_ms": round(statistics.fmean(values), 1),
            "p95_ms": round(_percentile(values, 0.95), 1),
            "mean_tokens": round(statistics.fmean(toks[purpose]), 1),
        }
        for purpose, values in times.items()
    }


def _outcomes(results: list[CaseResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        status = str(
            (result.record.get("outcome") or {}).get("status") or "unknown"
        )
        counts[status] = counts.get(status, 0) + 1
    return counts


def _coverage(results: list[CaseResult]) -> dict[str, Any]:
    """Supplementary SQL-accuracy figures, independent of analysis outcome."""

    executed = [r for r in results if r.execution_succeeded]
    accurate = 0
    for result in executed:
        for score in result.supplementary:
            if score.name == SUPPLEMENTARY_SQL_ACCURACY and score.passed:
                accurate += 1
    return {
        "execution_coverage": {
            "executed": len(executed),
            "attempted": len(results),
        },
        SUPPLEMENTARY_SQL_ACCURACY: {
            "passed": accurate,
            "total": len(executed),
        },
    }


def _headline(results: list[CaseResult]) -> dict[str, Any]:
    durations = [result.duration_ms for result in results]
    tokens = [result.total_tokens for result in results]
    passed = [result for result in results if result.passed]
    return {
        "cases": len(results),
        "passed": len(passed),
        "pass_rate": (len(passed) / len(results)) if results else 0.0,
        "metrics": summarise_metrics(results),
        **_coverage(results),
        "latency_ms": _lat(durations),
        "tokens": {
            "mean": round(statistics.fmean(tokens), 1) if tokens else 0.0,
            "total": sum(tokens),
        },
    }


def _failure_class(result: CaseResult) -> str:
    return result.failure_class or classify_failure(result.record)


def _failures(results: list[CaseResult]) -> dict[str, int]:
    classes = [_failure_class(result) for result in results]
    return {
        name: classes.count(name)
        for name in ("rejected", "error", "unavailable")
    }


def _warehouse(results: list[CaseResult]) -> dict[str, int]:
    """Final execution per run only; repairs and retries are not summed."""

    billed = 0
    processed = 0
    hits = 0
    executions = 0
    for result in results:
        record = result.record
        cost = record.get("cost") or {}
        billed += int(cost.get("total_bytes_billed") or 0)
        processed += int(cost.get("total_bytes_processed") or 0)
        executions += len(
            (record.get("runtime") or {}).get("sql_execution_run_ids") or []
        )
        if cost.get("cache_hit"):
            hits += 1
    return {
        "final_execution_bytes_billed": billed,
        "final_execution_bytes_processed": processed,
        "final_executions_cache_hits": hits,
        "sql_executions": executions,
    }


def build_summary(results: list[CaseResult]) -> dict[str, Any]:
    """Compute the headline numbers as data, for JSON as well as Markdown."""

    durations = [result.duration_ms for result in results]
    tokens = [result.total_tokens for result in results]

    passed = [result for result in results if result.passed]

    latency = _lat(durations)

    trials = sorted({result.trial for result in results})
    by_trial = []
    for trial in trials:
        entry = _headline([r for r in results if r.trial == trial])
        by_trial.append({"trial": trial, **entry})

    return {
        "cases": len(results),
        "passed": len(passed),
        "pass_rate": (len(passed) / len(results)) if results else 0.0,
        "metrics": summarise_metrics(results),
        **_coverage(results),
        "warehouse": _warehouse(results),
        "failures": _failures(results),
        "latency_ms": {
            "mean": latency["mean"],
            "median": latency["median"],
            "p50": round(_percentile(durations, 0.5), 1),
            "p95": latency["p95"],
        },
        "node_latency_ms": _node_latency(results),
        "llm_latency_ms": _llm_latency(results),
        "outcomes": _outcomes(results),
        "by_trial": by_trial,
        "tokens": {
            "mean": round(statistics.fmean(tokens), 1) if tokens else 0.0,
            "total": sum(tokens),
        },
        "repairs": {
            "total": sum(result.repair_attempts for result in results),
            "cases_needing_repair": sum(
                1 for result in results if result.repair_attempts
            ),
        },
    }


def _cell(value: Any) -> str:
    if value is None or value == "":
        return "n/a"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _render_provenance(manifest: dict[str, Any]) -> list[str]:
    lines = ["## Provenance", ""]

    if "error" in manifest:
        lines.append(f"- Manifest unavailable: {_cell(manifest['error'])}")
        lines.append("")

    git = manifest.get("git") or {}
    if "error" in git:
        lines.append(f"- Git: unavailable ({_cell(git['error'])})")
    else:
        lines.append(f"- Git head: `{_cell(git.get('head'))}`")
        lines.append(f"- Branch: `{_cell(git.get('branch'))}`")
        lines.append(f"- Dirty files: {len(git.get('dirty_files') or [])}")
        lines.append(
            f"- Uncommitted diff sha256: `{_cell(git.get('diff_sha256'))}`"
        )
    lines.append("")

    model = manifest.get("model") or {}
    config = manifest.get("config") or {}
    lines.append("### Model")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    for label, value in [
        ("Tag", model.get("tag")),
        ("Digest", model.get("digest")),
        ("Family", model.get("family")),
        ("Parameter size", model.get("parameter_size")),
        ("Quantisation", model.get("quantization_level")),
        ("Ollama server", model.get("server_version")),
        ("Temperature", config.get("OLLAMA_TEMPERATURE")),
    ]:
        lines.append(f"| {label} | {_cell(value)} |")
    if model.get("error"):
        lines.append(f"| Error | {_cell(model['error'])} |")
    lines.append("")

    lines.append("### Caps")
    lines.append("")
    lines.append("| Setting | Value |")
    lines.append("|---|---|")
    for key in (
        "MAX_QUERY_BYTES",
        "MAX_RESULT_ROWS",
        "QUERY_TIMEOUT_SECONDS",
        "MAX_SQL_REPAIR_ATTEMPTS",
        "MAX_ANALYSIS_ROWS",
        "SCHEMA_CACHE_TTL_SECONDS",
        "OLLAMA_TIMEOUT_SECONDS",
    ):
        lines.append(f"| `{key}` | {_cell(config.get(key))} |")
    lines.append("")

    dataset = manifest.get("dataset") or {}
    lines.append("### Dataset reconciliation")
    lines.append("")
    lines.append("| Table | Figure | CSV | BigQuery | Match |")
    lines.append("|---|---|---|---|---|")
    reconciliation = dataset.get("reconciliation")
    all_rows = reconciliation if isinstance(reconciliation, list) else []
    column_rows = [
        row for row in all_rows if _is_column_row(row)
    ]
    for row in [r for r in all_rows if not _is_column_row(r)]:
        lines.append(
            f"| `{_cell(row.get('table'))}` | {_cell(row.get('figure'))} | "
            f"{_cell(row.get('csv'))} | {_cell(row.get('bigquery'))} | "
            f"{'yes' if row.get('match') else 'no'} |"
        )
    lines.append("")
    lines.extend(_render_column_reconciliation(column_rows))
    lines.append(
        "All reconciliation figures match: "
        f"{'yes' if dataset.get('all_match') else 'no'}"
    )
    lines.append("")
    if dataset.get("all_match") is False:
        lines.append(
            "**WARNING: the CSV and BigQuery figures do not all match; "
            "the dataset may have drifted.**"
        )
        lines.append("")

    csv_hashes = dataset.get("csv") or {}
    if "error" in csv_hashes:
        lines.append(f"- CSV hashes unavailable: {_cell(csv_hashes['error'])}")
        lines.append("")
    elif csv_hashes:
        lines.append("### CSV hashes")
        lines.append("")
        lines.append("| File | Rows | sha256 |")
        lines.append("|---|---|---|")
        for name, entry in csv_hashes.items():
            lines.append(
                f"| `{name}` | {_cell(entry.get('rows'))} | "
                f"`{_cell(entry.get('sha256'))}` |"
            )
        lines.append("")

    lines.extend(_render_trials_provenance(manifest.get("trials")))

    return lines


def _render_trials_provenance(trials: Any) -> list[str]:
    if not isinstance(trials, dict):
        return []

    lines: list[str] = []

    warm = trials.get("warm_up")
    if isinstance(warm, dict):
        duration = warm.get("duration_ms")
        shown = (
            f"{float(duration) / 1000:.1f}s"
            if isinstance(duration, (int, float))
            else "n/a"
        )
        lines.append(
            f"- Warm-up: question `{_cell(warm.get('question'))}` · "
            f"status {_cell(warm.get('status'))} · {shown}"
        )
    elif "warm_up" in trials:
        lines.append("- Warm-up: none")

    if trials.get("run_log_file"):
        lines.append(f"- Run log: `{_cell(trials.get('run_log_file'))}`")

    loaded = trials.get("loaded_before_trial")
    if isinstance(loaded, dict) and loaded:
        lines.append("")
        lines.append("### Models loaded before each trial")
        lines.append("")
        lines.append("| Trial | Model tag | Loaded |")
        lines.append("|---|---|---|")
        for trial, models in loaded.items():
            if isinstance(models, list) and models:
                for item in models:
                    tag = (
                        item.get("model") if isinstance(item, dict) else item
                    )
                    lines.append(f"| {trial} | `{_cell(tag)}` | yes |")
            elif isinstance(models, list):
                lines.append(f"| {trial} | n/a | no |")
            else:
                lines.append(
                    f"| {trial} | unknown ({_cell(models)}) | unknown |"
                )

    if lines:
        lines.append("")
    return lines


def _is_column_row(row: Any) -> bool:
    figure = str(row.get("figure") or "") if isinstance(row, dict) else ""
    return figure.startswith(("distinct:", "sha256:"))


def _render_column_reconciliation(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return []

    grouped: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for row in rows:
        kind, _, column = str(row.get("figure")).partition(":")
        grouped.setdefault((str(row.get("table")), column), {})[kind] = row

    lines = [
        "### Categorical column reconciliation",
        "",
        "| Table | Column | Distinct (CSV/BQ) | Values match |",
        "|---|---|---|---|",
    ]
    mismatching: list[str] = []
    for (table, column), pair in grouped.items():
        distinct = pair.get("distinct") or {}
        ok = all(entry.get("match") for entry in pair.values())
        if not ok:
            mismatching.append(f"{table}.{column}")
        lines.append(
            f"| `{table}` | `{column}` | "
            f"{_cell(distinct.get('csv'))}/{_cell(distinct.get('bigquery'))}"
            f" | {'yes' if ok else 'no'} |"
        )
    lines.append("")
    if mismatching:
        lines.append(f"**Mismatching columns:** {', '.join(mismatching)}")
        lines.append("")
    return lines


def _render_latency(summary: dict[str, Any]) -> list[str]:
    latency = summary["latency_ms"]
    lines = ["## Latency", ""]
    lines.append(
        f"Pooled: mean {latency['mean'] / 1000:.1f}s · "
        f"median {latency['median'] / 1000:.1f}s · "
        f"p95 {latency['p95'] / 1000:.1f}s"
    )
    lines.append("")

    lines.append("| Trial | Passed | Mean | Median | p95 | Tokens mean |")
    lines.append("|---|---|---|---|---|---|")
    for entry in summary["by_trial"]:
        lat = entry["latency_ms"]
        lines.append(
            f"| {entry['trial']} | {entry['passed']}/{entry['cases']} | "
            f"{lat['mean'] / 1000:.1f}s | {lat['median'] / 1000:.1f}s | "
            f"{lat['p95'] / 1000:.1f}s | {entry['tokens']['mean']:.0f} |"
        )
    lines.append("")

    if summary["node_latency_ms"]:
        lines.append("| Node | Calls | Mean | p95 |")
        lines.append("|---|---|---|---|")
        for node, entry in summary["node_latency_ms"].items():
            lines.append(
                f"| `{node}` | {entry['count']} | "
                f"{entry['mean_ms'] / 1000:.2f}s | "
                f"{entry['p95_ms'] / 1000:.2f}s |"
            )
        lines.append("")

    if summary["llm_latency_ms"]:
        lines.append("| LLM role | Calls | Mean | p95 | Mean tokens |")
        lines.append("|---|---|---|---|---|")
        for purpose, entry in summary["llm_latency_ms"].items():
            lines.append(
                f"| `{purpose}` | {entry['count']} | "
                f"{entry['mean_ms'] / 1000:.2f}s | "
                f"{entry['p95_ms'] / 1000:.2f}s | "
                f"{entry['mean_tokens']:.0f} |"
            )
        lines.append("")

    outcomes = ", ".join(
        f"{status}: {count}"
        for status, count in sorted(summary["outcomes"].items())
    )
    lines.append(f"Outcomes: {outcomes or 'none'}")
    lines.append("")

    failures = summary.get("failures") or {}
    lines.append(
        "Failures: "
        + ", ".join(
            f"{name}: {failures.get(name, 0)}"
            for name in ("rejected", "error", "unavailable")
        )
    )
    lines.append("")
    warehouse = summary.get("warehouse") or {}
    lines.append(
        "Warehouse (final execution per run): "
        f"billed {warehouse.get('final_execution_bytes_billed', 0)} B · "
        f"processed {warehouse.get('final_execution_bytes_processed', 0)} B"
        f" · cache hits {warehouse.get('final_executions_cache_hits', 0)}"
        f" · SQL executions (all pipeline runs, incl. repairs) "
        f"{warehouse.get('sql_executions', 0)}"
    )
    lines.append("")
    return lines


def render_markdown(
    results: list[CaseResult],
    *,
    attack_summary: dict[str, Any] | None = None,
    model_name: str = "unknown",
    timestamp: str = "",
    manifest: dict[str, Any] | None = None,
    warning: str = "",
) -> str:
    """Render the full report."""

    summary = build_summary(results)

    lines: list[str] = ["# Evaluation report", ""]

    if warning:
        lines.extend([warning, ""])

    lines.append(
        f"- Cases: **{summary['cases']}** · "
        f"Passed: **{summary['passed']}** "
        f"({_percent(summary['passed'], summary['cases'])})"
    )
    lines.append(f"- Model: `{model_name}`")
    if timestamp:
        lines.append(f"- Run: {timestamp}")
    lines.append(
        f"- Mean latency: {summary['latency_ms']['mean'] / 1000:.1f}s · "
        f"p95: {summary['latency_ms']['p95'] / 1000:.1f}s"
    )
    lines.append(
        f"- Mean tokens per question: "
        f"{summary['tokens']['mean']:.0f}"
    )
    lines.append(
        f"- Cases needing a repair: "
        f"{summary['repairs']['cases_needing_repair']} of "
        f"{summary['cases']}"
    )
    lines.append("")

    if manifest:
        lines.extend(_render_provenance(manifest))

    lines.extend(_render_latency(summary))

    # Per-metric
    lines.append("## By metric")
    lines.append("")
    lines.append(
        "Downstream metrics are scored only on successful runs; their "
        "denominators exclude failed/rejected runs. Overall pass rate uses "
        f"all {summary['cases']} case-runs."
    )
    lines.append("")
    lines.append("| Metric | Passed | Rate | What it proves |")
    lines.append("|---|---|---|---|")

    metrics = summary["metrics"]
    for name in METRIC_ORDER:
        if name not in metrics:
            continue
        entry = metrics[name]
        lines.append(
            f"| `{name}` | {entry['passed']}/{entry['total']} | "
            f"{_percent(entry['passed'], entry['total'])} | "
            f"{METRIC_MEANING.get(name, '')} |"
        )
    lines.append("")

    coverage = summary["execution_coverage"]
    any_outcome = summary[SUPPLEMENTARY_SQL_ACCURACY]
    lines.append("### Supplementary (not part of the pass rate)")
    lines.append("")
    lines.append("| Metric | Passed | Rate |")
    lines.append("|---|---|---|")
    lines.append(
        f"| `execution_coverage` | {coverage['executed']}/"
        f"{coverage['attempted']} | "
        f"{_percent(coverage['executed'], coverage['attempted'])} |"
    )
    lines.append(
        f"| `{SUPPLEMENTARY_SQL_ACCURACY}` | {any_outcome['passed']}/"
        f"{any_outcome['total']} | "
        f"{_percent(any_outcome['passed'], any_outcome['total'])} |"
    )
    lines.append("")
    lines.append(
        f"`{SUPPLEMENTARY_SQL_ACCURACY}` scores the final SQL on every run "
        "whose execution succeeded, whatever happened to the analysis."
    )
    lines.append("")

    # Per-case
    lines.append("## By case")
    lines.append("")
    header = "| Case | Trial | Category | " + " | ".join(
        name.replace("_", " ") for name in METRIC_ORDER
    )
    lines.append(header + " | Repairs | Tokens | Time |")
    lines.append("|---" * (len(METRIC_ORDER) + 6) + "|")

    for result in results:
        by_name = result.scores.by_name()
        cells = []
        for name in METRIC_ORDER:
            score = by_name.get(name)
            if score is None:
                cells.append("–")
            else:
                cells.append("✅" if score.passed else "❌")
        lines.append(
            f"| `{result.case.case_id}` | {result.trial} | "
            f"{result.case.category} | "
            + " | ".join(cells)
            + f" | {result.repair_attempts} | {result.total_tokens} | "
            f"{result.duration_ms / 1000:.1f}s |"
        )
    lines.append("")

    # Guardrails
    if attack_summary:
        lines.append("## Guardrails")
        lines.append("")
        lines.append(
            f"**{attack_summary['blocked']}/{attack_summary['total']}** "
            "adversarial queries refused, every one before a byte was "
            "scanned."
        )
        lines.append("")
        lines.append("| Refused at stage | Cases |")
        lines.append("|---|---|")
        for stage, count in sorted(
            attack_summary.get("by_stage", {}).items()
        ):
            lines.append(f"| `{stage}` | {count} |")
        lines.append("")

    # Failures
    failures = [result for result in results if not result.passed]

    if failures:
        lines.append("## Failures")
        lines.append("")
        for result in failures:
            lines.append(
                f"### `{result.case.case_id}` (trial {result.trial})"
            )
            lines.append("")
            lines.append(f"> {result.case.question}")
            lines.append("")

            for score in result.scores.scores:
                if not score.passed:
                    lines.append(
                        f"- **{score.name}** — {score.detail}"
                    )
            lines.append("")

            if result.generated_sql:
                lines.append("Agent SQL:")
                lines.append("")
                lines.append("```sql")
                lines.append(result.generated_sql.strip())
                lines.append("```")
                lines.append("")

            if result.case.reference_sql:
                lines.append("Reference SQL:")
                lines.append("")
                lines.append("```sql")
                lines.append(result.case.reference_sql.strip())
                lines.append("```")
                lines.append("")
    else:
        lines.append("## Failures")
        lines.append("")
        lines.append("None.")
        lines.append("")

    return "\n".join(lines)


def _case_detail(result: CaseResult) -> dict[str, Any]:
    """Per-case evidence, tolerant of crash records missing sections."""

    record = result.record
    outcome = record.get("outcome") or {}
    sql = record.get("sql") or {}
    runtime = record.get("runtime") or {}
    analysis = record.get("analysis") or {}

    trace = [
        entry
        for entry in runtime.get("node_trace") or []
        if isinstance(entry, dict)
    ]
    node_totals: dict[str, dict[str, Any]] = {}
    for entry in trace:
        slot = node_totals.setdefault(
            str(entry.get("node")), {"total_ms": 0.0, "count": 0}
        )
        slot["total_ms"] = round(
            slot["total_ms"] + float(entry.get("duration_ms") or 0.0), 2
        )
        slot["count"] += 1

    cost = record.get("cost") or {}

    return {
        "failure_class": _failure_class(result),
        "execution_succeeded": result.execution_succeeded,
        "supplementary": {
            score.name: {"passed": score.passed, "detail": score.detail}
            for score in result.supplementary
        },
        "total_bytes_processed": cost.get("total_bytes_processed"),
        "total_bytes_billed": cost.get("total_bytes_billed"),
        "sql_execution_count": len(
            runtime.get("sql_execution_run_ids") or []
        ),
        "llm_call_count": len(
            [
                call
                for call in (record.get("tokens") or {}).get("llm_calls")
                or []
                if isinstance(call, dict)
            ]
        ),
        "outcome": {
            "status": outcome.get("status"),
            "terminal_stage": outcome.get("terminal_stage"),
            "error_stage": outcome.get("error_stage"),
            "error_message": outcome.get("error_message"),
        },
        "executed_sql": sql.get("executed_sql"),
        "referenced_tables": sql.get("referenced_tables") or [],
        "node_trace": [
            {
                "node": entry.get("node"),
                "duration_ms": entry.get("duration_ms"),
                "ok": entry.get("ok"),
            }
            for entry in trace
        ],
        "node_timings_ms": node_totals,
        "llm_calls": [
            {
                "purpose": call.get("purpose"),
                "response_time_ms": call.get("response_time_ms"),
                "input_tokens": call.get("input_tokens"),
                "output_tokens": call.get("output_tokens"),
                "total_tokens": call.get("total_tokens"),
            }
            for call in (record.get("tokens") or {}).get("llm_calls") or []
            if isinstance(call, dict)
        ],
        "ungrounded_numbers": analysis.get("ungrounded_numbers") or [],
        "contract_violations": analysis.get("contract_violations") or [],
        "is_grounded": analysis.get("is_grounded"),
        "was_generated_deterministically": analysis.get(
            "was_generated_deterministically"
        ),
        "row_count": (record.get("data") or {}).get("row_count"),
        "cache_hit": (record.get("cost") or {}).get("cache_hit"),
        "reference_error": result.reference_error,
    }


def build_json_report(
    results: list[CaseResult],
    *,
    attack_summary: dict[str, Any] | None = None,
    model_name: str = "unknown",
    timestamp: str = "",
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The same report as data, for tracking across runs."""

    return {
        "timestamp_utc": timestamp,
        "model_name": model_name,
        "manifest": manifest or {},
        "summary": build_summary(results),
        "guardrails": attack_summary or {},
        "cases": [
            {
                "case_id": result.case.case_id,
                "trial": result.trial,
                "question": result.case.question,
                "category": result.case.category,
                "difficulty": result.case.difficulty,
                "passed": result.passed,
                "scores": {
                    score.name: {
                        "passed": score.passed,
                        "detail": score.detail,
                    }
                    for score in result.scores.scores
                },
                "generated_sql": result.generated_sql,
                "repair_attempts": result.repair_attempts,
                "total_tokens": result.total_tokens,
                "duration_ms": result.duration_ms,
                "graph_run_id": result.record.get("graph_run_id"),
                **_case_detail(result),
            }
            for result in results
        ],
    }


def redact_project_id(
    value: Any,
    project_id: str,
    placeholder: str = PROJECT_PLACEHOLDER,
) -> Any:
    """Replace the real project id with the placeholder, recursively.

    Values only: dict keys are never rewritten. The id is matched only when
    not embedded in a longer identifier.
    """

    if not project_id or project_id == placeholder:
        return value

    pattern = re.compile(
        r"(?<![A-Za-z0-9_-])"
        + re.escape(project_id)
        + r"(?![A-Za-z0-9_-])"
    )

    def walk(item: Any) -> Any:
        if isinstance(item, str):
            return pattern.sub(placeholder, item)
        if isinstance(item, list):
            return [walk(v) for v in item]
        if isinstance(item, tuple):
            return tuple(walk(v) for v in item)
        if isinstance(item, dict):
            return {k: walk(v) for k, v in item.items()}
        return item

    return walk(value)
