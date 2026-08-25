"""Catch numbers the model stated that the data does not support.

The project's central claim is that the LLM is untrusted and deterministic
code must check its output. That claim is easy to make about SQL, where the
guardrails parse an AST. This module extends it to the prose: every figure in
the written analysis is checked against the figures the query actually
returned.

The precise claim: every figure in the analysis is either present in the
returned rows, a bounded arithmetic derivation of them, or a number the
QUESTION already contained. Anything else is reported.

"Bounded derivation" means:

- a whole-column aggregate: sum, min, max or mean of a numeric column
- a contiguous subtotal — the sum of an adjacent run of rows, which is what a
  quarter or period total is over an ordered result
- a fraction rendered as a percentage, so a `discount_pct` of 0.125 supports
  "12.5%"
- the row count

Two rules exist to keep the trust model sound:

**The generated SQL is not trusted as context.** It is the same untrusted
model output this module exists to check, so grounding the prose in it would
let a model launder a fabrication by first writing the number into its own
WHERE clause. Only the question — genuine human input — grounds the prose.

**A figure carrying a unit is always checked.** Bare small integers are
skipped as structural ("three points", "Q4", "the top 5"), but "$8" and "8%"
are factual claims about the data, and a fabricated margin or churn count is
exactly what a reader would act on.

Known limitations, all of which cause over-reporting rather than silence:
ratios ("4.5 times greater"), differences between rows, and means over a
subset are not enumerated, so a model that computes one is reported. A flag
therefore means "not mechanically derivable from the result", which is a
prompt to check — not proof of a lie. In the other direction, the grounded
set grows with the result, so on integer-valued data a fabricated value can
easily coincide with a real derivation; the check is far stronger on
high-precision decimals than on small counts.
"""

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

#: A number with optional sign, currency and percent.
#:
#: The sign is only a sign when it is not preceded by a digit or a hyphen, so
#: the "-12-31" of an ISO date yields positive 12 and 31 rather than negative
#: ones. The thousands group requires exactly three following digits, so
#: "December 31, 2025" yields 31 and 2025 rather than the token "31,".
_NUMBER = re.compile(
    r"(?P<sign>(?<![\d-])-)?"
    r"\s?(?P<currency>\$)?\s?"
    r"(?P<value>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?P<percent>\s?%)?"
)

#: Detects scientific notation immediately after a match, which the plain
#: number pattern would otherwise split into a misleading mantissa.
_EXPONENT = re.compile(r"[eE][+-]?\d+")

#: A month name immediately before or after a number marks it as a calendar
#: day rather than a measurement, so "December 31, 2025" does not report 31.
_MONTHS = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?"
)
_MONTH_BEFORE = re.compile(rf"(?:{_MONTHS})\.?\s+$", re.IGNORECASE)
_MONTH_AFTER = re.compile(rf"^(?:st|nd|rd|th)?[\s,]+(?:{_MONTHS})\b", re.IGNORECASE)

CALENDAR_DAY_CEILING = 31

#: Bare whole numbers at or below this are treated as structural rather than
#: factual — "two to four insights", "Q4", "the top 5". A number carrying a
#: currency or percent sign is never exempt.
SMALL_INTEGER_CEILING = 12

DEFAULT_TOLERANCE = Decimal("0.01")

#: Above this row count the quadratic subtotal enumeration is skipped.
MAX_WINDOW_ROWS = 120


class NumberToken:
    """One numeric mention in the text, with the units it carried."""

    __slots__ = (
        "text",
        "value",
        "is_percent",
        "has_currency",
        "suspect",
        "is_calendar_day",
    )

    def __init__(
        self,
        text: str,
        value: Decimal,
        *,
        is_percent: bool = False,
        has_currency: bool = False,
        suspect: bool = False,
        is_calendar_day: bool = False,
    ) -> None:
        self.text = text
        self.value = value
        self.is_percent = is_percent
        self.has_currency = has_currency
        # True when the token is part of a form this module cannot parse
        # faithfully, such as scientific notation.
        self.suspect = suspect
        self.is_calendar_day = is_calendar_day

    @property
    def has_unit(self) -> bool:
        return self.is_percent or self.has_currency

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"NumberToken({self.text!r}, {self.value})"


def _to_decimal(raw: str) -> Decimal | None:
    cleaned = raw.replace(",", "").strip()

    if not cleaned:
        return None

    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def extract_number_tokens(text: str) -> list[NumberToken]:
    """Return every numeric mention with the units attached to it."""

    tokens: list[NumberToken] = []

    for match in _NUMBER.finditer(text or ""):
        value = _to_decimal(match.group("value"))
        if value is None:
            continue

        if match.group("sign"):
            value = -value

        start = match.start("value")
        tail = text[match.end() : match.end() + 16]
        head = text[max(0, start - 16) : start]

        near_month = bool(
            _MONTH_BEFORE.search(head) or _MONTH_AFTER.match(tail)
        )

        tokens.append(
            NumberToken(
                match.group().strip(),
                value,
                is_percent=bool(match.group("percent")),
                has_currency=bool(match.group("currency")),
                suspect=bool(_EXPONENT.match(tail)),
                is_calendar_day=(
                    near_month
                    and not match.group("percent")
                    and not match.group("currency")
                    and value == value.to_integral_value()
                    and 1 <= value <= CALENDAR_DAY_CEILING
                ),
            )
        )

    return tokens


def extract_numbers(text: str) -> list[tuple[str, Decimal]]:
    """Return every numeric token as (text, value)."""

    return [(token.text, token.value) for token in extract_number_tokens(text)]


def _numeric_cells(rows: list[dict[str, Any]]) -> dict[str, list[Decimal]]:
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
    """Return the sum of every adjacent run of two or more rows.

    Covers period subtotals — a quarter, a half-year — which a model computes
    routinely from an ordered result. Skipped on an oversized result, where
    the quadratic enumeration would ground so many values that the check
    stops meaning anything.
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


def build_measured_values(rows: list[dict[str, Any]]) -> set[Decimal]:
    """Return values that are genuinely measurements, not text fragments.

    Kept apart from numbers scraped out of string cells because a year inside
    a date is not a quantity, and must not be allowed to ground a percentage.
    """

    grounded: set[Decimal] = set()

    for row in rows:
        for value in row.values():
            if isinstance(value, bool) or value is None:
                continue
            if isinstance(value, (int, float, Decimal)):
                grounded.add(Decimal(str(value)))

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

    return grounded


def build_data_values(rows: list[dict[str, Any]]) -> set[Decimal]:
    """Return every value derivable from the result, text fragments included."""

    grounded = build_measured_values(rows)

    for row in rows:
        for value in row.values():
            if value is None or isinstance(
                value, (bool, int, float, Decimal)
            ):
                continue
            # A date renders as "2025-12-31"; its parts are legitimate things
            # for the prose to mention.
            for token in extract_number_tokens(str(value)):
                grounded.add(token.value)

    return grounded


def build_grounded_values(
    rows: list[dict[str, Any]],
    *,
    context: Iterable[str] = (),
) -> set[Decimal]:
    """Return the data values plus any numbers the question supplied."""

    grounded = build_data_values(rows)

    for text in context:
        for token in extract_number_tokens(text or ""):
            grounded.add(token.value)

    return grounded


def _matches(
    value: Decimal,
    candidates: set[Decimal],
    tolerance: Decimal,
) -> bool:
    if value in candidates:
        return True

    return any(
        abs(value - candidate) <= tolerance for candidate in candidates
    )


def find_ungrounded_numbers(
    analysis: str,
    rows: list[dict[str, Any]],
    *,
    context: Iterable[str] = (),
    tolerance: Decimal = DEFAULT_TOLERANCE,
    small_integer_ceiling: int = SMALL_INTEGER_CEILING,
) -> list[str]:
    """Return the figures in the analysis the result does not support.

    ``context`` should carry the QUESTION only. Do not pass the generated
    SQL: it is untrusted model output, and using it here would let the model
    ground its own prose in its own invention.
    """

    if not analysis or not analysis.strip():
        return []

    data_values = build_data_values(rows)

    question_values: set[Decimal] = set()
    for text in context:
        for token in extract_number_tokens(text or ""):
            question_values.add(token.value)

    # A fraction in the data supports the same figure stated as a percentage,
    # but only a measured value can — not a year scraped from a date string.
    measured = build_measured_values(rows)
    percent_values = {value * 100 for value in measured} | measured

    ungrounded: list[str] = []
    seen: set[tuple[Decimal, bool]] = set()

    for token in extract_number_tokens(analysis):
        key = (token.value, token.is_percent)
        if key in seen:
            continue

        if token.suspect:
            seen.add(key)
            ungrounded.append(token.text)
            continue

        # A calendar day beside a month name is a date, not a measurement.
        if token.is_calendar_day:
            continue

        # Structural counts, but only when nothing marks them as a measurement.
        if (
            not token.has_unit
            and token.value == token.value.to_integral_value()
            and abs(token.value) <= small_integer_ceiling
        ):
            continue

        if token.is_percent:
            # A year or a row count is not a percentage, so the question's
            # numbers do not ground one.
            candidates = percent_values
        else:
            candidates = data_values | question_values

        if _matches(token.value, candidates, tolerance):
            continue

        seen.add(key)
        ungrounded.append(token.text)

    return ungrounded
