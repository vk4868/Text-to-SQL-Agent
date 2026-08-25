"""The CLI is the project's front door, so its contract is pinned here.

Exit codes matter: the evaluation harness and any shell pipeline distinguish
"answered" from "refused or broken" by them.
"""

import json

import pytest

from src.cli import EXIT_FAILED, EXIT_OK, EXIT_USAGE, main
from src.llm.scripted import ScriptedLLMClient
from tests.fakes import data
from tests.fakes.factories import WELL_FORMED_ANALYSIS


class FakeAgent:
    """Return a canned run record without touching the graph."""

    def __init__(self, record):
        self.record = record
        self.questions = []

    def run(self, question):
        self.questions.append(question)
        return self.record


def success_record():
    return {
        "question": "What were net sales by month?",
        "outcome": {
            "status": "success",
            "terminal_stage": "complete",
            "error_message": None,
        },
        "sql": {
            "final_sql": data.VALID_SQL,
            "repair_attempts": 1,
            "repair_history": [],
        },
        "data": {"row_count": 3},
        "cost": {"total_bytes_billed": 24010},
        "runtime": {
            "total_duration_ms": 1234.5,
            "node_trace": [
                {"node": "get_schema", "duration_ms": 10.0, "ok": True},
                {"node": "generate_sql", "duration_ms": 20.0, "ok": True},
            ],
        },
        "tokens": {"totals": {"total_tokens": 840}},
        "analysis": {"business_analysis": WELL_FORMED_ANALYSIS},
        "rows": list(data.MONTHLY_SALES_ROWS),
    }


def failure_record():
    return {
        "question": "drop everything",
        "outcome": {
            "status": "rejected",
            "terminal_stage": "table_access",
            "error_message": "Table 'other.x.y' is not allowed.",
        },
        "sql": {
            "final_sql": None,
            "repair_attempts": 2,
            "repair_history": [{"repair_attempt": 1}, {"repair_attempt": 2}],
        },
        "data": {"row_count": None},
        "cost": {},
        "runtime": {"total_duration_ms": 900.0, "node_trace": []},
        "tokens": {"totals": {"total_tokens": 120}},
        "analysis": {},
        "rows": [],
    }


class TestExitCodes:
    def test_success_exits_zero(self, capsys):
        code = main(["a question"], agent=FakeAgent(success_record()))

        assert code == EXIT_OK
        assert "DIRECT ANSWER" in capsys.readouterr().out

    def test_rejection_exits_one_and_reports_the_stage(self, capsys):
        code = main(["a question"], agent=FakeAgent(failure_record()))

        captured = capsys.readouterr()

        assert code == EXIT_FAILED
        assert "table_access" in captured.err
        assert "REJECTED" in captured.err

    def test_missing_question_is_a_usage_error(self, capsys):
        code = main([], agent=FakeAgent(success_record()))

        assert code == EXIT_USAGE
        assert "question is required" in capsys.readouterr().err

    def test_blank_question_is_a_usage_error(self, capsys):
        code = main(["   "], agent=FakeAgent(success_record()))

        assert code == EXIT_USAGE


class TestOutputModes:
    def test_json_mode_emits_the_whole_record(self, capsys):
        code = main(
            ["a question", "--json"], agent=FakeAgent(success_record())
        )

        payload = json.loads(capsys.readouterr().out)

        assert code == EXIT_OK
        assert payload["outcome"]["status"] == "success"
        assert payload["tokens"]["totals"]["total_tokens"] == 840

    def test_sql_only_mode_emits_just_the_sql(self, capsys):
        code = main(
            ["a question", "--sql-only"], agent=FakeAgent(success_record())
        )

        out = capsys.readouterr().out.strip()

        assert code == EXIT_OK
        assert out == data.VALID_SQL

    def test_sql_only_reports_when_no_sql_was_produced(self, capsys):
        code = main(
            ["a question", "--sql-only"], agent=FakeAgent(failure_record())
        )

        assert code == EXIT_FAILED
        assert "No SQL was produced" in capsys.readouterr().err

    def test_trace_mode_shows_node_timings(self, capsys):
        main(
            ["a question", "--trace"], agent=FakeAgent(success_record())
        )

        out = capsys.readouterr().out

        assert "TRACE" in out
        assert "get_schema" in out

    def test_default_output_hides_the_trace(self, capsys):
        main(["a question"], agent=FakeAgent(success_record()))

        assert "TRACE" not in capsys.readouterr().out

    def test_footer_summarises_cost_and_timing(self, capsys):
        main(["a question"], agent=FakeAgent(success_record()))

        out = capsys.readouterr().out

        assert "repairs 1" in out
        assert "rows 3" in out
        assert "tokens 840" in out

    def test_question_is_passed_through_verbatim(self):
        agent = FakeAgent(success_record())

        main(["  Which product sold best?  "], agent=agent)

        assert agent.questions == ["  Which product sold best?  "]


class TestEndToEndThroughTheRealAgent:
    """Drive the CLI through the real graph, only the services are faked."""

    def test_cli_answers_a_question_over_fakes(self, capsys):
        from src.agent import InsightsAgent
        from tests.fakes.bigquery import FakeBigQueryService

        agent = InsightsAgent(
            bigquery_service=FakeBigQueryService(),
            llm=ScriptedLLMClient(
                [data.VALID_SQL, WELL_FORMED_ANALYSIS]
            ),
            relationships=list(data.RELATIONSHIPS),
        )

        code = main(["What were net sales by month?"], agent=agent)

        out = capsys.readouterr().out

        assert code == EXIT_OK
        assert "DIRECT ANSWER" in out
        assert "total_net_sales" in out

    def test_empty_question_is_rejected_by_the_graph(self):
        """The graph must refuse before spending an LLM call or a query."""

        from src.agent import InsightsAgent
        from tests.fakes.bigquery import FakeBigQueryService

        service = FakeBigQueryService()
        llm = ScriptedLLMClient([])

        agent = InsightsAgent(
            bigquery_service=service,
            llm=llm,
            relationships=list(data.RELATIONSHIPS),
        )

        record = agent.run("   ")

        assert record["outcome"]["status"] == "error"
        assert record["outcome"]["terminal_stage"] == "question_validation"
        assert llm.call_count == 0
        assert service.run_query_calls == []
