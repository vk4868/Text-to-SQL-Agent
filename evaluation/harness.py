"""Run one golden case through the agent and score it.

The reference query is executed through the SAME SQLExecutionPipeline as the
agent's query. That keeps the comparison honest — the reference is subject to
the same row cap and the same normalisation, so a difference between the two
result sets is a real difference in the SQL, not an artefact of one side
being run differently.
"""

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from evaluation.cases import GoldenCase
from evaluation.scorers import (
    CaseScores,
    ScoreResult,
    score_analysis_grounding,
    score_execution_accuracy,
    score_executed,
    score_guardrail_pass,
    score_repair_efficiency,
    score_table_grounding,
)


@dataclass
class CaseResult:
    """Everything one evaluated case produced."""

    case: GoldenCase
    record: dict[str, Any]
    scores: CaseScores
    reference_rows: list[dict[str, Any]] = field(default_factory=list)
    reference_error: str = ""
    duration_ms: float = 0.0
    trial: int = 1
    execution_succeeded: bool = False
    failure_class: str = ""
    supplementary: list[ScoreResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.scores.passed

    @property
    def generated_sql(self) -> str:
        return str(self.record.get("sql", {}).get("final_sql") or "")

    @property
    def total_tokens(self) -> int:
        return int(
            self.record.get("tokens", {})
            .get("totals", {})
            .get("total_tokens", 0)
            or 0
        )

    @property
    def repair_attempts(self) -> int:
        return int(
            self.record.get("sql", {}).get("repair_attempts", 0) or 0
        )


# Single source of truth for "the model provider could not be reached":
# matched case-insensitively against outcome.error_message.
UNAVAILABLE_KEYWORDS = (
    "ollama",
    "unable to communicate",
    "timed out",
    "timeout",
    "connection",
    "deadline",
    "unavailable",
    "503",
    "llmprovidererror",
)


def classify_failure(record: dict[str, Any]) -> str:
    """"success" | "rejected" | "error" | "unavailable" (else the status)."""

    outcome = (
        record.get("outcome") if isinstance(record, dict) else None
    ) or {}
    status = str(outcome.get("status") or "unknown")

    if status == "error":
        message = str(outcome.get("error_message") or "").lower()
        if any(keyword in message for keyword in UNAVAILABLE_KEYWORDS):
            return "unavailable"

    return status


SUPPLEMENTARY_SQL_ACCURACY = "sql_accuracy_any_outcome"

# The only graph node after execute_sql today. A run that fails here still
# executed its SQL successfully.
_POST_EXECUTION_STAGES = {"result_analysis"}


def execution_succeeded(record: dict[str, Any]) -> bool:
    """Whether the final SQL executed, whatever happened afterwards.

    ``outcome.status`` is "success" only when execution succeeded. A run that
    ended in error/rejected at ``result_analysis`` also executed its SQL (its
    rows are present); every other stage is on the SQL side of the graph.
    """

    if not isinstance(record, dict):
        return False

    outcome = record.get("outcome") or {}

    if outcome.get("status") == "success":
        return True

    stage = outcome.get("error_stage") or outcome.get("terminal_stage")

    if stage in _POST_EXECUTION_STAGES:
        row_count = (record.get("data") or {}).get("row_count")
        return row_count is not None

    return False


def run_reference_query(
    case: GoldenCase,
    execution_pipeline: Any,
) -> tuple[list[dict[str, Any]], str]:
    """Execute the hand-written reference query, returning rows or an error."""

    if not case.reference_sql:
        return [], ""

    result = execution_pipeline.execute(case.reference_sql)

    if result.get("status") != "success":
        return [], (
            f"reference query failed at {result.get('stage')}: "
            f"{result.get('message')}"
        )

    rows = result.get("rows", [])

    return rows if isinstance(rows, list) else [], ""


def evaluate_case(
    case: GoldenCase,
    *,
    agent: Any,
    execution_pipeline: Any,
) -> CaseResult:
    """Answer the question, run the reference, and score the difference."""

    started_at = perf_counter()

    record = agent.run(case.question)

    duration_ms = round((perf_counter() - started_at) * 1000, 2)

    reference_rows, reference_error = run_reference_query(
        case, execution_pipeline
    )

    scores = CaseScores(case_id=case.case_id)

    scores.add(score_guardrail_pass(record))
    scores.add(score_executed(record))

    executed = record.get("outcome", {}).get("status") == "success"

    if executed:
        scores.add(
            score_table_grounding(record, case.expected_tables)
        )

        if reference_error:
            # A broken reference query is a fault in the dataset, not in the
            # agent. Fail loudly rather than silently scoring it as a pass.
            scores.add(
                ScoreResult(
                    "execution_accuracy", False, reference_error
                )
            )
        else:
            scores.add(
                score_execution_accuracy(
                    record.get("rows", []),
                    reference_rows,
                    comparison=case.comparison,
                    tolerance=case.numeric_tolerance,
                )
            )

        scores.add(
            score_repair_efficiency(record, case.max_repair_attempts)
        )
        scores.add(score_analysis_grounding(record))

    ran_sql = execution_succeeded(record)
    supplementary: list[ScoreResult] = []

    if ran_sql:
        if reference_error:
            supplementary.append(
                ScoreResult(
                    SUPPLEMENTARY_SQL_ACCURACY, False, reference_error
                )
            )
        else:
            measured = score_execution_accuracy(
                record.get("rows", []),
                reference_rows,
                comparison=case.comparison,
                tolerance=case.numeric_tolerance,
            )
            supplementary.append(
                ScoreResult(
                    SUPPLEMENTARY_SQL_ACCURACY,
                    measured.passed,
                    measured.detail,
                )
            )

    return CaseResult(
        case=case,
        record=record,
        scores=scores,
        execution_succeeded=ran_sql,
        failure_class=classify_failure(record),
        supplementary=supplementary,
        reference_rows=reference_rows,
        reference_error=reference_error,
        duration_ms=duration_ms,
    )
