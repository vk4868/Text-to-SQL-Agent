"""What the reader actually sees, including when the model misbehaves."""

from decimal import Decimal

from src.llm.scripted import ScriptedLLMClient
from src.presentation import (
    format_grounding_warning,
    format_rows,
    format_run_for_terminal,
)
from tests.fakes import data
from tests.fakes.bigquery import FakeBigQueryService
from tests.fakes.factories import WELL_FORMED_ANALYSIS


def make_agent(**kwargs):
    from src.agent import InsightsAgent

    kwargs.setdefault("relationships", list(data.RELATIONSHIPS))
    return InsightsAgent(**kwargs)


class TestRowRendering:
    def test_columns_are_aligned(self):
        rendered = format_rows(
            [
                {"month": "2025-01", "total": Decimal("1462.66")},
                {"month": "2025-12", "total": Decimal("2958.02")},
            ]
        )

        lines = rendered.splitlines()

        assert lines[0].startswith("month")
        assert set(lines[1]) <= {"-", " "}
        assert "2025-01" in lines[2]

    def test_empty_rows_say_so(self):
        assert format_rows([]) == "(no rows returned)"

    def test_extra_rows_are_summarised(self):
        rendered = format_rows(
            [{"n": i} for i in range(25)], max_rows=10
        )

        assert "... 15 more row(s)" in rendered

    def test_none_renders_as_null(self):
        assert "NULL" in format_rows([{"a": None}])


class TestGroundingWarning:
    def test_ungrounded_numbers_are_surfaced(self):
        warning = format_grounding_warning(
            {"ungrounded_numbers": ["$48,300.00"]}
        )

        assert "NOT DERIVABLE FROM THE RESULT" in warning
        assert "$48,300.00" in warning

    def test_contract_violations_are_surfaced(self):
        warning = format_grounding_warning(
            {"contract_violations": ["Missing required section: LIMITATIONS"]}
        )

        assert "FORMAT" in warning
        assert "LIMITATIONS" in warning

    def test_a_clean_analysis_produces_no_warning(self):
        assert format_grounding_warning(
            {"ungrounded_numbers": [], "contract_violations": []}
        ) == ""

    def test_missing_keys_produce_no_warning(self):
        assert format_grounding_warning({}) == ""


class TestEndToEndRendering:
    def test_an_invented_figure_reaches_the_reader(self):
        """The whole point: the reader is told what the data did not support."""

        analysis = WELL_FORMED_ANALYSIS + "\nA grand total of $99,123.45."

        agent = make_agent(
            bigquery_service=FakeBigQueryService(),
            llm=ScriptedLLMClient([data.VALID_SQL, analysis]),
        )

        record = agent.run("What were net sales by month?")
        rendered = format_run_for_terminal(record)

        assert record["analysis"]["is_grounded"] is False
        assert "$99,123.45" in record["analysis"]["ungrounded_numbers"]
        assert "NOT DERIVABLE FROM THE RESULT" in rendered

    def test_a_grounded_analysis_renders_without_a_warning(self):
        agent = make_agent(
            bigquery_service=FakeBigQueryService(),
            llm=ScriptedLLMClient([data.VALID_SQL, WELL_FORMED_ANALYSIS]),
        )

        record = agent.run("What were net sales by month?")
        rendered = format_run_for_terminal(record)

        assert record["analysis"]["is_grounded"] is True
        assert "NOT DERIVABLE" not in rendered

    def test_a_zero_row_result_is_answered_without_the_model(self):
        llm = ScriptedLLMClient([data.VALID_SQL])
        agent = make_agent(
            bigquery_service=FakeBigQueryService(rows=[]),
            llm=llm,
        )

        record = agent.run("What were net sales in 1999?")
        rendered = format_run_for_terminal(record)

        assert record["outcome"]["status"] == "success"
        # Only the SQL-generation call: analysis never reached the model.
        assert llm.call_count == 1
        assert record["analysis"]["was_generated_deterministically"] is True
        assert "no rows" in record["analysis"]["business_analysis"]
        assert "(no rows returned)" in rendered

    def test_zero_row_run_records_only_one_llm_call(self):
        agent = make_agent(
            bigquery_service=FakeBigQueryService(rows=[]),
            llm=ScriptedLLMClient([data.VALID_SQL]),
        )

        record = agent.run("What were net sales in 1999?")

        assert record["tokens"]["totals"]["llm_call_count"] == 1
        assert [
            call["purpose"] for call in record["tokens"]["llm_calls"]
        ] == ["sql_generation"]

    def test_a_failed_run_still_renders(self):
        from src.exceptions import LLMProviderError

        agent = make_agent(
            bigquery_service=FakeBigQueryService(),
            llm=ScriptedLLMClient([LLMProviderError("model is down")]),
        )

        record = agent.run("anything")
        rendered = format_run_for_terminal(record)

        assert "ERROR at stage 'sql_generation'" in rendered
        assert "model is down" in rendered


class TestChecksSurviveTheRunRecordBoundary:
    """Both checks must reach the durable record, not just the analyzer.

    An audit mutation dropped contract_violations at the analysis_metadata ->
    run_record boundary and nothing failed; ungrounded_numbers was covered but
    its sibling was not.
    """

    def test_contract_violations_reach_the_record(self):
        # Well-formed apart from a missing LIMITATIONS section.
        analysis = WELL_FORMED_ANALYSIS.replace(
            "LIMITATIONS:\nNone identified from the supplied result.", ""
        )

        agent = make_agent(
            bigquery_service=FakeBigQueryService(),
            llm=ScriptedLLMClient([data.VALID_SQL, analysis]),
        )

        record = agent.run("What were net sales by month?")

        violations = record["analysis"]["contract_violations"]

        assert violations, (
            "contract_violations did not survive into the run record"
        )
        assert any("LIMITATIONS" in item for item in violations)
        assert record["analysis"]["is_grounded"] is False

    def test_contract_violations_reach_the_terminal(self):
        analysis = WELL_FORMED_ANALYSIS.replace(
            "SUGGESTED FOLLOW-UP:\nBreak the same period down by product "
            "category.",
            "",
        )

        agent = make_agent(
            bigquery_service=FakeBigQueryService(),
            llm=ScriptedLLMClient([data.VALID_SQL, analysis]),
        )

        rendered = format_run_for_terminal(
            agent.run("What were net sales by month?")
        )

        assert "FORMAT:" in rendered
        assert "SUGGESTED FOLLOW-UP" in rendered
