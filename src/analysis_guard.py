"""Catch numbers the model stated that the data does not support.

The project's central claim is that the LLM is untrusted and deterministic
code must check its output. That claim is easy to make about SQL, where the
guardrails parse an AST. This module extends it to the prose: every figure in
the written analysis is checked against the figures the query actually
returned.

The precise claim: every figure in the analysis is either present in the
returned rows, or a bounded arithmetic derivation of them, or was given to
the model in the question or the SQL. Anything else is flagged.

"Bounded derivation" means:

- a whole-column aggregate: sum, min, max or mean of a numeric column
- a contiguous subtotal — the sum of an adjacent run of rows. This is what a
  quarter or period total is over an ordered result, and a live run showed
  the model producing exactly that ("Q1 Total (Jan-Mar): 5,366.05"). It is
  O(n^2) to enumerate, which is affordable because the result-row guardrail
  caps rows at MAX_RESULT_ROWS.
- the row count

Two honest limitations. Ratios and percentage changes are not enumerated, so
a model-computed "December was 148% of January" is flagged — correctly, in
the sense that the figure is model arithmetic rather than data, though a
reader may find it reasonable. And the grounded set grows with the result, so
on a large result a fabricated value can coincide with a real derivation.
This is a strong signal, not a proof.
"""

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

#: A number, with optional currency, thousands separators, decimals, percent.
_NUMBER = re.compile(r"-?\$?\s?\d[\d,]*(?:\.\d+)?%?")

#: Values below this are usually structural rather than factual — list
#: counts, "2 to 4 insights", quarter numbers — and flagging them is noise.
SMALL_INTEGER_CEILING = 12

DEFAULT_TOLERANCE = Decimal("0.01")

#: Above this row count the quadratic subtotal enumeration is skipped. The
#: result-row guardrail caps results well below it in normal operation.
MAX_WINDOW_ROWS = 120


def _to_decimal(raw: str) -> Decimal | None:
    """Parse a matched numeric token, or None if it is not a number."""

    cleaned = (
        raw.replace("$", "")
        .replace(",", "")
        .replace("%", "")
        .replace(" ", "")
        .strip()
    )

    if not cleaned or cleaned in {"-", "."}:
        return None

    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def extract_numbers(text: str) -> list[tuple[str, Decimal]]:
    """Return every numeric token in the text with its parsed value."""

    found: list[tuple[str, Decimal]] = []

    for match in _NUMBER.finditer(text or ""):
        value = _to_decimal(match.group())
        if value is not None:
            found.append((match.group().strip(), value))

    return found


def _numeric_cells(rows: list[dict[str, Any]]) -> dict[str, list[Decimal]]:
    """Collect the numeric values of each column."""

    columns: dict[str, list[Decimal]] = {}

    for row in rows:
        for column, value in row.items():
            if isinstance(value, bool) or value is None:
                continue
            if isinstance(value, (int, float, Decimal)):
                columns.setdefault(column, []).append(Decimal(str(value)))

    return columns


def _contiguous_sums(
    values: list[Decimal],
    *,
    max_rows: int = MAX_WINDOW_ROWS,
) -> set[Decimal]:
    """Return the sum of every adjacent run of rows.

    Covers period subtotals — a quarter, a half-year — which a model computes
    routinely from an ordered result. Includes the whole-column sum as the
    widest window. Skipped entirely on an oversized result, where the
    quadratic enumeration would stop being worth its cost and would ground so
    many values that the check loses its meaning.
    """

    if len(values) > max_rows:
        return set()

    sums: set[Decimal] = set()

    for start in range(len(values)):
        running = Decimal(0)
        for end in range(start, len(values)):
            running += values[end]
            if end > start:
                sums.add(running)

    return sums


def build_grounded_values(
    rows: list[dict[str, Any]],
    *,
    context: Iterable[str] = (),
) -> set[Decimal]:
    """Return every value the analysis is allowed to state."""

    grounded: set[Decimal] = set()

    # Every cell, numeric or numeric-looking.
    for row in rows:
        for value in row.values():
            if isinstance(value, bool) or value is None:
                continue
            if isinstance(value, (int, float, Decimal)):
                grounded.add(Decimal(str(value)))
            else:
                # A date renders as "2025-01-01"; the model may cite 2025.
                for _, parsed in extract_numbers(str(value)):
                    grounded.add(parsed)

    # Aggregates a reader would expect the model to be able to compute.
    for values in _numeric_cells(rows).values():
        if not values:
            continue
        grounded.add(min(values))
        grounded.add(max(values))
        grounded.add(
            (sum(values, Decimal(0)) / Decimal(len(values))).quantize(
                Decimal("0.01")
            )
        )
        grounded.update(_contiguous_sums(values))

    grounded.add(Decimal(len(rows)))

    # Numbers the question or the SQL already put in front of the model.
    for text in context:
        for _, parsed in extract_numbers(text or ""):
            grounded.add(parsed)

    return grounded


def _is_grounded(
    value: Decimal,
    grounded: set[Decimal],
    tolerance: Decimal,
) -> bool:
    """Return whether a value matches a grounded one within tolerance."""

    if value in grounded:
        return True

    return any(abs(value - candidate) <= tolerance for candidate in grounded)


def find_ungrounded_numbers(
    analysis: str,
    rows: list[dict[str, Any]],
    *,
    context: Iterable[str] = (),
    tolerance: Decimal = DEFAULT_TOLERANCE,
    small_integer_ceiling: int = SMALL_INTEGER_CEILING,
) -> list[str]:
    """Return the figures in the analysis the result does not support.

    ``context`` should carry the question and the executed SQL, so numbers the
    model was handed are not reported back as inventions.
    """

    if not analysis or not analysis.strip():
        return []

    grounded = build_grounded_values(rows, context=context)

    ungrounded: list[str] = []
    seen: set[Decimal] = set()

    for token, value in extract_numbers(analysis):
        if value in seen:
            continue

        # Small whole numbers are almost always structural, not factual.
        if (
            value == value.to_integral_value()
            and abs(value) <= small_integer_ceiling
        ):
            continue

        if _is_grounded(value, grounded, tolerance):
            continue

        seen.add(value)
        ungrounded.append(token)

    return ungrounded
