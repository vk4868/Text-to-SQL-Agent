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
- a share of the column total, which is what "the Northeast and West account
  for 58.6% of net revenue" is — the most common percentage in an analysis
- the difference between any two values in a column, which is what "the
  Northeast exceeded the West by 168.83" is
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

Only the claim-bearing sections are checked. LIMITATIONS and SUGGESTED
FOLLOW-UP are asked by the prompt to discuss data that is not in the result,
so figures there are proposals rather than assertions.

A hedged figure is judged as the claim it actually makes. "over 58%" is a
lower bound, satisfied by a real 58.6%. "roughly $1,463" is an approximation,
matched within 1%. A hedge licenses rounding, not invention: "approximately
24,000" against a real 24,800 is 3% out and is still reported.

This module checks VALUES, not ATTRIBUTIONS. It can tell you that 87.3%
exists somewhere in the result; it cannot tell you the model attached it to
the right row. "Online is over 80% of revenue" passes when Online is 28% but
In-Store plus Online is 87%, because the figure is real even though the
sentence is wrong. Catching that would require parsing the claim, not the
number.

Known limitations that cause over-reporting rather than silence: ratios
("4.5 times greater") and means over a subset are not enumerated, so a model
that computes one is reported. A flag
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

#: Words marking the following number as an approximation rather than an
#: exact claim: "roughly $1,463" about a real 1462.66.
_HEDGE_APPROX = re.compile(
    r"\b(?:about|approximately|approx\.?|roughly|around|nearly|almost|"
    r"circa|~)\s*\$?\s*$",
    re.IGNORECASE,
)

#: Words making the number a lower bound: "over 58%" of a real 58.6% is true.
_HEDGE_AT_LEAST = re.compile(
    r"\b(?:over|above|more than|greater than|at least|upwards of|"
    r"exceeding|in excess of)\s*\$?\s*$",
    re.IGNORECASE,
)

#: Words making the number an upper bound.
_HEDGE_AT_MOST = re.compile(
    r"\b(?:under|below|less than|fewer than|at most|no more than)"
    r"\s*\$?\s*$",
    re.IGNORECASE,
)

#: Relative tolerance applied to a hedged figure. A hedge licenses rounding,
#: not invention: "approximately 24,000" against a real 24,800 is 3% out and
#: is still reported.
HEDGED_RELATIVE_TOLERANCE = Decimal("0.01")

#: How far past a stated bound a value may sit and still be taken as the
#: quantity that bound describes.
HEDGE_BOUND_FACTOR = Decimal("1.5")

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
        "hedge",
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
        hedge: str = "",
    ) -> None:
        self.text = text
        self.value = value
        self.is_percent = is_percent
        self.has_currency = has_currency
        # True when the token is part of a form this module cannot parse
        # faithfully, such as scientific notation.
        self.suspect = suspect
        self.is_calendar_day = is_calendar_day
        # "", "approx", "at_least" or "at_most".
        self.hedge = hedge

    @property
    def has_unit(self) -> bool:
        return self.is_percent or self.has_currency

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"NumberToken({self.text!r}, {self.value})"


def _classify_hedge(head: str) -> str:
    """Return which kind of hedge, if any, introduces the next number."""

    if _HEDGE_AT_LEAST.search(head):
        return "at_least"
    if _HEDGE_AT_MOST.search(head):
        return "at_most"
    if _HEDGE_APPROX.search(head):
        return "approx"
    return ""


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
                hedge=_classify_hedge(head),
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


def _pairwise_differences(
    values: list[Decimal],
    *,
    max_rows: int = MAX_WINDOW_ROWS,
) -> set[Decimal]:
    """Return the difference between every pair of values in a column.

    "The Northeast exceeded the West by 168.83" is a comparison a reader
    expects an analyst to make, and it is O(n^2) to enumerate, same as the
    subtotals. A model that gets the subtraction WRONG is still reported,
    which is the point.
    """

    if len(values) > max_rows:
        return set()

    differences: set[Decimal] = set()

    for index, left in enumerate(values):
        for right in values[index + 1 :]:
            differences.add(abs(left - right))

    return differences


def build_share_percentages(rows: list[dict[str, Any]]) -> set[Decimal]:
    """Return every value expressed as a percentage of its column total.

    "X accounts for 58.6% of the total" is the percentage an analyst reaches
    for most often. Shares of individual cells and of contiguous subtotals
    are both included, since "these three regions are 58% of revenue" is as
    natural as "this one region is 30%".
    """

    shares: set[Decimal] = set()

    for values in _numeric_cells(rows).values():
        total = sum(values, Decimal(0))

        if not total:
            continue

        parts = set(values) | _contiguous_sums(values)

        for part in parts:
            shares.add(
                (part / total * 100).quantize(Decimal("0.01"))
            )

    return shares


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
        grounded.update(_pairwise_differences(values))

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
    *,
    hedge: str = "",
) -> bool:
    if value in candidates:
        return True

    # A bound must be satisfied by a value that is plausibly the quantity
    # being described. Without the upper limit, "over 80%" would be satisfied
    # by any larger number anywhere in the result.
    if hedge == "at_least":
        # "over 58%" is satisfied by a real 58.64%, not by an unrelated
        # larger figure.
        return any(
            value <= candidate <= value * HEDGE_BOUND_FACTOR
            for candidate in candidates
        )

    if hedge == "at_most":
        return any(
            value / HEDGE_BOUND_FACTOR <= candidate <= value
            for candidate in candidates
        )

    allowed = tolerance

    if hedge == "approx":
        # An approximation may be a rounded version of a real value, but a
        # hedge licenses rounding rather than invention.
        allowed = max(tolerance, abs(value) * HEDGED_RELATIVE_TOLERANCE)

    return any(
        abs(value - candidate) <= allowed for candidate in candidates
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
    percent_values = (
        {value * 100 for value in measured}
        | measured
        | build_share_percentages(rows)
    )

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

        if _matches(
            token.value, candidates, tolerance, hedge=token.hedge
        ):
            continue

        seen.add(key)
        ungrounded.append(token.text)

    return ungrounded
