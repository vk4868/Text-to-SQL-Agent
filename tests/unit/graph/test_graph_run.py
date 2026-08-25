"""Gate 7: the graph is a single, fully-instrumented orchestrator.

Every test here drives the real compiled graph over fakes, so routing,
reducers, instrumentation and the run record are exercised together rather
than asserted in isolation.
"""

import json

import pytest
from google.api_core.exceptions import BadRequest, GoogleAPIError

from src.exceptions import LLMProviderError, QueryExecutionTimeoutError
from src.graph.builder import (
    build_insights_graph,
    build_schema_graph,
    build_sql_execution_graph,
    build_sql_generation_graph,
    build_sql_repair_graph,
)
from src.llm.scripted import ScriptedLLMClient
from src.repair_policy import REPAIRABLE_STAGES, should_repair
from tests.fakes import data
from tests.fakes.bigquery import FakeBigQueryService
from tests.fakes.factories import (
    WELL_FORMED_ANALYSIS,
    initial_state,
    make_insights_graph,
    make_nodes,
)

GOOD_SQL = data.VALID_SQL
UNQUALIFIED_SQL = "SELECT SUM(net_revenue) AS total FROM fact_sales"


def run_graph(graph, question="What were net sales by month?"):
    return graph.invoke(initial_state(question))


def read_records(run_log_path):
    if not run_log_path.exists():
        return []
    return [
        json.loads(line)
        for line in run_log_path.read_text().splitlines()
        if line.strip()
    ]


def graph_records(run_log_path):
    return [
        record
        for record in read_records(run_log_path)
        if record.get("event") == "graph_run"
    ]


class TestSuccessPath:
    def test_question_reaches_a_business_analysis(self):
        graph = make_insights_graph(
            llm=ScriptedLLMClient([GOOD_SQL, WELL_FORMED_ANALYSIS])
        )

        final = run_graph(graph)

        assert final["status"] == "success"
        assert final["terminal_stage"] == "complete"
        assert final["generated_sql"] == GOOD_SQL
        assert final["final_sql"] == GOOD_SQL
        assert "DIRECT ANSWER" in final["business_analysis"]
        assert final["repair_attempts"] == 0
        assert not final["error_stage"]

    def test_run_identity_is_stamped(self):
        import uuid

        graph = make_insights_graph(
            llm=ScriptedLLMClient([GOOD_SQL, WELL_FORMED_ANALYSIS])
        )

        final = run_graph(graph)

        # Raises if it is not a valid uuid4.
        uuid.UUID(final["graph_run_id"], version=4)

    def test_each_run_gets_a_distinct_id(self):
        first = run_graph(
            make_insights_graph(
                llm=ScriptedLLMClient([GOOD_SQL, WELL_FORMED_ANALYSIS])
            )
        )
        second = run_graph(
            make_insights_graph(
                llm=ScriptedLLMClient([GOOD_SQL, WELL_FORMED_ANALYSIS])
            )
        )

        assert first["graph_run_id"] != second["graph_run_id"]

    def test_execution_run_id_links_to_the_pipeline_record(
        self, run_log_path
    ):
        graph = make_insights_graph(
            llm=ScriptedLLMClient([GOOD_SQL, WELL_FORMED_ANALYSIS])
        )

        final = run_graph(graph)

        linked = final["sql_execution_run_ids"]
        assert len(linked) == 1

        execution_records = [
            record
            for record in read_records(run_log_path)
            if record.get("event") == "sql_execution"
        ]

        assert execution_records[0]["run_id"] == linked[0]


class TestNodeTrace:
    """Instrumentation must cover every node and every exit path."""

    def test_one_entry_per_executed_node_in_order(self):
        graph = make_insights_graph(
            llm=ScriptedLLMClient([GOOD_SQL, WELL_FORMED_ANALYSIS])
        )

        final = run_graph(graph)

        assert [entry["node"] for entry in final["node_trace"]] == [
            "get_schema",
            "generate_sql",
            "execute_sql",
            "analyze_result",
        ]
        assert all(entry["ok"] for entry in final["node_trace"])
        assert all(
            entry["duration_ms"] >= 0 for entry in final["node_trace"]
        )

    def test_failing_node_is_traced_as_not_ok(self):
        failing = FakeBigQueryService(
            dataset_structure=[],
        )
        failing.get_dataset_structure = _raise(  # type: ignore[method-assign]
            GoogleAPIError("dataset unavailable")
        )

        graph = make_insights_graph(bigquery_service=failing)

        final = run_graph(graph)

        assert len(final["node_trace"]) == 1
        entry = final["node_trace"][0]
        assert entry["node"] == "get_schema"
        assert entry["ok"] is False
        assert entry["error_stage"] == "schema_retrieval"

    def test_repair_run_traces_execute_twice(self):
        graph = _repair_then_success_graph()

        final = run_graph(graph)

        nodes_visited = [entry["node"] for entry in final["node_trace"]]

        assert nodes_visited.count("execute_sql") == 2
        assert nodes_visited.count("repair_sql") == 1
        assert nodes_visited == [
            "get_schema",
            "generate_sql",
            "execute_sql",
            "repair_sql",
            "execute_sql",
            "analyze_result",
        ]


class TestAccumulatorsDoNotDuplicate:
    """The reducer contract: nodes return only their new entries."""

    def test_repair_history_has_one_entry_per_repair(self):
        final = run_graph(_repair_then_success_graph())

        assert final["repair_attempts"] == 1
        assert len(final["repair_history"]) == 1
        assert final["repair_history"][0]["repair_attempt"] == 1
        assert (
            final["repair_history"][0]["failure_stage"] == "table_access"
        )

    def test_two_repairs_accumulate_without_duplication(self):
        # Fails table access twice, then succeeds.
        llm = ScriptedLLMClient(
            [
                UNQUALIFIED_SQL,
                UNQUALIFIED_SQL,
                GOOD_SQL,
                WELL_FORMED_ANALYSIS,
            ]
        )
        graph = make_insights_graph(llm=llm, max_repair_attempts=2)

        final = run_graph(graph)

        assert final["status"] == "success"
        assert final["repair_attempts"] == 2
        assert [
            entry["repair_attempt"] for entry in final["repair_history"]
        ] == [1, 2]

    def test_node_trace_is_not_duplicated_across_nodes(self):
        final = run_graph(
            make_insights_graph(
                llm=ScriptedLLMClient([GOOD_SQL, WELL_FORMED_ANALYSIS])
            )
        )

        # Four nodes ran; a whole-list return would have produced 1+2+3+4.
        assert len(final["node_trace"]) == 4


class TestTokenCapture:
    def test_every_llm_call_is_recorded_in_order(self):
        llm = ScriptedLLMClient(
            [UNQUALIFIED_SQL, GOOD_SQL, WELL_FORMED_ANALYSIS],
            input_tokens=100,
            output_tokens=20,
        )
        graph = make_insights_graph(llm=llm, max_repair_attempts=1)

        final = run_graph(graph)

        assert [call["purpose"] for call in final["llm_calls"]] == [
            "sql_generation",
            "sql_repair",
            "result_analysis",
        ]

    def test_totals_roll_up_into_the_run_record(self):
        llm = ScriptedLLMClient(
            [GOOD_SQL, WELL_FORMED_ANALYSIS],
            input_tokens=300,
            output_tokens=120,
        )
        graph = make_insights_graph(llm=llm)

        final = run_graph(graph)
        totals = final["run_record"]["tokens"]["totals"]

        assert totals["llm_call_count"] == 2
        assert totals["input_tokens"] == 600
        assert totals["output_tokens"] == 240
        assert totals["total_tokens"] == 840

    def test_prompt_text_is_not_stored_in_the_record(self):
        graph = make_insights_graph(
            llm=ScriptedLLMClient([GOOD_SQL, WELL_FORMED_ANALYSIS])
        )

        final = run_graph(graph)

        for call in final["run_record"]["tokens"]["llm_calls"]:
            assert "text" not in call


class TestTerminalPaths:
    """Five ways a run can end; all must be classified and recorded once."""

    def test_schema_failure(self, run_log_path):
        failing = FakeBigQueryService()
        failing.get_dataset_structure = _raise(  # type: ignore[method-assign]
            GoogleAPIError("dataset unavailable")
        )

        final = run_graph(make_insights_graph(bigquery_service=failing))

        assert final["status"] == "error"
        assert final["terminal_stage"] == "schema_retrieval"
        assert "dataset unavailable" in final["error_message"]
        assert len(graph_records(run_log_path)) == 1

    def test_generation_failure(self, run_log_path):
        graph = make_insights_graph(
            llm=ScriptedLLMClient([LLMProviderError("ollama is down")])
        )

        final = run_graph(graph)

        assert final["status"] == "error"
        assert final["terminal_stage"] == "sql_generation"
        assert "ollama is down" in final["error_message"]
        assert len(graph_records(run_log_path)) == 1

    def test_non_repairable_execution_failure(self, run_log_path):
        service = FakeBigQueryService(
            run_query_error=QueryExecutionTimeoutError(
                job_id="job-1",
                timeout_seconds=30,
                cancel_requested=True,
            )
        )
        graph = make_insights_graph(
            bigquery_service=service,
            llm=ScriptedLLMClient([GOOD_SQL]),
        )

        final = run_graph(graph)

        assert final["status"] == "error"
        assert final["terminal_stage"] == "execution_timeout"
        assert len(graph_records(run_log_path)) == 1

    def test_repair_exhausted(self, run_log_path):
        # Dry run always fails, so every attempt is repairable but never fixed.
        service = FakeBigQueryService(
            dry_run_error=BadRequest("Unrecognized name: revenue")
        )
        llm = ScriptedLLMClient([GOOD_SQL, GOOD_SQL, GOOD_SQL])
        graph = make_insights_graph(
            bigquery_service=service, llm=llm, max_repair_attempts=2
        )

        final = run_graph(graph)

        assert final["status"] == "error"
        assert final["terminal_stage"] == "dry_run"
        assert final["repair_attempts"] == 2
        assert len(final["repair_history"]) == 2
        assert len(graph_records(run_log_path)) == 1

    def test_guardrail_rejection_is_reported_as_rejected(
        self, run_log_path
    ):
        """A refused query is not the same outcome as a broken one."""

        llm = ScriptedLLMClient([UNQUALIFIED_SQL, UNQUALIFIED_SQL])
        graph = make_insights_graph(llm=llm, max_repair_attempts=1)

        final = run_graph(graph)

        assert final["status"] == "rejected"
        assert final["terminal_stage"] == "table_access"
        assert len(graph_records(run_log_path)) == 1

    def test_success(self, run_log_path):
        graph = make_insights_graph(
            llm=ScriptedLLMClient([GOOD_SQL, WELL_FORMED_ANALYSIS])
        )

        final = run_graph(graph)

        assert final["status"] == "success"
        assert len(graph_records(run_log_path)) == 1


class TestRunRecord:
    def test_record_is_written_once_with_the_expected_shape(
        self, run_log_path
    ):
        graph = make_insights_graph(
            llm=ScriptedLLMClient([GOOD_SQL, WELL_FORMED_ANALYSIS])
        )

        final = run_graph(graph)
        records = graph_records(run_log_path)

        assert len(records) == 1
        record = records[0]

        assert record["graph_run_id"] == final["graph_run_id"]
        assert record["record_version"] == 1
        for section in (
            "governance",
            "outcome",
            "sql",
            "data",
            "cost",
            "runtime",
            "tokens",
            "analysis",
        ):
            assert section in record, f"missing section: {section}"

    def test_record_is_json_serialisable(self, run_log_path):
        run_graph(
            make_insights_graph(
                llm=ScriptedLLMClient([GOOD_SQL, WELL_FORMED_ANALYSIS])
            )
        )

        # Reading it back from disk is the round-trip proof.
        assert graph_records(run_log_path)[0]["outcome"]["status"] == (
            "success"
        )

    def test_governance_captures_the_caps_in_force(self, run_log_path):
        run_graph(
            make_insights_graph(
                llm=ScriptedLLMClient([GOOD_SQL, WELL_FORMED_ANALYSIS])
            )
        )

        governance = graph_records(run_log_path)[0]["governance"]

        assert governance["config"]["max_result_rows"] > 0
        assert governance["config"]["max_query_bytes"] > 0
        assert governance["model_name"] == "scripted-model"

    def test_runtime_section_links_timings(self, run_log_path):
        run_graph(
            make_insights_graph(
                llm=ScriptedLLMClient([GOOD_SQL, WELL_FORMED_ANALYSIS])
            )
        )

        runtime = graph_records(run_log_path)[0]["runtime"]

        assert runtime["total_duration_ms"] >= 0
        assert len(runtime["node_trace"]) == 4
        assert runtime["stage_timings_ms"]
        assert len(runtime["sql_execution_run_ids"]) == 1

    def test_failed_run_record_tolerates_missing_sections(
        self, run_log_path
    ):
        graph = make_insights_graph(
            llm=ScriptedLLMClient([LLMProviderError("down")])
        )

        run_graph(graph)
        record = graph_records(run_log_path)[0]

        assert record["sql"]["final_sql"] is None
        assert record["data"]["row_count"] is None
        assert record["analysis"]["business_analysis"] is None
        assert record["tokens"]["totals"]["llm_call_count"] == 0


class TestRepairPolicy:
    @pytest.mark.parametrize(
        "stage", sorted(REPAIRABLE_STAGES)
    )
    def test_repairable_stages_allow_a_retry(self, stage):
        assert should_repair(
            failure_stage=stage, repairs_used=0, max_repair_attempts=2
        )

    @pytest.mark.parametrize(
        "stage",
        ["cost_check", "result_limit", "execution", "execution_timeout"],
    )
    def test_other_stages_never_retry(self, stage):
        assert not should_repair(
            failure_stage=stage, repairs_used=0, max_repair_attempts=2
        )

    def test_budget_is_respected(self):
        assert not should_repair(
            failure_stage="dry_run",
            repairs_used=2,
            max_repair_attempts=2,
        )

    def test_zero_budget_disables_repair(self):
        service = FakeBigQueryService(
            dry_run_error=BadRequest("Unrecognized name: revenue")
        )
        graph = make_insights_graph(
            bigquery_service=service,
            llm=ScriptedLLMClient([GOOD_SQL]),
            max_repair_attempts=0,
        )

        final = run_graph(graph)

        assert final["repair_attempts"] == 0
        assert final["repair_history"] == []


class TestAllBuildersStillCompile:
    """The four incremental builders must keep working."""

    def test_schema_graph(self):
        graph = build_schema_graph(nodes=make_nodes())

        final = graph.invoke(initial_state())

        assert "fact_sales" in final["schema_document"]

    def test_generation_graph(self):
        graph = build_sql_generation_graph(
            nodes=make_nodes(llm=ScriptedLLMClient([GOOD_SQL]))
        )

        final = graph.invoke(initial_state())

        assert final["generated_sql"] == GOOD_SQL

    def test_execution_graph(self):
        graph = build_sql_execution_graph(
            nodes=make_nodes(llm=ScriptedLLMClient([GOOD_SQL]))
        )

        final = graph.invoke(initial_state())

        assert final["execution_result"]["status"] == "success"

    def test_repair_graph_stops_before_analysis(self):
        """This slice routes "analyze" to END on purpose."""

        graph = build_sql_repair_graph(
            nodes=make_nodes(llm=ScriptedLLMClient([GOOD_SQL]))
        )

        final = graph.invoke(initial_state())

        assert final["execution_result"]["status"] == "success"
        assert "business_analysis" not in final

    def test_smaller_builders_write_no_graph_run_record(
        self, run_log_path
    ):
        graph = build_sql_execution_graph(
            nodes=make_nodes(llm=ScriptedLLMClient([GOOD_SQL]))
        )

        graph.invoke(initial_state())

        assert graph_records(run_log_path) == []


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def _raise(error):
    def _raiser(*args, **kwargs):
        raise error

    return _raiser


def _repair_then_success_graph():
    """First attempt fails table access, the repair succeeds."""

    return make_insights_graph(
        llm=ScriptedLLMClient(
            [UNQUALIFIED_SQL, GOOD_SQL, WELL_FORMED_ANALYSIS]
        ),
        max_repair_attempts=2,
    )
