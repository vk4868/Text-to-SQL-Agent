"""Gate 8: the untrusted-LLM principle applied to prose.

The guard's job is to catch figures the model stated that the returned rows
do not support. Its harder job is to NOT cry wolf on the many legitimate
numbers an analysis contains — years, row counts, column totals — because a
check that fires constantly gets ignored.
"""

from decimal import Decimal

import pytest

from src.analysis_contract import (
    REQUIRED_SECTIONS,
    validate_analysis_structure,
)
from src.analysis_guard import (
    build_grounded_values,
    extract_numbers,
    find_ungrounded_numbers,
)

MONTHLY_ROWS = [
    {"month": "2025-01", "total_net_sales": Decimal("1462.66")},
    {"month": "2025-02", "total_net_sales": Decimal("1189.28")},
    {"month": "2025-12", "total_net_sales": Decimal("2958.02")},
]

QUESTION = "What were total net sales by month in 2025?"
SQL = (
    "SELECT FORMAT_DATE('%Y-%m', sale_date) AS month, "
    "SUM(net_revenue) AS total_net_sales "
    "FROM `p.d.fact_sales` WHERE sale_date BETWEEN '2025-01-01' "
    "AND '2025-12-31' GROUP BY month ORDER BY month LIMIT 100"
)


class TestExtraction:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("$1,462.66", Decimal("1462.66")),
            ("1462.66", Decimal("1462.66")),
            ("2025", Decimal("2025")),
            ("48%", Decimal("48")),
            ("-12.5", Decimal("-12.5")),
            ("$ 2,958.02", Decimal("2958.02")),
        ],
    )
    def test_numeric_forms_are_parsed(self, text, expected):
        found = extract_numbers(text)

        assert found[0][1] == expected

    def test_prose_without_numbers_yields_nothing(self):
        assert extract_numbers("Sales rose steadily across the year.") == []

    def test_empty_text_is_safe(self):
        assert extract_numbers("") == []


class TestCatchesInventedFigures:
    """The reason this module exists."""

    def test_a_fabricated_total_is_flagged(self):
        analysis = (
            "DIRECT ANSWER:\nSales reached $48,300.00 for the year."
        )

        flagged = find_ungrounded_numbers(
            analysis, MONTHLY_ROWS, context=(QUESTION, SQL)
        )

        assert flagged == ["$48,300.00"]

    def test_a_plausible_but_wrong_row_value_is_flagged(self):
        """1462.99 is close to a real 1462.66 but is not it."""

        analysis = "January sales were $1,462.99."

        flagged = find_ungrounded_numbers(
            analysis, MONTHLY_ROWS, context=(QUESTION, SQL)
        )

        assert flagged == ["$1,462.99"]

    def test_multiple_inventions_are_all_reported(self):
        analysis = "Sales were $9,999.11 in March and $8,888.22 in April."

        flagged = find_ungrounded_numbers(
            analysis, MONTHLY_ROWS, context=(QUESTION, SQL)
        )

        assert flagged == ["$9,999.11", "$8,888.22"]

    def test_a_repeated_invention_is_reported_once(self):
        analysis = "It was $48,300.00, yes $48,300.00."

        flagged = find_ungrounded_numbers(
            analysis, MONTHLY_ROWS, context=(QUESTION, SQL)
        )

        assert flagged == ["$48,300.00"]

    def test_empty_rows_make_every_figure_ungrounded(self):
        analysis = "Sales totalled $5,000.00."

        flagged = find_ungrounded_numbers(analysis, [])

        assert flagged == ["$5,000.00"]


class TestDoesNotCryWolf:
    """False positives make a check worthless, so these matter as much."""

    def test_values_present_in_rows_are_accepted(self):
        analysis = (
            "January was $1,462.66, February $1,189.28, "
            "December $2,958.02."
        )

        assert (
            find_ungrounded_numbers(
                analysis, MONTHLY_ROWS, context=(QUESTION, SQL)
            )
            == []
        )

    def test_a_column_total_is_accepted(self):
        """The real case from a live run: the model summed the column.

        1462.66 + 1189.28 + 2958.02 = 5609.96, which appears in no row.
        """

        analysis = "Total net sales for the year reached $5,609.96."

        assert (
            find_ungrounded_numbers(
                analysis, MONTHLY_ROWS, context=(QUESTION, SQL)
            )
            == []
        )

    def test_min_max_and_mean_are_accepted(self):
        analysis = (
            "The peak was 2958.02, the trough 1189.28, "
            "and the average 1869.99."
        )

        assert (
            find_ungrounded_numbers(
                analysis, MONTHLY_ROWS, context=(QUESTION, SQL)
            )
            == []
        )

    def test_a_year_from_the_question_is_accepted(self):
        analysis = "Across 2025, sales rose."

        assert (
            find_ungrounded_numbers(
                analysis, MONTHLY_ROWS, context=(QUESTION, SQL)
            )
            == []
        )

    def test_a_year_inside_a_date_string_is_accepted(self):
        """Rows hold "2025-01"; the model may cite the month as 2025-01."""

        analysis = "The first period was 2025-01."

        assert (
            find_ungrounded_numbers(analysis, MONTHLY_ROWS) == []
        )

    def test_small_structural_integers_are_ignored(self):
        analysis = (
            "Three points stand out. The top 5 months, and Q4 in "
            "particular, drove growth."
        )

        assert (
            find_ungrounded_numbers(
                analysis, MONTHLY_ROWS, context=(QUESTION, SQL)
            )
            == []
        )

    def test_the_row_count_is_accepted(self):
        analysis = "The query returned 3 rows."

        assert (
            find_ungrounded_numbers(analysis, MONTHLY_ROWS) == []
        )

    def test_a_limit_from_the_sql_is_accepted(self):
        analysis = "At most 100 rows were considered."

        assert (
            find_ungrounded_numbers(
                analysis, MONTHLY_ROWS, context=(QUESTION, SQL)
            )
            == []
        )

    def test_prose_with_no_numbers_is_clean(self):
        analysis = "Sales rose steadily and then fell back."

        assert find_ungrounded_numbers(analysis, MONTHLY_ROWS) == []

    def test_the_deterministic_empty_analysis_is_clean(self):
        """The canned zero-row text must not trip its own guard."""

        from src.result_analyzer import EMPTY_RESULT_ANALYSIS

        assert find_ungrounded_numbers(EMPTY_RESULT_ANALYSIS, []) == []


class TestGroundedValues:
    def test_integers_and_floats_are_collected(self):
        grounded = build_grounded_values(
            [{"a": 10, "b": 2.5}, {"a": 20, "b": 7.5}]
        )

        assert Decimal("10") in grounded
        assert Decimal("2.5") in grounded
        assert Decimal("30") in grounded  # sum of a
        assert Decimal("10") in grounded  # sum of b

    def test_booleans_are_not_treated_as_numbers(self):
        """True would otherwise ground the number 1."""

        grounded = build_grounded_values([{"flag": True}])

        # Only the row count, which is 1.
        assert grounded == {Decimal(1)}

    def test_none_values_are_skipped(self):
        grounded = build_grounded_values([{"a": None, "b": 5}])

        assert Decimal("5") in grounded


class TestAnalysisContract:
    def test_a_well_formed_analysis_has_no_violations(self):
        analysis = "\n\n".join(
            f"{section}:\nsomething" for section in REQUIRED_SECTIONS
        )

        assert validate_analysis_structure(analysis) == []

    def test_a_missing_section_is_reported(self):
        analysis = "\n\n".join(
            f"{section}:\nsomething"
            for section in REQUIRED_SECTIONS
            if section != "LIMITATIONS"
        )

        violations = validate_analysis_structure(analysis)

        assert len(violations) == 1
        assert "LIMITATIONS" in violations[0]

    def test_an_empty_analysis_is_reported(self):
        assert validate_analysis_structure("   ") == [
            "The analysis is empty."
        ]

    def test_a_markdown_table_is_reported(self):
        analysis = (
            "\n\n".join(
                f"{section}:\nsomething" for section in REQUIRED_SECTIONS
            )
            + "\n\n| month | sales |\n| --- | --- |\n"
        )

        violations = validate_analysis_structure(analysis)

        assert any("Markdown table" in item for item in violations)

    def test_headers_are_matched_case_insensitively(self):
        analysis = "\n\n".join(
            f"{section.title()}:\nsomething"
            for section in REQUIRED_SECTIONS
        )

        assert validate_analysis_structure(analysis) == []


class TestContiguousSubtotals:
    """Period subtotals are bounded arithmetic, not invention.

    Added after a live run produced "Q1 Total (Jan-Mar): 5,366.05" — correct
    arithmetic over an adjacent run of rows, which the first version of this
    guard reported as unsupported.
    """

    QUARTERLY_ROWS = [
        {"month": "2025-01", "sales": Decimal("1462.66")},
        {"month": "2025-02", "sales": Decimal("1189.28")},
        {"month": "2025-03", "sales": Decimal("2714.11")},
        {"month": "2025-04", "sales": Decimal("2315.33")},
    ]

    def test_a_quarter_subtotal_is_accepted(self):
        # 1462.66 + 1189.28 + 2714.11
        analysis = "Q1 Total (Jan-Mar): 5,366.05."

        assert (
            find_ungrounded_numbers(analysis, self.QUARTERLY_ROWS) == []
        )

    def test_a_two_row_subtotal_is_accepted(self):
        analysis = "The first two months totalled 2,651.94."

        assert (
            find_ungrounded_numbers(analysis, self.QUARTERLY_ROWS) == []
        )

    def test_the_whole_column_sum_is_still_accepted(self):
        analysis = "The full period totalled 7,681.38."

        assert (
            find_ungrounded_numbers(analysis, self.QUARTERLY_ROWS) == []
        )

    def test_a_wrong_subtotal_is_still_flagged(self):
        """Arithmetic the model got wrong must not slip through."""

        analysis = "Q1 Total (Jan-Mar): 5,999.99."

        assert find_ungrounded_numbers(
            analysis, self.QUARTERLY_ROWS
        ) == ["5,999.99"]

    def test_a_non_contiguous_sum_is_flagged(self):
        """Jan + Apr is not a period, and is not enumerated."""

        # 1462.66 + 2315.33 = 3777.99
        analysis = "January and April together were 3,777.99."

        assert find_ungrounded_numbers(
            analysis, self.QUARTERLY_ROWS
        ) == ["3,777.99"]

    def test_enumeration_is_skipped_on_an_oversized_result(self):
        from src.analysis_guard import _contiguous_sums

        values = [Decimal(str(i)) for i in range(200)]

        assert _contiguous_sums(values, max_rows=120) == set()
