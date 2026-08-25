"""The harness and report, driven entirely by fakes.

An evaluation that only runs live is one nobody runs. These prove the
scoring, the pass/fail logic and the report render correctly without
BigQuery or a model.
"""

from decimal import Decimal

import pytest

from evaluation.cases import GoldenCase
from evaluation.harness import evaluate_case
from evaluation.report import build_json_report, render_markdown
from evaluation.run_evaluation import run_guardrail_suite
from src.agent import InsightsAgent
from src.llm.scripted import ScriptedLLMClient
from src.sql_execution_pipeline import SQLExecutionPipeline
from tests.fakes import data
from tests.fakes.bigquery import FakeBigQueryService
from tests.fakes.factories import WELL_FORMED_ANALYSIS

AGENT_SQL = data.VALID_SQL
REFERENCE_SQL = (
    "SELECT FORMAT_DATE('%Y-%m', sale_date) AS m, "
    "SUM(net_revenue) AS v "
    f"FROM `{data.DATASET_PATH}.fact_sales` GROUP BY m"
)


def make_case(**overrides) -> GoldenCase:
    defaults = dict(
        case_id="demo_case",
        question="What were net sales by month?",
        category="aggregation",
        reference_sql=REFERENCE_SQL,
        expected_tables=[f"{data.DATASET_PATH}.fact_sales"],
        comparison="multiset",
        numeric_tolerance=0.01,
        max_repair_attempts=2,
    )
    defaults.update(overrides)
    return GoldenCase(**defaults)


def make_agent(service, llm) -> InsightsAgent:
    return InsightsAgent(
        bigquery_service=service,
        llm=llm,
        relationships=list(data.RELATIONSHIPS),
    )


class TestScoringAPassingCase:
    def test_a_correct_answer_passes_every_metric(self):
        service = FakeBigQueryService()
        agent = make_agent(
            service, ScriptedLLMClient([AGENT_SQL, WELL_FORMED_ANALYSIS])
        )

        result = evaluate_case(
            make_case(),
            agent=agent,
            execution_pipeline=SQLExecutionPipeline(
                bigquery_service=service
            ),
        )

        assert result.passed
        assert {score.name for score in result.scores.scores} == {
            "guardrail_pass",
            "executed",
            "table_grounding",
            "execution_accuracy",
            "repair_efficiency",
            "analysis_grounding",
        }

    def test_the_reference_query_runs_through_the_same_pipeline(self):
        """Both sides must be subject to the same caps and normalisation."""

        service = FakeBigQueryService()
        agent = make_agent(
            service, ScriptedLLMClient([AGENT_SQL, WELL_FORMED_ANALYSIS])
        )

        evaluate_case(
            make_case(),
            agent=agent,
            execution_pipeline=SQLExecutionPipeline(
                bigquery_service=service
            ),
        )

        # One execution for the agent, one for the reference.
        assert len(service.run_query_calls) == 2


class TestScoringFailures:
    def test_a_wrong_result_set_fails_execution_accuracy(self):
        """The agent and the reference return different numbers."""

        service = FakeBigQueryService()

        # The reference query gets different rows than the agent's query.
        # Keyed on the reference's distinctive alias — note "GROUP BY m"
        # would also match the agent's "GROUP BY month".
        def rows_for(sql):
            if "AS v" in sql:
                return [{"m": "2025-01", "v": Decimal("999.99")}]
            return list(data.MONTHLY_SALES_ROWS)

        original = service.run_query

        def patched(sql, **kwargs):
            service.rows = rows_for(sql)
            return original(sql, **kwargs)

        service.run_query = patched  # type: ignore[method-assign]

        agent = make_agent(
            service, ScriptedLLMClient([AGENT_SQL, WELL_FORMED_ANALYSIS])
        )

        result = evaluate_case(
            make_case(),
            agent=agent,
            execution_pipeline=SQLExecutionPipeline(
                bigquery_service=service
            ),
        )

        assert not result.passed
        assert not result.scores.by_name()["execution_accuracy"].passed

    def test_a_guardrail_refusal_fails_and_skips_later_metrics(self):
        service = FakeBigQueryService()
        agent = make_agent(
            service,
            ScriptedLLMClient(
                ["SELECT SUM(net_revenue) FROM fact_sales"] * 4
            ),
        )

        result = evaluate_case(
            make_case(),
            agent=agent,
            execution_pipeline=SQLExecutionPipeline(
                bigquery_service=service
            ),
        )

        by_name = result.scores.by_name()

        assert not result.passed
        assert not by_name["guardrail_pass"].passed
        # Downstream metrics are not scored on a run that never executed.
        assert "execution_accuracy" not in by_name

    def test_an_invented_figure_fails_analysis_grounding(self):
        service = FakeBigQueryService()
        analysis = WELL_FORMED_ANALYSIS + "\nA total of $77,777.77."

        agent = make_agent(
            service, ScriptedLLMClient([AGENT_SQL, analysis])
        )

        result = evaluate_case(
            make_case(),
            agent=agent,
            execution_pipeline=SQLExecutionPipeline(
                bigquery_service=service
            ),
        )

        assert not result.passed
        assert not result.scores.by_name()["analysis_grounding"].passed

    def test_the_wrong_table_fails_table_grounding(self):
        service = FakeBigQueryService()
        agent = make_agent(
            service, ScriptedLLMClient([AGENT_SQL, WELL_FORMED_ANALYSIS])
        )

        result = evaluate_case(
            make_case(
                expected_tables=[f"{data.DATASET_PATH}.dim_products"]
            ),
            agent=agent,
            execution_pipeline=SQLExecutionPipeline(
                bigquery_service=service
            ),
        )

        assert not result.scores.by_name()["table_grounding"].passed

    def test_a_broken_reference_query_fails_loudly(self):
        """A dataset fault must not silently score as a pass."""

        service = FakeBigQueryService()
        agent = make_agent(
            service, ScriptedLLMClient([AGENT_SQL, WELL_FORMED_ANALYSIS])
        )

        result = evaluate_case(
            make_case(reference_sql="DELETE FROM `p.d.t` WHERE TRUE"),
            agent=agent,
            execution_pipeline=SQLExecutionPipeline(
                bigquery_service=service
            ),
        )

        score = result.scores.by_name()["execution_accuracy"]

        assert not score.passed
        assert "reference query failed" in score.detail


class TestGuardrailSuite:
    def test_every_adversarial_case_is_refused(self):
        pipeline = SQLExecutionPipeline(
            bigquery_service=FakeBigQueryService(
                project_id="sql-bigquery-502206",
                dataset_id="business_insights",
            )
        )

        summary = run_guardrail_suite(pipeline)

        assert summary["blocked"] == summary["total"]
        assert summary["failures"] == []
        assert summary["total"] >= 15


class TestReporting:
    @pytest.fixture
    def results(self):
        service = FakeBigQueryService()
        pipeline = SQLExecutionPipeline(bigquery_service=service)

        passing = evaluate_case(
            make_case(case_id="passing_case"),
            agent=make_agent(
                service,
                ScriptedLLMClient([AGENT_SQL, WELL_FORMED_ANALYSIS]),
            ),
            execution_pipeline=pipeline,
        )

        failing = evaluate_case(
            make_case(
                case_id="failing_case",
                expected_tables=["p.d.nonexistent"],
            ),
            agent=make_agent(
                service,
                ScriptedLLMClient([AGENT_SQL, WELL_FORMED_ANALYSIS]),
            ),
            execution_pipeline=pipeline,
        )

        return [passing, failing]

    def test_markdown_reports_the_pass_rate(self, results):
        markdown = render_markdown(results, model_name="test-model")

        assert "# Evaluation report" in markdown
        assert "Passed: **1**" in markdown
        assert "test-model" in markdown

    def test_markdown_lists_every_case(self, results):
        markdown = render_markdown(results)

        assert "passing_case" in markdown
        assert "failing_case" in markdown

    def test_markdown_explains_each_failure(self, results):
        markdown = render_markdown(results)

        assert "## Failures" in markdown
        assert "table_grounding" in markdown
        # The agent's SQL is shown so the failure is actionable.
        assert "SELECT" in markdown

    def test_markdown_includes_the_guardrail_section(self, results):
        markdown = render_markdown(
            results,
            attack_summary={
                "total": 19,
                "blocked": 19,
                "by_stage": {"validation": 11, "table_access": 7},
            },
        )

        assert "19/19" in markdown
        assert "before a byte was" in markdown

    def test_json_report_round_trips(self, results):
        import json

        payload = build_json_report(results, model_name="test-model")

        restored = json.loads(json.dumps(payload, default=str))

        assert restored["summary"]["cases"] == 2
        assert restored["summary"]["passed"] == 1
        assert len(restored["cases"]) == 2

    def test_summary_reports_latency_and_tokens(self, results):
        payload = build_json_report(results)
        summary = payload["summary"]

        assert summary["latency_ms"]["mean"] >= 0
        assert summary["tokens"]["total"] > 0
        assert "p95" in summary["latency_ms"]
