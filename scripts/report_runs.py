"""Summarise the run log.

    uv run python scripts/report_runs.py
    uv run python scripts/report_runs.py --last 20 --failures

The point of writing structured records is being able to ask questions of
them afterwards. This answers the obvious ones: how often does it work, where
does the time go, what does a question cost, and what is failing.
"""

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_records(path: Path) -> list[dict[str, Any]]:
    """Read the JSONL run log, skipping any malformed line."""

    if not path.exists():
        return []

    records = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    return records


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(
        len(ordered) - 1,
        max(0, int(round(fraction * (len(ordered) - 1)))),
    )
    return ordered[index]


def summarise_graph_runs(records: list[dict[str, Any]]) -> str:
    runs = [r for r in records if r.get("event") == "graph_run"]

    if not runs:
        return "No graph runs recorded yet."

    lines: list[str] = []

    statuses = Counter(
        run.get("outcome", {}).get("status") for run in runs
    )
    total = len(runs)

    lines.append(f"RUNS  {total}")
    for status in ("success", "rejected", "error"):
        count = statuses.get(status, 0)
        if count:
            lines.append(
                f"  {status:<9} {count:>4}  "
                f"({100 * count / total:.0f}%)"
            )
    lines.append("")

    # Where failures happen
    failures = [
        run
        for run in runs
        if run.get("outcome", {}).get("status") != "success"
    ]
    if failures:
        stages = Counter(
            run.get("outcome", {}).get("terminal_stage")
            for run in failures
        )
        lines.append("FAILED AT")
        for stage, count in stages.most_common():
            lines.append(f"  {str(stage):<22} {count:>4}")
        lines.append("")

    # Repair behaviour
    repairs = [
        int(run.get("sql", {}).get("repair_attempts", 0) or 0)
        for run in runs
    ]
    needed_repair = sum(1 for value in repairs if value)
    lines.append("REPAIRS")
    lines.append(
        f"  runs needing repair    {needed_repair:>4}  "
        f"({100 * needed_repair / total:.0f}%)"
    )
    lines.append(f"  total repair attempts  {sum(repairs):>4}")
    lines.append("")

    # Latency
    durations = [
        float(run.get("runtime", {}).get("total_duration_ms") or 0)
        for run in runs
    ]
    durations = [value for value in durations if value > 0]
    if durations:
        lines.append("LATENCY (s)")
        lines.append(
            f"  mean {statistics.fmean(durations) / 1000:>6.1f}   "
            f"p50 {_percentile(durations, 0.5) / 1000:>6.1f}   "
            f"p95 {_percentile(durations, 0.95) / 1000:>6.1f}"
        )
        lines.append("")

    # Per-node timing, which is where the time actually goes
    node_times: dict[str, list[float]] = defaultdict(list)
    for run in runs:
        for entry in run.get("runtime", {}).get("node_trace", []) or []:
            node_times[entry.get("node", "?")].append(
                float(entry.get("duration_ms") or 0)
            )

    if node_times:
        lines.append("TIME BY NODE (s, mean)")
        for node, values in sorted(
            node_times.items(),
            key=lambda item: statistics.fmean(item[1]),
            reverse=True,
        ):
            lines.append(
                f"  {node:<18} {statistics.fmean(values) / 1000:>6.1f}   "
                f"n={len(values)}"
            )
        lines.append("")

    # Cost
    tokens = [
        int(
            run.get("tokens", {}).get("totals", {}).get("total_tokens", 0)
            or 0
        )
        for run in runs
    ]
    tokens = [value for value in tokens if value]
    billed = [
        int(run.get("cost", {}).get("total_bytes_billed") or 0)
        for run in runs
    ]

    lines.append("COST")
    if tokens:
        lines.append(
            f"  tokens per run   mean {statistics.fmean(tokens):>8.0f}   "
            f"total {sum(tokens):>9,}"
        )
    lines.append(f"  bytes billed     total {sum(billed):>9,}")

    cache_hits = sum(
        1 for run in runs if run.get("cost", {}).get("cache_hit")
    )
    lines.append(
        f"  cache hits       {cache_hits:>4} of {total}"
    )
    lines.append("")

    # Grounding, once Phase 8 records it
    grounded = [
        run.get("analysis", {}).get("is_grounded")
        for run in runs
        if run.get("analysis", {}).get("is_grounded") is not None
    ]
    if grounded:
        clean = sum(1 for value in grounded if value)
        lines.append("ANALYSIS GROUNDING")
        lines.append(
            f"  analyses with every figure derivable  {clean}/"
            f"{len(grounded)}"
        )
        lines.append("")

    return "\n".join(lines)


def summarise_executions(records: list[dict[str, Any]]) -> str:
    executions = [
        r for r in records if r.get("event") == "sql_execution"
    ]

    if not executions:
        return ""

    lines = [f"SQL EXECUTIONS  {len(executions)}"]

    statuses = Counter(item.get("status") for item in executions)
    for status, count in statuses.most_common():
        lines.append(f"  {str(status):<9} {count:>4}")

    rejections = [
        item for item in executions if item.get("status") == "rejected"
    ]
    if rejections:
        stages = Counter(item.get("stage") for item in rejections)
        lines.append("  refused at:")
        for stage, count in stages.most_common():
            lines.append(f"    {str(stage):<20} {count:>4}")

    return "\n".join(lines)


def list_failures(records: list[dict[str, Any]], limit: int) -> str:
    runs = [
        run
        for run in records
        if run.get("event") == "graph_run"
        and run.get("outcome", {}).get("status") != "success"
    ]

    if not runs:
        return "No failed runs."

    lines = ["RECENT FAILURES", ""]

    for run in runs[-limit:]:
        outcome = run.get("outcome", {})
        lines.append(f"  {run.get('timestamp_utc', '')}")
        lines.append(f"    question : {run.get('question')}")
        lines.append(
            f"    outcome  : {outcome.get('status')} at "
            f"{outcome.get('terminal_stage')}"
        )
        message = outcome.get("error_message")
        if message:
            lines.append(f"    message  : {str(message)[:120]}")
        lines.append("")

    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="report_runs",
        description="Summarise logs/runs.jsonl.",
    )
    parser.add_argument(
        "--log",
        default=None,
        help="path to the JSONL run log (default: the configured one)",
    )
    parser.add_argument(
        "--last",
        type=int,
        default=None,
        help="only consider the most recent N graph runs",
    )
    parser.add_argument(
        "--failures",
        action="store_true",
        help="list recent failed runs",
    )

    args = parser.parse_args(argv)

    from src import config

    path = Path(args.log or config.RUN_LOG_FILE)
    records = load_records(path)

    if not records:
        print(f"No records found in {path}")
        return 1

    if args.last:
        graph_runs = [
            r for r in records if r.get("event") == "graph_run"
        ][-args.last :]
        keep_ids = {run.get("graph_run_id") for run in graph_runs}
        records = graph_runs + [
            r
            for r in records
            if r.get("event") == "sql_execution"
            and r.get("run_id")
            in {
                run_id
                for run in graph_runs
                for run_id in run.get("runtime", {}).get(
                    "sql_execution_run_ids", []
                )
            }
        ]
        del keep_ids

    print(f"Run log: {path}")
    print()
    print(summarise_graph_runs(records))

    executions = summarise_executions(records)
    if executions:
        print(executions)
        print()

    if args.failures:
        print(list_failures(records, limit=10))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
