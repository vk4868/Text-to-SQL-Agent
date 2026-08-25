"""Governance: the guardrails refuse hostile SQL regardless of its origin.

These run the real SQLExecutionPipeline against the real validators with no
LLM and no BigQuery — stages 1 to 3 are pure parsing, so a refusal here proves
nothing reached the warehouse. One source of truth: the same YAML drives the
evaluation report's guardrail section.
"""

import pytest

from evaluation.cases import load_attack_cases
from tests.fakes.bigquery import FakeBigQueryService

ATTACK_CASES = load_attack_cases()


def case_id(case):
    return case.case_id


@pytest.fixture
def pipeline():
    from src.sql_execution_pipeline import SQLExecutionPipeline

    # Configured to match the dataset the attack cases name.
    return SQLExecutionPipeline(
        bigquery_service=FakeBigQueryService(
            project_id="sql-bigquery-502206",
            dataset_id="business_insights",
        )
    )


class TestAdversarialQueriesAreRefused:
    @pytest.mark.parametrize("case", ATTACK_CASES, ids=case_id)
    def test_case_is_refused_at_the_expected_stage(self, case, pipeline):
        result = pipeline.execute(case.sql)

        assert result["status"] == case.expected_status, (
            f"{case.case_id}: expected {case.expected_status}, got "
            f"{result['status']} at stage {result['stage']} — {case.description}"
        )
        assert result["stage"] == case.expected_stage, (
            f"{case.case_id}: expected refusal at stage "
            f"{case.expected_stage}, got {result['stage']}"
        )

    @pytest.mark.parametrize("case", ATTACK_CASES, ids=case_id)
    def test_nothing_reached_bigquery(self, case, pipeline):
        """The refusal must happen before any dry run or execution."""

        pipeline.execute(case.sql)

        service = pipeline.bigquery_service

        assert service.dry_run_calls == [], (
            f"{case.case_id} reached the BigQuery dry run"
        )
        assert service.run_query_calls == [], (
            f"{case.case_id} reached BigQuery execution"
        )

    @pytest.mark.parametrize("case", ATTACK_CASES, ids=case_id)
    def test_refusal_is_structured_not_an_exception(self, case, pipeline):
        result = pipeline.execute(case.sql)

        assert "error_type" in result
        assert "message" in result
        assert result["message"]


class TestTheSuiteItself:
    def test_every_case_has_a_description(self):
        undocumented = [
            case.case_id for case in ATTACK_CASES if not case.description
        ]

        assert not undocumented, (
            f"attack cases need a description explaining the threat: "
            f"{undocumented}"
        )

    def test_the_threat_classes_are_all_covered(self):
        stages = {case.expected_stage for case in ATTACK_CASES}

        assert stages == {"validation", "table_access", "result_limit"}

    def test_there_are_enough_cases_to_be_meaningful(self):
        assert len(ATTACK_CASES) >= 15


class TestComputedLimitIsFoldedNotRejected:
    """A computed LIMIT is bounded by a different mechanism than expected.

    validate_read_only_sql normalises via sqlglot, which constant-folds
    `LIMIT 10 + 90` into `LIMIT 100` before enforce_result_limit ever sees it.
    So the validator's "no calculated LIMIT" rule is unreachable through the
    pipeline for foldable arithmetic — it still fires for a parameter, which
    genuinely cannot be resolved.

    The guarantee that matters is unaffected: the row bound is enforced either
    way. These tests pin that, so a future change to normalisation cannot
    quietly remove the bound.
    """

    def test_a_computed_limit_is_folded_and_capped(self, pipeline):
        result = pipeline.execute(
            "SELECT sale_id "
            "FROM `sql-bigquery-502206.business_insights.fact_sales` "
            "LIMIT 10 + 90"
        )

        assert result["status"] == "success"
        assert result["result_row_limit"] == 100
        assert "LIMIT 100" in result["executed_sql"]

    def test_an_over_cap_computed_limit_is_reduced(self, pipeline):
        """The bound holds even when the folded value exceeds the cap."""

        result = pipeline.execute(
            "SELECT sale_id "
            "FROM `sql-bigquery-502206.business_insights.fact_sales` "
            "LIMIT 50 + 500"
        )

        assert result["status"] == "success"
        assert result["limit_was_modified"] is True
        assert result["result_row_limit"] == 100
        assert "LIMIT 100" in result["executed_sql"]

    def test_a_parameterised_limit_is_still_refused(self, pipeline):
        """A parameter cannot be folded, so the rule still applies."""

        result = pipeline.execute(
            "SELECT sale_id "
            "FROM `sql-bigquery-502206.business_insights.fact_sales` "
            "LIMIT @row_count"
        )

        assert result["status"] == "rejected"
        assert result["stage"] == "result_limit"

    def test_the_validator_alone_still_rejects_computed_limits(self):
        """Called directly on raw SQL, the rule does fire."""

        from src.sql_validator import enforce_result_limit

        result = enforce_result_limit(
            "SELECT a FROM `p.d.t` LIMIT 10 + 90", max_rows=100
        )

        assert result.is_valid is False
