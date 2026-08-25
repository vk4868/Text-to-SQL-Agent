"""Deterministic scoring. No LLM judge anywhere in this file.

The central metric is execution accuracy: run the agent's SQL and a
hand-written reference query, then compare the RESULT SETS by value. That
frees the agent to alias columns, order joins and phrase the query however it
likes, while still proving the numbers are right. It is the standard
Spider/BIRD metric and it is fully reproducible, which an LLM judge is not.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

#: A cell that is NULL, normalised so it compares equal across sources.
NULL_SENTINEL = "\x00NULL"


@dataclass
class ScoreResult:
    """The outcome of one scorer."""

    name: str
    passed: bool
    detail: str = ""

    def __bool__(self) -> bool:
        return self.passed


@dataclass
class CaseScores:
    """All scores for one golden case."""

    case_id: str
    scores: list[ScoreResult] = field(default_factory=list)

    def add(self, score: ScoreResult) -> None:
        self.scores.append(score)

    @property
    def passed(self) -> bool:
        """A case passes only when every scorer passes."""

        return all(score.passed for score in self.scores)

    def by_name(self) -> dict[str, ScoreResult]:
        return {score.name: score for score in self.scores}


def normalise_cell(value: Any, tolerance: Decimal) -> Any:
    """Reduce a cell to a comparable form.

    Numbers are rounded to the tolerance so that NUMERIC, FLOAT and a
    model's slightly different arithmetic all land on the same value. Dates
    become ISO strings so a DATE and a formatted string agree.
    """

    if value is None:
        return NULL_SENTINEL

    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float, Decimal)):
        try:
            quantised = Decimal(str(value)).quantize(tolerance)
        except (InvalidOperation, ValueError):
            return str(value)
        # -0 and 0 must not compare differently.
        return quantised + Decimal(0)

    if isinstance(value, (date, datetime)):
        return value.isoformat()

    text = str(value).strip()

    # A numeric string is compared as a number, so "1462.66" == 1462.66.
    try:
        return Decimal(text).quantize(tolerance) + Decimal(0)
    except (InvalidOperation, ValueError):
        return text


def normalise_row(row: dict[str, Any], tolerance: Decimal) -> tuple:
    """Reduce a row to a comparable tuple, ignoring column names.

    Values are sorted within the row so that column ORDER does not matter
    either — the agent may return (month, total) or (total, month).
    """

    normalised = [normalise_cell(value, tolerance) for value in row.values()]

    return tuple(sorted(normalised, key=repr))


def normalise_rows(
    rows: list[dict[str, Any]],
    tolerance: Decimal,
) -> list[tuple]:
    return [normalise_row(row, tolerance) for row in rows]


def score_guardrail_pass(
    record: dict[str, Any],
) -> ScoreResult:
    """The run must not have been refused by a guardrail.

    A rejection here means the agent wrote SQL its own safety layer would not
    allow — a real failure, distinct from the guardrails working correctly on
    hostile input (which the attack suite covers).
    """

    status = record.get("outcome", {}).get("status")
    stage = record.get("outcome", {}).get("terminal_stage")

    if status == "rejected":
        return ScoreResult(
            "guardrail_pass",
            False,
            f"refused at stage {stage}",
        )

    return ScoreResult("guardrail_pass", True)


def score_executed(record: dict[str, Any]) -> ScoreResult:
    """The run must have produced a result."""

    status = record.get("outcome", {}).get("status")

    if status != "success":
        return ScoreResult(
            "executed",
            False,
            f"status={status} at "
            f"{record.get('outcome', {}).get('terminal_stage')}",
        )

    return ScoreResult("executed", True)


def score_table_grounding(
    record: dict[str, Any],
    expected_tables: Iterable[str],
) -> ScoreResult:
    """The right answer from the wrong table is still wrong."""

    expected = {table.strip() for table in expected_tables if table.strip()}

    if not expected:
        return ScoreResult("table_grounding", True, "no expectation set")

    actual = {
        str(table)
        for table in record.get("sql", {}).get("referenced_tables", [])
    }

    if actual == expected:
        return ScoreResult("table_grounding", True)

    missing = sorted(expected - actual)
    extra = sorted(actual - expected)

    detail_parts = []
    if missing:
        detail_parts.append(f"missing {missing}")
    if extra:
        detail_parts.append(f"unexpected {extra}")

    return ScoreResult(
        "table_grounding", False, "; ".join(detail_parts)
    )


def score_execution_accuracy(
    actual_rows: list[dict[str, Any]],
    reference_rows: list[dict[str, Any]],
    *,
    comparison: str = "multiset",
    tolerance: float = 0.01,
) -> ScoreResult:
    """Compare result sets by value.

    Column names and column order are ignored on purpose: two correct
    answers to the same question routinely differ in both.
    """

    if comparison == "none":
        return ScoreResult("execution_accuracy", True, "not compared")

    quantum = Decimal(str(tolerance))

    actual = normalise_rows(actual_rows, quantum)
    reference = normalise_rows(reference_rows, quantum)

    if comparison == "scalar":
        if len(actual) != 1 or len(reference) != 1:
            return ScoreResult(
                "execution_accuracy",
                False,
                f"expected one row each, got {len(actual)} vs "
                f"{len(reference)}",
            )
        if actual[0] != reference[0]:
            return ScoreResult(
                "execution_accuracy",
                False,
                f"{_describe(actual[0])} != {_describe(reference[0])}",
            )
        return ScoreResult("execution_accuracy", True)

    if comparison == "ordered":
        if actual != reference:
            return ScoreResult(
                "execution_accuracy",
                False,
                _first_difference(actual, reference),
            )
        return ScoreResult("execution_accuracy", True)

    # multiset
    from collections import Counter

    if Counter(actual) != Counter(reference):
        return ScoreResult(
            "execution_accuracy",
            False,
            _describe_multiset_difference(actual, reference),
        )

    return ScoreResult("execution_accuracy", True)


def score_repair_efficiency(
    record: dict[str, Any],
    max_repair_attempts: int,
) -> ScoreResult:
    """Getting there eventually still counts, but the budget is finite."""

    used = int(record.get("sql", {}).get("repair_attempts", 0) or 0)

    if used > max_repair_attempts:
        return ScoreResult(
            "repair_efficiency",
            False,
            f"used {used}, budget {max_repair_attempts}",
        )

    return ScoreResult(
        "repair_efficiency", True, f"{used} repair(s)"
    )


def score_analysis_grounding(record: dict[str, Any]) -> ScoreResult:
    """Reuse the Phase 8 checks as an evaluation metric.

    This is what replaces an LLM judge for output quality: the analysis is
    scored on whether every figure in it is derivable from the rows, and
    whether it kept the mandated structure.
    """

    analysis = record.get("analysis", {})

    ungrounded = analysis.get("ungrounded_numbers") or []
    violations = analysis.get("contract_violations") or []

    if ungrounded or violations:
        details = []
        if ungrounded:
            details.append(f"ungrounded figures: {ungrounded}")
        if violations:
            details.append(f"format: {violations}")
        return ScoreResult(
            "analysis_grounding", False, "; ".join(details)
        )

    return ScoreResult("analysis_grounding", True)


# ----------------------------------------------------------------------
# difference reporting, so a failure is actionable rather than just red
# ----------------------------------------------------------------------


def _describe(row: tuple) -> str:
    return "(" + ", ".join(_describe_cell(cell) for cell in row) + ")"


def _describe_cell(cell: Any) -> str:
    if cell == NULL_SENTINEL:
        return "NULL"
    return str(cell)


def _first_difference(
    actual: list[tuple],
    reference: list[tuple],
) -> str:
    if len(actual) != len(reference):
        return (
            f"row count {len(actual)} != {len(reference)}"
        )

    for index, (left, right) in enumerate(zip(actual, reference)):
        if left != right:
            return (
                f"row {index}: {_describe(left)} != {_describe(right)}"
            )

    return "identical"


def _describe_multiset_difference(
    actual: list[tuple],
    reference: list[tuple],
) -> str:
    from collections import Counter

    actual_counts = Counter(actual)
    reference_counts = Counter(reference)

    missing = list((reference_counts - actual_counts).elements())
    extra = list((actual_counts - reference_counts).elements())

    parts = [f"row count {len(actual)} vs {len(reference)}"]

    if missing:
        parts.append(f"missing e.g. {_describe(missing[0])}")
    if extra:
        parts.append(f"unexpected e.g. {_describe(extra[0])}")

    return "; ".join(parts)
