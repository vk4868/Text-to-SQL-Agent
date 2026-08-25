"""The scorers decide what "correct" means, so they get tested hardest.

A scorer that is too lenient makes the whole evaluation a rubber stamp; one
that is too strict fails correct answers for cosmetic reasons. Both failure
modes are covered here.
"""

from datetime import date
from decimal import Decimal

import pytest

from evaluation.scorers import (
    normalise_cell,
    score_analysis_grounding,
    score_execution_accuracy,
    score_guardrail_pass,
    score_executed,
    score_repair_efficiency,
    score_table_grounding,
)

REFERENCE = [
    {"month": "2025-01", "total": Decimal("1462.66")},
    {"month": "2025-02", "total": Decimal("1189.28")},
]


def record(**overrides):
    base = {
        "outcome": {"status": "success", "terminal_stage": "complete"},
        "sql": {
            "referenced_tables": ["p.d.fact_sales"],
            "repair_attempts": 0,
        },
        "analysis": {"ungrounded_numbers": [], "contract_violations": []},
    }
    base.update(overrides)
    return base


class TestNormalisation:
    @pytest.mark.parametrize(
        "left,right",
        [
            (Decimal("1462.66"), 1462.66),
            (Decimal("1462.664"), Decimal("1462.66")),
            ("1462.66", Decimal("1462.66")),
            (1462, Decimal("1462.00")),
            (date(2025, 1, 1), "2025-01-01"),
        ],
    )
    def test_equivalent_values_normalise_equal(self, left, right):
        quantum = Decimal("0.01")

        assert normalise_cell(left, quantum) == normalise_cell(
            right, quantum
        )

    def test_none_is_distinguishable_from_zero(self):
        quantum = Decimal("0.01")

        assert normalise_cell(None, quantum) != normalise_cell(0, quantum)

    def test_negative_zero_equals_zero(self):
        quantum = Decimal("0.01")

        assert normalise_cell(-0.0, quantum) == normalise_cell(
            0.0, quantum
        )

    def test_text_is_preserved(self):
        assert normalise_cell("  Beverages  ", Decimal("0.01")) == (
            "Beverages"
        )


class TestExecutionAccuracyAcceptsCorrectAnswers:
    """Cosmetic differences must not fail a correct answer."""

    def test_identical_rows_pass(self):
        assert score_execution_accuracy(REFERENCE, REFERENCE)

    def test_different_column_names_pass(self):
        actual = [
            {"m": "2025-01", "net_sales": Decimal("1462.66")},
            {"m": "2025-02", "net_sales": Decimal("1189.28")},
        ]

        assert score_execution_accuracy(actual, REFERENCE)

    def test_different_column_order_passes(self):
        actual = [
            {"total": Decimal("1462.66"), "month": "2025-01"},
            {"total": Decimal("1189.28"), "month": "2025-02"},
        ]

        assert score_execution_accuracy(actual, REFERENCE)

    def test_different_row_order_passes_a_multiset_comparison(self):
        actual = list(reversed(REFERENCE))

        assert score_execution_accuracy(
            actual, REFERENCE, comparison="multiset"
        )

    def test_float_versus_decimal_passes(self):
        actual = [
            {"month": "2025-01", "total": 1462.66},
            {"month": "2025-02", "total": 1189.28},
        ]

        assert score_execution_accuracy(actual, REFERENCE)

    def test_a_difference_inside_tolerance_passes(self):
        actual = [
            {"month": "2025-01", "total": Decimal("1462.664")},
            {"month": "2025-02", "total": Decimal("1189.283")},
        ]

        assert score_execution_accuracy(actual, REFERENCE)


class TestExecutionAccuracyRejectsWrongAnswers:
    def test_a_wrong_number_fails(self):
        actual = [
            {"month": "2025-01", "total": Decimal("9999.99")},
            {"month": "2025-02", "total": Decimal("1189.28")},
        ]

        score = score_execution_accuracy(actual, REFERENCE)

        assert not score
        assert "9999.99" in score.detail

    def test_a_missing_row_fails(self):
        score = score_execution_accuracy(REFERENCE[:1], REFERENCE)

        assert not score
        assert "row count 1 vs 2" in score.detail

    def test_an_extra_row_fails(self):
        actual = REFERENCE + [
            {"month": "2025-03", "total": Decimal("1.00")}
        ]

        assert not score_execution_accuracy(actual, REFERENCE)

    def test_null_instead_of_a_value_fails(self):
        actual = [
            {"month": "2025-01", "total": None},
            {"month": "2025-02", "total": Decimal("1189.28")},
        ]

        assert not score_execution_accuracy(actual, REFERENCE)

    def test_a_difference_outside_tolerance_fails(self):
        actual = [
            {"month": "2025-01", "total": Decimal("1462.70")},
            {"month": "2025-02", "total": Decimal("1189.28")},
        ]

        assert not score_execution_accuracy(actual, REFERENCE)

    def test_wrong_order_fails_an_ordered_comparison(self):
        """A "top 5" question is about the ranking, so order matters."""

        actual = list(reversed(REFERENCE))

        score = score_execution_accuracy(
            actual, REFERENCE, comparison="ordered"
        )

        assert not score
        assert "row 0" in score.detail

    def test_empty_result_versus_populated_fails(self):
        assert not score_execution_accuracy([], REFERENCE)

    def test_two_empty_results_match(self):
        assert score_execution_accuracy([], [])


class TestScalarComparison:
    def test_matching_scalars_pass(self):
        assert score_execution_accuracy(
            [{"total": Decimal("24800.65")}],
            [{"t": 24800.65}],
            comparison="scalar",
        )

    def test_differing_scalars_fail(self):
        assert not score_execution_accuracy(
            [{"total": Decimal("24800.65")}],
            [{"t": Decimal("24800.66")}],
            comparison="scalar",
        )

    def test_multiple_rows_fail_a_scalar_comparison(self):
        score = score_execution_accuracy(
            REFERENCE, [{"t": 1}], comparison="scalar"
        )

        assert not score
        assert "one row each" in score.detail


class TestOtherScorers:
    def test_guardrail_pass_fails_on_a_refusal(self):
        score = score_guardrail_pass(
            record(
                outcome={
                    "status": "rejected",
                    "terminal_stage": "table_access",
                }
            )
        )

        assert not score
        assert "table_access" in score.detail

    def test_guardrail_pass_succeeds_on_an_ordinary_error(self):
        """An infrastructure error is not a guardrail refusal."""

        assert score_guardrail_pass(
            record(
                outcome={
                    "status": "error",
                    "terminal_stage": "execution_timeout",
                }
            )
        )

    def test_executed_fails_on_a_non_success(self):
        assert not score_executed(
            record(
                outcome={"status": "error", "terminal_stage": "dry_run"}
            )
        )

    def test_table_grounding_detects_the_wrong_table(self):
        score = score_table_grounding(
            record(sql={"referenced_tables": ["p.d.dim_products"]}),
            ["p.d.fact_sales"],
        )

        assert not score
        assert "missing" in score.detail

    def test_table_grounding_detects_an_extra_table(self):
        score = score_table_grounding(
            record(
                sql={
                    "referenced_tables": [
                        "p.d.fact_sales",
                        "p.d.dim_customers",
                    ]
                }
            ),
            ["p.d.fact_sales"],
        )

        assert not score
        assert "unexpected" in score.detail

    def test_table_grounding_is_order_insensitive(self):
        assert score_table_grounding(
            record(
                sql={
                    "referenced_tables": [
                        "p.d.dim_products",
                        "p.d.fact_sales",
                    ]
                }
            ),
            ["p.d.fact_sales", "p.d.dim_products"],
        )

    def test_repair_efficiency_respects_the_budget(self):
        assert score_repair_efficiency(
            record(sql={"repair_attempts": 2}), 2
        )
        assert not score_repair_efficiency(
            record(sql={"repair_attempts": 3}), 2
        )

    def test_analysis_grounding_fails_on_an_invented_figure(self):
        score = score_analysis_grounding(
            record(
                analysis={
                    "ungrounded_numbers": ["$48,300.00"],
                    "contract_violations": [],
                }
            )
        )

        assert not score
        assert "48,300" in score.detail

    def test_analysis_grounding_fails_on_a_format_violation(self):
        assert not score_analysis_grounding(
            record(
                analysis={
                    "ungrounded_numbers": [],
                    "contract_violations": ["Missing section: LIMITATIONS"],
                }
            )
        )

    def test_analysis_grounding_passes_a_clean_analysis(self):
        assert score_analysis_grounding(record())


class TestDatasetIntegrity:
    """The case files themselves must be valid and meaningful."""

    def test_golden_cases_load(self):
        from evaluation.cases import load_golden_cases

        cases = load_golden_cases()

        assert len(cases) >= 12

    def test_every_golden_case_names_its_tables(self):
        from evaluation.cases import load_golden_cases

        missing = [
            case.case_id
            for case in load_golden_cases()
            if not case.expected_tables
        ]

        assert not missing

    def test_every_golden_case_has_a_reference_query(self):
        from evaluation.cases import load_golden_cases

        missing = [
            case.case_id
            for case in load_golden_cases()
            if case.comparison != "none" and not case.reference_sql
        ]

        assert not missing

    def test_categories_are_varied(self):
        from evaluation.cases import load_golden_cases

        categories = {case.category for case in load_golden_cases()}

        assert len(categories) >= 5

    def test_duplicate_case_ids_are_rejected(self, tmp_path):
        from evaluation.cases import load_golden_cases

        bad = tmp_path / "dupes.yaml"
        bad.write_text(
            "- case_id: a\n"
            "  question: q\n"
            "  category: c\n"
            "  expect: {comparison: none}\n"
            "- case_id: a\n"
            "  question: q\n"
            "  category: c\n"
            "  expect: {comparison: none}\n"
        )

        with pytest.raises(ValueError, match="Duplicate case_id"):
            load_golden_cases(bad)

    def test_an_unknown_comparison_is_rejected(self, tmp_path):
        from evaluation.cases import load_golden_cases

        bad = tmp_path / "bad.yaml"
        bad.write_text(
            "- case_id: a\n"
            "  question: q\n"
            "  category: c\n"
            "  expect: {comparison: vibes}\n"
        )

        with pytest.raises(ValueError, match="comparison must be"):
            load_golden_cases(bad)
