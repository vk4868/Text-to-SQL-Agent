"""Render a run record for a terminal.

Kept separate from the agent so that formatting decisions never leak into the
graph, and so the CLI, the demo script and any future UI share one renderer.
"""

from typing import Any

RULE = "─" * 72


def _format_cell(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def format_rows(
    rows: list[dict[str, Any]],
    *,
    max_rows: int = 10,
) -> str:
    """Render result rows as a plain aligned table."""

    if not rows:
        return "(no rows returned)"

    columns = list(rows[0].keys())
    shown = rows[:max_rows]

    cells = [
        [_format_cell(row.get(column)) for column in columns]
        for row in shown
    ]

    widths = [
        max(
            len(column),
            *(len(row[index]) for row in cells),
        )
        for index, column in enumerate(columns)
    ]

    lines = [
        "  ".join(
            column.ljust(widths[index])
            for index, column in enumerate(columns)
        ),
        "  ".join("-" * width for width in widths),
    ]

    for row in cells:
        lines.append(
            "  ".join(
                value.ljust(widths[index])
                for index, value in enumerate(row)
            )
        )

    if len(rows) > max_rows:
        lines.append(f"... {len(rows) - max_rows} more row(s)")

    return "\n".join(lines)


def format_trace(record: dict[str, Any]) -> str:
    """Render the per-node timings as one line."""

    trace = record.get("runtime", {}).get("node_trace", [])

    if not trace:
        return "(no node trace)"

    return " · ".join(
        f"{entry['node']} {entry['duration_ms']:.0f}ms"
        + ("" if entry.get("ok", True) else " [failed]")
        for entry in trace
    )


def format_footer(record: dict[str, Any]) -> str:
    """Render the one-line cost and timing summary."""

    sql = record.get("sql", {})
    data = record.get("data", {})
    cost = record.get("cost", {})
    runtime = record.get("runtime", {})
    totals = record.get("tokens", {}).get("totals", {})

    parts = [
        f"repairs {sql.get('repair_attempts', 0)}",
        f"rows {data.get('row_count') or 0}",
        f"bytes {cost.get('total_bytes_billed') or 0:,}",
        f"tokens {totals.get('total_tokens') or 0:,}",
        f"{(runtime.get('total_duration_ms') or 0) / 1000:.1f}s",
    ]

    return " | ".join(parts)


def format_run_for_terminal(
    record: dict[str, Any],
    *,
    show_trace: bool = False,
    max_rows: int = 10,
) -> str:
    """Render a complete run for a human reader."""

    outcome = record.get("outcome", {})
    status = outcome.get("status")

    sections: list[str] = [
        RULE,
        f"QUESTION  {record.get('question')}",
        RULE,
    ]

    final_sql = record.get("sql", {}).get("final_sql")
    if final_sql:
        sections.append("SQL")
        sections.append(_indent(final_sql))
        sections.append("")

    if status == "success":
        sections.append("RESULT")
        sections.append(
            _indent(format_rows(record.get("rows", []), max_rows=max_rows))
        )
        sections.append("")

        analysis = record.get("analysis", {}).get("business_analysis")
        if analysis:
            sections.append(analysis.strip())
            sections.append("")
    else:
        sections.append(
            f"{str(status).upper()} at stage "
            f"'{outcome.get('terminal_stage')}'"
        )
        message = outcome.get("error_message")
        if message:
            sections.append(_indent(str(message)))
        sections.append("")

        repairs = record.get("sql", {}).get("repair_history", [])
        if repairs:
            sections.append(
                f"({len(repairs)} repair attempt(s) were made)"
            )
            sections.append("")

    if show_trace:
        sections.append("TRACE     " + format_trace(record))

    sections.append(RULE)
    sections.append(format_footer(record))

    return "\n".join(sections)


def _indent(text: str, prefix: str = "  ") -> str:
    return "\n".join(
        prefix + line for line in str(text).splitlines()
    )
