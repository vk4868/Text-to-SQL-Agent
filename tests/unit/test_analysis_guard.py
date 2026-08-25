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


class TestTrustModel:
    """The generated SQL must not ground the generated prose.

    Found by an audit: passing the SQL as context let a model launder a
    fabrication by writing the number into its own WHERE clause first. One
    untrusted artefact cannot vouch for another.
    """

    ROWS = [{"month": "2025-01", "total": Decimal("1462.66")}]

    def test_a_number_from_the_models_own_sql_does_not_ground_prose(self):
        analysis = "The average order value was 5000."

        flagged = find_ungrounded_numbers(
            analysis,
            self.ROWS,
            context=("What were sales?",),
        )

        assert flagged == ["5000"]

    def test_the_question_does_still_ground_prose(self):
        """Genuine human input is trusted."""

        assert (
            find_ungrounded_numbers(
                "Across 2025 sales rose.",
                self.ROWS,
                context=("What were sales in 2025?",),
            )
            == []
        )

    def test_the_analyzer_does_not_pass_sql_as_context(self):
        """Pin the wiring, not just the function."""

        import inspect

        from src.result_analyzer import ResultAnalyzer

        source = inspect.getsource(ResultAnalyzer.analyze)

        assert "context=(question,)" in source, (
            "ResultAnalyzer must ground the analysis in the question only"
        )
        assert "context=(question, sql)" not in source


class TestUnitsAreAlwaysChecked:
    """A figure carrying a unit is a factual claim, however small."""

    ROWS = [{"month": "2025-01", "total": Decimal("1462.66")}]

    def test_a_fabricated_small_percentage_is_flagged(self):
        assert find_ungrounded_numbers(
            "The profit margin was 8%.", self.ROWS
        ) == ["8%"]

    def test_a_fabricated_small_currency_amount_is_flagged(self):
        assert find_ungrounded_numbers(
            "Each order averaged $8.", self.ROWS
        ) == ["$8"]

    def test_a_bare_small_integer_is_still_structural(self):
        assert (
            find_ungrounded_numbers("Q4 had the top 5 months.", self.ROWS)
            == []
        )

    def test_a_year_does_not_ground_a_percentage(self):
        """"Up 2025%" must not pass just because 2025 is in the question."""

        assert find_ungrounded_numbers(
            "Sales were up 2025%.",
            self.ROWS,
            context=("sales in 2025",),
        ) == ["2025%"]

    def test_a_fraction_supports_the_same_figure_as_a_percentage(self):
        """discount_pct of 0.125 legitimately supports "12.5%"."""

        rows = [
            {"promotion": "Seasonal", "avg_discount": Decimal("0.125")},
            {"promotion": "Clearance", "avg_discount": Decimal("0.0834")},
        ]

        assert (
            find_ungrounded_numbers(
                "Seasonal discounted 12.5%, Clearance 8.34%.", rows
            )
            == []
        )


class TestCalendarDatesAreNotMeasurements:
    """Found in production: "December 31, 2025" reported the token "31,"."""

    ROWS = [{"total_profit": Decimal("13866.1")}]

    def test_a_spelled_out_date_is_clean(self):
        assert (
            find_ungrounded_numbers(
                "As of December 31, 2025 profit was 13866.1.",
                self.ROWS,
                context=("profit in 2025",),
            )
            == []
        )

    def test_a_day_before_a_month_is_clean(self):
        assert (
            find_ungrounded_numbers(
                "Measured on 15 March, profit was 13866.1.",
                self.ROWS,
                context=("profit",),
            )
            == []
        )

    def test_no_token_ever_ends_in_a_comma(self):
        from src.analysis_guard import extract_number_tokens

        tokens = extract_number_tokens("On December 31, 2025 we saw 1,462.66")

        assert all(not token.text.endswith(",") for token in tokens)

    def test_a_similar_number_away_from_a_month_is_still_checked(self):
        assert find_ungrounded_numbers(
            "We shipped 31 pallets.", self.ROWS
        ) == ["31"]


class TestUnparseableForms:
    def test_scientific_notation_is_reported_not_silently_split(self):
        """1.2e9 would otherwise tokenise to a harmless-looking 1.2."""

        rows = [{"v": Decimal("1.2")}]

        assert find_ungrounded_numbers("Revenue hit 1.2e9.", rows) == ["1.2"]


class TestContiguousEnumerationIsTight:
    """The subtotal set must be exactly the adjacent runs, nothing wider.

    An audit widened _contiguous_sums by one and no test noticed. A loose
    enumeration silently grounds fabricated values.
    """

    def test_the_enumerated_set_is_exactly_the_adjacent_runs(self):
        from src.analysis_guard import _contiguous_sums

        values = [Decimal("1"), Decimal("2"), Decimal("4"), Decimal("8")]

        # Runs of length >= 2 only; single cells are already grounded.
        expected = {
            Decimal("3"),   # 1+2
            Decimal("6"),   # 2+4
            Decimal("12"),  # 4+8
            Decimal("7"),   # 1+2+4
            Decimal("14"),  # 2+4+8
            Decimal("15"),  # 1+2+4+8
        }

        assert _contiguous_sums(values) == expected

    def test_single_values_are_not_included(self):
        from src.analysis_guard import _contiguous_sums

        sums = _contiguous_sums([Decimal("5"), Decimal("9")])

        assert sums == {Decimal("14")}

    def test_an_off_by_one_sum_is_not_grounded(self):
        """Values above the structural-integer ceiling, so both are checked."""

        rows = [{"v": Decimal("100")}, {"v": Decimal("250")}]

        # 350 is the real subtotal; 351 must not be accepted.
        assert find_ungrounded_numbers("The pair totalled 350.", rows) == []
        assert find_ungrounded_numbers(
            "The pair totalled 351.", rows
        ) == ["351"]


class TestOnlyClaimBearingSectionsAreChecked:
    """LIMITATIONS and SUGGESTED FOLLOW-UP discuss data outside the result.

    The prompt asks for a follow-up suggestion, so the model routinely writes
    "compare against 2024" or "retrieve products ranked 6 through 15". Those
    are proposals, not assertions, and reporting them as hallucinations was
    the dominant false-positive class in the first live evaluation.
    """

    ROWS = [{"region": "Northeast", "v": Decimal("7356.42")}]

    ANALYSIS = """DIRECT ANSWER:
Northeast led with 7356.42.

KEY INSIGHTS:
- Northeast was the top region.

SUPPORTING NUMBERS:
- Northeast: 7356.42

LIMITATIONS:
Only 2025 is covered, so no 2024 comparison is possible.

SUGGESTED FOLLOW-UP:
Compare against 2024 and retrieve products ranked 6 through 15."""

    def test_figures_in_follow_up_are_not_reported(self):
        from src.analysis_contract import claim_bearing_text

        flagged = find_ungrounded_numbers(
            claim_bearing_text(self.ANALYSIS),
            self.ROWS,
            context=("regions in 2025",),
        )

        assert flagged == []

    def test_a_fabrication_in_a_claim_section_is_still_reported(self):
        from src.analysis_contract import claim_bearing_text

        analysis = self.ANALYSIS.replace(
            "Northeast led with 7356.42.",
            "Northeast led with 9999.99.",
        )

        flagged = find_ungrounded_numbers(
            claim_bearing_text(analysis),
            self.ROWS,
            context=("regions in 2025",),
        )

        assert flagged == ["9999.99"]

    def test_unstructured_text_is_checked_in_full(self):
        """A model that ignores the format must not escape the check."""

        from src.analysis_contract import claim_bearing_text

        analysis = "Sales were 9999.99 last year."

        assert claim_bearing_text(analysis) == analysis
        assert find_ungrounded_numbers(
            claim_bearing_text(analysis), self.ROWS
        ) == ["9999.99"]

    def test_sections_are_split_by_name(self):
        from src.analysis_contract import split_sections

        sections = split_sections(self.ANALYSIS)

        assert set(sections) == {
            "DIRECT ANSWER",
            "KEY INSIGHTS",
            "SUPPORTING NUMBERS",
            "LIMITATIONS",
            "SUGGESTED FOLLOW-UP",
        }
        assert "7356.42" in sections["DIRECT ANSWER"]
        assert "2024" in sections["SUGGESTED FOLLOW-UP"]


class TestDifferencesBetweenValues:
    """"X exceeded Y by Z" is a comparison an analyst makes routinely."""

    ROWS = [
        {"region": "Northeast", "v": Decimal("7356.42")},
        {"region": "West", "v": Decimal("7187.59")},
        {"region": "South", "v": Decimal("4384.61")},
    ]

    def test_a_correct_difference_is_accepted(self):
        # 7356.42 - 7187.59 = 168.83
        assert (
            find_ungrounded_numbers(
                "The Northeast exceeded the West by 168.83.", self.ROWS
            )
            == []
        )

    def test_a_wrong_difference_is_reported(self):
        """This exact error appeared in a live run."""

        assert find_ungrounded_numbers(
            "The Northeast exceeded the West by 169.83.", self.ROWS
        ) == ["169.83"]

    def test_a_non_adjacent_difference_is_accepted(self):
        # 7356.42 - 4384.61 = 2971.81
        assert (
            find_ungrounded_numbers(
                "Northeast beat South by 2971.81.", self.ROWS
            )
            == []
        )


class TestRealArithmeticErrorsFromLiveRuns:
    """Regression cases: every one of these was a real model mistake.

    Captured from live evaluation runs so the guard can never lose them.
    """

    MONTHLY = [
        {"m": f"2025-{i:02d}", "v": Decimal(str(v))}
        for i, v in enumerate(
            [
                1462.66, 1189.28, 2714.11, 2315.33, 1936.36, 1933.90,
                1694.03, 2057.30, 1860.04, 2105.37, 2574.25, 2958.02,
            ],
            start=1,
        )
    ]

    UNITS = [
        {"c": "Beverages", "q": 828},
        {"c": "Fruits", "q": 692},
        {"c": "Stationery", "q": 679},
    ]

    def test_q1_subtotal_off_by_one_dollar(self):
        # Real Q1 is 5366.05.
        assert find_ungrounded_numbers(
            "Q1 Total (Jan-Mar): $5,365.05", self.MONTHLY
        ) == ["$5,365.05"]

    def test_q4_subtotal_off_by_thirty_one_dollars(self):
        # Real Q4 is 7637.64.
        assert find_ungrounded_numbers(
            "Q4 Total (Oct-Dec): $7,668.64", self.MONTHLY
        ) == ["$7,668.64"]

    def test_year_total_off_by_ten_dollars(self):
        # Real total is 24800.65.
        assert find_ungrounded_numbers(
            "The total combined net revenue was $24,790.65.", self.MONTHLY
        ) == ["$24,790.65"]

    def test_unit_total_off_by_ten(self):
        # 828 + 692 + 679 = 2199.
        assert find_ungrounded_numbers(
            "The top three categories account for 2,209 units.", self.UNITS
        ) == ["2,209"]

    def test_the_correct_versions_all_pass(self):
        assert (
            find_ungrounded_numbers(
                "Q1 was $5,366.05, Q4 was $7,637.64, the year $24,800.65.",
                self.MONTHLY,
            )
            == []
        )
        assert (
            find_ungrounded_numbers(
                "The top three account for 2,199 units.", self.UNITS
            )
            == []
        )


class TestHedgedApproximations:
    """A hedge licenses rounding, not invention."""

    ROWS = [
        {"region": "Northeast", "v": Decimal("7356.42")},
        {"region": "West", "v": Decimal("7187.59")},
    ]
    MONTH = [{"month": "2025-01", "v": Decimal("1462.66")}]

    def test_a_true_hedged_claim_is_accepted(self):
        """Real combined revenue is 14544.01, so "over 14,500" is true."""

        assert (
            find_ungrounded_numbers(
                "The two regions collectively generated over 14,500.",
                self.ROWS,
            )
            == []
        )

    def test_a_rounded_hedged_figure_is_accepted(self):
        assert (
            find_ungrounded_numbers(
                "January was roughly $1,463.", self.MONTH
            )
            == []
        )

    def test_the_same_figure_unhedged_is_reported(self):
        """Without the hedge it is a precise claim, and a wrong one."""

        assert find_ungrounded_numbers(
            "January was $1,463.", self.MONTH
        ) == ["$1,463"]

    def test_a_hedge_does_not_license_invention(self):
        """3% out is beyond rounding."""

        assert find_ungrounded_numbers(
            "Approximately 24,000 in total.", self.MONTH
        ) == ["24,000"]

    @pytest.mark.parametrize(
        "hedge",
        ["about", "approximately", "roughly", "around", "nearly",
         "over", "more than", "at least"],
    )
    def test_common_hedges_are_recognised(self, hedge):
        assert (
            find_ungrounded_numbers(
                f"Revenue was {hedge} 14,500.", self.ROWS
            )
            == []
        )

    def test_an_upper_bound_the_data_contradicts_is_reported(self):
        """Combined revenue is 14,544.01, so "under 14,500" is false."""

        assert find_ungrounded_numbers(
            "Revenue was under 14,500.", self.ROWS
        ) == ["14,500"]


class TestShareAndBoundSemantics:
    """Shares of a total, and hedges judged as the claim they make."""

    REGIONS = [
        {"region": "Northeast", "v": Decimal("7356.42")},
        {"region": "West", "v": Decimal("7187.59")},
        {"region": "Midwest", "v": Decimal("5872.03")},
        {"region": "South", "v": Decimal("4384.61")},
    ]
    CHANNELS = [
        {"channel": "In-Store", "v": Decimal("14602.22")},
        {"channel": "Online", "v": Decimal("7046.20")},
        {"channel": "Click-and-Collect", "v": Decimal("3152.23")},
    ]

    def test_a_single_share_of_total_is_accepted(self):
        # 7356.42 / 24800.65 = 29.66%
        assert (
            find_ungrounded_numbers(
                "Northeast alone is 29.66% of revenue.", self.REGIONS
            )
            == []
        )

    def test_a_subtotal_share_is_accepted(self):
        # (7356.42 + 7187.59) / 24800.65 = 58.64%
        assert (
            find_ungrounded_numbers(
                "The Northeast and West are 58.64% of revenue.",
                self.REGIONS,
            )
            == []
        )

    def test_a_true_lower_bound_is_accepted(self):
        """"over 58%" is satisfied by a real 58.64%."""

        assert (
            find_ungrounded_numbers(
                "They account for over 58% of revenue.", self.REGIONS
            )
            == []
        )

    def test_a_wrong_share_is_reported(self):
        """Online is 28.4%; a claimed 48% is a real error from a live run."""

        assert find_ungrounded_numbers(
            "Online represents approximately 48% of revenue.",
            self.CHANNELS,
        ) == ["48%"]

    def test_a_bound_is_not_satisfied_by_an_unrelated_larger_value(self):
        """Without an upper limit any bigger number would satisfy it."""

        assert find_ungrounded_numbers(
            "Revenue was over 100.", self.REGIONS
        ) == ["100"]

    def test_the_guard_checks_values_not_attributions(self):
        """A documented limitation, pinned so it stays a known one.

        "Online is over 80%" passes because In-Store plus Online really is
        87.3%. The figure exists; the sentence attaches it to the wrong
        entity, which needs claim parsing rather than number checking.
        """

        assert (
            find_ungrounded_numbers(
                "Online is over 80% of revenue.", self.CHANNELS
            )
            == []
        )
