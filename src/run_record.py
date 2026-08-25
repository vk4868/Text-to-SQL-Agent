"""Build the durable record of one graph run.

A pure function over the final graph state, which is what makes it testable
without running anything. It is the only place that decides what a run leaves
behind, and it is emitted from exactly one node so the record exists even when
the graph is invoked directly rather than through the agent.

Deliberately excluded: raw prompts, raw model output, and the result rows.
Prompts and completions dominate the record size, and rows are the caller's
data — they belong in the return value, not in an append-only log on disk.
"""

from typing import Any

from src import config
from src.graph.state import AgentState
from src.observability import utc_timestamp

#: Bump when the shape below changes, so old lines stay interpretable.
RUN_RECORD_VERSION = 1

GRAPH_RUN_EVENT = "graph_run"


def build_governance_snapshot(
    *,
    model_name: str | None = None,
) -> dict[str, Any]:
    """Capture the settings a run was governed by.

    Recorded per run rather than assumed, so a record from a run with
    different caps is still interpretable months later.
    """

    return {
        "project_id": config.PROJECT_ID,
        "dataset_path": f"{config.PROJECT_ID}.{config.DATASET_ID}",
        "bigquery_location": config.BIGQUERY_LOCATION,
        "model_name": model_name,
        "config": {
            "max_query_bytes": config.MAX_QUERY_BYTES,
            "max_result_rows": config.MAX_RESULT_ROWS,
            "query_timeout_seconds": config.QUERY_TIMEOUT_SECONDS,
            "max_sql_repair_attempts": config.MAX_SQL_REPAIR_ATTEMPTS,
            "max_analysis_rows": config.MAX_ANALYSIS_ROWS,
            "ollama_temperature": config.OLLAMA_TEMPERATURE,
        },
    }


def summarize_llm_calls(
    llm_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    """Roll per-call token accounting up to run totals."""

    def total(field: str) -> int:
        return sum(
            int(call.get(field) or 0)
            for call in llm_calls
        )

    return {
        "llm_call_count": len(llm_calls),
        "input_tokens": total("input_tokens"),
        "output_tokens": total("output_tokens"),
        "total_tokens": total("total_tokens"),
        "llm_time_ms": round(
            sum(
                float(call.get("response_time_ms") or 0.0)
                for call in llm_calls
            ),
            2,
        ),
    }


def _redact_repair_history(
    repair_history: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep what a repair did, drop the model metadata duplicated elsewhere."""

    return [
        {
            "repair_attempt": entry.get("repair_attempt"),
            "failure_stage": entry.get("failure_stage"),
            "error_message": entry.get("error_message"),
            "failed_sql": entry.get("failed_sql"),
            "repaired_sql": entry.get("repaired_sql"),
        }
        for entry in repair_history
    ]


def build_crash_record(
    *,
    question: str,
    error: BaseException,
    graph_run_id: str | None = None,
    total_duration_ms: float | None = None,
) -> dict[str, Any]:
    """Return a record for a run that died before it could finish.

    A node only converts the failures it expects into routed state. Anything
    else propagates out of the graph, which means finalize_run never runs and
    the run would otherwise vanish — exactly the run most worth having a
    record of. This keeps the invariant "every run leaves exactly one record"
    true at the outermost boundary.
    """

    minimal: AgentState = {
        "question": question,
        "status": "error",
        "terminal_stage": "unhandled_exception",
        "error_stage": "unhandled_exception",
        "error_message": f"{type(error).__name__}: {error}",
    }

    if graph_run_id:
        minimal["graph_run_id"] = graph_run_id

    record = build_graph_run_record(
        minimal,
        total_duration_ms=total_duration_ms,
    )

    # Name the node it escaped from, when timed_node was able to attach it.
    node = getattr(error, "graph_node", None)
    if node:
        record["outcome"]["failed_node"] = node

    return record


def build_graph_run_record(
    state: AgentState,
    *,
    total_duration_ms: float | None = None,
) -> dict[str, Any]:
    """Return the structured record for one completed graph run."""

    execution_result: dict[str, Any] = state.get("execution_result") or {}
    analysis_metadata: dict[str, Any] = state.get("analysis_metadata") or {}
    llm_calls: list[dict[str, Any]] = list(state.get("llm_calls") or [])

    model_name = next(
        (
            call.get("model_name")
            for call in llm_calls
            if call.get("model_name")
        ),
        None,
    )

    return {
        "event": GRAPH_RUN_EVENT,
        "record_version": RUN_RECORD_VERSION,
        "timestamp_utc": utc_timestamp(),
        "graph_run_id": state.get("graph_run_id"),
        "question": state.get("question"),
        "governance": build_governance_snapshot(model_name=model_name),
        "outcome": {
            "status": state.get("status"),
            "terminal_stage": state.get("terminal_stage"),
            "error_stage": state.get("error_stage") or None,
            "error_message": state.get("error_message") or None,
        },
        "sql": {
            "generated_sql": state.get("generated_sql"),
            "final_sql": state.get("final_sql"),
            "executed_sql": execution_result.get("executed_sql"),
            "referenced_tables": execution_result.get(
                "referenced_tables", []
            ),
            "result_row_limit": execution_result.get("result_row_limit"),
            "limit_was_modified": execution_result.get(
                "limit_was_modified"
            ),
            "repair_attempts": state.get("repair_attempts", 0),
            "repair_history": _redact_repair_history(
                list(state.get("repair_history") or [])
            ),
        },
        "data": {
            "row_count": execution_result.get("row_count"),
            "total_result_rows": execution_result.get("total_result_rows"),
            "result_truncated_by_client": execution_result.get(
                "result_truncated_by_client"
            ),
        },
        "cost": {
            "estimated_bytes_processed": execution_result.get(
                "estimated_bytes_processed"
            ),
            "total_bytes_processed": execution_result.get(
                "total_bytes_processed"
            ),
            "total_bytes_billed": execution_result.get(
                "total_bytes_billed"
            ),
            "cache_hit": execution_result.get("cache_hit"),
        },
        "runtime": {
            "total_duration_ms": total_duration_ms,
            "node_trace": list(state.get("node_trace") or []),
            "stage_timings_ms": execution_result.get("stage_timings_ms"),
            "sql_execution_run_ids": list(
                state.get("sql_execution_run_ids") or []
            ),
        },
        "tokens": {
            "llm_calls": llm_calls,
            "totals": summarize_llm_calls(llm_calls),
        },
        "analysis": {
            "business_analysis": state.get("business_analysis"),
            "rows_analyzed": analysis_metadata.get("rows_analyzed"),
            "rows_were_truncated": analysis_metadata.get(
                "rows_were_truncated"
            ),
        },
    }
