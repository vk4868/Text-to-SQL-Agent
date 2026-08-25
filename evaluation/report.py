"""Turn evaluation results into a report a human can act on.

A pass rate alone tells you nothing about what to fix, so the report ends
with a failure appendix showing the agent's SQL against the reference and the
first differing row.
"""

import statistics
from typing import Any

from evaluation.harness import CaseResult

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
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(
        len(ordered) - 1,
        max(0, int(round(fraction * (len(ordered) - 1)))),
    )
    return ordered[index]


def build_summary(results: list[CaseResult]) -> dict[str, Any]:
    """Compute the headline numbers as data, for JSON as well as Markdown."""

    durations = [result.duration_ms for result in results]
    tokens = [result.total_tokens for result in results]

    passed = [result for result in results if result.passed]

    return {
        "cases": len(results),
        "passed": len(passed),
        "pass_rate": (len(passed) / len(results)) if results else 0.0,
        "metrics": summarise_metrics(results),
        "latency_ms": {
            "mean": round(statistics.fmean(durations), 1)
            if durations
            else 0.0,
            "p50": round(_percentile(durations, 0.5), 1),
            "p95": round(_percentile(durations, 0.95), 1),
        },
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


def render_markdown(
    results: list[CaseResult],
    *,
    attack_summary: dict[str, Any] | None = None,
    model_name: str = "unknown",
    timestamp: str = "",
) -> str:
    """Render the full report."""

    summary = build_summary(results)

    lines: list[str] = ["# Evaluation report", ""]

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

    # Per-metric
    lines.append("## By metric")
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

    # Per-case
    lines.append("## By case")
    lines.append("")
    header = "| Case | Category | " + " | ".join(
        name.replace("_", " ") for name in METRIC_ORDER
    )
    lines.append(header + " | Repairs | Tokens | Time |")
    lines.append("|---" * (len(METRIC_ORDER) + 5) + "|")

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
            f"| `{result.case.case_id}` | {result.case.category} | "
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
            lines.append(f"### `{result.case.case_id}`")
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


def build_json_report(
    results: list[CaseResult],
    *,
    attack_summary: dict[str, Any] | None = None,
    model_name: str = "unknown",
    timestamp: str = "",
) -> dict[str, Any]:
    """The same report as data, for tracking across runs."""

    return {
        "timestamp_utc": timestamp,
        "model_name": model_name,
        "summary": build_summary(results),
        "guardrails": attack_summary or {},
        "cases": [
            {
                "case_id": result.case.case_id,
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
            }
            for result in results
        ],
    }
